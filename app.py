from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import sqlite3
import threading
import time
from uuid import uuid4

import docker
from docker.errors import DockerException, NotFound
from flask import Flask, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)

# Demo BTC address placeholder. Replace this with your real payout wallet.
BTC_WALLET_ADDRESS = "bc1qbuttervmsxxxxxxxxxxxxxxxxxxxxxxxxxxx"
DB_PATH = os.getenv("BUTTERVMS_DB_PATH", "buttervms.db")
VNC_IMAGE = os.getenv("BUTTERVMS_VNC_IMAGE", "dorowu/ubuntu-desktop-lxde-vnc:latest")
CONTAINER_PREFIX = os.getenv("BUTTERVMS_CONTAINER_PREFIX", "buttervms-session")
SESSION_SWEEPER_SECONDS = int(os.getenv("BUTTERVMS_SWEEPER_SECONDS", "30"))
_DOCKER_CLIENT = docker.DockerClient(base_url="unix://var/run/docker.sock")


@dataclass(frozen=True)
class PerformanceTier:
    key: str
    name: str
    price_label: str
    cpu: int
    ram_gb: int
    storage_gb: int
    vnc_profile: str
    description: str
    available: bool
    session_minutes: int


TIERS: dict[str, PerformanceTier] = {
    "standard": PerformanceTier(
        key="standard",
        name="Standard",
        price_label="Free",
        cpu=2,
        ram_gb=4,
        storage_gb=25,
        vnc_profile="KasmVNC Standard Performance",
        description="Great for light browsing, terminal work, and basic development.",
        available=True,
        session_minutes=45,
    ),
    "premium": PerformanceTier(
        key="premium",
        name="Premium",
        price_label="Paid (BTC)",
        cpu=6,
        ram_gb=16,
        storage_gb=100,
        vnc_profile="KasmVNC Premium Performance",
        description="Higher CPU and memory for heavy workloads. Browser session lasts up to 8 hours.",
        available=True,
        session_minutes=8 * 60,
    ),
}


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    vm_reference: str
    tier_key: str
    status: str
    web_port: int
    vnc_port: int
    container_name: str
    payment_reference: str
    created_at: str
    expires_at: str


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def display_utc(value: str) -> str:
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                vm_reference TEXT NOT NULL,
                tier_key TEXT NOT NULL,
                status TEXT NOT NULL,
                web_port INTEGER NOT NULL,
                vnc_port INTEGER NOT NULL,
                container_name TEXT NOT NULL,
                payment_reference TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )


def row_to_session(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        session_id=row["session_id"],
        vm_reference=row["vm_reference"],
        tier_key=row["tier_key"],
        status=row["status"],
        web_port=row["web_port"],
        vnc_port=row["vnc_port"],
        container_name=row["container_name"],
        payment_reference=row["payment_reference"] or "",
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


def save_session(record: SessionRecord) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, vm_reference, tier_key, status, web_port, vnc_port,
                container_name, payment_reference, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.session_id,
                record.vm_reference,
                record.tier_key,
                record.status,
                record.web_port,
                record.vnc_port,
                record.container_name,
                record.payment_reference,
                record.created_at,
                record.expires_at,
            ),
        )


def get_session(session_id: str) -> SessionRecord | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return row_to_session(row)


def update_session_status(session_id: str, status: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE sessions SET status = ? WHERE session_id = ?", (status, session_id))


def get_live_sessions() -> list[SessionRecord]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sessions
            WHERE status = 'running'
            ORDER BY datetime(created_at) DESC
            """
        ).fetchall()
    return [row_to_session(row) for row in rows]


def stop_container(container_name: str) -> tuple[bool, str]:
    try:
        container = _DOCKER_CLIENT.containers.get(container_name)
        container.remove(force=True)
        return True, "Container stopped."
    except NotFound:
        return True, "Container already removed."
    except DockerException as exc:
        return False, str(exc)


def get_mapped_port(container_name: str, internal_port: str) -> tuple[bool, int | None, str]:
    try:
        container = _DOCKER_CLIENT.containers.get(container_name)
        container.reload()
    except DockerException as exc:
        return False, None, str(exc)

    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get(internal_port)
    if not bindings:
        return False, None, "Port lookup returned no bindings."
    host_port = bindings[0].get("HostPort")
    if not host_port:
        return False, None, "Port lookup returned empty host port."
    try:
        return True, int(host_port), ""
    except ValueError:
        return False, None, f"Could not parse mapped port from '{host_port}'."


def launch_session(tier: PerformanceTier, payment_reference: str) -> tuple[bool, str, SessionRecord | None]:
    session_id = uuid4().hex
    vm_reference = f"bvm-{session_id[:10]}"
    container_name = f"{CONTAINER_PREFIX}-{session_id[:12]}"

    common_kwargs = {
        "name": container_name,
        "detach": True,
        "shm_size": "1g",
        "ports": {"80/tcp": None, "5900/tcp": None},
        "environment": {
            "VNC_PASSWORD": "buttervms",
            "RESOLUTION": "1440x900",
            "USER": "butter",
            "PASSWORD": "butter",
        },
        "auto_remove": False,
    }

    try:
        _DOCKER_CLIENT.containers.run(
            VNC_IMAGE,
            nano_cpus=tier.cpu * 1_000_000_000,
            mem_limit=f"{tier.ram_gb}g",
            **common_kwargs,
        )
    except DockerException:
        # Some hosts reject strict resource reservations. Retry without hard limits.
        try:
            _DOCKER_CLIENT.containers.run(VNC_IMAGE, **common_kwargs)
        except DockerException as exc:
            return False, f"Failed to start VM container: {exc}", None

    web_ok, web_port, web_msg = get_mapped_port(container_name, "80/tcp")
    vnc_ok, vnc_port, vnc_msg = get_mapped_port(container_name, "5900/tcp")
    if not web_ok or not vnc_ok or web_port is None or vnc_port is None:
        stop_container(container_name)
        return False, f"Container started but ports were not mapped correctly: {web_msg or vnc_msg}", None

    created = now_utc()
    expires = created + timedelta(minutes=tier.session_minutes)
    record = SessionRecord(
        session_id=session_id,
        vm_reference=vm_reference,
        tier_key=tier.key,
        status="running",
        web_port=web_port,
        vnc_port=vnc_port,
        container_name=container_name,
        payment_reference=payment_reference,
        created_at=utc_text(created),
        expires_at=utc_text(expires),
    )
    save_session(record)
    return True, "VM session started successfully.", record


def host_only(host_header: str) -> str:
    if not host_header:
        return "127.0.0.1"
    return host_header.split(":", 1)[0]


def session_urls(record: SessionRecord, request_host: str) -> tuple[str, str]:
    host = host_only(request_host)
    return f"http://{host}:{record.web_port}", f"{host}:{record.vnc_port}"


def expire_sessions_loop() -> None:
    while True:
        now_text = utc_text(now_utc())
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT session_id, container_name
                FROM sessions
                WHERE status = 'running' AND datetime(expires_at) <= datetime(?)
                """,
                (now_text,),
            ).fetchall()

        for row in rows:
            stop_container(row["container_name"])
            update_session_status(row["session_id"], "expired")

        time.sleep(SESSION_SWEEPER_SECONDS)


def start_sweeper() -> None:
    sweeper = threading.Thread(target=expire_sessions_loop, daemon=True)
    sweeper.start()


def boot_runtime() -> None:
    init_db()
    start_sweeper()


@app.get("/")
def home():
    live_sessions = get_live_sessions()[:10]
    sessions_payload: list[dict[str, str]] = []
    for item in live_sessions:
        web_url, vnc_target = session_urls(item, request.host)
        sessions_payload.append(
            {
                "vm_reference": item.vm_reference,
                "tier": TIERS[item.tier_key].name,
                "web_url": web_url,
                "vnc_target": vnc_target,
                "expires_at": display_utc(item.expires_at),
            }
        )

    return render_template(
        "index.html",
        tiers=TIERS.values(),
        btc_wallet=BTC_WALLET_ADDRESS,
        live_sessions=sessions_payload,
    )


@app.post("/create-vm")
def create_vm():
    tier_key = request.form.get("tier", "standard").lower()
    selected_tier = TIERS.get(tier_key, TIERS["standard"])
    payment_reference = request.form.get("payment_reference", "").strip()

    if selected_tier.key == "premium" and not payment_reference:
        return render_template(
            "result.html",
            success=False,
            title="Payment Reference Required",
            message="Premium sessions require a BTC payment reference (transaction ID or invoice ID).",
            vm_reference="n/a",
            selected_tier=selected_tier,
            created_at=now_utc().strftime("%Y-%m-%d %H:%M UTC"),
            btc_wallet=BTC_WALLET_ADDRESS,
            launch_ok=False,
            launch_message="Submit premium again with your BTC payment reference.",
            web_url="",
            vnc_target="",
            expires_at="",
            session_id="",
            payment_reference=payment_reference,
        )

    if not selected_tier.available:
        return render_template(
            "result.html",
            success=False,
            title="Premium Is Coming Soon",
            message=(
                "Premium VM provisioning is planned but not enabled yet. "
                "You can still accept BTC for upcoming subscriptions."
            ),
            vm_reference="n/a",
            selected_tier=selected_tier,
            created_at=now_utc().strftime("%Y-%m-%d %H:%M UTC"),
            btc_wallet=BTC_WALLET_ADDRESS,
            launch_ok=False,
            launch_message="",
            web_url="",
            vnc_target="",
            expires_at="",
            session_id="",
            payment_reference=payment_reference,
        )

    launch_ok, launch_message, session = launch_session(selected_tier, payment_reference)
    web_url = ""
    vnc_target = ""
    vm_reference = "n/a"
    created_at = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    expires_at = ""
    session_id = ""
    if session:
        web_url, vnc_target = session_urls(session, request.host)
        vm_reference = session.vm_reference
        created_at = display_utc(session.created_at)
        expires_at = display_utc(session.expires_at)
        session_id = session.session_id

    return render_template(
        "result.html",
        success=launch_ok,
        title="ButterVM Created",
        message=(
            "Your Standard VM request has been accepted."
            if launch_ok
            else "Your VM request was saved, but the VNC session did not start automatically."
        ),
        vm_reference=vm_reference,
        selected_tier=selected_tier,
        created_at=created_at,
        btc_wallet=BTC_WALLET_ADDRESS,
        launch_ok=launch_ok,
        launch_message=launch_message,
        web_url=web_url,
        vnc_target=vnc_target,
        expires_at=expires_at,
        session_id=session_id,
        payment_reference=payment_reference,
    )


@app.get("/session/<session_id>")
def session_details(session_id: str):
    session = get_session(session_id)
    if not session:
        return render_template("session_not_found.html"), 404

    tier = TIERS[session.tier_key]
    web_url, vnc_target = session_urls(session, request.host)
    return render_template(
        "session.html",
        session=session,
        tier=tier,
        web_url=web_url,
        vnc_target=vnc_target,
        created_at=display_utc(session.created_at),
        expires_at=display_utc(session.expires_at),
    )


@app.post("/session/<session_id>/stop")
def stop_session(session_id: str):
    session = get_session(session_id)
    if not session:
        return redirect(url_for("home"))

    if session.status == "running":
        ok, _ = stop_container(session.container_name)
        if ok:
            update_session_status(session_id, "stopped")

    return redirect(url_for("session_details", session_id=session_id))


@app.get("/api/session/<session_id>")
def api_session(session_id: str):
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "not_found"}), 404

    web_url, vnc_target = session_urls(session, request.host)
    now = now_utc()
    expires = datetime.strptime(session.expires_at, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    remaining_seconds = int((expires - now).total_seconds())
    if remaining_seconds < 0:
        remaining_seconds = 0

    return jsonify(
        {
            "session_id": session.session_id,
            "vm_reference": session.vm_reference,
            "tier": session.tier_key,
            "status": session.status,
            "web_url": web_url,
            "vnc_target": vnc_target,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "remaining_seconds": remaining_seconds,
        }
    )


boot_runtime()


if __name__ == "__main__":
    debug_mode = os.getenv("BUTTERVMS_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=8000, debug=debug_mode)
