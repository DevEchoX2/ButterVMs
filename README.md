# ButterVMS (Backend First)

This is the v2 backend-first runtime.

The backend is API-driven and launches real browser desktop VM containers (KasmVNC-style behavior, but custom implementation).

## What is implemented

- Flask backend API
- SQLite session storage
- Docker-based VM runtime (`jlesage/firefox`)
- Per-session random mapped ports (`5800/tcp` and `5900/tcp` mapped dynamically)
- Session ownership controls (`owner_id` required for user operations)
- Automatic expiry sweeper thread
- Admin API endpoint protected by header token
- Production Gunicorn serving with container health checks
- VM readiness checks before a session is returned
- Password-gated, one-time six-digit resume codes

## Run locally

```bash
docker compose up -d --build
```

Open API root:
- http://localhost:8000

Health check:
- http://localhost:8000/health

## API quick start

### 1. Create VM session

```bash
curl -s -X POST http://localhost:8000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"tier":"standard"}'
```

Example response:

```json
{
  "ok": true,
  "message": "VM started.",
  "session": {
    "session_id": "...",
    "owner_id": "...",
    "tier": "standard",
    "minutes": 120,
    "vm_url": "http://your-host:32768",
    "web_port": 32768,
    "vnc_port": 32769
  }
}
```

### 2. Read session

```bash
curl -s "http://localhost:8000/api/sessions/<session_id>?owner_id=<owner_id>"
```

### Resume an expired VM once

Enter the VM password (`Celsius001` by default) to issue a six-digit code. The API stores only a hash of the code; the UI keeps the code in the browser sidebar for reference.

```bash
curl -s -X POST http://localhost:8000/api/sessions/<session_id>/resume-code \
  -H "Content-Type: application/json" \
  -d '{"owner_id":"<owner_id>","password":"Celsius001"}'
```

After the session expires, redeem it once:

```bash
curl -s -X POST http://localhost:8000/api/sessions/<session_id>/resume \
  -H "Content-Type: application/json" \
  -d '{"owner_id":"<owner_id>","resume_code":"123456"}'
```

Expired containers are stopped but retained, so the same browser desktop filesystem can be restored. A redeemed code cannot be reused.

### 3. Stop session

```bash
curl -s -X POST http://localhost:8000/api/sessions/<session_id>/stop \
  -H "Content-Type: application/json" \
  -d '{"owner_id":"<owner_id>"}'
```

### 4. Delete session record

```bash
curl -s -X DELETE http://localhost:8000/api/sessions/<session_id> \
  -H "Content-Type: application/json" \
  -d '{"owner_id":"<owner_id>"}'
```

### 5. Admin list sessions

```bash
curl -s http://localhost:8000/api/admin/sessions \
  -H "X-Admin-Token: replace-with-private-admin-token"
```

## Environment

See `.env.example` for all variables.

Key variables:

- `BUTTERVMS_VNC_IMAGE`
- `BUTTERVMS_PUBLIC_VM_HOST`
- `BUTTERVMS_PUBLIC_VM_SCHEME`
- `BUTTERVMS_PUBLIC_VM_HOST_TEMPLATE`
- `BUTTERVMS_ADMIN_API_TOKEN`
- `BUTTERVMS_VERSION` and `BUTTERVMS_VM_READY_TIMEOUT_SECONDS`
- `BUTTERVMS_VM_PASSWORD` and `BUTTERVMS_CORS_ORIGIN`

## Notes

- Everyone gets different VM ports because each container publishes to random host ports.
- Standard sessions last 2 hours by default. The VM image remains configurable with `BUTTERVMS_VNC_IMAGE` so a tested image tag or digest can be promoted without code changes.
- This stage is backend-first only; UI and a serverless control plane can be added later. VM containers still require a Docker-capable host.
