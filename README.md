# WhatsApp Home Control — VPS Codebase

## Project Structure

```
whatsapp-home-control/
├── webhook/                    # Node.js — WhatsApp webhook server
│   ├── app.js                  # Express server (HTTPS port 443)
│   ├── security.js             # HMAC verification, whitelist, deduplication
│   ├── whatsapp.js             # Meta Graph API: send messages
│   ├── state.js                # In-memory device state cache
│   ├── logger.js               # File + console logger
│   ├── package.json
│   ├── .env.example            # Copy to .env and fill in values
│   ├── logs/
│   └── tests/
│       ├── test_security.js    # 12 tests — HMAC, whitelist, dedup
│       └── test_state.js       # 11 tests — state cache
│
└── fastapi/                    # Python — IPX800 control service
    ├── main.py                 # FastAPI app (/control, /health)
    ├── commands.py             # Command parser (EN + FR keywords)
    ├── ipx800.py               # IPX800 HTTP controller + interlock
    ├── config.py               # Settings from .env (pydantic)
    ├── logger.py               # Rotating file logger
    ├── requirements.txt
    ├── .env.example            # Copy to .env and fill in values
    ├── logs/
    └── tests/
        ├── test_commands.py    # 19 tests — command parsing
        ├── test_ipx800.py      # 12 tests — relay control, interlock
        └── test_api.py         # 14 tests — API endpoints
```

---

## Test Results (All Verified)

| Service  | Test File          | Tests | Status |
|----------|--------------------|-------|--------|
| FastAPI  | test_commands.py   | 19    | ✅ All pass |
| FastAPI  | test_ipx800.py     | 12    | ✅ All pass |
| FastAPI  | test_api.py        | 14    | ✅ All pass |
| Node.js  | test_security.js   | 12    | ✅ All pass |
| Node.js  | test_state.js      | 11    | ✅ All pass |
| **TOTAL**|                    | **68**| ✅ **All pass** |

---

## Setup on VPS

### 1. Clone / upload project
```bash
scp -r whatsapp-home-control/ user@your-vps:/opt/
cd /opt/whatsapp-home-control
```

### 2. Setup FastAPI
```bash
cd fastapi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
nano .env   # fill in real values
```

### 3. Setup Node.js Webhook
```bash
cd ../webhook
npm install
cp .env.example .env
chmod 600 .env
nano .env   # fill in real values
```

### 4. Run Tests
```bash
# Python tests
cd fastapi
python -m pytest tests/ -v --asyncio-mode=auto

# Node.js tests
cd webhook
node tests/test_security.js
node tests/test_state.js
```

### 5. Start Services
```bash
# FastAPI (internal, port 8000)
cd fastapi && uvicorn main:app --host 127.0.0.1 --port 8000

# Node.js (public HTTPS, port 443)
cd webhook && node app.js
```

### 6. Systemd Services
Create `/etc/systemd/system/home-fastapi.service`:
```ini
[Unit]
Description=Home Control FastAPI
After=network.target

[Service]
WorkingDirectory=/opt/whatsapp-home-control/fastapi
ExecStart=/opt/whatsapp-home-control/fastapi/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/home-webhook.service`:
```ini
[Unit]
Description=Home Control Webhook
After=network.target home-fastapi.service

[Service]
WorkingDirectory=/opt/whatsapp-home-control/webhook
ExecStart=/usr/bin/node app.js
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable home-fastapi home-webhook
systemctl start home-fastapi home-webhook
```

---

## Environment Variables

### webhook/.env
```ini
NODE_PORT=443
USE_SSL=true
SSL_KEY_PATH=/etc/letsencrypt/live/yourdomain.com/privkey.pem
SSL_CERT_PATH=/etc/letsencrypt/live/yourdomain.com/fullchain.pem
WA_PHONE_NUMBER_ID=1234567890123456
WA_ACCESS_TOKEN=EAAxxxxxxxxxx
WA_VERIFY_TOKEN=your-random-uuid
WA_APP_SECRET=your-app-secret
ALLOWED_NUMBERS=33612345678,33698765432
FASTAPI_HOST=127.0.0.1
FASTAPI_PORT=8000
LOG_FILE=logs/webhook.log
LOG_LEVEL=INFO
```

### fastapi/.env
```ini
IPX800_HOST=myhome.duckdns.org
IPX800_PORT=8080
IPX800_APIKEY=your-ipx800-apikey
IPX800_TIMEOUT=5.0
IPX800_RETRY=3
RELAY_LIGHT=1
RELAY_CURTAIN_UP=2
RELAY_CURTAIN_DOWN=3
LOG_LEVEL=INFO
LOG_FILE=logs/fastapi.log
```

---

## Commands Reference

| You send       | Action              | Reply                    |
|----------------|---------------------|--------------------------|
| `light on`     | Relay 1 = ON        | ✅ Light turned ON       |
| `light off`    | Relay 1 = OFF       | ⭕ Light turned OFF      |
| `curtain up`   | Relay 3=OFF, 2=ON   | ⬆️ Curtain moving UP    |
| `curtain down` | Relay 2=OFF, 3=ON   | ⬇️ Curtain moving DOWN  |
| `curtain stop` | Relay 2=OFF, 3=OFF  | ⏹️ Curtain stopped      |
| `status`       | Query IPX800        | 📊 Home Status report    |
| `help`         | Show commands       | 🏠 Command list          |
