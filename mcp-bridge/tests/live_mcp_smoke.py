"""Optional live end-to-end MCP image test. Requires a visible Chiaki-ng stream.

No screenshots written to disk. --image-json emits one JSON object that
other local tools can forward as image data. Default mode reports metadata only.
"""
import argparse
import base64
import json
import subprocess
from pathlib import Path

EXE = Path(__file__).resolve().parents[1] / "dist" / "chiaki-screen-mcp.exe"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--image-json", action="store_true",
                        help="Emit metadata and base64 PNG for a trusted MCP image consumer")
    args = parser.parse_args()
    if not 320 <= args.width <= 1920 or not 240 <= args.height <= 1080:
        parser.error("Dimensions out of supported range")

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "chiaki-live-smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "chiaki_capture_frame", "arguments": {
             "max_width": args.width, "max_height": args.height}}},
    ]
    encoded_requests = ("\n".join(json.dumps(r) for r in messages) + "\n").encode()
    result = subprocess.run([str(EXE)], input=encoded_requests,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=True, timeout=35)
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(responses) == 2 and responses[0]["id"] == 1
    assert responses[1]["id"] == 2
    capture = responses[1]["result"]
    if capture.get("isError"):
        raise RuntimeError(capture["content"][0]["text"])
    entries = capture["content"]
    assert len(entries) == 2 and entries[1]["mimeType"] == "image/png"
    png = base64.b64decode(entries[1]["data"], validate=True)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    metadata = json.loads(entries[0]["text"])
    assert metadata["width"] <= args.width and metadata["height"] <= args.height
    if args.image_json:
        print(json.dumps({"metadata": metadata, "base64": entries[1]["data"]}))
    else:
        print(json.dumps({"status": "PASS", "source": "live packaged MCP tools/call",
                          "metadata": metadata, "png_bytes": len(png)}))


if __name__ == "__main__":
    main()
