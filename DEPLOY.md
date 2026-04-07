# ButterVMS Deployment Guide

## Local Development
```bash
docker compose up -d --build
```
Open http://localhost:8000
Browser demo: http://localhost:6080
Admin panel: disabled by default (enable with `BUTTERVMS_ENABLE_ADMIN=1`)

## Production Deployment to wafflev1.me

### Prerequisites
- A server with Docker and Docker Compose installed
- Domain: wafflev1.me pointing to your server IP
- SSH access to your server

### Step 1: Clone repository on your server

```bash
ssh your-user@your-server-ip
git clone https://github.com/DevEchoX2/ButterVMs.git
cd ButterVMs
```

### Step 2: Configure environment (optional)
```bash
cp .env.example .env
# Edit .env if needed (DB path, browser image, debug mode, etc.)
```

### Step 3: Start the full-stack app
```bash
docker compose up -d --build
```

The Flask app runs on `0.0.0.0:8000` inside the container.

### Step 4: Set up reverse proxy (required for wafflev1.me)

You need a reverse proxy to route wafflev1.me traffic to localhost:8000. Options:

#### Option A: Nginx (recommended)
```bash
sudo apt-get install -y nginx
sudo vim /etc/nginx/sites-available/wafflev1.me
```

Add this config:
```nginx
server {
    listen 80;
    server_name wafflev1.me;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/wafflev1.me /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### Option B: Caddy (simpler)
```bash
sudo apt-get install -y caddy
echo "wafflev1.me {
  reverse_proxy 127.0.0.1:8000
}" | sudo tee /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

### Step 5: Set up HTTPS (strongly recommended)

#### With Nginx + Certbot:
```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d wafflev1.me
```

#### With Caddy:
Caddy handles HTTPS automatically with Let's Encrypt.

### Step 6: Verify deployment

1. Open https://wafflev1.me in browser
2. Create a Standard VM
3. Click "→ Open Browser VM Now"
4. The browser VM should open in a new window
5. Use the browser demo at port 6080 if you want a known test endpoint

### Port Architecture

- **Port 8000**: ButterVMS control panel (proxied via Nginx/Caddy to wafflev1.me)
- **Port 6080**: Browser demo VM for quick testing
- **Ports 32768+**: Auto-assigned for individual VM sessions
  - Each VM gets a web port (e.g., 32770) and VNC port (e.g., 32771)
  - Users click "Open Session Dashboard" to manage their session
  - URLs are automatically generated with the server hostname

### Admin panel

For safety, `/admin` is disabled by default and returns 404.
Enable it only when needed by setting `BUTTERVMS_ENABLE_ADMIN=1`.
Then visit `/admin` to log in and stop individual VMs or kill all running sessions.

### Database & Persistence

Sessions are stored in SQLite (`/data/buttervms.db`).

Check with:
```bash
docker exec -it buttervms-api sqlite3 /data/buttervms.db "SELECT * FROM sessions;"
```

### Monitoring

View logs:
```bash
docker compose logs -f buttervms-api
```

List running sessions:
```bash
docker ps --filter name=buttervms-session
```

### Troubleshooting

If VMs don't open from the links:
1. Verify reverse proxy is running: `curl http://127.0.0.1:8000`
2. Check domain resolves: `nslookup wafflev1.me`
3. Check firewall allows port 80/443: `sudo ufw allow 80/443/tcp`
4. View app logs: `docker compose logs buttervms-api`

### Updating

Pull latest changes and redeploy:
```bash
git pull origin main
docker compose up -d --build
```

### Production Recommendations

For commercial use, add:
- User authentication (sessions per user)
- Rate limiting (max VMs per user/minute)
- BTC payment verification (before Premium tier launches)
- TLS certificates for all domains
- Regular backups of SQLite database
- Resource limits per user
- Automated session cleanup monitoring
