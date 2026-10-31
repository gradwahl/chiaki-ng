"""Protocol, PNG and privacy-policy tests. No PS5 or Chiaki connection required."""
import base64
import io
import json
import struct
import sys
import unittest
from pathlib import Path
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chiaki_screen_mcp as mod


class FakeCapture:
    def __init__(self):
        self.calls = 0
        self.frame = mod.encode_png_bgrx(bytes([3, 2, 1, 0]) * 4, 2, 2)

    def status(self):
        return {"ready": True, "chiaki_windows": [{"title": "Chiaki", "focused": True}]}

    def capture(self, max_width, max_height):
        self.calls += 1
        return self.frame, 2, 2, "Chiaki"


def rpc(method, params=None, mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


class PNGTests(unittest.TestCase):
    def test_valid_png_and_bgr_conversion(self):
        png = mod.encode_png_bgrx(bytes([3, 2, 1, 255]) * 2, 2, 1)
        self.assertTrue(png.startswith(mod.PNG_MAGIC))
        pos = len(mod.PNG_MAGIC)
        chunks = {}
        while pos < len(png):
            length = struct.unpack(">I", png[pos:pos + 4])[0]
            name = png[pos + 4:pos + 8]
            payload = png[pos + 8:pos + 8 + length]
            crc = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])[0]
            self.assertEqual(crc, zlib.crc32(name + payload) & 0xffffffff)
            chunks[name] = payload
            pos += length + 12
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (2, 1))
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), b"\x00\x01\x02\x03\x01\x02\x03")
        self.assertIn(b"IEND", chunks)

    def test_invalid_dimensions(self):
        with self.assertRaises(ValueError):
            mod.encode_png_bgrx(b"", 2000, 1)
        with self.assertRaises(ValueError):
            mod.encode_png_bgrx(b"\x00" * 4, 2, 1)


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCapture()
        self.server = mod.MCPBridge(self.fake)

    def test_initialize(self):
        result = self.server.dispatch(rpc("initialize", {
            "protocolVersion": "2025-06-18", "clientInfo": {"name": "test", "version": "0"},
            "capabilities": {}}, 7))
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(result["result"]["capabilities"]["tools"]["listChanged"], False)

    def test_list_and_status(self):
        tools = self.server.dispatch(rpc("tools/list"))["result"]["tools"]
        self.assertEqual({x["name"] for x in tools},
                         {"chiaki_screen_status", "chiaki_capture_frame",
                          "chiaki_press_button", "chiaki_disable_control"})
        assert all(x["annotations"]["readOnlyHint"] for x in tools[:2])
        assert all(not x["annotations"]["readOnlyHint"] for x in tools[2:])
        response = self.server.dispatch(rpc("tools/call", {
            "name": "chiaki_screen_status", "arguments": {}}))
        self.assertEqual(self.fake.calls, 0)
        self.assertTrue(json.loads(response["result"]["content"][0]["text"])["ready"])

    def test_png_tool_result(self):
        response = self.server.dispatch(rpc("tools/call", {
            "name": "chiaki_capture_frame",
            "arguments": {"max_width": 640, "max_height": 480}}))
        content = response["result"]["content"]
        self.assertEqual(content[1]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(content[1]["data"]), self.fake.frame)
        self.assertEqual(json.loads(content[0]["text"])["source"],
                         "visible Chiaki-ng client area (foreground or per-window background)")
        self.assertEqual(self.fake.calls, 1)

    def test_privacy_and_rate_limit(self):
        for bad in ({"max_width": True}, {"max_width": 100},
                    {"max_height": 5000}, {"window": "Notepad"},
                    {"max_width": 640.0}):
            response = self.server.dispatch(rpc("tools/call", {
                "name": "chiaki_capture_frame", "arguments": bad}))
            self.assertTrue(response["result"]["isError"])
        self.assertEqual(self.fake.calls, 0)
        self.server.dispatch(rpc("tools/call", {"name": "chiaki_capture_frame"}))
        response = self.server.dispatch(rpc("tools/call", {"name": "chiaki_capture_frame"}))
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(self.fake.calls, 1)

    def test_errors_and_notifications(self):
        self.assertIsNone(self.server.dispatch({"jsonrpc": "2.0",
                                                 "method": "notifications/initialized"}))
        self.assertEqual(self.server.dispatch(rpc("fake"))["error"]["code"], -32601)
        self.assertEqual(self.server.dispatch(rpc("tools/call", {
            "name": "other"}))["error"]["code"], -32602)
        self.assertEqual(self.server.dispatch(rpc("ping"))["result"], {})

    def test_stdio_no_extra_output(self):
        requests = [
            rpc("initialize", {"protocolVersion": "2025-06-18"}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc("tools/list", mid=2),
            rpc("tools/call", {"name": "chiaki_capture_frame"}, mid=3),
        ]
        incoming = io.BytesIO(("\n".join(json.dumps(x) for x in requests) + "\n").encode())
        outgoing = io.BytesIO()
        mod.serve(self.server, incoming, outgoing)
        responses = [json.loads(x) for x in outgoing.getvalue().decode().splitlines()]
        self.assertEqual([x["id"] for x in responses], [1, 2, 3])
        self.assertEqual(len(responses), 3)

    def test_tool_error_stays_in_result(self):
        class NoWindow:
            def status(self):
                return {"ready": False}
            def capture(self, w, h):
                raise mod.BridgeError("No foreground Chiaki-ng window")
        server = mod.MCPBridge(NoWindow())
        response = server.dispatch(rpc("tools/call", {"name": "chiaki_capture_frame"}))
        self.assertTrue(response["result"]["isError"])
        self.assertNotIn("error", response)


class FakeController:
    def __init__(self):
        self.calls = []

    def press(self, button, duration):
        self.calls.append((button, duration))
        return {"button": button, "duration_ms": duration,
                "status": "test-only; no real device"}


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCapture()
        self.controller = FakeController()

    def call(self, server, button, **other):
        return server.dispatch(rpc("tools/call", {
            "name": "chiaki_press_button",
            "arguments": {"button": button, **other}}))["result"]

    def test_disabled_by_default_even_when_backend_available(self):
        server = mod.MCPBridge(self.fake, control_backend=self.controller)
        response = self.call(server, "ps")
        self.assertTrue(response["isError"])
        self.assertEqual(self.controller.calls, [])
        status = server.dispatch(rpc("tools/call", {"name": "chiaki_screen_status"}))
        self.assertFalse(json.loads(status["result"]["content"][0]["text"])["control_enabled"])

    def test_explicit_opt_in_and_ps_hold(self):
        server = mod.MCPBridge(self.fake, self.controller, control_enabled=True)
        result = self.call(server, "ps_hold")
        self.assertNotIn("isError", result)
        self.assertEqual(self.controller.calls, [("ps_hold", 1350)])

    def test_input_validation_and_no_arbitrary_keys(self):
        bad_values = [
            {"button": "windows"}, {"button": "alt-tab"},
            {"button": "ps", "duration_ms": 1000},
            {"button": "cross", "duration_ms": 600},
            {"button": "ps_hold", "duration_ms": 300},
            {"button": "ps_hold", "duration_ms": 1801},
            {"button": "up", "duration_ms": True},
            {"button": "cross", "key": "P"},
            {"button": ["ps"]},
        ]
        server = mod.MCPBridge(self.fake, self.controller, control_enabled=True)
        for args in bad_values:
            with self.subTest(args=args):
                result = server.dispatch(rpc("tools/call", {
                    "name": "chiaki_press_button", "arguments": args}))["result"]
                self.assertTrue(result["isError"])
        self.assertEqual(self.controller.calls, [])

    def test_kill_switch_blocks_further_controls(self):
        server = mod.MCPBridge(self.fake, self.controller, control_enabled=True)
        result = server.dispatch(rpc("tools/call", {"name": "chiaki_disable_control"}))
        self.assertFalse(json.loads(result["result"]["content"][0]["text"])["control_enabled"])
        self.assertTrue(self.call(server, "ps")["isError"])
        self.assertEqual(self.controller.calls, [])
        self.assertTrue(server.dispatch(rpc("tools/call", {
            "name": "chiaki_disable_control", "arguments": {"ignore": True}}))["result"]["isError"])

    def test_control_rate_limit(self):
        server = mod.MCPBridge(self.fake, self.controller, control_enabled=True)
        self.assertNotIn("isError", self.call(server, "up"))
        self.assertTrue(self.call(server, "down")["isError"])
        self.assertEqual(self.controller.calls, [("up", 120)])

    def test_bounded_stick_movement(self):
        server = mod.MCPBridge(self.fake, self.controller, control_enabled=True)
        result = self.call(server, "move_forward", duration_ms=900)
        self.assertNotIn("isError", result)
        self.assertEqual(self.controller.calls, [("move_forward", 900)])
        server.last_input = 0
        self.assertTrue(self.call(server, "move_backward", duration_ms=1801)["isError"])
        self.assertTrue(self.call(server, "move_left", duration_ms=10)["isError"])
        self.assertEqual(len(self.controller.calls), 1)

    def test_default_virtual_keys_match_chiaki_source(self):
        self.assertEqual({k: vk for k, (vk, _ext) in mod.BUTTONS.items()},
                         {"ps": 0x1B, "ps_hold": 0x1B, "up": 0x26,
                          "down": 0x28, "left": 0x25, "right": 0x27,
                          "cross": 0x0D, "circle": 0x08, "options": 0x4F,
                          "move_forward": 0x2D, "move_backward": 0x2E,
                          "move_left": 0xDB, "move_right": 0xDD})


if __name__ == "__main__":
    unittest.main()
