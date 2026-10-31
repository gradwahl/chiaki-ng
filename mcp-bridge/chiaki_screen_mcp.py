#!/usr/bin/env python3
"""Windows MCP bridge for Chiaki-ng video and opt-in PS5 button input.

Uses native Win32 APIs and Python standard library. Read-only by default;
input requires --enable-control. No socket server, credentials, or desktop-wide control.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import os
from pathlib import Path
import struct
import sys
import time
import zlib

VERSION = "0.3.0"
PROTOCOL_VERSION = "2025-06-18"
ALLOWED_EXES = frozenset(("chiaki.exe", "chiaki-ng.exe", "chiaki4deck.exe", "chiaking.exe"))
MAX_MESSAGE_BYTES = 128 * 1024
MIN_INTERVAL_SECONDS = 1.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

TOOLS = [
    {
        "name": "chiaki_screen_status",
        "title": "Chiaki screen status",
        "description": "Read-only: check whether a Chiaki-ng window is visible and focused. No image is sent.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "chiaki_capture_frame",
        "title": "Capture Chiaki frame (foreground or background)",
        "description": (
            "Read-only screenshot of a visible, non-minimized Chiaki-ng client area. "
            "Works while the Chiaki window is in the background using per-window PrintWindow; "
            "foreground capture uses display GDI. No other application may be selected. "
            "Returns one PNG image and UTC timestamp; does not control the console. "
            "Foreground overlays may appear when Chiaki is focused."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_width": {
                    "type": "integer", "minimum": 320, "maximum": 1920,
                    "default": 1280, "description": "Maximum output PNG width.",
                },
                "max_height": {
                    "type": "integer", "minimum": 240, "maximum": 1080,
                    "default": 720, "description": "Maximum output PNG height.",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
    },
]


BUTTONS = {
    # Default Chiaki-ng GUI keyboard map (gui/src/settings.cpp).
    "ps": (0x1B, False),        # Escape -> PS
    "ps_hold": (0x1B, False),   # held Escape -> PS home
    "up": (0x26, True),        # VK_UP -> D-pad up
    "down": (0x28, True),      # VK_DOWN
    "left": (0x25, True),      # VK_LEFT
    "right": (0x27, True),     # VK_RIGHT
    "cross": (0x0D, False),    # Return -> Cross / X
    "circle": (0x08, False),   # Backspace -> Circle
    "options": (0x4F, False),  # O -> PS5 Options (Chiaki default mapping)
    "move_forward": (0x2D, True),  # Insert -> left stick forward
    "move_backward": (0x2E, True), # Delete -> left stick backward
    "move_left": (0xDB, False),    # [ -> left stick left
    "move_right": (0xDD, False),   # ] -> left stick right
}

CONTROL_TOOLS = [
    {
        "name": "chiaki_press_button",
        "title": "Press a PS5 button through Chiaki",
        "description": (
            "Requires server --enable-control and a visible Chiaki-ng Remote Play window. "
            "Posts one allowlisted default Chiaki keyboard mapping as key down/up, "
            "without typing into other applications. ps_hold returns to PS5 home "
            "when the PS5 accepts long PS presses. No arbitrary keys or combos. "
            "Button delivery is not confirmed until a screenshot verifies it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "button": {"type": "string", "enum": list(BUTTONS.keys())},
                "duration_ms": {
                    "type": "integer", "minimum": 40, "maximum": 1800,
                    "description": "Optional. Tap defaults 120 ms; ps_hold defaults 1350 ms.",
                },
            },
            "required": ["button"], "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
    {
        "name": "chiaki_disable_control",
        "title": "Disable all Chiaki controller input for this MCP session",
        "description": (
            "Immediately disable controller input in this MCP process; "
            "the process must be restarted with --enable-control to re-enable."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    },
]


class BridgeError(Exception):
    """An expected error suitable for a tool-result message."""


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def encode_png_bgrx(bgrx: bytes, width: int, height: int) -> bytes:
    """Encode a top-down, tightly packed 32-bit BGRX DIB to PNG without Pillow."""
    if not (1 <= width <= 1920 and 1 <= height <= 1080):
        raise ValueError("Invalid frame dimensions")
    stride = width * 4
    if len(bgrx) != stride * height:
        raise ValueError("Invalid frame buffer length")
    compressor = zlib.compressobj(level=3)
    compressed = bytearray()
    for y in range(height):
        row = bgrx[y * stride:(y + 1) * stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        compressed.extend(compressor.compress(b"\x00" + rgb))
    compressed.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_MAGIC + _png_chunk(b"IHDR", header)
            + _png_chunk(b"IDAT", bytes(compressed)) + _png_chunk(b"IEND", b""))


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class WindowsCapture:
    """Only select allowlisted, visible Chiaki processes; never capture other apps."""

    def __init__(self, allowed_exes=ALLOWED_EXES):
        if sys.platform != "win32":
            raise BridgeError("Screen capture requires a Windows desktop session")
        self.allowed_exes = frozenset(n.lower() for n in allowed_exes)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure()
        # Avoid logical/physical pixel mismatches with Windows display scaling.
        try:
            awareness = ctypes.c_void_p(-4)  # PER_MONITOR_AWARE_V2
            self.user32.SetProcessDpiAwarenessContext(awareness)
        except (AttributeError, OSError):
            pass

    def _configure(self):
        u, g, k = self.user32, self.gdi32, self.kernel32
        HWND, HDC = wintypes.HWND, wintypes.HDC
        HBITMAP = wintypes.HBITMAP
        HANDLE = wintypes.HANDLE
        u.GetForegroundWindow.restype = HWND
        u.GetWindowThreadProcessId.argtypes = (HWND, ctypes.POINTER(wintypes.DWORD))
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.IsWindowVisible.argtypes = (HWND,)
        u.IsWindowVisible.restype = wintypes.BOOL
        u.IsIconic.argtypes = (HWND,)
        u.IsIconic.restype = wintypes.BOOL
        u.GetClientRect.argtypes = (HWND, ctypes.POINTER(RECT))
        u.GetClientRect.restype = wintypes.BOOL
        u.ClientToScreen.argtypes = (HWND, ctypes.POINTER(POINT))
        u.ClientToScreen.restype = wintypes.BOOL
        u.GetWindowTextLengthW.argtypes = (HWND,)
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = (HWND, wintypes.LPWSTR, ctypes.c_int)
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetSystemMetrics.argtypes = (ctypes.c_int,)
        u.GetSystemMetrics.restype = ctypes.c_int
        u.GetDC.argtypes = (HWND,)
        u.GetDC.restype = HDC
        u.ReleaseDC.argtypes = (HWND, HDC)
        u.ReleaseDC.restype = ctypes.c_int
        u.EnumWindows.argtypes = (ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM),
                                  wintypes.LPARAM)
        u.EnumWindows.restype = wintypes.BOOL
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.OpenProcess.restype = HANDLE
        k.QueryFullProcessImageNameW.argtypes = (HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (HANDLE,)
        k.CloseHandle.restype = wintypes.BOOL
        g.CreateCompatibleDC.argtypes = (HDC,)
        g.CreateCompatibleDC.restype = HDC
        g.DeleteDC.argtypes = (HDC,)
        g.DeleteDC.restype = wintypes.BOOL
        g.CreateDIBSection.argtypes = (HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                      ctypes.POINTER(ctypes.c_void_p), HANDLE, wintypes.DWORD)
        g.CreateDIBSection.restype = HBITMAP
        g.SelectObject.argtypes = (HDC, wintypes.HGDIOBJ)
        g.SelectObject.restype = wintypes.HGDIOBJ
        g.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
        g.DeleteObject.restype = wintypes.BOOL
        g.StretchBlt.argtypes = (HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.DWORD)
        g.StretchBlt.restype = wintypes.BOOL
        g.SetStretchBltMode.argtypes = (HDC, ctypes.c_int)
        g.SetStretchBltMode.restype = ctypes.c_int
        u.PrintWindow.argtypes = (HWND, HDC, wintypes.UINT)
        u.PrintWindow.restype = wintypes.BOOL

    def _exe(self, hwnd):
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)  # query only
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not self.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(length)):
                return ""
            return buffer.value.replace("\\", "/").rsplit("/", 1)[-1].lower()
        finally:
            self.kernel32.CloseHandle(handle)

    def _title(self, hwnd):
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(min(length + 1, 512))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _is_eligible(self, hwnd):
        return (bool(hwnd) and bool(self.user32.IsWindowVisible(hwnd))
                and not bool(self.user32.IsIconic(hwnd))
                and self._exe(hwnd) in self.allowed_exes)

    def status(self):
        focused = self.user32.GetForegroundWindow()
        windows = []
        # Keep the callback alive until EnumWindows returns.
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def on_window(hwnd, _):
            if self._is_eligible(hwnd):
                windows.append({"title": self._title(hwnd)[:120],
                                "focused": int(hwnd) == int(focused)})
            return True

        callback = callback_type(on_window)
        self.user32.EnumWindows(callback, 0)
        return {"platform": "windows", "chiaki_windows": windows,
                "ready": bool(windows),
                "capture_policy": "visible Chiaki client area only; background via PrintWindow"}

    def capture(self, max_width=1280, max_height=720):
        hwnd = self.user32.GetForegroundWindow()
        if not self._is_eligible(hwnd):
            hwnds = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def add_chiaki(candidate, _):
                if self._is_eligible(candidate):
                    hwnds.append(candidate)
                return True
            callback = callback_type(add_chiaki)
            self.user32.EnumWindows(callback, 0)
            if not hwnds:
                raise BridgeError("No visible Chiaki-ng window. Open the Remote Play stream and retry.")
            if len(hwnds) != 1:
                raise BridgeError("Multiple Chiaki windows: focus the stream window to select it.")
            return self._capture_background(hwnds[0], max_width, max_height)
        return self._capture_foreground(hwnd, max_width, max_height)

    def _capture_foreground(self, hwnd, max_width, max_height):
        # The client rectangle excludes title bars and most OS window chrome.
        rc = RECT()
        origin = POINT()
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rc)):
            raise BridgeError("Could not read Chiaki client rectangle")
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise BridgeError("Could not locate Chiaki window on the display")
        src_w, src_h = rc.right - rc.left, rc.bottom - rc.top
        if not 16 <= src_w <= 16384 or not 16 <= src_h <= 16384:
            raise BridgeError("Chiaki window has no usable visible area")
        x, y = origin.x, origin.y
        virtual_left = self.user32.GetSystemMetrics(76)
        virtual_top = self.user32.GetSystemMetrics(77)
        virtual_right = virtual_left + self.user32.GetSystemMetrics(78)
        virtual_bottom = virtual_top + self.user32.GetSystemMetrics(79)
        if (x < virtual_left or y < virtual_top
                or x + src_w > virtual_right or y + src_h > virtual_bottom):
            raise BridgeError("Chiaki client area must be entirely on screen")
        scale = min(1, max_width / src_w, max_height / src_h)
        w = max(1, round(src_w * scale))
        h = max(1, round(src_h * scale))
        if w > 1920 or h > 1080:
            raise BridgeError("Requested image exceeds the bridge safety limit")
        u, g = self.user32, self.gdi32
        src = u.GetDC(None)
        if not src:
            raise BridgeError("Could not acquire display DC")
        mem = None
        bmp = None
        previous = None
        try:
            mem = g.CreateCompatibleDC(src)
            if not mem:
                raise BridgeError("Could not allocate screenshot DC")
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = w
            info.bmiHeader.biHeight = -h  # top-down pixels
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0  # BI_RGB
            pixels = ctypes.c_void_p()
            bmp = g.CreateDIBSection(src, ctypes.byref(info), 0, ctypes.byref(pixels), None, 0)
            if not bmp or not pixels.value:
                raise BridgeError("Could not create screenshot bitmap")
            previous = g.SelectObject(mem, bmp)
            if not previous:
                raise BridgeError("Could not select screenshot bitmap")
            g.SetStretchBltMode(mem, 3)  # COLORONCOLOR, fast and deterministic
            if not g.StretchBlt(mem, 0, 0, w, h, src, x, y, src_w, src_h,
                                0x00CC0020 | 0x40000000):  # SRCCOPY | CAPTUREBLT
                raise BridgeError("Windows refused screen capture")
            raw = ctypes.string_at(pixels, w * h * 4)
        finally:
            if previous:
                g.SelectObject(mem, previous)
            if bmp:
                g.DeleteObject(bmp)
            if mem:
                g.DeleteDC(mem)
            u.ReleaseDC(None, src)
        # Foreground can change while screenshotting; reject if that happened.
        if self.user32.GetForegroundWindow() != hwnd:
            raise BridgeError("Foreground window changed during capture; image discarded")
        return encode_png_bgrx(raw, w, h), w, h, self._title(hwnd)


    def _capture_background(self, hwnd, max_width, max_height):
        """Capture only the allowlisted Chiaki HWND via PrintWindow; no desktop pixels.

        Prints the *client area* even when another application has focus. Some
        GPU renderers may return black pixels on Windows; do not fall back to
        a desktop rectangle for background windows (it may expose other apps).
        """
        if not self._is_eligible(hwnd):
            raise BridgeError("Chiaki window is no longer visible")
        rect = RECT()
        origin = POINT()
        u, g = self.user32, self.gdi32
        if not u.GetClientRect(hwnd, ctypes.byref(rect)):
            raise BridgeError("Could not read Chiaki client rectangle")
        if not u.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise BridgeError("Could not locate Chiaki client area")
        src_w, src_h = rect.right - rect.left, rect.bottom - rect.top
        if not (16 <= src_w <= 3840 and 16 <= src_h <= 2160):
            raise BridgeError("Chiaki window client area is empty or too large")
        virtual_left = u.GetSystemMetrics(76)
        virtual_top = u.GetSystemMetrics(77)
        virtual_right = virtual_left + u.GetSystemMetrics(78)
        virtual_bottom = virtual_top + u.GetSystemMetrics(79)
        if (origin.x < virtual_left or origin.y < virtual_top
                or origin.x + src_w > virtual_right
                or origin.y + src_h > virtual_bottom):
            raise BridgeError("Chiaki client area must be entirely on screen")
        scale = min(1, max_width / src_w, max_height / src_h)
        width = max(1, round(src_w * scale))
        height = max(1, round(src_h * scale))
        screen_dc = u.GetDC(None)
        if not screen_dc:
            raise BridgeError("Could not allocate a Win32 reference DC")
        source_dc = None
        destination_dc = None
        source_bitmap = None
        destination_bitmap = None
        source_old = None
        destination_old = None
        try:
            source_dc = g.CreateCompatibleDC(screen_dc)
            destination_dc = g.CreateCompatibleDC(screen_dc)
            if not source_dc or not destination_dc:
                raise BridgeError("Could not allocate offscreen Chiaki capture DCs")

            def dib_info(w, h):
                info = BITMAPINFO()
                info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                info.bmiHeader.biWidth = w
                info.bmiHeader.biHeight = -h
                info.bmiHeader.biPlanes = 1
                info.bmiHeader.biBitCount = 32
                info.bmiHeader.biCompression = 0
                return info

            source_info = dib_info(src_w, src_h)
            source_pixels = ctypes.c_void_p()
            source_bitmap = g.CreateDIBSection(
                screen_dc, ctypes.byref(source_info), 0,
                ctypes.byref(source_pixels), None, 0)
            if not source_bitmap or not source_pixels.value:
                raise BridgeError("Could not allocate original Chiaki frame")
            source_old = g.SelectObject(source_dc, source_bitmap)
            if not source_old:
                raise BridgeError("Could not select original Chiaki frame")
            # PW_CLIENTONLY | PW_RENDERFULLCONTENT = 0x00000003.
            # PrintWindow receives an allowlisted Chiaki HWND, not a desktop DC.
            if not u.PrintWindow(hwnd, source_dc, 3):
                raise BridgeError("Chiaki refused background frame capture")
            if width == src_w and height == src_h:
                frame = ctypes.string_at(source_pixels, width * height * 4)
            else:
                destination_info = dib_info(width, height)
                destination_pixels = ctypes.c_void_p()
                destination_bitmap = g.CreateDIBSection(
                    screen_dc, ctypes.byref(destination_info), 0,
                    ctypes.byref(destination_pixels), None, 0)
                if not destination_bitmap or not destination_pixels.value:
                    raise BridgeError("Could not allocate scaled Chiaki frame")
                destination_old = g.SelectObject(destination_dc, destination_bitmap)
                if not destination_old:
                    raise BridgeError("Could not select scaled Chiaki frame")
                g.SetStretchBltMode(destination_dc, 3)  # COLORONCOLOR
                if not g.StretchBlt(destination_dc, 0, 0, width, height,
                                    source_dc, 0, 0, src_w, src_h, 0x00CC0020):
                    raise BridgeError("Could not scale Chiaki frame")
                frame = ctypes.string_at(destination_pixels, width * height * 4)
        finally:
            if destination_old:
                g.SelectObject(destination_dc, destination_old)
            if source_old:
                g.SelectObject(source_dc, source_old)
            if destination_bitmap:
                g.DeleteObject(destination_bitmap)
            if source_bitmap:
                g.DeleteObject(source_bitmap)
            if destination_dc:
                g.DeleteDC(destination_dc)
            if source_dc:
                g.DeleteDC(source_dc)
            u.ReleaseDC(None, screen_dc)
        if not self._is_eligible(hwnd):
            raise BridgeError("Chiaki window disappeared during capture")
        return encode_png_bgrx(frame, width, height), width, height, self._title(hwnd)



class WindowsController:
    """Allowlisted Windows window-message input into one verified Chiaki HWND.

    The shipping bridge never sends inputs to the actual foreground application
    unless that application is itself Chiaki-ng. The specific HWND is checked
    both before and after the press. PostMessage means queued, not delivered
    or observed by the PS5: verify effects with a separate screenshot.
    """

    def __init__(self, capture_backend):
        if sys.platform != "win32":
            raise BridgeError("PS5 keyboard bridge is Windows-only")
        self.capture_backend = capture_backend
        self.user32 = capture_backend.user32
        self.user32.PostMessageW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self.user32.MapVirtualKeyW.restype = wintypes.UINT

    def _target(self):
        c = self.capture_backend
        focused = c.user32.GetForegroundWindow()
        if c._is_eligible(focused):
            return focused
        hwnds = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def add_chiaki(hwnd, _):
            if c._is_eligible(hwnd):
                hwnds.append(hwnd)
            return True

        callback = callback_type(add_chiaki)
        c.user32.EnumWindows(callback, 0)
        if len(hwnds) != 1:
            raise BridgeError(
                "Expected exactly one visible Chiaki window; found "
                + str(len(hwnds)) + ". Focus the intended Chiaki stream.")
        return hwnds[0]

    def press(self, button, duration_ms):
        if button not in BUTTONS:
            raise BridgeError("Unsupported controller button")
        vk, extended = BUTTONS[button]
        hwnd = self._target()
        if not self.capture_backend._is_eligible(hwnd):
            raise BridgeError("Chiaki window disappeared before input")
        scan = self.user32.MapVirtualKeyW(vk, 0) & 0xff
        down = 1 | (scan << 16) | (int(extended) << 24)
        up = down | (1 << 30) | (1 << 31)
        if not self.user32.PostMessageW(hwnd, 0x100, vk, down):  # WM_KEYDOWN
            raise BridgeError("Windows could not queue Chiaki key-down")
        try:
            time.sleep(duration_ms / 1000.0)
        finally:
            # Best effort release even on exceptions. Never leave held input.
            released = bool(self.user32.PostMessageW(hwnd, 0x101, vk, up))
        if not released:
            raise BridgeError("Windows could not queue key release; inspect Chiaki")
        return {"button": button, "duration_ms": duration_ms,
                "window_title": self.capture_backend._title(hwnd),
                "status": "key down/up queued; verify PS5 response by screenshot",
                "mapping": "Chiaki default keyboard bindings"}


def _result_text(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}


class MCPBridge:
    def __init__(self, capture_backend, control_backend=None, control_enabled=False):
        self.capture_backend = capture_backend
        self.control_backend = control_backend
        self.control_enabled = bool(control_enabled and control_backend is not None)
        self.last_capture = 0.0
        self.last_input = 0.0

    def tool_call(self, name, args):
        if not isinstance(args, dict):
            raise BridgeError("Tool arguments must be an object")
        if name == "chiaki_screen_status":
            if args:
                raise BridgeError("chiaki_screen_status takes no parameters")
            result = self.capture_backend.status()
            return _result_text({**result, "control_enabled": self.control_enabled})
        if name == "chiaki_disable_control":
            if args:
                raise BridgeError("chiaki_disable_control takes no parameters")
            self.control_enabled = False
            return _result_text({"control_enabled": False,
                                 "note": "Controller input disabled in this process. "
                                         "Restart with --enable-control to re-enable."})
        if name == "chiaki_press_button":
            if not self.control_enabled:
                raise BridgeError("Controller input disabled. Start this MCP process with --enable-control.")
            if set(args) - {"button", "duration_ms"}:
                raise BridgeError("Unexpected controller tool parameter")
            button = args.get("button")
            if type(button) is not str or button not in BUTTONS:
                raise BridgeError("Button must be one of: " + ", ".join(BUTTONS))
            duration = args.get("duration_ms", 1350 if button == "ps_hold" else
                                650 if button.startswith("move_") else 120)
            if type(duration) is not int:
                raise BridgeError("duration_ms must be an integer")
            low, high = ((1000, 1800) if button == "ps_hold" else
                         (60, 1800) if button.startswith("move_") else (40, 350))
            if not low <= duration <= high:
                raise BridgeError(f"{button} requires duration_ms between {low} and {high}")
            now = time.monotonic()
            if self.last_input and now - self.last_input < 0.3:
                raise BridgeError("Controller rate limit; wait 300 ms between inputs")
            self.last_input = now
            return _result_text(self.control_backend.press(button, duration))
        if name != "chiaki_capture_frame":
            raise ValueError("Unknown tool: " + str(name))
        if set(args) - {"max_width", "max_height"}:
            raise BridgeError("Unknown screenshot argument")
        width = args.get("max_width", 1280)
        height = args.get("max_height", 720)
        if type(width) is not int or not 320 <= width <= 1920:
            raise BridgeError("max_width must be an integer between 320 and 1920")
        if type(height) is not int or not 240 <= height <= 1080:
            raise BridgeError("max_height must be an integer between 240 and 1080")
        now = time.monotonic()
        if self.last_capture and now - self.last_capture < MIN_INTERVAL_SECONDS:
            raise BridgeError("Capture rate limited to one frame per second")
        frame, actual_width, actual_height, title = self.capture_backend.capture(width, height)
        if not frame.startswith(PNG_MAGIC):
            raise BridgeError("Capture did not return a PNG")
        self.last_capture = time.monotonic()
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
        return {"content": [
            {"type": "text", "text": json.dumps({
                "captured_at_utc": timestamp, "width": actual_width,
                "height": actual_height, "window_title": title,
                "source": "visible Chiaki-ng client area (foreground or per-window background)",
                "note": "This is a displayed frame, not a direct FPS measurement.",
            }, ensure_ascii=False)},
            {"type": "image", "data": base64.b64encode(frame).decode("ascii"),
             "mimeType": "image/png"},
        ]}

    def dispatch(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                    "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}
        if "id" not in request:
            # Notifications must not produce a response.
            return None
        mid = request["id"]
        method = request.get("method")
        params = request.get("params") or {}
        try:
            if not isinstance(params, dict):
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32602, "message": "Invalid parameters"}}
            if method == "initialize":
                proposed = params.get("protocolVersion")
                version = proposed if proposed in ("2024-11-05", "2025-03-26", "2025-06-18") else PROTOCOL_VERSION
                result = {"protocolVersion": version,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "chiaki-screen-mcp", "version": VERSION},
                          "instructions": "Screenshots of visible Chiaki windows. Controller buttons are disabled "
                                          "unless this executable was started with --enable-control; "
                                          "presses are limited to allowlisted Chiaki keyboard mappings."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS + CONTROL_TOOLS}
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                if name not in {t["name"] for t in TOOLS + CONTROL_TOOLS}:
                    return {"jsonrpc": "2.0", "id": mid,
                            "error": {"code": -32602, "message": "Unknown tool"}}
                try:
                    result = self.tool_call(name, args)
                except BridgeError as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": "Method not found"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as exc:
            # No traceback or system details in tool results; stderr only for logs.
            print(f"Bridge error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": "Internal bridge error"}}


def serve(bridge, stdin=None, stdout=None):
    in_stream = stdin or sys.stdin.buffer
    out_stream = stdout or sys.stdout.buffer
    for line in in_stream:
        if len(line) > MAX_MESSAGE_BYTES:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "Request too large"}}
        else:
            try:
                request = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": "Parse error"}}
            else:
                response = bridge.dispatch(request)
        if response is not None:
            out_stream.write((json.dumps(response, separators=(",", ":"),
                                         ensure_ascii=False) + "\n").encode("utf-8"))
            out_stream.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true",
                        help="Check supported platform, bridge setup and PNG codec without capturing")
    parser.add_argument("--enable-control", action="store_true",
                        help="Explicitly allow limited PS5 button injection into Chiaki for this MCP process")
    args = parser.parse_args()
    if args.self_test:
        pixels = b"\x00\x00\xff\x00" * 4
        encoded = encode_png_bgrx(pixels, 2, 2)
        assert encoded.startswith(PNG_MAGIC)
        backend = WindowsCapture()
        print(json.dumps({"ok": True, "bridge": VERSION, "windows": backend.status(),
                          "png_codec": "PASS"}))
        return
    try:
        backend = WindowsCapture()
    except BridgeError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
    # Binary stdio. Never print progress or image data to stdout outside MCP.
    serve(MCPBridge(
        backend,
        control_backend=WindowsController(backend) if args.enable_control else None,
        control_enabled=args.enable_control))


if __name__ == "__main__":
    main()
