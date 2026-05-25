# main.py — WhatsApp Home Control: Python FastAPI Service
#
# Responsibilities:
#   - Receive control commands from Node.js webhook service
#   - Parse and route commands to IPX800 relay controller
#   - Return structured reply text back to Node.js

from fastapi import FastAPI
from pydantic import BaseModel, Field
import uvicorn
import os

from config   import settings
from commands import parse_command
from ipx800   import IPX800Controller
from logger   import get_logger

logger = get_logger(__name__)
app    = FastAPI(title="WhatsApp Home Control — FastAPI", version="1.0.0")
ipx    = IPX800Controller()


# ─────────────────────────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────────────────────────
class ControlRequest(BaseModel):
    command: str
    # Accept either {"from": "..."} (from Node webhook) or {"from_number": "..."}.
    # Pydantic populates `from_number` from either key via alias + populate_by_name.
    from_number: str = Field(default="", alias="from")

    model_config = {"populate_by_name": True}


class ControlResponse(BaseModel):
    reply:   str
    success: bool
    action:  str = ""


# ─────────────────────────────────────────────────────────────────
# POST /control — receive command from Node.js, execute on IPX800
# ─────────────────────────────────────────────────────────────────
@app.post("/control", response_model=ControlResponse)
async def control(req: ControlRequest):
    logger.info(f"Command received from {req.from_number or '<unknown>'}: '{req.command}'")

    action, relay_ops = parse_command(req.command)

    if action == "UNKNOWN":
        return ControlResponse(
            reply   = "❓ Unknown command.\nSend *help* to see available commands.",
            success = False,
            action  = "UNKNOWN",
        )

    if action == "HELP":
        return ControlResponse(
            reply   = build_help_text(),
            success = True,
            action  = "HELP",
        )

    if action == "STATUS":
        status_text = await ipx.get_status_report()
        return ControlResponse(reply=status_text, success=True, action="STATUS")

    # Execute relay operation(s)
    try:
        await ipx.execute(relay_ops)
        reply = build_reply(action)
        logger.info(f"Action '{action}' executed successfully")
        return ControlResponse(reply=reply, success=True, action=action)

    except Exception as e:
        logger.error(f"IPX800 error for action '{action}': {e}")
        return ControlResponse(
            reply   = f"❌ Control failed: {str(e)}\nPlease check the device connection.",
            success = False,
            action  = action,
        )


# ─────────────────────────────────────────────────────────────────
# GET /health — service + IPX800 connectivity check
# ─────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    ipx_ok = await ipx.ping()
    return {
        "status":  "ok" if ipx_ok else "degraded",
        "service": "fastapi-control",
        "ipx800":  "reachable" if ipx_ok else "unreachable",
        "ipx800_host": settings.IPX800_HOST,
        "ipx800_port": settings.IPX800_PORT,
    }


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────
def build_reply(action: str) -> str:
    replies = {
        "LIGHT_ON":      "✅ Light turned ON",
        "LIGHT_OFF":     "⭕ Light turned OFF",
        "CURTAIN_UP":    "⬆️  Curtain moving UP",
        "CURTAIN_DOWN":  "⬇️  Curtain moving DOWN",
        "CURTAIN_STOP":  "⏹️  Curtain stopped",
    }
    return replies.get(action, f"✅ Action '{action}' executed")


def build_help_text() -> str:
    return (
        "🏠 *Home Control Commands*\n\n"
        "💡 *Light*\n"
        "  `light on`  — turn light ON\n"
        "  `light off` — turn light OFF\n\n"
        "🪟 *Curtain*\n"
        "  `curtain up`   — move curtain UP\n"
        "  `curtain down` — move curtain DOWN\n"
        "  `curtain stop` — stop curtain\n\n"
        "📊 *Other*\n"
        "  `status` — show current device states\n"
        "  `help`   — show this message"
    )


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host    = "127.0.0.1",
        port    = int(os.getenv("FASTAPI_PORT", "8000")),
        reload  = False,
        log_level = "info",
    )
