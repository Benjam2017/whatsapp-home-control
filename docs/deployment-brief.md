# Deployment Brief — WhatsApp Home Control

> Generated 2026-05-30. Authoritative source of truth is always the code; this document summarises the as-built state for lab deployment.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Software Architecture](#2-software-architecture)
3. [Call Flows](#3-call-flows)
4. [Security Layers](#4-security-layers)
5. [Environment Variables](#5-environment-variables)
6. [Smoke Tests](#6-smoke-tests)

---

## 1. Prerequisites

### 1.1 Infrastructure (VPS)

| Requirement | Detail |
|---|---|
| OS | Linux VPS — Ubuntu 22.04 tested |
| IP | Static public IP address |
| Domain | DNS A record pointing to VPS IP |
| TLS certificate | Let's Encrypt via Certbot — `privkey.pem` + `fullchain.pem` |
| Firewall | Port **443 inbound** open; port **8000 must NOT be exposed** (loopback only) |

### 1.2 Home LAN

| Requirement | Detail |
|---|---|
| Device | GCE Electronics **IPX800 V4** relay controller |
| API key | Set in IPX800 web UI → Settings → API |
| Relay wiring | Relay 1 = light · Relay 2 = curtain UP · Relay 3 = curtain DOWN |
| Network | VPS and IPX800 on the **same LAN** — IPX800 reached by local IP, no DDNS or port forwarding needed |

### 1.3 Software on VPS

```
Node.js 18+    (npm bundled)
Python 3.11+   (pip + venv bundled)
```

### 1.4 Meta / WhatsApp

- Meta Developer account → create a **Business app** → add **WhatsApp** product
- **`WA_PHONE_NUMBER_ID`** — WhatsApp → API Setup
- **`WA_ACCESS_TOKEN`** — permanent system user token (**not** the 24-hour temporary token)
- **`WA_APP_SECRET`** — App Settings → Basic → App Secret → Show
- **`WA_VERIFY_TOKEN`** — any string you choose; must match what you register in Meta webhook config
- Webhook registered: `https://yourdomain.com/webhook`, field subscribed: **messages**

---

## 2. Software Architecture

Two independent services run on one VPS, separated by responsibility.

```
┌──────────────────────────────────────────────────────────────┐
│                           VPS                                │
│                                                              │
│  webhook/  (Node.js · Express · HTTPS :443)  ← public-facing │
│    app.js          routes, orchestration                     │
│    security.js     HMAC-SHA256 · whitelist · dedup           │
│    whatsapp.js     Meta Graph API sender                     │
│    state.js        in-memory device state cache              │
│    logger.js       file + console logger                     │
│         │                                                    │
│         │  POST /control   (loopback 127.0.0.1 only)         │
│         │  GET  /health                                      │
│         ▼                                                    │
│  fastapi/  (Python · FastAPI · uvicorn · HTTP :8000)         │
│    main.py         /control POST · /health GET               │
│    commands.py     keyword → (action, relay_ops)             │
│    ipx800.py       HTTP calls to IPX800 · interlock logic    │
│    config.py       pydantic-settings · reads .env            │
│    logger.py       rotating file + console logger            │
│         │                                                    │
│         │  GET /preset.htm   (outbound to local LAN)         │
│         │  GET /status.xml                                   │
│         ▼                                                    │
│  IPX800 V4  (same LAN · direct local IP:80)                 │
└──────────────────────────────────────────────────────────────┘
```

### Service boundary

| Service | Language | Port | Responsibility |
|---|---|---|---|
| `webhook/` | Node.js (Express) | 443 HTTPS, public | WhatsApp interface: HMAC verify, whitelist, dedup, Meta Graph API |
| `fastapi/` | Python (FastAPI) | 8000 HTTP, loopback only | Device interface: command parse, relay sequencing, IPX800 HTTP control |

The only contract between them is `POST /control` (send a command) and `GET /health`. Keeping them separate means either side can be upgraded or replaced without touching the other.

### IPX800 HTTP endpoints used

| Purpose | Endpoint | Notes |
|---|---|---|
| Set a relay | `GET /preset.htm?ledN=<0\|1>&apikey=<key>` | `N` is 1-based (led1, led2, led3) |
| Read all states | `GET /status.xml?apikey=<key>` | Returns XML; tags are 0-based (led0, led1, led2) |

> The code handles the 1-based / 0-based offset in `ipx800.py::get_status_report` by subtracting 1 from the relay number when reading `status.xml`.

---

## 3. Call Flows

### 3.1 User sends a WhatsApp message ("light on")

```
1.  User types "light on" in WhatsApp

2.  Meta Cloud API ──► POST https://yourdomain.com/webhook

3.  Node / security.js
        ├─ verifySignature()   HMAC-SHA256 against WA_APP_SECRET → 403 if mismatch
        ├─ checkWhitelist()    sender in ALLOWED_NUMBERS? → drop silently if not
        └─ checkDedup()        message ID seen in last 200? → drop if duplicate

4.  Node returns HTTP 200 to Meta immediately
    (Meta requires a response within 20 s; processing continues asynchronously)

5.  Node ──► POST http://127.0.0.1:8000/control
            Body: { "command": "light on", "from": "336xxxxxxxx" }

6.  FastAPI / commands.py
        parse_command("light on")
        → action = "LIGHT_ON"
        → relay_ops = [ { relay: 1, state: 1 } ]

7.  FastAPI / ipx800.py — IPX800Controller.execute()
        → GET http://192.168.1.100:80/preset.htm?led1=1&apikey=<key>
          (retries up to 3× with a 10 s gap on failure)

8.  IPX800 toggles relay 1 → light turns ON

9.  FastAPI returns:
        { "reply": "✅ Light turned ON", "success": true, "action": "LIGHT_ON" }

10. Node ──► POST https://graph.facebook.com/v19.0/<phone_id>/messages
    User receives "✅ Light turned ON" on WhatsApp
```

### 3.2 Curtain command ("curtain up") — motor interlock

Curtains are driven by two raw relays (UP = relay 2, DOWN = relay 3). Energising both simultaneously would short the motor windings. The software enforces a safety sequence:

```
parse_command("curtain up")
→ relay_ops = [
    { relay: 3, state: 0 },   ← de-energise DOWN relay first
    { relay: 2, state: 1 },   ← then energise UP relay
  ]

IPX800Controller.execute():
    1. GET /preset.htm?led3=0&apikey=...   (clear DOWN)
    2. asyncio.sleep(0.200)                (200 ms safety gap — INTERLOCK_DELAY)
    3. GET /preset.htm?led2=1&apikey=...   (set UP)
```

The same opposite-relay-off pattern applies to `curtain down` and `curtain stop`.

### 3.3 Physical switch → WhatsApp alert (push notification)

```
1.  Someone flips a wall switch at home

2.  IPX800 Scenario fires:
        GET https://yourdomain.com/ipx-notify?device=light&state=1

3.  Node / app.js
        ├─ updateState("light", "ON", "physical")   → updates in-memory state cache
        └─ for each number in ALLOWED_NUMBERS:
               sendWhatsAppMessage(number,
                   "✅ 💡 Light was physically switched ON at 14:32:07")
```

IPX800 Scenario configuration (in IPX800 web UI):
- Trigger: relay/input state change
- Action: `HTTP GET http://yourdomain.com/ipx-notify?device=light&state={led0}`

Supported `device` values: `light` · `curtain_up` · `curtain_down`

### 3.4 Status query ("status" or "?")

```
1.  User sends "status"
2.  POST /control  { "command": "status" }
3.  FastAPI → GET http://<local-ip>:80/status.xml?apikey=<key>
4.  IPX800 returns XML:
        <response>
            <led0>1</led0>   ← relay 1 (light),        0-based index
            <led1>0</led1>   ← relay 2 (curtain UP),   0-based index
            <led2>0</led2>   ← relay 3 (curtain DOWN),  0-based index
            ...
        </response>
5.  FastAPI formats reply:
        📊 Home Status — 14:32:07

        💡 Light: ON
        🪟 Curtain UP relay: OFF
        🪟 Curtain DOWN relay: OFF
6.  User receives the status report on WhatsApp
```

### 3.5 Command keyword table

| Intent | Recognised phrases (exact, case-insensitive) | Relay ops |
|---|---|---|
| `LIGHT_ON` | `light on` · `on` · `allume` · `lumière on` · `lumiere on` · `1 on` | relay 1 → 1 |
| `LIGHT_OFF` | `light off` · `off` · `éteins` · `lumière off` · `lumiere off` · `1 off` | relay 1 → 0 |
| `CURTAIN_UP` | `curtain up` · `up` · `monte` · `volet monte` · `ouvre` · `open curtain` · `2 up` | relay 3 → 0, relay 2 → 1 |
| `CURTAIN_DOWN` | `curtain down` · `down` · `descend` · `volet descend` · `ferme` · `close curtain` · `2 down` | relay 2 → 0, relay 3 → 1 |
| `CURTAIN_STOP` | `curtain stop` · `stop` · `arrête` · `stoppe` · `stop curtain` · `2 stop` | relay 2 → 0, relay 3 → 0 |
| `STATUS` | `status` · `état` · `etat` · `s` · `?` | fetch status.xml |
| `HELP` | `help` · `aide` · `h` · `commands` · `commandes` | return help text |

Any unrecognised text returns: `❓ Unknown command. Send *help* to see available commands.`

---

## 4. Security Layers

| Layer | Mechanism | Where in code |
|---|---|---|
| Transport | TLS 1.2/1.3, Let's Encrypt, Node terminates | `app.js` — `https.createServer` |
| Authenticity | HMAC-SHA256 on every Meta POST (`X-Hub-Signature-256`) | `security.js::verifySignature` |
| Authorization | Phone number whitelist (E.164 without `+`) | `security.js::checkWhitelist` |
| Replay protection | Last-200 message ID deduplication (in-memory) | `security.js::checkDedup` |
| Internal isolation | FastAPI binds `127.0.0.1:8000` only — never exposed publicly | `main.py` + systemd unit |
| Motor safety | Software interlock, 200 ms gap between opposite-direction ops | `ipx800.py::execute` / `INTERLOCK_DELAY` |
| Secrets | Both `.env` files are `chmod 600` and excluded from git | `.gitignore` |

> **Critical:** Do not expose FastAPI port 8000 publicly. It has no authentication — it trusts that only the Node.js service can reach it via loopback.

---

## 5. Environment Variables

### `webhook/.env`

| Variable | Example | Description |
|---|---|---|
| `NODE_PORT` | `443` | HTTPS listening port |
| `USE_SSL` | `true` | Set `false` for local HTTP testing only |
| `SSL_KEY_PATH` | `/etc/letsencrypt/live/yourdomain.com/privkey.pem` | Let's Encrypt private key |
| `SSL_CERT_PATH` | `/etc/letsencrypt/live/yourdomain.com/fullchain.pem` | Let's Encrypt full chain |
| `WA_PHONE_NUMBER_ID` | `1234567890123456` | WhatsApp Cloud API phone number ID |
| `WA_ACCESS_TOKEN` | `EAAxxxxxxxxxx` | Meta access token (permanent system user token) |
| `WA_VERIFY_TOKEN` | `my-secret-uuid` | Token registered in Meta webhook config |
| `WA_APP_SECRET` | `abc123...` | App Secret from Meta App Dashboard (used for HMAC) |
| `ALLOWED_NUMBERS` | `33612345678,33698765432` | Authorized E.164 numbers without `+`, comma-separated |
| `FASTAPI_HOST` | `127.0.0.1` | FastAPI internal address |
| `FASTAPI_PORT` | `8000` | FastAPI internal port |
| `LOG_FILE` | `logs/webhook.log` | Log file path |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

### `fastapi/.env`

| Variable | Example | Description |
|---|---|---|
| `IPX800_HOST` | `192.168.1.100` | Local IP address of the IPX800 on the shared LAN |
| `IPX800_PORT` | `80` | IPX800 HTTP port (default 80) |
| `IPX800_APIKEY` | `your-apikey` | IPX800 API key (set in IPX800 web UI) |
| `IPX800_TIMEOUT` | `5.0` | HTTP request timeout in seconds |
| `IPX800_RETRY` | `3` | Retry attempts on connection failure |
| `RELAY_LIGHT` | `1` | Relay number for the light (1-based) |
| `RELAY_CURTAIN_UP` | `2` | Relay number for curtain UP direction |
| `RELAY_CURTAIN_DOWN` | `3` | Relay number for curtain DOWN direction |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FILE` | `logs/fastapi.log` | Log file path |
| `LOG_MAX_BYTES` | `10485760` | Max log file size before rotation (10 MB) |
| `LOG_BACKUP_COUNT` | `7` | Number of rotated log files to keep |

### Mandatory secrets before first start

**`webhook/.env`** — 5 required:
```
WA_PHONE_NUMBER_ID=
WA_ACCESS_TOKEN=
WA_APP_SECRET=
WA_VERIFY_TOKEN=
ALLOWED_NUMBERS=
SSL_KEY_PATH=
SSL_CERT_PATH=
```

**`fastapi/.env`** — 3 required:
```
IPX800_HOST=
IPX800_PORT=80
IPX800_APIKEY=
```

---

## 6. Smoke Tests

Run these after installation to confirm each layer of the stack is working.

### Step 1 — FastAPI health (Python + IPX800 reachability)

```bash
curl http://127.0.0.1:8000/health
```

Expected (IPX800 reachable):
```json
{
  "status": "ok",
  "service": "fastapi-control",
  "ipx800": "reachable",
  "ipx800_host": "192.168.1.100",
  "ipx800_port": 80
}
```

`"status": "degraded"` means the IPX800 is unreachable — check the local IP, port, and API key.

### Step 2 — Webhook health (full stack)

```bash
curl https://yourdomain.com/health
```

Expected:
```json
{
  "status": "ok",
  "service": "whatsapp-webhook",
  "uptime": 120.5,
  "fastapi": { "status": "ok", "ipx800": "reachable" },
  "time": "2026-05-30T14:32:07.000Z"
}
```

### Step 3 — IPX800 direct connectivity (from VPS)

```bash
curl "http://192.168.1.100:80/status.xml?apikey=<your-key>"
```

Expected: XML with `<led0>`, `<led1>`, etc. Any HTTP error or timeout means the local IP, port, or API key is wrong.

### Step 4 — Meta webhook handshake

Trigger manually from the Meta Developer console (WhatsApp → Configuration → Webhook → Test) or register the webhook and check the Node.js log:

```bash
sudo journalctl -u home-webhook -n 20
# Expected line: "Webhook handshake verified by Meta"
```

### Step 5 — End-to-end command

Send `status` from an authorised WhatsApp number. A correctly functioning stack returns the relay state report within a few seconds.

---

## Systemd Service Units (reference)

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

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable home-fastapi home-webhook
sudo systemctl start home-fastapi home-webhook
sudo systemctl status home-fastapi home-webhook
```
