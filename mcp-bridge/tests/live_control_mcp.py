"""One explicit, allowlisted PS5 controller input via the packaged MCP server.

Never runs automatically during builds or unit tests. Requires --button.
The executable is launched with --enable-control for one short-lived stdio
session; it exits immediately afterward, disabling all input.
"""
import argparse
import json
from pathlib import Path
import subprocess

EXE = Path(__file__).resolve().parents[1] / "dist" / "chiaki-screen-mcp.exe"
BUTTONS = ("ps", "ps_hold", "up", "down", "left", "right", "cross", "circle", "options",
           "move_forward", "move_backward", "move_left", "move_right")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--button", required=True, choices=BUTTONS)
    p.add_argument("--duration-ms", type=int)
    args = p.parse_args()
    params = {"button": args.button}
    if args.duration_ms is not None:
        params["duration_ms"] = args.duration_ms
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "chiaki-control-smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "chiaki_press_button", "arguments": params}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "chiaki_disable_control", "arguments": {}}},
    ]
    wire = ("\n".join(json.dumps(x) for x in requests) + "\n").encode()
    done = subprocess.run([str(EXE), "--enable-control"], input=wire,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=True, timeout=30)
    replies = [json.loads(row) for row in done.stdout.splitlines()]
    assert [x["id"] for x in replies] == [1, 2, 3], replies
    call = replies[1]["result"]
    if call.get("isError"):
        raise RuntimeError(call["content"][0]["text"])
    result = json.loads(call["content"][0]["text"])
    disabled = json.loads(replies[2]["result"]["content"][0]["text"])
    assert disabled["control_enabled"] is False
    print(json.dumps({"result": result, "session_disabled": True,
                      "note": "Check the PS5 screenshot for the actual effect."}))


if __name__ == "__main__":
    main()
