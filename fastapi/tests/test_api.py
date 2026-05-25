import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from main import app, ControlRequest

client = TestClient(app)

def mock_ok():
    m = AsyncMock()
    m.execute           = AsyncMock(return_value=None)
    m.get_status_report = AsyncMock(return_value="📊 Home Status — 14:00:00\n\n💡 Light: ON\n🪟 Curtain UP relay: OFF")
    m.ping              = AsyncMock(return_value=True)
    return m

def mock_fail():
    m = AsyncMock()
    m.execute = AsyncMock(side_effect=ConnectionError("IPX800 unreachable"))
    m.ping    = AsyncMock(return_value=False)
    return m

class TestControl:
    def test_light_on(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "light on"})
        assert r.status_code == 200
        d = r.json()
        assert d["success"] and d["action"] == "LIGHT_ON" and "ON" in d["reply"]

    def test_light_off(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "light off"})
        assert r.json()["action"] == "LIGHT_OFF"

    def test_curtain_up(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "curtain up"})
        assert r.json()["action"] == "CURTAIN_UP"

    def test_curtain_down(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "curtain down"})
        assert r.json()["action"] == "CURTAIN_DOWN"

    def test_curtain_stop(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "curtain stop"})
        assert r.json()["action"] == "CURTAIN_STOP"

    def test_status(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "status"})
        d = r.json()
        assert d["success"] and d["action"] == "STATUS" and "Light" in d["reply"]

    def test_help(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "help"})
        d = r.json()
        assert d["success"] and d["action"] == "HELP"
        assert "light on" in d["reply"] and "curtain up" in d["reply"]

    def test_unknown(self):
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "make coffee"})
        d = r.json()
        assert not d["success"] and d["action"] == "UNKNOWN"

    def test_ipx_failure(self):
        with patch("main.ipx", mock_fail()):
            r = client.post("/control", json={"command": "light on"})
        d = r.json()
        assert not d["success"] and "❌" in d["reply"]

class TestHealth:
    def test_ok(self):
        with patch("main.ipx", mock_ok()):
            r = client.get("/health")
        d = r.json()
        assert d["status"] == "ok" and d["ipx800"] == "reachable"

    def test_degraded(self):
        with patch("main.ipx", mock_fail()):
            r = client.get("/health")
        d = r.json()
        assert d["status"] == "degraded" and d["ipx800"] == "unreachable"

class TestValidation:
    def test_missing_command(self):
        assert client.post("/control", json={}).status_code == 422

    def test_from_number_optional(self):
        with patch("main.ipx", mock_ok()):
            assert client.post("/control", json={"command": "light on"}).status_code == 200


class TestSenderContract:
    """ControlRequest must accept both 'from' (Node webhook) and 'from_number' (direct)."""

    def test_accepts_node_style_from_field(self):
        """The Node webhook sends {command, from}. FastAPI must accept this."""
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "light on", "from": "33612345678"})
        assert r.status_code == 200
        assert r.json()["action"] == "LIGHT_ON"

    def test_accepts_direct_style_from_number_field(self):
        """Direct API callers may send {command, from_number}. FastAPI must accept this too."""
        with patch("main.ipx", mock_ok()):
            r = client.post("/control", json={"command": "light on", "from_number": "33612345678"})
        assert r.status_code == 200
        assert r.json()["action"] == "LIGHT_ON"

    def test_model_populates_from_either_key(self):
        """Direct model instantiation: both 'from' and 'from_number' should set the same field."""
        a = ControlRequest(**{"command": "light on", "from": "111"})
        b = ControlRequest(command="light on", from_number="222")
        assert a.from_number == "111"
        assert b.from_number == "222"
