"""Test the packaged executable over real MCP stdio (no PS5 required)."""
import json
import subprocess
import sys

exe = sys.argv[1]
requests = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "bridge-smoke", "version": "1.0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
     "params": {"name": "chiaki_screen_status", "arguments": {}}},
]
wire = ("\n".join(json.dumps(r) for r in requests) + "\n").encode("utf-8")
result = subprocess.run([exe], input=wire, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, timeout=25, check=True)
responses = [json.loads(row) for row in result.stdout.decode("utf-8").splitlines()]
assert len(responses) == 3, responses
assert [r["id"] for r in responses] == [1, 2, 3]
assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
assert {t["name"] for t in responses[1]["result"]["tools"]} == {
    "chiaki_screen_status", "chiaki_capture_frame",
    "chiaki_press_button", "chiaki_disable_control"}
assert responses[2]["result"]["content"][0]["type"] == "text"
assert "ready" in json.loads(responses[2]["result"]["content"][0]["text"])
print("PASS: packaged MCP initialize/list/status via stdio; no PS5 capture required")
