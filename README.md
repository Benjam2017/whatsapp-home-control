# WhatsApp Home Control

Control home devices (lights, motorised curtains) by sending WhatsApp messages to a **GCE Electronics IPX800 V4** relay controller, from anywhere in the world.
Physical switch changes are pushed back as WhatsApp alerts automatically.

---

## Table of Contents

1. [How It Works](#1-how-it-works)
2. [Architecture](#2-architecture)
3. [Prerequisites](#3-prerequisites)
4. [Project Structure](#4-project-structure)
5. [Installation](#5-installation)
6. [Environment Variables](#6-environment-variables)
7. [Meta Cloud API Setup](#7-meta-cloud-api-setup)
8. [IPX800 Configuration](#8-ipx800-configuration)
9. [Run as Systemd Services](#9-run-as-systemd-services)
10. [Commands Reference](#10-commands-reference)
11. [IPX800 Push Notifications](#11-ipx800-push-notifications)
12. [Health Checks](#12-health-checks)
13. [Log Management](#13-log-management)
14. [Security Overview](#14-security-overview)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. How It Works

```
You (WhatsApp)
    │
    │  "light on"
    ▼
Meta Cloud API  ──►  webhook/ (Node.js, HTTPS :443, your VPS)
                         │
                         │ verifies HMAC signature
                         │ checks phone whitelist
                         │ deduplicates message
                         │ POST /control
                         ▼
                     fastapi/ (Python, HTTP :8000, loopback only)
                         │
                         │ parses command
                         │ applies curtain interlock
                         │ GET /preset.htm
                         ▼
                     IPX800 V4  (on your home LAN, via DDNS + port forward)
                         │
                         │ physical switch toggled by someone at home
                         ▼
                     IPX800 Scenario  ──►  webhook/ GET /ipx-notify
                                               │
                                               ▼
                                           WhatsApp alert sent to all
                                           authorised numbers
```

---

## 2. Architecture

Two independent services share one VPS:

| Service | Language | Port | Role |
|---------|----------|------|------|
| `webhook/` | Node.js (Express) | 443 (HTTPS, public) | WhatsApp interface: HMAC verify, whitelist, dedup, Meta Graph API |
| `fastapi/` | Python (FastAPI) | 8000 (HTTP, loopback) | Device interface: command parse, relay sequencing, IPX800 HTTP control |

The boundary between the two is `POST /control` (send a command) and `GET /health` on the FastAPI side.
Keeping them separate means you can upgrade or replace either side without touching the other.

**Why the curtain needs two relays:** The IPX800 drives the curtain motor with two raw relays — UP (relay 2) and DOWN (relay 3). Energising both simultaneously would short the motor windings. The code enforces a **software interlock**: the opposite-direction relay is always de-energised before the target relay is set, with a 200 ms safety gap.

---

## 3. Prerequisites

### VPS / Server
- Linux VPS with a **static public IP address** (tested on Ubuntu 22.04)
- A **registered domain name** pointing to that IP (A record)
- **TLS certificate** from Let's Encrypt (free) — see [Certbot](https://certbot.eff.org/)
- Open ports: 443 (HTTPS inbound), 8080 optional outbound to home router

### Home LAN
- **GCE Electronics IPX800 V4** relay controller
- **DDNS hostname** (e.g. DuckDNS, No-IP) pointing to your home router's WAN IP
- **Port forwarding** on your router: external TCP 8080 → IPX800 LAN IP port 80 (or the port IPX800 uses)
- IPX800 API key configured (IPX800 web interface → Settings → API)

### Software (VPS)
- **Node.js 18+** — `node --version`
- **Python 3.11+** — `python3 --version`
- **npm** — bundled with Node
- **pip / venv** — bundled with Python

### Meta / WhatsApp
- Meta Business Account (free)
- WhatsApp Cloud API app (free tier covers this use-case)
- A permanent system user access token (or a long-lived token)

---

## 4. Project Structure

```
whatsapp-home-control/
│
├── webhook/                    Node.js — public WhatsApp webhook
│   ├── app.js                  Express server (HTTPS :443)
│   ├── security.js             HMAC verification · whitelist · dedup
│   ├── whatsapp.js             Meta Graph API: send messages
│   ├── state.js                In-memory device state cache
│   ├── logger.js               File + console logger
│   ├── package.json
│   ├── .env.example            ← copy to .env, fill in values
│   └── logs/
│
└── fastapi/                    Python — IPX800 control service
    ├── main.py                 FastAPI app (/control POST, /health GET)
    ├── commands.py             Command parser (EN + FR keyword map)
    ├── ipx800.py               IPX800 HTTP controller + curtain interlock
    ├── config.py               Settings loaded from .env (pydantic-settings)
    ├── logger.py               Rotating file + console logger
    ├── requirements.txt
    ├── .env.example            ← copy to .env, fill in values
    └── logs/
```

---

## 5. Installation

### Clone the repository

```bash
git clone https://github.com/<your-user>/whatsapp-home-control.git /opt/whatsapp-home-control
cd /opt/whatsapp-home-control
```

### 5.1 FastAPI service (Python)

```bash
cd fastapi

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create configuration file
cp .env.example .env
chmod 600 .env
nano .env          # fill in IPX800_HOST, IPX800_APIKEY, etc.

# Create log directory
mkdir -p logs
```

### 5.2 Node.js webhook service

```bash
cd ../webhook

# Install dependencies
npm install

# Create configuration file
cp .env.example .env
chmod 600 .env
nano .env          # fill in Meta tokens, ALLOWED_NUMBERS, SSL paths

# Create log directory
mkdir -p logs
```

### 5.3 Verify installation (optional smoke test)

```bash
# Start FastAPI in one terminal
cd /opt/whatsapp-home-control/fastapi
source venv/bin/activate
uvicorn main:app --host 127.0.0.1 --port 8000

# In another terminal — check health
curl http://127.0.0.1:8000/health
# Expected: {"status":"degraded","service":"fastapi-control","ipx800":"unreachable",...}
# (degraded because the IPX800 is not reachable in this test — that is normal)
```

---

## 6. Environment Variables

### `webhook/.env`

| Variable | Example | Description |
|----------|---------|-------------|
| `NODE_PORT` | `443` | HTTPS port to listen on |
| `USE_SSL` | `true` | Set `false` for local HTTP testing only |
| `SSL_KEY_PATH` | `/etc/letsencrypt/live/yourdomain.com/privkey.pem` | Let's Encrypt private key |
| `SSL_CERT_PATH` | `/etc/letsencrypt/live/yourdomain.com/fullchain.pem` | Let's Encrypt full chain |
| `WA_PHONE_NUMBER_ID` | `1234567890123456` | WhatsApp Cloud API phone number ID |
| `WA_ACCESS_TOKEN` | `EAAxxxxxxxxxx` | Meta access token (permanent system user token) |
| `WA_VERIFY_TOKEN` | `my-secret-uuid` | Token you choose when registering the webhook with Meta |
| `WA_APP_SECRET` | `abc123...` | App Secret from Meta App Dashboard (used for HMAC) |
| `ALLOWED_NUMBERS` | `33612345678,33698765432` | Authorized E.164 numbers without `+`, comma-separated |
| `FASTAPI_HOST` | `127.0.0.1` | FastAPI internal address (do not expose publicly) |
| `FASTAPI_PORT` | `8000` | FastAPI internal port |
| `LOG_FILE` | `logs/webhook.log` | Log file path |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

### `fastapi/.env`

| Variable | Example | Description |
|----------|---------|-------------|
| `IPX800_HOST` | `myhome.duckdns.org` | DDNS hostname of your home router |
| `IPX800_PORT` | `8080` | External port forwarded to the IPX800 |
| `IPX800_APIKEY` | `your-apikey` | IPX800 API key (set in IPX800 web interface) |
| `IPX800_TIMEOUT` | `5.0` | HTTP request timeout in seconds |
| `IPX800_RETRY` | `3` | Retry attempts on connection failure |
| `RELAY_LIGHT` | `1` | Relay number for the light (1-based) |
| `RELAY_CURTAIN_UP` | `2` | Relay number for curtain UP direction |
| `RELAY_CURTAIN_DOWN` | `3` | Relay number for curtain DOWN direction |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | `logs/fastapi.log` | Log file path |
| `LOG_MAX_BYTES` | `10485760` | Max log file size before rotation (10 MB) |
| `LOG_BACKUP_COUNT` | `7` | Number of rotated log files to keep |

> **Security:** Both `.env` files must be `chmod 600`. Never commit them to git — they are listed in `.gitignore`.

---

## 7. Meta Cloud API Setup

### 7.1 Create a Meta App

1. Go to [Meta for Developers](https://developers.facebook.com/) → **My Apps** → **Create App**
2. Select **Business** → give it a name → **Create App**
3. Under **Add Products**, find **WhatsApp** → **Set up**

### 7.2 Configure the WhatsApp product

1. In the app sidebar: **WhatsApp** → **API Setup**
2. Note your **Phone Number ID** → set as `WA_PHONE_NUMBER_ID`
3. Generate (or copy) a **Temporary Access Token** → set as `WA_ACCESS_TOKEN`
   - For production, create a **System User** in Meta Business Suite and generate a permanent token

### 7.3 Get the App Secret

1. **App Settings** → **Basic** → **App Secret** → **Show** → copy it
2. Set as `WA_APP_SECRET`

### 7.4 Register the webhook

1. **WhatsApp** → **Configuration** → **Webhook** → **Edit**
2. **Callback URL:** `https://yourdomain.com/webhook`
3. **Verify Token:** the value you chose for `WA_VERIFY_TOKEN`
4. Click **Verify and Save** — Meta will call `GET /webhook` to confirm
5. Subscribe to the **messages** field under **Webhook Fields**

### 7.5 Get your permanent access token (production)

1. In **Meta Business Suite** → **Settings** → **System Users** → **Add**
2. Assign the WhatsApp app → generate token with `whatsapp_business_messaging` permission
3. Update `WA_ACCESS_TOKEN` with this token (it does not expire)

---

## 8. IPX800 Configuration

### 8.1 Access the IPX800 web interface

Open `http://<ipx800-lan-ip>/` in a browser on your home network.

### 8.2 Set an API key

1. **Settings** → **API** → enter a strong random string → **Save**
2. Use this string as `IPX800_APIKEY`

### 8.3 Check your relay wiring

Default mapping in `.env.example`:

| Relay | Function | Physical terminal |
|-------|----------|-------------------|
| 1 | Light | your light circuit |
| 2 | Curtain UP motor | UP winding |
| 3 | Curtain DOWN motor | DOWN winding |

If your wiring differs, update `RELAY_LIGHT`, `RELAY_CURTAIN_UP`, `RELAY_CURTAIN_DOWN` accordingly.

### 8.4 Set up DDNS + port forwarding

1. Create a free DDNS hostname (e.g. [DuckDNS](https://www.duckdns.org/)) and install the DDNS updater client on your router or a home computer.
2. On your home router: **Port Forwarding** → add rule: external TCP port `8080` → IPX800 LAN IP, port `80`.
3. Test from your VPS: `curl http://myhome.duckdns.org:8080/status.xml?apikey=<key>`

### 8.5 Verify connectivity

```bash
curl "http://127.0.0.1:8000/health"
# "ipx800": "reachable"  ← success
# "ipx800": "unreachable" ← check DDNS / port forward / API key
```

---

## 9. Run as Systemd Services

### Create service files

**`/etc/systemd/system/home-fastapi.service`**
```ini
[Unit]
Description=Home Control — FastAPI (IPX800)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/opt/whatsapp-home-control/fastapi
ExecStart=/opt/whatsapp-home-control/fastapi/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/home-webhook.service`**
```ini
[Unit]
Description=Home Control — Node.js Webhook (WhatsApp)
After=network.target home-fastapi.service
Requires=home-fastapi.service

[Service]
User=ubuntu
WorkingDirectory=/opt/whatsapp-home-control/webhook
ExecStart=/usr/bin/node app.js
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

> Replace `User=ubuntu` with the Linux user that owns the project files.

### Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable home-fastapi home-webhook
sudo systemctl start home-fastapi home-webhook

# Verify
sudo systemctl status home-fastapi
sudo systemctl status home-webhook
```

### View live logs

```bash
sudo journalctl -u home-fastapi -f
sudo journalctl -u home-webhook -f
```

---

## 10. Commands Reference

Send any of these messages from an authorised WhatsApp number.
Commands are **case-insensitive** and **trimmed** — `Light On`, `LIGHT ON`, and `light on` all work.

### Light

| Message | Action | Reply |
|---------|--------|-------|
| `light on` · `on` · `allume` · `lumière on` · `1 on` | Relay 1 → ON | ✅ Light turned ON |
| `light off` · `off` · `éteins` · `lumière off` · `1 off` | Relay 1 → OFF | ⭕ Light turned OFF |

### Curtain

| Message | Action | Reply |
|---------|--------|-------|
| `curtain up` · `up` · `monte` · `volet monte` · `ouvre` · `open curtain` · `2 up` | Relay 3 → OFF then Relay 2 → ON | ⬆️ Curtain moving UP |
| `curtain down` · `down` · `descend` · `volet descend` · `ferme` · `close curtain` · `2 down` | Relay 2 → OFF then Relay 3 → ON | ⬇️ Curtain moving DOWN |
| `curtain stop` · `stop` · `arrête` · `stoppe` · `stop curtain` · `2 stop` | Relay 2 → OFF, Relay 3 → OFF | ⏹️ Curtain stopped |

### Utilities

| Message | Action | Reply |
|---------|--------|-------|
| `status` · `état` · `etat` · `s` · `?` | Query IPX800 state | 📊 Home Status report |
| `help` · `aide` · `h` · `commands` · `commandes` | Show command list | 🏠 Help message |

### Unknown command

Any unrecognised text returns:
> ❓ Unknown command. Send *help* to see available commands.

---

## 11. IPX800 Push Notifications

When a physical wall switch changes the state of a relay, the IPX800 can call your VPS to send a WhatsApp alert.

### Configure an IPX800 Scenario

In the IPX800 web interface:
1. **Scenarios** → **Add Scenario**
2. **Trigger:** Input change (or relay change) for the relevant input/relay
3. **Action:** HTTP GET request:
   ```
   http://yourdomain.com/ipx-notify?device=light&state={led0}
   ```
   Replace `{led0}` with the IPX800 macro for the relay state (0 = OFF, 1 = ON).

### Supported device names

| `device` query param | Meaning |
|----------------------|---------|
| `light` | Main light (relay 1) |
| `curtain_up` | Curtain UP relay (relay 2) |
| `curtain_down` | Curtain DOWN relay (relay 3) |

### Example alert received on WhatsApp

```
✅ 💡 Light was physically switched ON at 14:32:07
```

All numbers in `ALLOWED_NUMBERS` receive the alert.

---

## 12. Health Checks

### FastAPI health

```bash
curl http://127.0.0.1:8000/health
```
```json
{
  "status": "ok",
  "service": "fastapi-control",
  "ipx800": "reachable",
  "ipx800_host": "myhome.duckdns.org",
  "ipx800_port": 8080
}
```
`status` is `"degraded"` when the IPX800 is unreachable.

### Webhook health

```bash
curl https://yourdomain.com/health
```
```json
{
  "status": "ok",
  "service": "whatsapp-webhook",
  "uptime": 3600.12,
  "fastapi": { "status": "ok", "ipx800": "reachable" },
  "time": "2026-05-29T14:32:07.000Z"
}
```

---

## 13. Log Management

Log files are written to `fastapi/logs/` and `webhook/logs/`.
Files are **excluded from git** (see `.gitignore`).

### Python (FastAPI) — rotating logs

Configured via `.env`:
- `LOG_MAX_BYTES=10485760` — rotate at 10 MB
- `LOG_BACKUP_COUNT=7` — keep 7 rotated files

### Node.js (webhook) — flat log

The Node logger appends to `webhook/logs/webhook.log`.
Use `logrotate` on the VPS for size management if needed.

### Read recent logs

```bash
# FastAPI
tail -f /opt/whatsapp-home-control/fastapi/logs/fastapi.log

# Webhook
tail -f /opt/whatsapp-home-control/webhook/logs/webhook.log

# Or via journald
sudo journalctl -u home-fastapi -n 100
sudo journalctl -u home-webhook -n 100
```

---

## 14. Security Overview

| Layer | Mechanism |
|-------|-----------|
| Transport | TLS 1.2/1.3 via Let's Encrypt (Node handles termination) |
| Authenticity | HMAC-SHA256 signature on every Meta POST (`X-Hub-Signature-256`) |
| Authorisation | Phone number whitelist (`ALLOWED_NUMBERS`) |
| Replay protection | Message ID deduplication (last 200 message IDs in memory) |
| Internal isolation | FastAPI listens on loopback only (`127.0.0.1:8000`) |
| Secrets | Both `.env` files are `chmod 600` and excluded from git |
| Curtain safety | Software interlock prevents both UP/DOWN relays being ON simultaneously |

**Do not expose the FastAPI port (8000) publicly.** It has no authentication — it trusts that only the Node.js service can reach it via loopback.

---

## 15. Troubleshooting

### Meta webhook verification fails

- Check `WA_VERIFY_TOKEN` matches exactly in `.env` and in the Meta webhook configuration.
- Make sure port 443 is open and the domain's DNS resolves to the VPS IP.
- Check the Node.js log: `journalctl -u home-webhook -n 50`

### Messages arrive but nothing happens on the IPX800

1. Check the FastAPI log for the parsed command and any IPX800 errors.
2. Run: `curl http://127.0.0.1:8000/health` — is `ipx800` reachable?
3. Test DDNS resolution from the VPS: `curl "http://myhome.duckdns.org:8080/status.xml?apikey=<key>"`
4. Check your router's port forwarding rule (external 8080 → IPX800 LAN IP:80).

### "Unknown command" reply

The command parser does an **exact match** on trimmed, lowercased text.
- Send `help` to see all accepted phrases.
- Check for leading/trailing spaces or accented characters.

### WhatsApp replies are not sent

- Verify `WA_PHONE_NUMBER_ID` and `WA_ACCESS_TOKEN` are correct.
- Permanent system user tokens do not expire; temporary tokens expire after 24 h.
- Check `curl https://yourdomain.com/health` → `fastapi.status` should be `ok`.

### Curtain motor does not respond

- Verify relay wiring matches `RELAY_CURTAIN_UP` and `RELAY_CURTAIN_DOWN` in `.env`.
- Check the IPX800 web interface — can you operate the relays manually there?
- The interlock delay is 200 ms — normal operation, not a bug.

### Node.js service fails to start (TLS error)

- Confirm Let's Encrypt certificate paths are correct in `SSL_KEY_PATH` / `SSL_CERT_PATH`.
- For local testing without TLS: set `USE_SSL=false` in `webhook/.env`.

### Service restarts in a loop

```bash
sudo journalctl -u home-fastapi -n 20 --no-pager
sudo journalctl -u home-webhook -n 20 --no-pager
```
Common causes: missing `.env` file, wrong Python venv path, port already in use.

---

## License

MIT — see [LICENSE](LICENSE) if present, or use freely with attribution.
