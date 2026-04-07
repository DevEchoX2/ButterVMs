# ButterVMS

ButterVMS is a browser-VM platform (Kasm-style experience) built with Python.

It includes:
- Full-stack control plane in [app.py](app.py)
- Browser VM sessions per request using Docker containers
- Free tier: 45-minute session limit
- Paid tier: 8-hour session limit
- BTC payment reference workflow for premium sessions
- Automatic session expiry and container cleanup
- Optional admin panel for killing sessions (disabled by default)
- Normal users do not see raw VM ports

## Full stack deployment (server)

See [DEPLOY.md](DEPLOY.md) for complete deployment instructions including local, Docker Compose, and production setups.

### Quick Start

```bash
docker compose up -d --build
```

Open http://localhost:8000
Browser demo: http://localhost:6080

## Session limits and monetization

- Standard (Free): 45 minutes
- Premium (Paid): 8 hours

Premium sessions require a BTC payment reference string in the UI (transaction ID or invoice ID).

BTC wallet configured in [app.py](app.py):
- `BTC_WALLET_ADDRESS`

## Environment configuration

Key runtime environment variables:
- `BUTTERVMS_DB_PATH`: SQLite DB location
- `BUTTERVMS_VNC_IMAGE`: Browser VM container image
- `BUTTERVMS_CONTAINER_PREFIX`: launched session container prefix
- `BUTTERVMS_SWEEPER_SECONDS`: expiry cleanup loop interval
- `BUTTERVMS_DEBUG`: set `1` for debug mode
- `BUTTERVMS_DEMO_BROWSER_URL`: stable browser demo URL shown on the home page
- `BUTTERVMS_ENABLE_ADMIN`: set `1` to enable `/admin` (default `0`)
- `BUTTERVMS_ADMIN_PASSWORD`: password for the admin control panel

See deployment wiring in [docker-compose.yml](docker-compose.yml).

## Production notes

- This is now connected end-to-end for browser VM launch and timed lifecycle.
- For commercial production, add:
	- user authentication and org quotas
	- BTC on-chain/payment processor verification
	- TLS + reverse proxy
	- billing records and audit logs
	- per-customer resource and abuse controls