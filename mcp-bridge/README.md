# Chiaki-ng screen and optional controller MCP bridge (Windows)

A screenshot MCP server with **opt-in**, tightly limited PS5 button control.
It is a separate Windows executable, not a replacement for the Chiaki-ng Qt
GUI. It does not modify video decoding, controller code, console files, user
settings, or PSN credentials. **Control is disabled by default.**

The existing Chiaki-ng executable does **not** need to be rebuilt. This server
requires only Python's standard library at runtime and native Win32 APIs.
The build packages Python into a Windows .exe using PyInstaller.

## What the bridge can do

- chiaki_screen_status: list visible Chiaki-ng windows, whether one is focused.
- chiaki_capture_frame: return a single PNG image and UTC timestamp through
  MCP; default maximum image size 1280 x 720 (upper limit 1920 x 1080).
- Optional chiaki_press_button: press one allowlisted button when the MCP
  process is launched with --enable-control. Supported buttons: ps, ps_hold,
  up, down, left, right, cross (X), circle, options and bounded
  left-stick movement (`move_forward`, `move_backward`, `move_left`,
  `move_right`).
- chiaki_disable_control: immediately revoke input for the current MCP process.
- No general keyboard access, controller combinations, PS5 session start,
  arbitrary file writes, TCP listener or background recording. Capture is rate
  limited to once per second.

Only Windows processes named chiaki.exe, chiaki-ng.exe, chiaki4deck.exe, or
chiaking.exe qualify. A visible Chiaki window can be captured even if
**another application is focused**: the bridge asks Windows to render that
specific Chiaki window via PrintWindow, not the background desktop.
Minimized and entirely/partly off-screen windows are rejected. If more than
one Chiaki window is visible, focus the stream window to disambiguate.

**Privacy and capture limits:** For a background Chiaki window, PrintWindow
captures the Chiaki client area only; it does not capture the surrounding
desktop or other applications. When Chiaki is focused, the tool can fall back
to display GDI, so visible overlays inside that rectangle may be included.
GPU overlays/HDR/video surfaces can be black or distorted. It does not bypass
protected video. The stream must actually be running. No screenshot is
recorded on disk; screenshots are returned only on explicit tool requests.

## Build and test on Windows

From the Chiaki-ng repository root:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\mcp-bridge\build.ps1

Prerequisites for the **build**: Python 3.10+ and PyInstaller.

    python -m pip install pyinstaller

The build script runs sixteen Python unit tests, builds the .exe, executes the
native Windows self-test, and checks the packaged MCP initialize/list/status
protocol through a real stdio subprocess.

Result:

    mcp-bridge\dist\chiaki-screen-mcp.exe

Run it standalone only for a non-capturing check:

    .\mcp-bridge\dist\chiaki-screen-mcp.exe --self-test

To re-run source tests without packaging:

    python -m unittest discover -s mcp-bridge/tests -v

## Connect it to an MCP client

In a client that can register **local stdio MCP servers**, configure a
process command pointing to the built executable:

    {
      "mcpServers": {
        "chiaki-screen": {
          "command": "C:\\Users\\Callum\\Documents\\GitHub\\PS5 RPCS3\\chiaki-ng\\mcp-bridge\\dist\\chiaki-screen-mcp.exe",
          "args": []
        }
      }
    }

This is *generic* MCP-client configuration; clients vary. Building an
executable does **not** connect it to this ChatGPT conversation by itself.
To use it directly in ChatGPT, a supported connector/gateway must register
it. This conversation can use the Local File Editor command connector for
on-demand tools and screenshots. Do not expose this bridge
to the public internet without strong authentication, isolation and explicit
user consent. An MCP client can receive screenshots when it requests them.

The server uses newline-delimited JSON-RPC 2.0 over local stdin/stdout and
MCP tools with accurate readOnlyHint annotations (read-only on screenshots,
write/side-effect on limited button input). It responds to initialize, ping, tools/list,
tools/call and accepts normal notifications. It advertises 2025-06-18
(and handles compatible initialization for 2024-11-05 and 2025-03-26).

## Game testing workflow

1. Launch your existing Chiaki-ng GUI, connect to the PS5 and leave the
   Chiaki streaming window visible and not minimized (it may be in background).
2. From a connected MCP client, call chiaki_screen_status, then
   chiaki_capture_frame. The latter returns actual image content.
3. For GTA IV, use images to confirm scene/visual state, but use the RPCS3
   native FPS/frame-time log to measure performance. Remote Play can affect
   resource use, and previous baselines may no longer be comparable without
   a matched capture/control.

**Live validation (2026-10-30):** A read-only PrintWindow capture of the
running 1920x1080 Chiaki-ng window successfully returned a real GTA IV game
image, downsampled to 640x360, even while the app was in the background.
The background image path was then added to the MCP server and validated
against the current live Chiaki window. Real MCP protocol and built-executable
tests are independently verified below; future OS/backend combinations may
behave differently.

## Files

- chiaki_screen_mcp.py — stdlib MCP server + Win32 foreground or background
  Chiaki-only capture + PNG.
- build.ps1 — repeatable local Windows executable build and verification.
- tests/test_bridge.py — protocol, PNG, read-only default, input limits,
  stop switch and rate-limit unit tests.
- tests/live_control_mcp.py — sends one explicitly requested button via a
  temporary --enable-control MCP process, then disables input and exits.
- tests/smoke_exe.py — real packaged-executable stdio protocol check.

## Optional screen-capture smoke test

With a logged-in Windows desktop, run:

    python mcp-bridge/tests/smoke_window.py

This opens a disposable solid-colour Tk window and exercises the native
Win32 capture and PNG conversion. In a tool-launched process, Windows may
refuse to give that window actual foreground focus, so the test *simulates*
foreground ownership only inside the synthetic harness. The shipping MCP
server does not have this synthetic test bypass; it selects real visible
Chiaki windows. The synthetic test passed. Live Chiaki background capture
was subsequently verified in this Windows session.

## Verified live executable image capture

The version 0.2.0 packaged executable successfully returned real GTA IV
video from the 1920x1080 Chiaki-ng client window via an actual MCP
`initialize` / `tools/call` exchange while Chiaki was **not focused**.
The resulting PNG was 640x360. This confirms that PrintWindow works with
the current PS5/Chiaki-ng display path on this machine. The test did not
save screenshots to disk or modify the console.

To repeat this test while Chiaki-ng is visibly streaming:

    python mcp-bridge/tests/live_mcp_smoke.py

A trusted local tool can use `--image-json` to receive an in-memory
JSON object with base64 PNG (do not post that data publicly).

The current ChatGPT conversation can reach the executable using the
connected Local File Editor command tool and inspect the returned image,
without a separately registered MCP plugin. That is an on-demand workflow,
not a permanent direct server connection or continuous video stream.


## Opt-in PS5 controller input (version 0.3.0)

The input tool is deliberately **disabled by default**. Starting
chiaki-screen-mcp.exe normally allows screenshots only. To allow controller
input, a local MCP host must explicitly launch:

    mcp-bridge\dist\chiaki-screen-mcp.exe --enable-control

The MCP tool chiaki_press_button accepts one button per request:

| MCP button | Chiaki default keyboard mapping | Purpose |
| --- | --- | --- |
| ps | Escape | Open/close PS5 Control Centre |
| ps_hold | Escape held approximately 1.35 seconds | Return to PS5 home |
| up/down/left/right | Arrow keys | D-pad movement |
| cross | Return/Enter | Select |
| circle | Backspace | Back |
| options | O | Open the selected PS5 title's context menu |
| move_forward | Insert | Left stick forward (gameplay) |
| move_backward | Delete | Left stick backward (gameplay) |
| move_left | [ | Left stick left (gameplay) |
| move_right | ] | Left stick right (gameplay) |

Only these exact button names are permitted. Default taps are 120ms with
a maximum of 350ms; ps_hold requires 1000-1800ms. Bounded left-stick
movements default to 650ms and allow 60-1800ms; inspect a new image
between movements and never navigate blind in unfamiliar game scenes. A rate limit of 300ms
between queued inputs applies within a server session. It uses Win32
PostMessage to the validated visible Chiaki-ng window rather than global
SendInput, and never types into ChatGPT or other foreground apps. The
controller mapping must use Chiaki's defaults and keyboard controller
support must remain enabled. Windows accepting a posted key is *not*
confirmation that the PS5 received it: inspect a fresh screenshot afterward.

Emergency disable for the current MCP process: call chiaki_disable_control,
or simply terminate the MCP process. There is no network endpoint or
persistent background worker. A future new process would need to be started
again with --enable-control. To revert fully to read-only, remove that flag.

For an explicit **single button** test, from the Chiaki checkout root:

    python mcp-bridge/tests/live_control_mcp.py --button ps
    python mcp-bridge/tests/live_control_mcp.py --button ps_hold
    python mcp-bridge/tests/live_control_mcp.py --button down

The helper starts an opt-in MCP session, posts only the requested press,
then disables input and exits. It never executes automatically in builds.
For a new screenshot after navigation:

    python mcp-bridge/tests/live_mcp_smoke.py

**First live validation (2026-10-30):** A short PS press sent directly to
the Chiaki-ng window using its default Escape mapping visibly opened the
PS5 Control Centre. The screen said "Please reconnect controller" at that
moment, so PS5 controller connectivity may affect other inputs.


## Verified live PS5 control — version 0.3.0, 2026-10-30

Built the opt-in controller extension and confirmed its actual effect on the
PS5. A short default Escape / PS press delivered with Win32 PostMessage
opened the PS5 Control Centre. Then the packaged 0.3.0 MCP executable
with --enable-control accepted a single ps_hold for 1350ms, queued key
down/up, disabled control with chiaki_disable_control, and exited.
An independently captured Chiaki screenshot confirmed PS5 **Games Home**
with **KB-RPCS3** selected. Subsequent packaged MCP right and left D-pad
presses each used a fresh single-action opt-in process and immediate
disable; screenshots showed navigation right to PS5X360 then back left to
KB-RPCS3. No title was selected or launched. Each button test was
visually confirmed, not merely inferred from PostMessage success.

There are 15 passing bridge unit tests. The executable remains opt-in
only for control (default is read-only). Only ps / ps_hold, D-pad four
directions, cross and circle are exposed; no typing arbitrary keys,
controller combos or OS-wide SendInput. The bridge is a *separate* local
Windows executable; Chiaki-ng GUI binaries, PSN data and console files
were not changed. Source/binary and setup guide in sibling
chiaki-ng/mcp-bridge/ repository path.

This does **not** prove the GTA IV title or KB-RPCS3 process is closed:
pressing/holding PS can simply put the game in the background. Do not
deploy/replace the PS5 executable until independently confirming both
game and launcher are stopped. The patch0064 gate1 candidate in-game
FPS comparison remains outstanding.
