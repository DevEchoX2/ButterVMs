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
from flask import Flask, jsonify, request


@dataclass(frozen=True)
class Config:
    secret_key: str
    db_path: str
    debug: bool
    sweeper_seconds: int
    vnc_image: str
    container_prefix: str
    public_vm_host: str
    public_vm_scheme: str
    public_vm_host_template: str
    standard_minutes: int
    premium_minutes: int
    admin_api_token: str


def load_config() -> Config:
    return Config(
        secret_key=os.getenv("BUTTERVMS_SECRET_KEY", "dev-secret-change-me"),
        db_path=os.getenv("BUTTERVMS_DB_PATH", "buttervms.db"),
        debug=os.getenv("BUTTERVMS_DEBUG", "0") == "1",
        sweeper_seconds=int(os.getenv("BUTTERVMS_SWEEPER_SECONDS", "30")),
        vnc_image=os.getenv("BUTTERVMS_VNC_IMAGE", "jlesage/firefox:latest"),
        container_prefix=os.getenv("BUTTERVMS_CONTAINER_PREFIX", "buttervms-session"),
        public_vm_host=os.getenv("BUTTERVMS_PUBLIC_VM_HOST", "").strip(),
        public_vm_scheme=os.getenv("BUTTERVMS_PUBLIC_VM_SCHEME", "http").strip().lower(),
        public_vm_host_template=os.getenv("BUTTERVMS_PUBLIC_VM_HOST_TEMPLATE", "").strip(),
        standard_minutes=int(os.getenv("BUTTERVMS_STANDARD_MINUTES", "45")),
        premium_minutes=int(os.getenv("BUTTERVMS_PREMIUM_MINUTES", "480")),
        admin_api_token=os.getenv("BUTTERVMS_ADMIN_API_TOKEN", "").strip(),
    )


CONFIG = load_config()
DOCKER_CLIENT = docker.DockerClient(base_url="unix://var/run/docker.sock")

app = Flask(__name__)
app.secret_key = CONFIG.secret_key


@dataclass(frozen=True)
class Tier:
    key: str
    minutes: int
    cpu: int
    ram_gb: int


TIERS: dict[str, Tier] = {
    "standard": Tier("standard", CONFIG.standard_minutes, 2, 4),
    "premium": Tier("premium", CONFIG.premium_minutes, 6, 16),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def host_only(value: str) -> str:
    if not value:
        return "127.0.0.1"
    return value.split(":", 1)[0]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(CONFIG.db_path)
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
                owner_id TEXT NOT NULL,
                status TEXT NOT NULL,
                container_name TEXT NOT NULL,
                web_port INTEGER NOT NULL,
                vnc_port INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "owner_id" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT DEFAULT ''")
        if "tier_key" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN tier_key TEXT DEFAULT 'standard'")
        if "vm_reference" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN vm_reference TEXT DEFAULT ''")
        if "container_name" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN container_name TEXT DEFAULT ''")
        if "web_port" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN web_port INTEGER DEFAULT 0")
        if "vnc_port" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN vnc_port INTEGER DEFAULT 0")


def mapped_port(container_name: str, internal_port: str) -> tuple[bool, int | None, str]:
    try:
        container = DOCKER_CLIENT.containers.get(container_name)
        container.reload()
    except DockerException as exc:
        return False, None, str(exc)

    ports = container.attrs.get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get(internal_port)
    if not bindings:
        return False, None, "No port bindings found."

    host_port = bindings[0].get("HostPort", "")
    if not host_port:
        return False, None, "Empty host port."

    try:
        return True, int(host_port), ""
    except ValueError:
        return False, None, f"Invalid host port: {host_port}"


def stop_container(container_name: str) -> tuple[bool, str]:
    if not container_name:
        return True, "No container bound."

    try:
        container = DOCKER_CLIENT.containers.get(container_name)
        container.remove(force=True)
        return True, "Container stopped."
    except NotFound:
        return True, "Container already removed."
    except DockerException as exc:
        return False, str(exc)


def build_vm_url(request_host: str, web_port: int) -> str:
    if CONFIG.public_vm_host_template:
        host = CONFIG.public_vm_host_template.format(port=web_port)
        return f"{CONFIG.public_vm_scheme}://{host}"

    if CONFIG.public_vm_host:
        host = host_only(CONFIG.public_vm_host)
    else:
        host = host_only(request_host)

    return f"{CONFIG.public_vm_scheme}://{host}:{web_port}"


def create_session_record(
    session_id: str,
    vm_reference: str,
    tier_key: str,
    owner_id: str,
    container_name: str,
    web_port: int,
    vnc_port: int,
    expires_at: str,
) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, vm_reference, tier_key, owner_id, status, container_name, web_port, vnc_port, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                vm_reference,
                tier_key,
                owner_id,
                "running",
                container_name,
                web_port,
                vnc_port,
                utc_text(now_utc()),
                expires_at,
            ),
        )


def get_session(session_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()


def set_session_status(session_id: str, status: str) -> None:
    with get_db() as conn:
        conn.execute("UPDATE sessions SET status = ? WHERE session_id = ?", (status, session_id))


def delete_session(session_id: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def admin_allowed() -> bool:
    token = request.headers.get("X-Admin-Token", "").strip()
    return bool(CONFIG.admin_api_token) and token == CONFIG.admin_api_token


def session_access_allowed(row: sqlite3.Row, owner_id: str) -> bool:
    return row["owner_id"] == owner_id


def launch_vm(tier: Tier) -> tuple[bool, str, dict[str, int | str] | None]:
    session_id = uuid4().hex
    vm_reference = f"bvm-{session_id[:10]}"
    owner_id = uuid4().hex
    container_name = f"{CONFIG.container_prefix}-{session_id[:12]}"

    common = {
        "name": container_name,
        "detach": True,
        "shm_size": "1g",
        "ports": {"5800/tcp": None, "5900/tcp": None},
        "environment": {
            "VNC_PASSWORD": "buttervms",
            "KEEP_APP_RUNNING": "1",
            "DISPLAY_WIDTH": "1366",
            "DISPLAY_HEIGHT": "768",
        },
        "auto_remove": False,
    }

    try:
        DOCKER_CLIENT.containers.run(
            CONFIG.vnc_image,
            nano_cpus=tier.cpu * 1_000_000_000,
            mem_limit=f"{tier.ram_gb}g",
            **common,
        )
    except DockerException:
        try:
            DOCKER_CLIENT.containers.run(CONFIG.vnc_image, **common)
        except DockerException as exc:
            return False, f"Failed to start VM container: {exc}", None

    web_ok, web_port, web_message = mapped_port(container_name, "5800/tcp")
    vnc_ok, vnc_port, vnc_message = mapped_port(container_name, "5900/tcp")

    if not web_ok or not vnc_ok or web_port is None or vnc_port is None:
        stop_container(container_name)
        return False, f"VM launched but port mapping failed: {web_message or vnc_message}", None

    expires = now_utc() + timedelta(minutes=tier.minutes)
    create_session_record(
        session_id=session_id,
        vm_reference=vm_reference,
        tier_key=tier.key,
        owner_id=owner_id,
        container_name=container_name,
        web_port=web_port,
        vnc_port=vnc_port,
        expires_at=utc_text(expires),
    )

    return True, "VM started.", {
        "session_id": session_id,
        "vm_reference": vm_reference,
        "owner_id": owner_id,
        "container_name": container_name,
        "web_port": web_port,
        "vnc_port": vnc_port,
    }


def sweeper_loop() -> None:
    while True:
        cutoff = utc_text(now_utc())
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT session_id, container_name
                FROM sessions
                WHERE status = 'running' AND datetime(expires_at) <= datetime(?)
                """,
                (cutoff,),
            ).fetchall()

        for row in rows:
            stop_container(row["container_name"])
            set_session_status(row["session_id"], "expired")

        time.sleep(CONFIG.sweeper_seconds)


def start_sweeper() -> None:
    worker = threading.Thread(target=sweeper_loop, daemon=True)
    worker.start()


@app.get("/")
def root():
    return jsonify(
        {
            "name": "ButterVMS Backend",
            "status": "ok",
            "mode": "backend-first",
            "endpoints": [
                "GET /health",
                "POST /api/sessions",
                "GET /api/sessions/<session_id>?owner_id=...",
                "POST /api/sessions/<session_id>/stop",
                "DELETE /api/sessions/<session_id>",
                "GET /api/admin/sessions",
            ],
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "time": utc_text(now_utc())})


@app.post("/api/sessions")
def create_session():
    payload = request.get_json(silent=True) or {}
    tier_key = str(payload.get("tier", "standard")).lower().strip()
    tier = TIERS.get(tier_key)
    if not tier:
        return jsonify({"error": "invalid_tier", "allowed": list(TIERS.keys())}), 400

    ok, message, data = launch_vm(tier)
    if not ok or data is None:
        return jsonify({"error": "launch_failed", "message": message}), 500

    vm_url = build_vm_url(request.host, int(data["web_port"]))
    return jsonify(
        {
            "ok": True,
            "message": message,
            "session": {
                "session_id": data["session_id"],
                "owner_id": data["owner_id"],
                "vm_reference": data["vm_reference"],
                "tier": tier.key,
                "minutes": tier.minutes,
                "vm_url": vm_url,
                "web_port": data["web_port"],
                "vnc_port": data["vnc_port"],
            },
        }
    )


@app.get("/api/sessions/<session_id>")
def fetch_session(session_id: str):
    owner_id = request.args.get("owner_id", "").strip()
    if not owner_id:
        return jsonify({"error": "owner_id_required"}), 400

    row = get_session(session_id)
    if not row:
        return jsonify({"error": "not_found"}), 404

    if not session_access_allowed(row, owner_id):
        return jsonify({"error": "forbidden"}), 403

    return jsonify(
        {
            "session_id": row["session_id"],
            "vm_reference": row["vm_reference"],
            "tier": row["tier_key"],
            "status": row["status"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "vm_url": build_vm_url(request.host, row["web_port"]),
            "web_port": row["web_port"],
            "vnc_port": row["vnc_port"],
        }
    )


@app.post("/api/sessions/<session_id>/stop")
def stop_session(session_id: str):
    payload = request.get_json(silent=True) or {}
    owner_id = str(payload.get("owner_id", "")).strip()
    if not owner_id:
        return jsonify({"error": "owner_id_required"}), 400

    row = get_session(session_id)
    if not row:
        return jsonify({"error": "not_found"}), 404

    if not session_access_allowed(row, owner_id):
        return jsonify({"error": "forbidden"}), 403

    if row["status"] == "running":
        ok, message = stop_container(row["container_name"])
        if not ok:
            return jsonify({"error": "stop_failed", "message": message}), 500
        set_session_status(session_id, "stopped")

    return jsonify({"ok": True, "status": "stopped"})


@app.delete("/api/sessions/<session_id>")
def remove_session(session_id: str):
    payload = request.get_json(silent=True) or {}
    owner_id = str(payload.get("owner_id", "")).strip()
    if not owner_id:
        return jsonify({"error": "owner_id_required"}), 400

    row = get_session(session_id)
    if not row:
        return jsonify({"error": "not_found"}), 404

    if not session_access_allowed(row, owner_id):
        return jsonify({"error": "forbidden"}), 403

    if row["status"] == "running":
        stop_container(row["container_name"])
        set_session_status(session_id, "stopped")

    delete_session(session_id)
    return jsonify({"ok": True, "deleted": session_id})


@app.get("/api/admin/sessions")
def admin_sessions():
    if not admin_allowed():
        return jsonify({"error": "forbidden"}), 403

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT session_id, vm_reference, tier_key, owner_id, status, web_port, vnc_port, created_at, expires_at
            FROM sessions
            ORDER BY datetime(created_at) DESC
            """
        ).fetchall()

    items = []
    for row in rows:
        items.append(
            {
                "session_id": row["session_id"],
                "vm_reference": row["vm_reference"],
                "tier": row["tier_key"],
                "owner_id": row["owner_id"],
                "status": row["status"],
                "web_port": row["web_port"],
                "vnc_port": row["vnc_port"],
                "vm_url": build_vm_url(request.host, row["web_port"]),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
        )

    return jsonify({"count": len(items), "sessions": items})


def boot() -> None:
    init_db()
    start_sweeper()


boot()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=CONFIG.debug)
