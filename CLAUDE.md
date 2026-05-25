# CLAUDE.md

Guidance for Claude when working in this repository. This file describes **what is actually here**, the conventions to follow, and the known divergences from the design documentation.

---

## 1. What this project does

A WhatsApp message ("light on", "curtain up", "status", etc.) flips a relay on a **GCE Electronics IPX800 V4** controller, and the device's physical switches push state changes back to WhatsApp.

The deployment target is a Linux VPS with a public IP and TLS-terminated domain. The IPX800 lives on a remote LAN, reachable from the VPS via DDNS + port-forward.

---

## 2. Architecture as built

Two services, both running on the VPS, separated by responsibility:

```
WhatsApp user  ──► Meta Cloud API ──► webhook/ (Node.js, HTTPS :443)
                                       │
                                       │ HTTP POST /control
                                       ▼
                                     fastapi/ (Python, HTTP :8000, loopback only)
                                       │
                                       │ HTTP GET /preset.htm, /status.xml
                                       ▼
                                     IPX800 V4 (on LAN, reached via DDNS:8080)

IPX800 physical switch ─► IPX800 Scenario push ─► webhook/ GET /ipx-notify
                                                   │
                                                   ▼
                                                 Meta Cloud API ─► WhatsApp user
```

**Why two services and not one:** the Node side owns everything WhatsApp-shaped (HMAC verification, whitelist, dedup, Meta API replies, IPX800 push notifications). The Python side owns everything device-shaped (command parsing, relay sequencing, interlock, status XML parsing). Each can be replaced without touching the other.

The boundary is `POST /control` and `GET /health` on the FastAPI side — see `fastapi/main.py`.

---

## 3. Directory layout

```
whatsapp-home-control/
├── CLAUDE.md                  ← this file
├── README.md                  ← VPS setup, env vars, systemd units
├── docs/
│   └── documentation.md       ← original design doc (see §8: divergence)
├── whatsapp_iot_requirements_v2_en_1.docx   ← original requirements
│
├── webhook/                   ← Node.js — public-facing webhook
│   ├── app.js                 ← Express server, /webhook GET+POST, /ipx-notify, /health
│   ├── security.js            ← HMAC verify, whitelist, dedup
│   ├── whatsapp.js            ← Meta Graph API sender
│   ├── state.js               ← in-memory device state cache
│   ├── logger.js              ← console + file logger
│   ├── package.json
│   ├── .env / .env.example
│   ├── logs/
│   └── tests/
│       ├── test_security.js   ← 12 tests
│       └── test_state.js      ← 11 tests
│
└── fastapi/                   ← Python — IPX800 control
    ├── main.py                ← FastAPI app, /control POST, /health GET
    ├── commands.py            ← parse_command(): text → (action, relay_ops)
    ├── ipx800.py              ← IPX800Controller: execute, get_status_report, ping
    ├── config.py              ← pydantic-settings, loads .env
    ├── logger.py              ← rotating file + console logger
    ├── requirements.txt
    ├── .env / .env.example
    ├── logs/
    └── tests/
        ├── test_commands.py   ← 19 tests
        ├── test_ipx800.py     ← 12 tests
        └── test_api.py        ← 14 tests
```

---

## 4. How the IPX800 is actually controlled

This is the most surprising part of the codebase if you've only read `docs/documentation.md`. The doc described `/api/xdevices.json?SetR=NN`. The code uses **a different, older endpoint** that is also valid on the IPX800 V4:

- **Set a relay:** `GET http://<host>:8080/preset.htm?ledN=0|1&apikey=<key>` (relay numbers are 1-based: `led1`, `led2`, `led3`)
- **Read all states:** `GET http://<host>:8080/status.xml?apikey=<key>` — returns XML with `<led0>`, `<led1>`, ... (note: status.xml is 0-indexed, while preset.htm is 1-indexed; the code handles this by subtracting 1 in `get_status_report`).

If you change the endpoint, do it in **one place**: `fastapi/ipx800.py`. Don't propagate the URL string into `commands.py` or `main.py`.

### Curtain interlock (important)

There is no X-4VR extension. Curtains are driven by **two raw relays** (UP = relay 2, DOWN = relay 3). Energising both at once would short the motor windings. The code enforces a **software interlock**:

- In `fastapi/commands.py`, every curtain action generates a *list* of relay ops where the opposite-direction relay is cleared *before* the target relay is set.
- In `fastapi/ipx800.py` → `IPX800Controller.execute()`, ops are applied sequentially with a `200 ms` gap (`INTERLOCK_DELAY = 0.2`).

**Do not change this without good reason.** If you ever switch to an X-4VR extension, this interlock becomes redundant and the curtain ops can collapse to a single call — but until then, leave it.

---

## 5. Commands and replies

Defined in `fastapi/commands.py`. The parser is exact-match on lowercased, trimmed text — no fuzzy matching, no NLP. Both English and French aliases are supported.

| Intent | Recognized phrases (lowercase, exact match) | Effect |
|---|---|---|
| `LIGHT_ON` | `light on`, `on`, `allume`, `lumière on`, `lumiere on`, `1 on` | relay 1 → 1 |
| `LIGHT_OFF` | `light off`, `off`, `éteins`, `lumière off`, `lumiere off`, `1 off` | relay 1 → 0 |
| `CURTAIN_UP` | `curtain up`, `up`, `monte`, `volet monte`, `ouvre`, `open curtain`, `2 up` | relay 3 → 0, then relay 2 → 1 |
| `CURTAIN_DOWN` | `curtain down`, `down`, `descend`, `volet descend`, `ferme`, `close curtain`, `2 down` | relay 2 → 0, then relay 3 → 1 |
| `CURTAIN_STOP` | `curtain stop`, `stop`, `arrête`, `stoppe`, `stop curtain`, `2 stop` | relay 2 → 0, relay 3 → 0 |
| `STATUS` | `status`, `état`, `etat`, `s`, `?` | fetch status.xml, format report |
| `HELP` | `help`, `aide`, `h`, `commands`, `commandes` | return help text |

To add a new phrase: edit the `COMMAND_MAP` dict in `commands.py` and add a test case to `tests/test_commands.py`.

---

## 6. Configuration

Two `.env` files, one per service. Both are listed in `.gitignore` (or should be — if they're not, add them). The `.env.example` files are the authoritative list of every variable; if you add a new setting, update the example.

### `webhook/.env`
- TLS cert paths (Let's Encrypt)
- Meta tokens (`WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_VERIFY_TOKEN`, `WA_APP_SECRET`)
- `ALLOWED_NUMBERS` — E.164 without `+`, comma-separated
- Internal pointer to FastAPI (`FASTAPI_HOST=127.0.0.1`, `FASTAPI_PORT=8000`)

### `fastapi/.env`
- `IPX800_HOST` / `IPX800_PORT` / `IPX800_APIKEY`
- Relay mapping (`RELAY_LIGHT=1`, `RELAY_CURTAIN_UP=2`, `RELAY_CURTAIN_DOWN=3`)
- Log rotation settings

**Never commit a real `.env`.** Both files should be `chmod 600` on the VPS.

---

## 7. Working with the codebase

### Running locally

Python:
```bash
cd fastapi
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # then edit
python -m pytest tests/ -v --asyncio-mode=auto
uvicorn main:app --host 127.0.0.1 --port 8000 --reload  # dev mode
```

Node:
```bash
cd webhook
npm install
cp .env.example .env  # then edit
node tests/test_security.js
node tests/test_state.js
# For local dev set USE_SSL=false in .env, otherwise it expects real certs
node app.js
```

### Running tests
The README claims 68 tests total. Always run both sides after changes; the contract between them lives in `webhook/app.js::callFastAPI` ↔ `fastapi/main.py::control`.

### Conventions to follow

- **Python**: 4-space indent, type hints on public functions, `async def` for anything that does I/O. Loggers obtained via `from logger import get_logger; logger = get_logger(__name__)` — do **not** create new `logging.Formatter` instances ad-hoc.
- **JavaScript**: CommonJS (`require`/`module.exports`), 2-space indent, the existing files use a 4-column alignment style on multi-line object literals — preserve it when editing those files.
- **No silent failures.** Both services log warnings on every recoverable failure (signature mismatch, IPX800 retry, unknown command). Don't add `try { ... } catch { /* ignore */ }`.
- **Replies use emojis** as in the existing tables. Keep the prefix style: `✅ ⭕ ⬆️ ⬇️ ⏹️ 📊 🏠 💡 🪟 ❌`.

### What NOT to do without checking

- Don't switch the IPX800 endpoint to `/api/xdevices.json` without confirming the device firmware supports it *and* updating `ipx800.py`, `test_ipx800.py`, and `docs/documentation.md`. The current `/preset.htm` + `/status.xml` pair is older but verified working on this hardware.
- Don't remove the interlock delay or the opposite-relay-off ops from curtain actions.
- Don't merge the two services. The split is intentional (see §2).
- Don't add LLM-based command parsing. The keyword map is the right tool for a fixed vocabulary; an LLM would add latency, cost, and a new failure mode for no benefit.

---

## 8. Divergence from `docs/documentation.md`

`docs/documentation.md` was written as a forward-looking design before the code was built. The implementation diverged on several points. **The code is the source of truth.** Treat the doc as historical context, not as a spec.

| Topic | Doc says | Code does |
|---|---|---|
| LAN-to-VPS link | Cloudflare Tunnel from LAN to VPS | DDNS + port-forward on the LAN side; IPX800 reached at `http://<ddns>:8080` |
| Webhook framework | FastAPI handles the Meta webhook directly | Node.js handles Meta; FastAPI is internal |
| IPX800 endpoint | `/api/xdevices.json?SetR=NN&ClearR=NN` | `/preset.htm?ledN=0\|1` and `/status.xml` |
| Curtain hardware | X-4VR extension (firmware interlock) | Two raw relays with software interlock |
| Languages | English only, FR/ZH "optional Phase 3" | EN + FR already implemented |
| Push from device → user | Not mentioned | Implemented at `GET /ipx-notify` in `webhook/app.js` |

If you reconcile the doc with the code later, update `docs/documentation.md` to match the code — not the other way around.

---

## 9. Deployment notes

The README has the canonical setup. Two systemd units:
- `home-fastapi.service` — runs `uvicorn main:app --host 127.0.0.1 --port 8000`
- `home-webhook.service` — runs `node app.js` (HTTPS :443), depends on `home-fastapi.service`

Both use `Restart=always`. Logs go to each service's `logs/` directory **and** to journald via stdout (the Python logger has both handlers; the Node logger writes to file and `console.log`).

If you change a port or path, update **all three**: the `.env`, the systemd unit, and any reverse-proxy config (if a future operator puts Nginx in front of Node).

---

## 10. Open items

- The `.env` files currently in the repo: confirm they don't contain real secrets before any commit. If they do, rotate and move to a secret manager.
- No CI yet. The test suite exists but nothing runs it automatically.
- No structured (JSON) logs. Both loggers produce human-readable lines, which is fine for a single-VPS demo but should be revisited if this grows.
- `state.js` is in-memory only. After a Node restart, `getStatusReport` returns `UNKNOWN` until the next `/ipx-notify` push or a `status` command (which proxies through FastAPI to the IPX800 anyway).
