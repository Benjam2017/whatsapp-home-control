from typing import Tuple, List, Dict, Any
from config import settings

COMMAND_MAP: Dict[str, List[str]] = {
    "LIGHT_ON":     ["light on",  "on",  "allume", "lumière on",  "lumiere on",  "1 on"],
    "LIGHT_OFF":    ["light off", "off", "éteins", "lumière off", "lumiere off", "1 off"],
    "CURTAIN_UP":   ["curtain up",   "up",   "monte",  "volet monte",  "ouvre", "open curtain",  "2 up"],
    "CURTAIN_DOWN": ["curtain down", "down", "descend","volet descend","ferme", "close curtain", "2 down"],
    "CURTAIN_STOP": ["curtain stop", "stop", "arrête", "stoppe",       "stop curtain",           "2 stop"],
    "STATUS":       ["status", "état", "etat", "s", "?"],
    "HELP":         ["help",   "aide", "h", "commands", "commandes"],
}

RELAY_OPS: Dict[str, List[Dict[str, int]]] = {
    "LIGHT_ON":     [{"relay": settings.RELAY_LIGHT,        "state": 1}],
    "LIGHT_OFF":    [{"relay": settings.RELAY_LIGHT,        "state": 0}],
    "CURTAIN_UP":   [{"relay": settings.RELAY_CURTAIN_DOWN, "state": 0},
                     {"relay": settings.RELAY_CURTAIN_UP,   "state": 1}],
    "CURTAIN_DOWN": [{"relay": settings.RELAY_CURTAIN_UP,   "state": 0},
                     {"relay": settings.RELAY_CURTAIN_DOWN, "state": 1}],
    "CURTAIN_STOP": [{"relay": settings.RELAY_CURTAIN_UP,   "state": 0},
                     {"relay": settings.RELAY_CURTAIN_DOWN, "state": 0}],
}


def parse_command(text: str) -> Tuple[str, List[Dict[str, int]]]:
    """Parse a free-text command into (action, relay_ops).

    Returns ("UNKNOWN", []) for any input that doesn't exactly match a known phrase
    (after .strip().lower()).
    """
    normalized = text.strip().lower()
    for action, keywords in COMMAND_MAP.items():
        if normalized in keywords:
            ops = RELAY_OPS.get(action, [])
            return action, ops
    return "UNKNOWN", []
