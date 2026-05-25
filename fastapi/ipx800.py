import asyncio
import xml.etree.ElementTree as ET
import httpx
from config import settings
from logger import get_logger

logger = get_logger(__name__)
INTERLOCK_DELAY = 0.2  # 200ms safety gap for curtain motor

class IPX800Controller:
    def __init__(self):
        self.base_url = f"http://{settings.IPX800_HOST}:{settings.IPX800_PORT}"
        self.apikey   = settings.IPX800_APIKEY
        self.timeout  = settings.IPX800_TIMEOUT
        self.retries  = settings.IPX800_RETRY

    async def execute(self, relay_ops: list) -> None:
        for i, op in enumerate(relay_ops):
            if i > 0:
                await asyncio.sleep(INTERLOCK_DELAY)
            await self._set_relay(op["relay"], op["state"])

    async def get_status_report(self) -> str:
        states = await self._fetch_status()
        relay_names = {
            settings.RELAY_LIGHT:        "💡 Light",
            settings.RELAY_CURTAIN_UP:   "🪟 Curtain UP relay",
            settings.RELAY_CURTAIN_DOWN: "🪟 Curtain DOWN relay",
        }
        lines = []
        for relay_num, name in relay_names.items():
            idx    = relay_num - 1  # status.xml is 0-based
            state  = states.get(f"led{idx}", "?")
            status = "ON" if state == "1" else "OFF"
            lines.append(f"{name}: {status}")
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")
        return f"📊 Home Status — {now}\n\n" + "\n".join(lines)

    async def ping(self) -> bool:
        try:
            await self._fetch_status()
            return True
        except Exception:
            return False

    async def _set_relay(self, relay_num: int, state: int) -> None:
        url    = f"{self.base_url}/preset.htm"
        params = {f"led{relay_num}": state, "apikey": self.apikey}
        for attempt in range(1, self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                logger.info(f"Relay {relay_num} set to {state} (attempt {attempt})")
                return
            except Exception as e:
                logger.warning(f"Relay {relay_num} attempt {attempt}/{self.retries} failed: {e}")
                if attempt < self.retries:
                    await asyncio.sleep(10)
        raise ConnectionError(
            f"IPX800 unreachable after {self.retries} attempts. "
            f"Check DDNS ({settings.IPX800_HOST}:{settings.IPX800_PORT}) and port forwarding."
        )

    async def _fetch_status(self) -> dict:
        url    = f"{self.base_url}/status.xml"
        params = {"apikey": self.apikey}
        for attempt in range(1, self.retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                return _parse_status_xml(resp.text)
            except Exception as e:
                logger.warning(f"Status fetch attempt {attempt}/{self.retries} failed: {e}")
                if attempt < self.retries:
                    await asyncio.sleep(10)
        raise ConnectionError(f"Cannot fetch status from IPX800 after {self.retries} attempts")


def _parse_status_xml(xml_text: str) -> dict:
    """Parse /status.xml. Returns {led0: '0', led1: '1', ...} (0-based index)."""
    try:
        root = ET.fromstring(xml_text)
        return {child.tag: child.text for child in root}
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse status.xml: {e}")
