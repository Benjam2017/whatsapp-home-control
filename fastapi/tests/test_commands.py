# tests/test_commands.py — Unit tests for command parser

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from commands import parse_command


# ─────────────────────────────────────────────────────────────────
# LIGHT commands
# ─────────────────────────────────────────────────────────────────
class TestLightCommands:
    def test_light_on_english(self):
        action, ops = parse_command("light on")
        assert action == "LIGHT_ON"
        assert len(ops) == 1
        assert ops[0]["relay"] == 1
        assert ops[0]["state"] == 1

    def test_light_off_english(self):
        action, ops = parse_command("light off")
        assert action == "LIGHT_OFF"
        assert ops[0]["relay"] == 1
        assert ops[0]["state"] == 0

    def test_light_on_french(self):
        action, ops = parse_command("allume")
        assert action == "LIGHT_ON"

    def test_light_off_french(self):
        action, ops = parse_command("off")
        assert action == "LIGHT_OFF"

    def test_light_shortcut(self):
        action, _ = parse_command("1 on")
        assert action == "LIGHT_ON"

    def test_light_on_uppercase_stripped(self):
        action, _ = parse_command("  Light On  ".strip().lower())
        assert action == "LIGHT_ON"


# ─────────────────────────────────────────────────────────────────
# CURTAIN commands
# ─────────────────────────────────────────────────────────────────
class TestCurtainCommands:
    def test_curtain_up_english(self):
        action, ops = parse_command("curtain up")
        assert action == "CURTAIN_UP"
        # Must have 2 ops: DOWN relay OFF first, then UP relay ON
        assert len(ops) == 2
        assert ops[0]["relay"] == 3  # RELAY_CURTAIN_DOWN
        assert ops[0]["state"] == 0  # OFF first (interlock)
        assert ops[1]["relay"] == 2  # RELAY_CURTAIN_UP
        assert ops[1]["state"] == 1  # then ON

    def test_curtain_down_english(self):
        action, ops = parse_command("curtain down")
        assert action == "CURTAIN_DOWN"
        assert len(ops) == 2
        assert ops[0]["relay"] == 2  # RELAY_CURTAIN_UP
        assert ops[0]["state"] == 0  # OFF first (interlock)
        assert ops[1]["relay"] == 3  # RELAY_CURTAIN_DOWN
        assert ops[1]["state"] == 1  # then ON

    def test_curtain_stop_english(self):
        action, ops = parse_command("curtain stop")
        assert action == "CURTAIN_STOP"
        assert len(ops) == 2
        assert all(op["state"] == 0 for op in ops)  # both OFF

    def test_curtain_up_french(self):
        action, _ = parse_command("monte")
        assert action == "CURTAIN_UP"

    def test_curtain_down_french(self):
        action, _ = parse_command("descend")
        assert action == "CURTAIN_DOWN"

    def test_curtain_stop_french(self):
        action, _ = parse_command("stop")
        assert action == "CURTAIN_STOP"

    def test_curtain_shortcut(self):
        action, _ = parse_command("2 up")
        assert action == "CURTAIN_UP"

    def test_curtain_interlock_never_both_on(self):
        """CRITICAL: UP and DOWN ops must never both be state=1 simultaneously."""
        for cmd in ["curtain up", "curtain down"]:
            _, ops = parse_command(cmd)
            on_count = sum(1 for op in ops if op["state"] == 1)
            assert on_count == 1, f"Only 1 relay should be ON for command '{cmd}', got {on_count}"


# ─────────────────────────────────────────────────────────────────
# STATUS and HELP
# ─────────────────────────────────────────────────────────────────
class TestMetaCommands:
    def test_status_english(self):
        action, ops = parse_command("status")
        assert action == "STATUS"
        assert ops == []

    def test_status_shortcut(self):
        action, _ = parse_command("s")
        assert action == "STATUS"

    def test_status_question_mark(self):
        action, _ = parse_command("?")
        assert action == "STATUS"

    def test_help_english(self):
        action, ops = parse_command("help")
        assert action == "HELP"
        assert ops == []

    def test_help_shortcut(self):
        action, _ = parse_command("h")
        assert action == "HELP"


# ─────────────────────────────────────────────────────────────────
# UNKNOWN commands
# ─────────────────────────────────────────────────────────────────
class TestUnknownCommands:
    def test_garbage_input(self):
        action, ops = parse_command("hello world")
        assert action == "UNKNOWN"
        assert ops == []

    def test_empty_string(self):
        action, ops = parse_command("")
        assert action == "UNKNOWN"
        assert ops == []

    def test_partial_command(self):
        action, _ = parse_command("curtain")
        assert action == "UNKNOWN"

    def test_typo(self):
        action, _ = parse_command("ligth on")
        assert action == "UNKNOWN"
