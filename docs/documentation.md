# WhatsApp Home Control — Technical Reference

Control lights and motorised curtains in a home installation by sending WhatsApp messages. Incoming commands are routed from the Meta Cloud API to a VPS, which drives a **GCE Electronics IPX800 V4** relay controller via its native HTTP API.

> **Note:** This document describes the **as-built implementation**. The original phased design document (covering Cloudflare Tunnel, single-service FastAPI, and X-4VR extension) is preserved in git history. Where the design diverged from the implementation, this document follows the code.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [IPX800 V4 API](#3-ipx800-v4-api)
4. [Command Vocabulary](#4-command-vocabulary)
5. [Curtain Motor Interlock](#5-curtain-motor-interlock)
6. [Security Layers](#6-security-layers)
7. [IPX800 Push Notifications](#7-ipx800-push-notifications)
8. [Service Boundaries and API Contract](#8-service-boundaries-and-api-contract)
9. [Configuration Reference](#9-configuration-reference)
10. [Design Decisions and Rationale](#10-design-decisions-and-rationale)
11. [Original Design vs. As-Built](#11-original-design-vs-as-built)

---

## 1. System Overview

**What it controls**

| Device | Relay | Direction |
|--------|-------|-----------|
| Light | Relay 1 | Single relay (ON / OFF) |
| Curtain motor — UP | Relay 2 | Energise to open |
| Curtain motor — DOWN | Relay 3 | Energise to close |

**Who can control it**

Authorised users are identified by phone number (E.164 format, without `+`). The list is configured in `webhook/.env` as `ALLOWED_NUMBERS`. Messages from any other number are silently ignored after HMAC signature verification.

**Languages supported**

English and French. Both command sets are fully implemented. See [§4](#4-command-vocabulary) for the full keyword table.

---

## 2. Architecture

### Two-service design

```
WhatsApp user
    │  "light on"
    ▼
Meta Cloud API
    │  HTTPS POST /webhook
    ▼
webhook/ service  (Node.js, Express, HTTPS :443)
    │  verify HMAC signature
    │  check phone whitelist
    │  deduplicate message ID
    │  HTTP POST /control
    ▼
fastapi/ service  (Python, FastAPI, HTTP :8000, loopback only)
    │  parse command keyword
    │  build relay operation list
    │  apply curtain interlock
    │  HTTP GET /preset.htm  (set relay)
    ▼
IPX800 V4  (same LAN as VPS, reached via local IP)
```

**Physical switch push flow (reverse direction)**

```
Home wall switch toggled
    │
    ▼
IPX800 V4  (Scenario triggers on input/relay change)
    │  HTTP GET /ipx-notify?device=light&state=1
    ▼
webhook/ service
    │  update in-memory state cache
    │  send WhatsApp alert to all authorised numbers
    ▼
All authorised WhatsApp numbers receive an alert
```

### Why two services?

The Node.js service owns everything WhatsApp-shaped: HMAC verification, phone whitelist, message dedup, Meta Graph API calls, and IPX800 push-notification relaying. The Python service owns everything device-shaped: command text parsing, relay sequencing, interlock logic, and IPX800 HTTP control.

Keeping them separate means either can be upgraded, replaced, or restarted without touching the other. The boundary is a simple JSON HTTP API (`POST /control`, `GET /health`).

### Network topology

```
LAN (VPS and IPX800 on the same network)
├── VPS (public IP, domain with TLS)
│   ├── :443  Node.js webhook  ← public, Meta-facing
│   └── :8000 Python FastAPI   ← loopback only, not publicly accessible
└── IPX800 V4  ← reached directly at http://<local-ip>:80
```

The VPS and IPX800 are on the same LAN. FastAPI connects to the IPX800 by its local IP address — no DDNS, no router port-forwarding needed.

---

## 3. IPX800 V4 API

The implementation uses two IPX800 HTTP endpoints. These are the older "LED preset" API, which is valid on all IPX800 V4 firmware versions.

### Set a relay state

```
GET http://<local-ip>:<port>/preset.htm?led<N>=<state>&apikey=<key>
```

- `N` is the **1-based** relay number (`led1`, `led2`, `led3`, …)
- `state` is `1` (ON) or `0` (OFF)
- `apikey` is the API key configured in the IPX800 web interface

**Example — turn relay 1 ON:**
```
GET http://192.168.1.100:80/preset.htm?led1=1&apikey=mysecretkey
```

**Example — turn relay 2 OFF:**
```
GET http://192.168.1.100:80/preset.htm?led2=0&apikey=mysecretkey
```

### Read all relay states

```
GET http://<local-ip>:<port>/status.xml?apikey=<key>
```

Returns XML. Relay tags are **0-based** (`<led0>`, `<led1>`, `<led2>`, …), values are `"0"` or `"1"`.

**Example response:**
```xml
<response>
  <led0>1</led0>   <!-- relay 1 = ON  -->
  <led1>0</led1>   <!-- relay 2 = OFF -->
  <led2>0</led2>   <!-- relay 3 = OFF -->
</response>
```

> **Index offset:** `preset.htm` uses 1-based relay numbers; `status.xml` uses 0-based indices. The code handles this in `fastapi/ipx800.py::get_status_report()` by subtracting 1: relay N maps to `led(N-1)` in the XML.

### All IPX800 logic is in one file

`fastapi/ipx800.py` is the single place that knows the IPX800 URL structure. Never copy the URL pattern into `commands.py`, `main.py`, or any other file. If the endpoint changes (e.g., a future migration to `/api/xdevices.json`), update only `ipx800.py`.

### Retry behaviour

On connection failure the client retries up to `IPX800_RETRY` times (default: 3) with a 10-second gap between attempts. After all retries are exhausted, a `ConnectionError` is raised and the Python service returns `success: false` to Node.js, which sends an error reply to the user.

---

## 4. Command Vocabulary

The parser (`fastapi/commands.py`) performs **exact-match** on trimmed, lowercased text. There is no fuzzy matching or NLP. Adding a new alias requires only editing the `COMMAND_MAP` dict and (locally) adding a test case.

### Light

| User sends | Language | Relay effect |
|------------|----------|--------------|
| `light on` | EN | relay 1 → ON |
| `on` | EN shortcut | relay 1 → ON |
| `1 on` | EN numeric | relay 1 → ON |
| `allume` | FR | relay 1 → ON |
| `lumière on` / `lumiere on` | FR | relay 1 → ON |
| `light off` | EN | relay 1 → OFF |
| `off` | EN shortcut | relay 1 → OFF |
| `1 off` | EN numeric | relay 1 → OFF |
| `éteins` | FR | relay 1 → OFF |
| `lumière off` / `lumiere off` | FR | relay 1 → OFF |

### Curtain

| User sends | Language | Relay effect |
|------------|----------|--------------|
| `curtain up` | EN | relay 3 → OFF, then relay 2 → ON |
| `up` | EN shortcut | relay 3 → OFF, then relay 2 → ON |
| `2 up` | EN numeric | relay 3 → OFF, then relay 2 → ON |
| `open curtain` | EN | relay 3 → OFF, then relay 2 → ON |
| `monte` | FR | relay 3 → OFF, then relay 2 → ON |
| `volet monte` | FR | relay 3 → OFF, then relay 2 → ON |
| `ouvre` | FR | relay 3 → OFF, then relay 2 → ON |
| `curtain down` | EN | relay 2 → OFF, then relay 3 → ON |
| `down` | EN shortcut | relay 2 → OFF, then relay 3 → ON |
| `2 down` | EN numeric | relay 2 → OFF, then relay 3 → ON |
| `close curtain` | EN | relay 2 → OFF, then relay 3 → ON |
| `descend` | FR | relay 2 → OFF, then relay 3 → ON |
| `volet descend` | FR | relay 2 → OFF, then relay 3 → ON |
| `ferme` | FR | relay 2 → OFF, then relay 3 → ON |
| `curtain stop` | EN | relay 2 → OFF, relay 3 → OFF |
| `stop` | EN shortcut | relay 2 → OFF, relay 3 → OFF |
| `2 stop` | EN numeric | relay 2 → OFF, relay 3 → OFF |
| `stop curtain` | EN | relay 2 → OFF, relay 3 → OFF |
| `arrête` | FR | relay 2 → OFF, relay 3 → OFF |
| `stoppe` | FR | relay 2 → OFF, relay 3 → OFF |

### Utilities

| User sends | Action |
|------------|--------|
| `status` / `état` / `etat` / `s` / `?` | Query IPX800 for relay states, return formatted report |
| `help` / `aide` / `h` / `commands` / `commandes` | Return the command list |

### Unknown command

Any text that does not match the above table returns:
> ❓ Unknown command. Send *help* to see available commands.

---

## 5. Curtain Motor Interlock

**Why this matters:** The curtain motor is driven by two raw relays. Energising both simultaneously (UP and DOWN) would apply voltage to both motor windings at once — a short that can burn the motor or trip a breaker.

**How the interlock works:**

Every curtain direction command (`CURTAIN_UP`, `CURTAIN_DOWN`) is represented as an **ordered list of two relay operations**, not a single one:

- `CURTAIN_UP` → `[{relay: 3, state: 0}, {relay: 2, state: 1}]`
  *(clear DOWN relay first, then set UP relay)*
- `CURTAIN_DOWN` → `[{relay: 2, state: 0}, {relay: 3, state: 1}]`
  *(clear UP relay first, then set DOWN relay)*
- `CURTAIN_STOP` → `[{relay: 2, state: 0}, {relay: 3, state: 0}]`
  *(clear both)*

`IPX800Controller.execute()` applies these sequentially with `INTERLOCK_DELAY = 0.2` seconds (200 ms) between operations. This guarantees the opposite relay is fully de-energised before the target relay is set.

**The interlock is enforced in two places:**

1. `fastapi/commands.py` — defines the ordered relay-op lists for every curtain action.
2. `fastapi/ipx800.py` — `execute()` iterates the list with the delay.

**Do not remove or bypass this.** If the hardware is ever upgraded to an X-4VR extension (which has its own firmware interlock), the software interlock becomes redundant and can be removed at that point.

---

## 6. Security Layers

| Layer | Where | Mechanism |
|-------|-------|-----------|
| Transport | Node.js | TLS 1.2/1.3 via Let's Encrypt; Node.js handles HTTPS termination directly (no Nginx) |
| Authenticity | `webhook/security.js` | HMAC-SHA256 over raw request body; verified against `X-Hub-Signature-256` header using timing-safe comparison |
| Authorisation | `webhook/security.js` | Phone number whitelist: `ALLOWED_NUMBERS` in `.env`; numbers in E.164 without `+` |
| Replay protection | `webhook/security.js` | In-memory dedup of last 200 message IDs; prevents double-execution on Meta webhook retries |
| Internal isolation | VPS network | FastAPI listens on `127.0.0.1:8000` only — no public exposure, no authentication needed on internal calls |
| Secrets management | `.gitignore` | Both `.env` files excluded from git; `chmod 600` on the VPS |
| Device protection | LAN isolation | IPX800 not internet-facing; reachable only within the LAN shared with the VPS |

### HMAC verification detail

Meta signs every webhook `POST` body with the app secret using HMAC-SHA256. The signature arrives in the `X-Hub-Signature-256` header as `sha256=<hex>`. The verification in `security.js`:

1. Reads the **raw request body** (before JSON parsing) — Express is configured with a `verify` callback to preserve `req.rawBody`.
2. Computes `HMAC-SHA256(raw_body, WA_APP_SECRET)`.
3. Compares using `crypto.timingSafeEqual` to prevent timing-based attacks.
4. Throws on mismatch; the route handler returns HTTP 403 immediately.

---

## 7. IPX800 Push Notifications

The IPX800 can call the VPS when a physical switch changes relay state, allowing the system to send proactive WhatsApp alerts without polling.

### Webhook endpoint

```
GET https://<vps-domain>/ipx-notify?device=<name>&state=<0|1>
```

| Query param | Values | Meaning |
|-------------|--------|---------|
| `device` | `light` / `curtain_up` / `curtain_down` | Which device changed |
| `state` | `0` / `1` | New state (OFF / ON) |

**Example:** physical light switch turned ON:
```
GET https://myhome.example.com/ipx-notify?device=light&state=1
```

This sends a WhatsApp alert to **all** numbers in `ALLOWED_NUMBERS`:
> ✅ 💡 Light was physically switched ON at 14:32:07

### IPX800 Scenario configuration

In the IPX800 web interface, create a **Scenario** for each physical input you want to monitor:

1. **Scenarios** → **Add Scenario**
2. **Trigger:** the relevant relay change or digital input
3. **Action:** HTTP GET request to `https://<vps-domain>/ipx-notify?device=<name>&state={ledN}` where `{ledN}` is the IPX800 macro that evaluates to the current relay state

### State cache

The Node.js service keeps an in-memory cache (`webhook/state.js`) of the last known state for each device. This cache is updated both by:
- `/ipx-notify` calls (physical switch changes)
- Any command sent through the system

After a Node.js restart the cache starts empty (`UNKNOWN`) until the next push notification or `status` command.

---

## 8. Service Boundaries and API Contract

### `POST /control`

The Node.js service calls this to execute a command.

**Request:**
```json
{
  "command": "light on",
  "from": "33612345678"
}
```

**Response:**
```json
{
  "reply": "✅ Light turned ON",
  "success": true,
  "action": "LIGHT_ON"
}
```

On failure (IPX800 unreachable):
```json
{
  "reply": "❌ Control failed: IPX800 unreachable after 3 attempts. Check local IP and API key.",
  "success": false,
  "action": "LIGHT_ON"
}
```

The `from` field can also be sent as `from_number` — the FastAPI model accepts both via Pydantic's `alias` + `populate_by_name`.

### `GET /health`

Returns service status plus IPX800 reachability.

**Response (IPX800 up):**
```json
{
  "status": "ok",
  "service": "fastapi-control",
  "ipx800": "reachable",
  "ipx800_host": "192.168.1.100",
  "ipx800_port": 80
}
```

**Response (IPX800 down):**
```json
{
  "status": "degraded",
  "service": "fastapi-control",
  "ipx800": "unreachable",
  "ipx800_host": "192.168.1.100",
  "ipx800_port": 80
}
```

---

## 9. Configuration Reference

### `fastapi/.env`

| Variable | Default in `.env.example` | Description |
|----------|--------------------------|-------------|
| `IPX800_HOST` | `192.168.1.100` | Local IP address of the IPX800 on the shared LAN |
| `IPX800_PORT` | `80` | IPX800 HTTP port (default 80) |
| `IPX800_APIKEY` | `your-ipx800-apikey-here` | API key set in IPX800 web interface |
| `IPX800_TIMEOUT` | `5.0` | HTTP timeout in seconds per attempt |
| `IPX800_RETRY` | `3` | Number of retry attempts before giving up |
| `RELAY_LIGHT` | `1` | 1-based relay number for the light |
| `RELAY_CURTAIN_UP` | `2` | 1-based relay number for curtain UP motor winding |
| `RELAY_CURTAIN_DOWN` | `3` | 1-based relay number for curtain DOWN motor winding |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `LOG_FILE` | `logs/fastapi.log` | Log file path (relative to `fastapi/`) |
| `LOG_MAX_BYTES` | `10485760` | Rotate log at this size (10 MB) |
| `LOG_BACKUP_COUNT` | `7` | Number of rotated log files to retain |

### `webhook/.env`

| Variable | Default in `.env.example` | Description |
|----------|--------------------------|-------------|
| `NODE_PORT` | `443` | Port the HTTPS server listens on |
| `USE_SSL` | `true` | Set `false` for local HTTP-only testing |
| `SSL_KEY_PATH` | `/etc/letsencrypt/live/…/privkey.pem` | Let's Encrypt private key path |
| `SSL_CERT_PATH` | `/etc/letsencrypt/live/…/fullchain.pem` | Let's Encrypt full chain path |
| `WA_PHONE_NUMBER_ID` | `1234567890123456` | Meta WhatsApp Cloud API phone number ID |
| `WA_ACCESS_TOKEN` | `EAAxxxxxxxxxx` | Meta access token (permanent system user recommended) |
| `WA_VERIFY_TOKEN` | `your-random-uuid` | Token you invent; used for webhook registration with Meta |
| `WA_APP_SECRET` | `your-app-secret` | App secret from Meta App Dashboard (for HMAC) |
| `ALLOWED_NUMBERS` | `33612345678,33698765432` | Authorised E.164 numbers without `+`, comma-separated |
| `FASTAPI_HOST` | `127.0.0.1` | Internal FastAPI address |
| `FASTAPI_PORT` | `8000` | Internal FastAPI port |
| `LOG_FILE` | `logs/webhook.log` | Log file path (relative to `webhook/`) |
| `LOG_LEVEL` | `INFO` | Log level: `DEBUG` / `INFO` / `WARN` / `ERROR` |

---

## 10. Design Decisions and Rationale

### Two services, not one

The WhatsApp interface and the device interface have different concerns, different failure modes, and different upgrade paths. Node.js is the natural choice for a Meta webhook (event-driven, async, well-documented with the Meta SDKs). Python/FastAPI is the natural choice for a typed, async HTTP service doing structured data work. Merging them would require choosing one language and reimplementing the strengths of the other.

### No Nginx in front of Node.js

Node.js handles TLS termination directly using the built-in `https` module and Let's Encrypt certificates. This removes one layer (Nginx) and one configuration file. For a single-service VPS with one public endpoint, the added complexity of a reverse proxy provides no benefit. If multiple services ever need to share port 443, add Nginx at that point.

### `/preset.htm` not `/api/xdevices.json`

The implementation uses the older `preset.htm` + `status.xml` API rather than the JSON API documented in the original design. This was chosen because it is verified working on the specific device firmware in use. Both APIs are valid on IPX800 V4. If the JSON API is preferred in the future, change only `fastapi/ipx800.py`.

### No LLM for command parsing

The keyword map (`COMMAND_MAP` in `commands.py`) is the right tool for a fixed vocabulary. An LLM-based parser would add:
- External API latency (~500 ms+)
- Per-call cost
- A new failure mode (API outage / hallucination)
- Unpredictable behaviour on edge-case inputs

The keyword map is deterministic, zero-latency, zero-cost, and testable. For a vocabulary that fits in one screen, this is the correct choice.

### Direct LAN instead of Cloudflare Tunnel

The original design specified a Cloudflare Tunnel. The as-built deployment has the VPS and IPX800 on the same LAN, so the VPS reaches the IPX800 directly by local IP address — no tunnel, no DDNS, no router port-forwarding needed. The IPX800 API key provides authentication; the device is not internet-facing.

---

## 11. Original Design vs. As-Built

The original design document was written before implementation and proposed several approaches that were later changed. This table records the divergences for transparency.

| Topic | Original design | As built | Reason for change |
|-------|-----------------|----------|-------------------|
| LAN-to-VPS link | Cloudflare Tunnel (`cloudflared`) | Direct LAN (VPS and IPX800 on same network) | Simpler; no tunnel or DDNS needed when both devices share the LAN |
| Webhook framework | FastAPI handles Meta webhook directly (single service) | Node.js for Meta, FastAPI for device (two services) | Separation of concerns; each service is replaceable |
| TLS termination | Nginx reverse proxy | Node.js native HTTPS | One less component for a single-endpoint VPS |
| IPX800 endpoint | `/api/xdevices.json?SetR=NN&ClearR=NN` | `/preset.htm?ledN=0\|1` and `/status.xml` | Verified working on the actual device firmware |
| Curtain hardware | X-4VR extension with firmware interlock | Two raw relays with software interlock (200 ms gap) | No X-4VR available; software interlock implemented instead |
| Languages | English only (FR/ZH "optional Phase 3") | EN + FR fully implemented | Both implemented together at no extra cost |
| Device → user push | Not in original design | Implemented via `/ipx-notify` in Node.js | Required for complete two-way interaction |
| Test distribution | All tests in one service | Tests split per service, excluded from git | Tests run locally; only production code shipped |
