"""Small, dependency-free command entry point for the bundled audit Hook.

The Hook runtime owns auditing and redaction.  This process deliberately does
not print the received payload; it only acknowledges the event using the
structured command protocol.
"""

from __future__ import annotations

import json
import sys


_MAX_INPUT_BYTES = 256 * 1024
_EVENTS = frozenset(
    {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    }
)


def _fail() -> int:
    sys.stderr.write("audit Hook input is invalid\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in _EVENTS:
        return _fail()

    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        return _fail()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail()
    if not isinstance(payload, dict):
        return _fail()

    output = {
        "continue": True,
        "hookSpecificOutput": {"hookEventName": args[0]},
    }
    sys.stdout.write(json.dumps(output, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
