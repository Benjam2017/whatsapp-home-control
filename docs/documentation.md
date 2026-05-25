# WhatsApp Home Control — Project Documentation

Control lights and curtains in a demo installation by sending WhatsApp messages. The system routes a WhatsApp message to a VPS, which then drives a local **GCE Electronics IPX800 V4** relay controller via its native HTTP/JSON API.

This is the single source of truth for the project. Part I covers project context (goals, hardware, architecture, vocabulary, risks). Parts II–IV are the three execution phases.

---

## Table of contents

**Part I — Project context**

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Hardware and software inventory](#2-hardware-and-software-inventory)
3. [Architecture](#3-architecture)
4. [Command vocabulary (v1)](#4-command-vocabulary-v1)
5. [Roadmap and exit criteria](#5-roadmap-and-exit-criteria)
6. [Risks and open questions](#6-risks-and-open-questions)
7. [Repository layout](#7-repository-layout)

**Part II — Phase 1: Foundation and local connectivity**

8. [Phase 1 overview](#8-phase-1-overview)
9. [Hardware confirmation and site survey](#9-hardware-confirmation-and-site-survey)
10. [Obtain and verify the IPX800 V4 API reference](#10-obtain-and-verify-the-ipx800-v4-api-reference)
11. [Harden the IPX800](#11-harden-the-ipx800)
12. [Establish the LAN-to-VPS tunnel](#12-establish-the-lan-to-vps-tunnel)
13. [Phase 1 deliverables and effort](#13-phase-1-deliverables-and-effort)

**Part III — Phase 2: WhatsApp integration**

14. [Phase 2 overview](#14-phase-2-overview)
15. [Meta WhatsApp Cloud API setup](#15-meta-whatsapp-cloud-api-setup)
16. [VPS environment](#16-vps-environment)
17. [The FastAPI backend](#17-the-fastapi-backend)
18. [Nginx and the webhook URL](#18-nginx-and-the-webhook-url)
19. [Register the webhook with Meta](#19-register-the-webhook-with-meta)
20. [End-to-end test](#20-end-to-end-test)
21. [Phase 2 deliverables and effort](#21-phase-2-deliverables-and-effort)

**Part IV — Phase 3: Hardening, polish, demo readiness**

22. [Phase 3 overview](#22-phase-3-overview)
23. [systemd unit for the FastAPI app](#23-systemd-unit-for-the-fastapi-app)
24. [Structured logging](#24-structured-logging)
25. [Pretty status reply](#25-pretty-status-reply)
26. [Healthcheck endpoint](#26-healthcheck-endpoint)
27. [Error handling improvements](#27-error-handling-improvements)
28. [Localization (optional)](#28-localization-optional)
29. [Operational tooling](#29-operational-tooling)
30. [Demo runbook](#30-demo-runbook)
31. [Pre-demo checklist](#31-pre-demo-checklist)
32. [Phase 3 deliverables and effort](#32-phase-3-deliverables-and-effort)

---

# Part I — Project context

## 1. Goals and non-goals

**Goals**

- Send a WhatsApp message like "lights on", "curtains up", "curtains stop" and have the demo room respond within ~2 seconds.
- Confirm state back to the sender ("✓ lights on").
- Demo-quality reliability: works repeatedly, recovers from network blips, restarts cleanly after a reboot.
- No exposure of the IPX800 V4 to the public internet.

**Non-goals (for now)**

- A full home-automation platform. We are not building Home Assistant.
- Voice control, schedules, scenes, dashboards. Out of scope for the demo.
- Multi-tenant or per-user permissions beyond a phone-number whitelist.
- Offline / no-internet operation. The demo assumes the LAN has internet.

## 2. Hardware and software inventory

| Item | Detail | Status |
|---|---|---|
| Relay controller | GCE Electronics IPX800 V4 (firmware IPX-OS4) | confirmed via `Manuel_IPX800V4.pdf` |
| Curtain extension | X-4VR (native shutter control, up/down/stop, percentage) | **assumed — please confirm** |
| Lights wiring | One relay on the main IPX800 V4 | relay number **TBD** |
| Curtain wiring | One shutter channel on X-4VR | channel number **TBD** |
| Network at demo site | LAN with internet access, behind NAT | assumed |
| VPS | Remote Ubuntu/Debian, public IP, domain | confirmed |
| WhatsApp transport | Meta WhatsApp Cloud API (official) | confirmed |
| Backend framework | FastAPI on Python 3.11+ | proposed |
| Reverse proxy / TLS | Nginx + Let's Encrypt | proposed |
| Process supervisor | systemd | proposed |
| LAN ↔ VPS link | **Cloudflare Tunnel** (cloudflared) | proposed (see §3) |

> **Items marked TBD or "assumed" must be confirmed before Phase 1 work begins.** See §9.

## 3. Architecture

```
┌────────────┐     WhatsApp Cloud API    ┌──────────────────────────┐
│  user's    │ ────────────────────────► │  Meta WhatsApp servers   │
│  phone     │ ◄──────────────────────── │                          │
└────────────┘                            └──────────┬───────────────┘
                                                     │ webhook (HTTPS POST)
                                                     ▼
                                          ┌──────────────────────────┐
                                          │  VPS (public IP, domain) │
                                          │  ┌─────────────────────┐ │
                                          │  │ Nginx (TLS, 443)    │ │
                                          │  │   │                 │ │
                                          │  │   ▼                 │ │
                                          │  │ FastAPI app         │ │
                                          │  │  - verify signature │ │
                                          │  │  - whitelist sender │ │
                                          │  │  - parse command    │ │
                                          │  │  - call IPX800 API  │ │
                                          │  │  - reply on WA      │ │
                                          │  └─────────┬───────────┘ │
                                          └────────────┼─────────────┘
                                                       │ HTTP (private)
                                                       │ via Cloudflare Tunnel
                                                       ▼
                                          ┌──────────────────────────┐
                                          │  Demo LAN                │
                                          │  ┌─────────────────────┐ │
                                          │  │  cloudflared        │ │
                                          │  │  (tunnel client)    │ │
                                          │  └─────────┬───────────┘ │
                                          │            │             │
                                          │            ▼             │
                                          │  ┌─────────────────────┐ │
                                          │  │  IPX800 V4 + X-4VR  │ │
                                          │  │  HTTP/JSON API      │ │
                                          │  │  /api/xdevices.json │ │
                                          │  └─────────┬───────────┘ │
                                          │            │             │
                                          │      ┌─────┴─────┐       │
                                          │      ▼           ▼       │
                                          │   Lights      Curtains   │
                                          └──────────────────────────┘
```

**Why Cloudflare Tunnel and not port-forwarding?**

The IPX800 V4 manual describes port-forwarding the device to the internet (p. 84). That works, but:

- The IPX800's only authentication is a single shared "API key" (`apikey` by default — many users never change it).
- The web UI has had CVEs in the past; exposing it directly is a real risk.
- A NAT change at the demo site breaks the demo.

A Cloudflare Tunnel reverses the direction: `cloudflared` runs on the LAN and dials out to Cloudflare; the VPS reaches the IPX800 over that outbound tunnel. No inbound firewall holes, no DDNS, no public exposure of the device.

Alternatives kept on the shelf:

- **Tailscale** — equally good, slightly simpler if you already use it. Same security properties.
- **WireGuard direct VPS → LAN** — works if you control both ends; more setup.
- **Port-forward + DDNS** (per manual) — only if Cloudflare/Tailscale are blocked.

## 4. Command vocabulary (v1)

WhatsApp messages are parsed case-insensitively. Initial vocabulary (English; FR/ZH can be added in Phase 3):

| Phrase | Action |
|---|---|
| `lights on`, `light on`, `on` | Energize lights relay |
| `lights off`, `light off`, `off` | De-energize lights relay |
| `curtains up`, `curtain up`, `up`, `open` | X-4VR open command on configured channel |
| `curtains down`, `curtain down`, `down`, `close` | X-4VR close command on configured channel |
| `curtains stop`, `curtain stop`, `stop` | X-4VR stop command on configured channel |
| `status` | Read current relay state + curtain position, reply with summary |
| `help` | Reply with the command list |

Anything else → reply with "Sorry, I didn't understand. Send `help` for commands."

> **Note on the X-4VR command names:** the IPX800 V4 user manual confirms the X-4VR extension exists and exposes presets and percentage control via the API. The exact query-string verbs (e.g., `SetVR`, `SetVR01=100`) are documented in GCE's separate **API reference PDF** which is *not yet in the project*. Phase 1 includes a step to obtain that document and verify the exact verbs before code is written.

## 5. Roadmap and exit criteria

**Phase 1 — Foundation and local connectivity (Part II).** Confirm hardware, decide tunnel vs port-forward, harden the IPX800 (change API key, set static IP), prove a single HTTP call from the VPS reaches the device and toggles a relay. No WhatsApp yet.

*Exit criterion:* `curl` on the VPS turns the lights on and off, and moves the curtains up, down, and stop.

**Phase 2 — WhatsApp integration (Part III).** Meta Cloud API setup, FastAPI webhook on the VPS with Nginx + Let's Encrypt, signature verification, sender whitelist, command parser, state echo back.

*Exit criterion:* from a whitelisted phone, sending "lights on" turns the lights on within ~2 seconds and the user receives a "✓ lights on" reply.

**Phase 3 — Hardening, polish, demo readiness (Part IV).** systemd, logs, idempotency cache, error handling, ack messages, a small `/status` query, and a written demo runbook.

*Exit criterion:* a written runbook walks an operator from cold-boot of all components through a successful demo, with rollback steps.

## 6. Risks and open questions

| # | Item | Owner | Resolution needed by |
|---|---|---|---|
| R1 | Exact IPX800 V4 API command syntax for X-4VR (up / down / stop / percentage) | Xiaosong (obtain GCE API PDF) | Phase 1 §10 |
| R2 | Which physical relay drives lights, which channel of X-4VR drives the demo curtain | Xiaosong (site survey) | Phase 1 §9 |
| R3 | Is the IPX800's API key still the default `apikey`? Demo box must have a strong key | Xiaosong | Phase 1 §11 |
| R4 | Cloudflare account / Tailscale account availability | Xiaosong | Phase 1 §12 |
| R5 | Meta Business account, verified WhatsApp Business number, test phone number quota | Xiaosong | Phase 2 §15 |
| R6 | VPS domain name and DNS access (for Let's Encrypt) | Xiaosong | Phase 2 §16 |

## 7. Repository layout

```
whatsapp-home-control/
├── README.md                  ← short pointer to this doc
├── docs/
│   ├── documentation.md       ← THIS FILE
│   └── api-notes/             ← copies of the IPX800 API PDF, screenshots, etc.
├── app/                       ← FastAPI backend (Phase 2)
│   ├── main.py
│   ├── ipx800_client.py
│   ├── whatsapp_client.py
│   ├── parser.py
│   ├── security.py
│   ├── config.py
│   └── tests/
├── deploy/
│   ├── nginx.conf.example
│   ├── systemd/
│   │   └── wahc.service
│   └── cloudflared/
│       └── config.yml.example
├── scripts/
│   └── smoke.sh
├── .env.example
├── pyproject.toml
└── .gitignore
```

---

# Part II — Phase 1: Foundation and local connectivity

## 8. Phase 1 overview

**Goal.** Prove that the VPS can drive a real relay and a real curtain on the demo LAN, end to end, with no WhatsApp in the loop yet. By the end of this phase a `curl` command from the VPS will turn the lights on and off, and open / close / stop the curtains.

**Why this phase exists.** Every later layer (WhatsApp, parsing, replies, hardening) is meaningless if the bottom layer isn't solid. Confirming hardware identity, command syntax, and tunnel connectivity now removes the largest single source of demo-day risk.

**Exit criterion.** From the VPS shell, all six commands below succeed and the physical room responds correctly:

```
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&SetR=$LIGHT_RELAY"      # lights ON
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&ClearR=$LIGHT_RELAY"    # lights OFF
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&<VR-up-verb>"           # curtains UP
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&<VR-down-verb>"         # curtains DOWN
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&<VR-stop-verb>"         # curtains STOP
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&Get=R"                  # read all relays
```

The `<VR-...-verb>` placeholders get resolved in §10 below once the GCE API reference is in hand.

## 9. Hardware confirmation and site survey

**Owner:** Xiaosong, on site or via remote check with the installer.

Confirm and record in `docs/api-notes/site-survey.md`:

1. **IPX800 V4 firmware version**. Reachable at `http://<lan-ip>/` → System → Information. Note the firmware and software versions.
2. **Extensions installed**. Confirm presence (and codes) of:
   - X-4VR (curtains) — code starts with `50`. Without it the curtain plan changes substantially.
   - X-8R (extra relays) — code starts with `20`. Optional; only matters if lights are on an extension and not the main unit.
3. **Wiring map**. For each fixture controlled in the demo:
   - Lights: which physical relay number (e.g., `R03` on the main IPX800)?
   - Curtains: which X-4VR channel (1 through 4 on the first X-4VR)?
4. **IPX800 LAN IP**. Confirm it is reachable from a laptop on the same LAN (`ping`, then load `http://<ip>/` in a browser).
5. **API protection status**. In the web UI → Network → API:
   - Is "Activation clef" (key activation) set to **OUI**?
   - What is the current key value? If it is still the factory default `apikey`, **flag for change** in §11.
   - Is M2M enabled? For the demo we only need the HTTP/JSON API, not the M2M TCP socket — leave M2M off unless you have a specific reason.

## 10. Obtain and verify the IPX800 V4 API reference

The user manual currently in the project (`Manuel_IPX800V4.pdf`) describes *what the device does* and gives one worked example of the command syntax:

```
GET /api/xdevices.json?SetR=01      ← relay 01 ON
GET /api/xdevices.json?ClearR=01    ← relay 01 OFF
```

The **full command table** (every verb, including X-4VR up / down / stop / position, status reads, error codes) is in a separate GCE document, referenced on p. 27 of the manual:

> "...cf. l'API de l'IPX800 V4 disponible sur le site de GCE Electronics dans la rubrique « Téléchargements »..."

**Action items:**

1. Download the official IPX800 V4 API reference PDF from `gce-electronics.com` (downloads section). Add it to `docs/api-notes/`.
2. Extract and verify these exact strings (the names below are best-guess from screenshots; **do not hard-code them in app code until verified**):
   - Relay set / clear: `SetR=NN`, `ClearR=NN` — **looks confirmed** by manual screenshots.
   - Relay read all states: probably `Get=R` or `Get=all` — **needs verification**.
   - X-4VR up / down / stop on channel N (extension 1): probably something like `SetVR0N=UP`, `SetVR0N=DOWN`, `SetVR0N=STOP`, or `SetVR0N=0` / `SetVR0N=100` for percentage open/closed — **needs verification**.
   - Authentication parameter: confirmed as `key=<apikey>`.
3. Once verified, fill out the canonical command table in `docs/api-notes/ipx800-commands.md`. The Phase 2 client code will read from this table.

## 11. Harden the IPX800

**Done from the IPX800 web UI**, by the installer or Xiaosong.

1. **Change the API key.** Network → API → Clef utilisateur. Replace `apikey` with a 32+ character random string. Generate with:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   Store this in a password manager. It will be the `IPX_API_KEY` env var on the VPS.
2. **Enable admin authentication.** Network → Paramètres → Administrateur **OUI**, set a strong admin password. Same for "Utilisateur" if you want to lock the dashboard too.
3. **Set a static LAN IP** for the IPX800 (either DHCP reservation on the router, or static IP on the device). The tunnel config will point at this IP.
4. **Disable services you do not need.** If you are not using M2M, leave it **NON**. If you are not using SmartGCE cloud, leave it **NON**.
5. **Note the MAC address.** Useful later for router-level allow-listing.

## 12. Establish the LAN-to-VPS tunnel

We are using **Cloudflare Tunnel** (`cloudflared`). The same outcome can be achieved with Tailscale; if you prefer Tailscale, substitute steps 12.1–12.3 with `tailscale up` on both ends and skip the rest.

### 12.1 Prerequisites

- A Cloudflare account (free tier is fine).
- The VPS's domain managed in Cloudflare DNS. We'll add the IPX800 tunnel as a subdomain, e.g., `ipx.<your-domain>`.
- A small always-on Linux box on the demo LAN to run `cloudflared`. A Raspberry Pi or any Linux mini-PC works. **It does not have to be on the same hardware as the IPX800.**

### 12.2 Install and authenticate cloudflared on the LAN box

```bash
# On the LAN box (Debian/Ubuntu/RPi OS)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared tunnel login                     # opens a browser; authenticates to your CF account
cloudflared tunnel create wahc-demo           # creates the tunnel, prints UUID
```

### 12.3 Configure the tunnel

Create `/etc/cloudflared/config.yml`:

```yaml
tunnel: <UUID-from-create-step>
credentials-file: /home/<user>/.cloudflared/<UUID>.json

ingress:
  - hostname: ipx.<your-domain>
    service: http://<ipx800-lan-ip>:80     # the device's LAN IP, port 80
  - service: http_status:404
```

Route the hostname to the tunnel:

```bash
cloudflared tunnel route dns wahc-demo ipx.<your-domain>
```

Install as a service so it starts on boot:

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### 12.4 Lock down access at Cloudflare

`ipx.<your-domain>` is now resolvable, but we **do not** want it open to the public. In the Cloudflare dashboard:

- **Zero Trust → Access → Applications → Add application → Self-hosted.**
- Hostname: `ipx.<your-domain>`.
- Policy: **Service Auth** with a service token. (Or, simpler for a demo: an IP-allow-list policy that only permits the VPS's public IP.)
- This means a random visitor hitting `https://ipx.<your-domain>/` gets blocked by Cloudflare *before* any request reaches your LAN.

The VPS app will present the service-token headers on every outbound request to `ipx.<your-domain>`.

### 12.5 Smoke test from the VPS

```bash
# On the VPS
export IPX_URL=https://ipx.<your-domain>
export KEY=<the-strong-key-from-§11>

# Should return JSON describing the IPX state (exact shape depends on firmware)
curl -s "$IPX_URL/api/xdevices.json?key=$KEY&Get=R" \
  -H "CF-Access-Client-Id: <token-id>" \
  -H "CF-Access-Client-Secret: <token-secret>"
```

If this returns JSON, the tunnel is working. If you get a Cloudflare login page in the response, the Access policy is set to interactive auth — switch it to Service Auth.

## 13. Phase 1 deliverables and effort

**Deliverables.** By the end of Phase 1 there should be, in the project:

1. `docs/api-notes/site-survey.md` — completed inventory from §9.
2. `docs/api-notes/IPX800V4_API.pdf` — the official command reference, downloaded.
3. `docs/api-notes/ipx800-commands.md` — verified command table (relay set/clear/get, X-4VR up/down/stop, error semantics).
4. `docs/api-notes/tunnel-setup.md` — short writeup of the cloudflared config actually used, with the chosen hostname and Access policy ID.
5. A working shell script `scripts/smoke-test.sh` on the VPS that runs the six smoke-test curls and prints PASS/FAIL.
6. Demonstrated, on a video call or in person: lights on / off / curtains up / down / stop, driven from the VPS shell.

**What is intentionally NOT in Phase 1.**

- No Meta / WhatsApp setup. That is Phase 2.
- No FastAPI code. The smoke tests are raw `curl`.
- No Nginx, no Let's Encrypt, no systemd unit for our own app.
- No command parser, no whitelist, no signature verification.

This separation matters: if Phase 1 has any problem, we want to find it in the bottom layer, not while debugging a webhook timeout.

**Effort.**

| Step | Effort |
|---|---|
| §9 Hardware survey | 1–2 h (on site) |
| §10 Get and verify API ref | 1 h |
| §11 Harden IPX800 | 30 min |
| §12 Tunnel setup | 2–3 h (first time with cloudflared) |
| §13 Write up & smoke test | 1 h |

About half a day of focused work, plus elapsed time for the site visit.

---

# Part III — Phase 2: WhatsApp integration

## 14. Phase 2 overview

**Prerequisites.** Phase 1 is complete: the VPS can already drive the IPX800 with raw curl commands. The command table in `docs/api-notes/ipx800-commands.md` is verified.

**Goal.** From a whitelisted phone, sending "lights on" to the demo's WhatsApp number turns the lights on within ~2 seconds and the user receives a "✓ lights on" reply. Same for curtains up / down / stop.

**Exit criterion.** A live demo from any of the whitelisted phones works for all commands in the v1 vocabulary (§4), including the `status` and `help` replies.

## 15. Meta WhatsApp Cloud API setup

**Owner:** Xiaosong (Meta account work cannot be automated).

1. Create / use an existing **Meta Business** account at `business.facebook.com`.
2. Create a **WhatsApp Business App** in `developers.facebook.com`.
3. In the app's WhatsApp panel:
   - Get the **temporary access token** (good for 24 h, fine for development). For the demo, generate a **System User permanent token** with `whatsapp_business_messaging` permission.
   - Get the **Phone Number ID** (a long integer; this is what your code will POST replies to, not the phone number itself).
   - Note the **Business Account ID** and the **App Secret** (Settings → Basic). The App Secret is what verifies inbound webhook signatures.
4. Add the **test recipient phone numbers** (up to 5 in dev mode) — these are the only numbers Meta will allow you to send to until the app is "live". For a demo this is fine.
5. Webhook configuration — leave this until §19, after the FastAPI endpoint is up.

**Secrets to record (will go into the VPS `.env`):**

```
META_VERIFY_TOKEN=<choose-a-random-string>    # you invent this
META_APP_SECRET=<from-app-settings-basic>
META_ACCESS_TOKEN=<system-user-permanent-token>
META_PHONE_NUMBER_ID=<from-whatsapp-panel>
```

## 16. VPS environment

### 16.1 OS prep

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv nginx certbot python3-certbot-nginx ufw
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

### 16.2 Application user and directory

```bash
sudo useradd --system --create-home --home /opt/wahc --shell /usr/sbin/nologin wahc
sudo mkdir -p /opt/wahc/app
sudo chown -R wahc:wahc /opt/wahc
```

Code is deployed to `/opt/wahc/app`. Virtualenv lives at `/opt/wahc/venv`.

### 16.3 DNS + TLS

In your DNS provider, point an `A` record for `wa.<your-domain>` (or whatever subdomain you choose for the webhook) at the VPS public IP. Then:

```bash
sudo certbot --nginx -d wa.<your-domain>
```

Certbot writes a working Nginx config and gets a Let's Encrypt cert. We'll edit that config in §18.

## 17. The FastAPI backend

### 17.1 Layout

```
/opt/wahc/app/
├── main.py            ← FastAPI entrypoint, routes
├── config.py          ← settings from env
├── ipx800_client.py   ← thin HTTP wrapper around the IPX800 API
├── whatsapp_client.py ← sends replies via Meta Graph API
├── parser.py          ← message text → command intent
├── security.py        ← signature verification, whitelist check, dedup cache
└── tests/
    ├── test_parser.py
    ├── test_security.py
    └── test_ipx800_client.py
```

### 17.2 Dependencies (`pyproject.toml`)

```toml
[project]
name = "wahc"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "httpx>=0.27",
  "pydantic>=2.7",
  "pydantic-settings>=2.4",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21", "ruff>=0.6"]
```

### 17.3 Settings (`config.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Meta / WhatsApp
    meta_verify_token: str
    meta_app_secret: str
    meta_access_token: str
    meta_phone_number_id: str

    # IPX800
    ipx_url: str                  # https://ipx.<your-domain>
    ipx_api_key: str
    ipx_light_relay: int          # e.g., 3  → SetR=03
    ipx_curtain_channel: int      # e.g., 1  → first channel of X-4VR ext 1

    # Cloudflare Access service token (so VPS can reach the tunneled IPX)
    cf_access_client_id: str
    cf_access_client_secret: str

    # Authorization
    allowed_senders: list[str]    # phone numbers in E.164, e.g., ["33612345678"]

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
```

### 17.4 IPX800 client (`ipx800_client.py`)

```python
import httpx
from .config import settings

class IPX800Client:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=settings.ipx_url,
            headers={
                "CF-Access-Client-Id": settings.cf_access_client_id,
                "CF-Access-Client-Secret": settings.cf_access_client_secret,
            },
            timeout=5.0,
        )

    async def _call(self, params: dict) -> dict:
        params = {"key": settings.ipx_api_key, **params}
        r = await self._client.get("/api/xdevices.json", params=params)
        r.raise_for_status()
        return r.json()

    async def light_on(self) -> dict:
        return await self._call({"SetR": f"{settings.ipx_light_relay:02d}"})

    async def light_off(self) -> dict:
        return await self._call({"ClearR": f"{settings.ipx_light_relay:02d}"})

    async def curtain_up(self) -> dict:
        # TODO: replace with the verified X-4VR up verb from phase1
        return await self._call({"SetVR" + f"{settings.ipx_curtain_channel:02d}": "0"})

    async def curtain_down(self) -> dict:
        # TODO: replace with the verified X-4VR down verb from phase1
        return await self._call({"SetVR" + f"{settings.ipx_curtain_channel:02d}": "100"})

    async def curtain_stop(self) -> dict:
        # TODO: replace with the verified X-4VR stop verb from phase1
        return await self._call({"SetVR" + f"{settings.ipx_curtain_channel:02d}": "STOP"})

    async def read_state(self) -> dict:
        # Phase 1 confirms whether this is Get=R or something else
        return await self._call({"Get": "R"})
```

> The `TODO` lines stay until Phase 1's API-reference task confirms the exact verbs. Until then, the curtain code should not be deployed beyond local tests.

### 17.5 Parser (`parser.py`)

```python
from dataclasses import dataclass
from enum import Enum

class Intent(str, Enum):
    LIGHT_ON = "light_on"
    LIGHT_OFF = "light_off"
    CURTAIN_UP = "curtain_up"
    CURTAIN_DOWN = "curtain_down"
    CURTAIN_STOP = "curtain_stop"
    STATUS = "status"
    HELP = "help"
    UNKNOWN = "unknown"

@dataclass
class ParseResult:
    intent: Intent
    original: str

# Simple keyword-matching is the right call for v1. Don't reach for LLMs.
_KEYWORDS = {
    Intent.LIGHT_ON:    {"lights on", "light on", "on"},
    Intent.LIGHT_OFF:   {"lights off", "light off", "off"},
    Intent.CURTAIN_UP:  {"curtains up", "curtain up", "up", "open"},
    Intent.CURTAIN_DOWN:{"curtains down", "curtain down", "down", "close"},
    Intent.CURTAIN_STOP:{"curtains stop", "curtain stop", "stop"},
    Intent.STATUS:      {"status"},
    Intent.HELP:        {"help", "?"},
}

def parse(text: str) -> ParseResult:
    norm = " ".join(text.strip().lower().split())
    for intent, phrases in _KEYWORDS.items():
        if norm in phrases:
            return ParseResult(intent, text)
    return ParseResult(Intent.UNKNOWN, text)
```

### 17.6 Security (`security.py`)

```python
import hmac, hashlib, time
from collections import OrderedDict
from .config import settings

def verify_signature(raw_body: bytes, header_value: str | None) -> bool:
    """Meta sends X-Hub-Signature-256: sha256=<hex>."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.meta_app_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, header_value.removeprefix("sha256="))

def is_allowed(sender_e164: str) -> bool:
    return sender_e164 in settings.allowed_senders

# Tiny in-memory dedup cache. Meta may redeliver webhooks; we don't want to
# double-toggle relays on a redelivery.
class DedupCache:
    def __init__(self, maxsize: int = 1024, ttl_seconds: int = 600):
        self._items: OrderedDict[str, float] = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl_seconds

    def seen(self, message_id: str) -> bool:
        now = time.monotonic()
        # purge expired
        for k in list(self._items):
            if now - self._items[k] > self._ttl:
                self._items.pop(k, None)
            else:
                break
        if message_id in self._items:
            return True
        self._items[message_id] = now
        if len(self._items) > self._maxsize:
            self._items.popitem(last=False)
        return False

dedup = DedupCache()
```

### 17.7 WhatsApp client (`whatsapp_client.py`)

```python
import httpx
from .config import settings

class WhatsAppClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=f"https://graph.facebook.com/v20.0/{settings.meta_phone_number_id}",
            headers={"Authorization": f"Bearer {settings.meta_access_token}"},
            timeout=10.0,
        )

    async def send_text(self, to: str, body: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        r = await self._client.post("/messages", json=payload)
        r.raise_for_status()
```

### 17.8 Routes (`main.py`)

```python
from fastapi import FastAPI, Request, HTTPException, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse
import logging

from .config import settings
from .security import verify_signature, is_allowed, dedup
from .parser import parse, Intent
from .ipx800_client import IPX800Client
from .whatsapp_client import WhatsAppClient

log = logging.getLogger("wahc")
app = FastAPI()
ipx = IPX800Client()
wa = WhatsAppClient()

HELP_TEXT = (
    "Commands:\n"
    "  lights on / lights off\n"
    "  curtains up / curtains down / curtains stop\n"
    "  status\n"
    "  help"
)

@app.get("/webhook")
def verify(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    if mode == "subscribe" and token == settings.meta_verify_token:
        return PlainTextResponse(challenge)
    raise HTTPException(403)

@app.post("/webhook")
async def receive(request: Request, background: BackgroundTasks):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not verify_signature(raw, sig):
        raise HTTPException(403, "bad signature")

    payload = await request.json()
    # Meta envelope: entry[].changes[].value.messages[]
    try:
        change = payload["entry"][0]["changes"][0]["value"]
        msg = change["messages"][0]
    except (KeyError, IndexError):
        return {"status": "ignored"}  # status updates, etc.

    if msg.get("type") != "text":
        return {"status": "ignored"}

    message_id = msg["id"]
    sender = msg["from"]
    text = msg["text"]["body"]

    if dedup.seen(message_id):
        return {"status": "dedup"}
    if not is_allowed(sender):
        log.warning("unauthorized sender", extra={"sender": sender})
        return {"status": "forbidden"}

    # Ack fast (Meta will retry if we take too long); do work in background.
    background.add_task(handle, sender, text)
    return {"status": "queued"}

async def handle(sender: str, text: str) -> None:
    res = parse(text)
    try:
        if res.intent == Intent.LIGHT_ON:
            await ipx.light_on();   reply = "✓ lights on"
        elif res.intent == Intent.LIGHT_OFF:
            await ipx.light_off();  reply = "✓ lights off"
        elif res.intent == Intent.CURTAIN_UP:
            await ipx.curtain_up();   reply = "✓ curtains opening"
        elif res.intent == Intent.CURTAIN_DOWN:
            await ipx.curtain_down(); reply = "✓ curtains closing"
        elif res.intent == Intent.CURTAIN_STOP:
            await ipx.curtain_stop(); reply = "✓ curtains stopped"
        elif res.intent == Intent.STATUS:
            state = await ipx.read_state()
            reply = f"State: {state}"  # Phase 3 will pretty-print this
        elif res.intent == Intent.HELP:
            reply = HELP_TEXT
        else:
            reply = "Sorry, I didn't understand. Send 'help' for commands."
    except Exception as e:
        log.exception("command failed")
        reply = f"⚠ command failed: {e.__class__.__name__}"
    await wa.send_text(sender, reply)
```

### 17.9 Local tests

Before deploying anything to the VPS, run the unit tests for parser and security locally. The IPX client gets tested with `respx` to mock the HTTP calls. Aim for these tests passing before §18.

## 18. Nginx and the webhook URL

Replace the cert-bot-generated `/etc/nginx/sites-enabled/wa.<your-domain>` with (Let's Encrypt cert paths preserved by certbot):

```nginx
server {
    server_name wa.<your-domain>;

    location /webhook {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_read_timeout 30s;
        client_max_body_size 1m;
    }

    # Block everything else
    location / {
        return 404;
    }

    listen 443 ssl;
    ssl_certificate     /etc/letsencrypt/live/wa.<your-domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/wa.<your-domain>/privkey.pem;
}

server {
    if ($host = wa.<your-domain>) { return 301 https://$host$request_uri; }
    listen 80;
    server_name wa.<your-domain>;
    return 404;
}
```

```bash
sudo nginx -t && sudo systemctl reload nginx
```

For Phase 2, run the FastAPI app in the foreground while testing:

```bash
cd /opt/wahc/app
sudo -u wahc /opt/wahc/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Phase 3 will move this to systemd.

## 19. Register the webhook with Meta

In the WhatsApp panel of the Meta App:

1. **Callback URL:** `https://wa.<your-domain>/webhook`
2. **Verify token:** the value of `META_VERIFY_TOKEN` from your `.env`.
3. Click **Verify and Save** → Meta sends a `GET /webhook?hub.mode=subscribe&...` and expects the challenge echoed back. Our `GET /webhook` route handles this.
4. **Subscribe to the `messages` webhook field.**

## 20. End-to-end test

From a whitelisted phone, message the WhatsApp business number with:

| Input | Expected reply | Expected physical effect |
|---|---|---|
| `lights on` | `✓ lights on` | lights energize |
| `lights off` | `✓ lights off` | lights de-energize |
| `curtains up` | `✓ curtains opening` | curtain begins opening |
| `curtains down` | `✓ curtains closing` | curtain begins closing |
| `curtains stop` | `✓ curtains stopped` | curtain stops |
| `status` | a state dump (raw for now) | none |
| `help` | the help message | none |
| `make me coffee` | `Sorry, I didn't understand...` | none |

If signature verification fails repeatedly, double-check that `META_APP_SECRET` matches the App's actual secret (not the access token — they are different values).

## 21. Phase 2 deliverables and effort

**Deliverables.**

1. Working FastAPI app under `/opt/wahc/app/`, started manually for now.
2. Nginx config with TLS, only `/webhook` exposed.
3. Meta webhook verified and subscribed.
4. `.env` populated on the VPS (file mode `0600`, owned by `wahc:wahc`, **not** in git).
5. Unit tests for parser and security passing.
6. A short `docs/phase2-runbook.md` covering how to start/stop the app manually and how to check logs.

**What is intentionally NOT in Phase 2.**

- systemd service. Manual `uvicorn` is fine for testing; Phase 3 packages it.
- Structured / JSON logs. Plain Python logging is enough to debug Phase 2.
- The `status` reply is raw JSON. Phase 3 prettifies it.
- Localization (FR / ZH). English only.
- Multi-tenant ACLs. Single shared whitelist.

**Effort.**

| Step | Effort |
|---|---|
| §15 Meta setup | 1–2 h (much of it waiting for Meta verification flows) |
| §16 VPS prep + TLS | 1 h |
| §17 App code + local tests | 4–6 h |
| §18 Nginx wiring | 30 min |
| §19 Webhook registration | 30 min |
| §20 End-to-end test + bugfix | 1–2 h |

About one full day.

---

# Part IV — Phase 3: Hardening, polish, demo readiness

## 22. Phase 3 overview

**Prerequisites.** Phase 2 is complete: the webhook is verified, signatures are validated, the whitelist works, and from a whitelisted phone the v1 vocabulary drives the lights and curtain.

**Goal.** Take the Phase 2 prototype to a state where a non-developer operator could run the demo from a written runbook, recover from common failures, and read clean logs if something is off.

**Exit criterion.** A reviewer follows `demo-runbook.md` from cold-boot of all components and successfully runs the demo without consulting the developer. Cold-boot includes: rebooting the VPS, the LAN tunnel host, and the IPX800.

## 23. systemd unit for the FastAPI app

Replace the manual `uvicorn` invocation from Phase 2.

`/etc/systemd/system/wahc.service`:

```ini
[Unit]
Description=WhatsApp Home Control - FastAPI webhook
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wahc
Group=wahc
WorkingDirectory=/opt/wahc/app
EnvironmentFile=/opt/wahc/app/.env
ExecStart=/opt/wahc/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=on-failure
RestartSec=3s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/wahc

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now wahc
sudo systemctl status wahc
journalctl -u wahc -f
```

Two workers is enough for a demo — the load is tiny. Restart-on-failure means a transient bug doesn't kill the service permanently.

## 24. Structured logging

Replace `logging.basicConfig` with a small JSON formatter. This makes `journalctl -u wahc -o cat | jq` work and gives you greppable, structured fields.

`logging_config.py`:

```python
import logging, json, sys, time

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("sender", "intent", "message_id", "duration_ms", "status_code"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

def configure() -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(logging.INFO)
```

Call `logging_config.configure()` at the top of `main.py`. In each handler, log with structured `extra=`:

```python
log.info("command executed",
         extra={"sender": sender, "intent": res.intent.value, "message_id": message_id,
                "duration_ms": int((time.monotonic() - t0) * 1000)})
```

## 25. Pretty status reply

In Phase 2 the `status` reply was raw JSON. In Phase 3 we map the IPX800 response into a human-readable line.

`status.py`:

```python
def format_status(raw: dict, light_relay: int, curtain_channel: int) -> str:
    # Exact field names depend on the IPX800 API doc obtained in Phase 1.
    # Typical shape: {"R1": 0, "R2": 1, ...} for relays.
    light_key = f"R{light_relay}"
    light = "on" if raw.get(light_key) == 1 else "off"
    # Curtain percentage field: TBD from API doc, e.g., "VR1-1" → 0..100
    pct = raw.get(f"VR1-{curtain_channel}", "?")
    if pct == 0:        curtain = "fully open"
    elif pct == 100:    curtain = "fully closed"
    elif isinstance(pct, int): curtain = f"{pct}% closed"
    else: curtain = "unknown"
    return f"Lights: {light}\nCurtains: {curtain}"
```

`handle()` in `main.py`:

```python
elif res.intent == Intent.STATUS:
    state = await ipx.read_state()
    reply = format_status(state, settings.ipx_light_relay, settings.ipx_curtain_channel)
```

## 26. Healthcheck endpoint

A simple internal health endpoint, **not exposed in Nginx**, that checks the IPX800 is reachable. Useful from the VPS shell when debugging.

`main.py`:

```python
@app.get("/_health")
async def health():
    try:
        await ipx.read_state()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": e.__class__.__name__}
```

In Nginx, **do not** add a `location /_health`. Reach it via `curl http://127.0.0.1:8000/_health` from the VPS only.

## 27. Error handling improvements

Replace the catch-all `except Exception` from Phase 2 with categorised handling:

```python
import httpx

async def handle(sender: str, text: str) -> None:
    res = parse(text)
    try:
        reply = await execute(res.intent)
    except httpx.TimeoutException:
        reply = "⚠ The relay didn't respond in time. Try again."
    except httpx.HTTPStatusError as e:
        reply = f"⚠ Relay refused the command (HTTP {e.response.status_code})."
    except httpx.RequestError:
        reply = "⚠ Can't reach the relay right now. The tunnel may be down."
    except Exception:
        log.exception("unhandled error")
        reply = "⚠ Internal error. The operator has been notified."
    await wa.send_text(sender, reply)
```

`execute(intent)` is a tiny dispatcher pulled out of the Phase 2 `if/elif` chain.

## 28. Localization (optional)

If FR / ZH commands are wanted for the demo audience, extend the parser keyword tables:

```python
_KEYWORDS = {
    Intent.LIGHT_ON:    {"lights on", "light on", "on",
                         "lumière on", "allume", "allumer la lumière",
                         "开灯", "灯打开"},
    Intent.LIGHT_OFF:   {"lights off", "light off", "off",
                         "lumière off", "éteins", "éteindre la lumière",
                         "关灯", "灯关闭"},
    # ... etc
}
```

Replies stay English for now (one source of truth). Localising replies is more work than localising input.

## 29. Operational tooling

### 29.1 Smoke-test script

`/opt/wahc/scripts/smoke.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
source /opt/wahc/app/.env
H=(-H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID"
   -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET")
echo "→ light ON ";  curl -fsS "${H[@]}" "$IPX_URL/api/xdevices.json?key=$IPX_API_KEY&SetR=$(printf %02d $IPX_LIGHT_RELAY)"  | head -c 200; echo
sleep 1
echo "→ light OFF"; curl -fsS "${H[@]}" "$IPX_URL/api/xdevices.json?key=$IPX_API_KEY&ClearR=$(printf %02d $IPX_LIGHT_RELAY)" | head -c 200; echo
echo "→ status";    curl -fsS "${H[@]}" "$IPX_URL/api/xdevices.json?key=$IPX_API_KEY&Get=R" | head -c 200; echo
```

`chmod +x` it. The operator runs `sudo -u wahc /opt/wahc/scripts/smoke.sh` to verify the lower layer before a demo.

### 29.2 Tail-logs alias

In `/etc/profile.d/wahc.sh`:

```bash
alias wahc-logs='journalctl -u wahc -f -o cat | jq'
alias wahc-status='systemctl status wahc'
```

## 30. Demo runbook

The deliverable that proves Phase 3 is done. Save as `docs/demo-runbook.md`.

### 30.1 The day before

- Confirm power and network at the demo site.
- Run `wahc-status` on the VPS — should show `active (running)`.
- Run `smoke.sh` on the VPS — all PASS.
- Send `status` from the demo phone — should get a clean reply within 3 s.

### 30.2 Cold-boot sequence (in this order)

- Power IPX800. Wait 30 s for it to acquire its DHCP/static IP.
- Power the LAN tunnel host (the Pi running `cloudflared`). Wait 30 s.
- On the VPS, `systemctl restart wahc` (optional, only if it's been running for weeks).
- From the demo phone, send `status`. If it replies in under 3 s, you are ready.

### 30.3 Demo script

- Greet the audience, hold up the phone so the screen is visible.
- Send `lights on` — they see the lights and the reply at the same time.
- Send `lights off` — same.
- Send `curtains down` — curtain begins closing.
- Send `curtains stop` mid-travel — curtain stops.
- Send `curtains up` — curtain opens fully.
- Send `status` — show the clean reply.

### 30.4 If something goes wrong

- **No reply at all on WhatsApp.** Check `wahc-status` on the VPS. If inactive, `systemctl restart wahc`. If active, run `smoke.sh` — if smoke fails, the issue is the tunnel or the IPX800 (see below).
- **Reply says "Can't reach the relay."** Tunnel is down. SSH to the LAN tunnel host, `systemctl status cloudflared`. Restart if needed.
- **Reply says "Relay refused."** Check the IPX800 web UI — has the API key changed? Has someone disabled the API?
- **Lights physically don't respond, but the reply says ✓.** The IPX800 received the command but the wiring or the relay has failed. This is a hardware issue, no software recovery.
- **Phone shows "message not delivered."** Meta is unreachable. Almost never fixable on the spot; rely on backup (a laptop with the IPX800 web UI bookmarked).

### 30.5 Backup channel

Laptop on the demo's wifi → `https://ipx.<your-domain>/` (login as admin) → manually drive relays from the IPX800's own dashboard.

## 31. Pre-demo checklist

A single-page checklist, separate from the runbook, to sign off the morning of the demo:

```
[ ] VPS reachable: ssh wahc-vps → ok
[ ] systemctl is-active wahc → active
[ ] smoke.sh → all PASS
[ ] From operator phone: send "status" → reply in <3 s
[ ] Lights on / off via WhatsApp → physical response
[ ] Curtains up / stop / down via WhatsApp → physical response
[ ] Cloudflare Access policy still in place (random visitor to ipx.<domain> blocked)
[ ] Battery on demo phone > 50%
[ ] Backup laptop charged, IPX800 dashboard bookmark works
```

## 32. Phase 3 deliverables and effort

**Deliverables.**

1. `wahc.service` deployed and running on the VPS.
2. JSON logging in production.
3. `format_status` integrated, `status` returns a clean human message.
4. Categorised error replies.
5. `smoke.sh` script and shell aliases installed.
6. `docs/demo-runbook.md` and `docs/pre-demo-checklist.md` written and reviewed.
7. (Optional) localized parser if FR / ZH commands are wanted.

**What is intentionally NOT in Phase 3.**

- Multi-room / multi-device support. The demo controls one set of lights and one curtain.
- Voice control. Out of scope for the demo.
- A UI dashboard. The IPX800's own UI is the backup.
- Encryption at rest for the dedup cache. It's in-memory, gone on restart, by design.
- Metrics / Prometheus / Grafana. `journalctl` is enough for a demo.
- High availability of the VPS. A single VPS is fine; if it dies, fall back to the backup laptop.

**Effort.**

| Step | Effort |
|---|---|
| §23 systemd | 30 min |
| §24 Structured logs | 1 h |
| §25 Pretty status | 1 h |
| §26 Healthcheck | 15 min |
| §27 Error handling | 1 h |
| §28 Localization (optional) | 1 h |
| §29 Tooling | 30 min |
| §30–31 Runbook + checklist | 2 h |

About half a day, plus the runbook writing.
