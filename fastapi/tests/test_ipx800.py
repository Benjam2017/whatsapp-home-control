import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx
from ipx800 import IPX800Controller, _parse_status_xml

MOCK_STATUS_XML = "<response><led0>1</led0><led1>0</led1><led2>0</led2></response>"

class TestParseStatusXml:
    def test_parses_relay_states(self):
        xml = "<response><led0>0</led0><led1>1</led1><led2>0</led2></response>"
        r = _parse_status_xml(xml)
        assert r["led0"] == "0"
        assert r["led1"] == "1"

    def test_raises_on_invalid_xml(self):
        with pytest.raises(ValueError, match="Failed to parse"):
            _parse_status_xml("not xml <<<")

    def test_0_based_index(self):
        r = _parse_status_xml("<response><led0>1</led0></response>")
        assert "led0" in r

@pytest.fixture
def controller():
    return IPX800Controller()

def make_mock_resp(code=200, text="OK"):
    m = MagicMock()
    m.status_code = code
    m.text = text
    m.raise_for_status = MagicMock()
    if code >= 400:
        m.raise_for_status.side_effect = httpx.HTTPStatusError("E", request=MagicMock(), response=m)
    return m

class TestSetRelay:
    @pytest.mark.asyncio
    async def test_relay_on(self, controller):
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = AsyncMock(return_value=make_mock_resp())
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            await controller._set_relay(1, 1)
            assert inst.get.call_args[1]["params"].get("led1") == 1

    @pytest.mark.asyncio
    async def test_relay_off(self, controller):
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = AsyncMock(return_value=make_mock_resp())
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            await controller._set_relay(1, 0)
            assert inst.get.call_args[1]["params"].get("led1") == 0

    @pytest.mark.asyncio
    async def test_retries_then_raises(self, controller):
        controller.retries = 2
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ConnectionError, match="unreachable"):
                    await controller._set_relay(1, 1)
            assert inst.get.call_count == 2

class TestExecute:
    @pytest.mark.asyncio
    async def test_curtain_interlock_order(self, controller):
        """CRITICAL SAFETY: DOWN=OFF must fire before UP=ON."""
        call_order = []
        async def mock_get(url, **kw):
            call_order.append(dict(kw.get("params", {})))
            return make_mock_resp()
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = mock_get
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await controller.execute([{"relay": 3, "state": 0}, {"relay": 2, "state": 1}])
        assert call_order[0].get("led3") == 0, "DOWN must be OFF first"
        assert call_order[1].get("led2") == 1, "UP must be ON second"

class TestPing:
    @pytest.mark.asyncio
    async def test_ping_true(self, controller):
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = AsyncMock(return_value=make_mock_resp(200, MOCK_STATUS_XML))
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            assert await controller.ping() is True

    @pytest.mark.asyncio
    async def test_ping_false(self, controller):
        controller.retries = 1
        with patch("httpx.AsyncClient") as M:
            inst = AsyncMock()
            inst.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            M.return_value.__aenter__ = AsyncMock(return_value=inst)
            M.return_value.__aexit__  = AsyncMock(return_value=False)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                assert await controller.ping() is False
