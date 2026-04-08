# ButterVMS (Backend First)

This is a fresh backend-first rebuild.

The backend is API-driven and launches real browser desktop VM containers (KasmVNC-style behavior, but custom implementation).

## What is implemented

- Flask backend API
- SQLite session storage
- Docker-based VM runtime (`jlesage/firefox`)
- Per-session random mapped ports (`5800/tcp` and `5900/tcp` mapped dynamically)
- Session ownership controls (`owner_id` required for user operations)
- Automatic expiry sweeper thread
- Admin API endpoint protected by header token

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
    "minutes": 45,
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

## Notes

- Everyone gets different VM ports because each container publishes to random host ports.
- This stage is backend-first only; UI comes next.
