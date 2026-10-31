"""Optional interactive Windows GDI capture test using a disposable Tk window.

Never touches Chiaki / PS5 / user files; screenshot bytes remain in memory.
Run manually from a logged-in interactive Windows desktop session.
"""
import struct
import sys
import time
import tkinter as tk
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chiaki_screen_mcp import WindowsCapture, PNG_MAGIC

root = tk.Tk()
try:
    root.title("CHIaki bridge synthetic capture smoke")
    root.geometry("480x300+120+120")
    root.configure(bg="#285a8e")
    root.lift()
    root.attributes("-topmost", True)
    root.update_idletasks()
    root.update()
    root.focus_force()
    time.sleep(0.3)
    root.update()

    backend = WindowsCapture(allowed_exes={"python.exe", "pythonw.exe"})
    # A tool subprocess cannot always take foreground focus on Windows.
    # Simulate only the foreground lookup in this disposable synthetic test.
    # The shipping MCP capture code still requires a real focused Chiaki window.
    import ctypes
    from ctypes import wintypes
    matched = []
    cbtype = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def find_synthetic(hwnd, _):
        if "synthetic capture smoke" in backend._title(hwnd):
            matched.append(hwnd)
        return True
    cb = cbtype(find_synthetic)
    backend.user32.EnumWindows(cb, 0)
    assert len(matched) == 1
    class SyntheticForeground:
        def __init__(self, native, hwnd):
            self.native, self.hwnd = native, hwnd
        def GetForegroundWindow(self):
            return self.hwnd
        def __getattr__(self, name):
            return getattr(self.native, name)
    backend.user32 = SyntheticForeground(backend.user32, matched[0])
    png, width, height, title = backend.capture(max_width=800, max_height=600)
    assert png.startswith(PNG_MAGIC) and width >= 400 and height >= 250
    assert "synthetic capture smoke" in title
    # Check interior test color, allowing for Windows border and DPI variation.
    offset = 8
    chunks = {}
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        kind = png[offset + 4:offset + 8]
        chunks[kind] = png[offset + 8:offset + 8 + length]
        offset += 12 + length
    pixels = zlib.decompress(chunks[b"IDAT"])
    scanline = 1 + width * 3
    center = (height // 2) * scanline + 1 + (width // 2) * 3
    r, g, b = pixels[center:center + 3]
    assert abs(r - 0x28) < 12 and abs(g - 0x5a) < 12 and abs(b - 0x8e) < 12, (r, g, b)
    print("PASS: Win32 GDI captured synthetic window with simulated foreground and correct PNG pixels")
finally:
    root.destroy()
