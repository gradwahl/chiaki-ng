# chiaki-ng agent guide — PS5 visual feedback and limited control

Read the workspace `../AGENTS.md` first, and `mcp-bridge/README.md`
before running the screenshot or controller helpers. This repository
is a distinct Git checkout; run status/diffs from `chiaki-ng/`.

## What is available

- `mcp-bridge/dist/chiaki-screen-mcp.exe` is the tested **v0.3.0**
  Windows stdio MCP companion. It does **not** replace or alter the
  existing Chiaki-ng Qt/FFmpeg client. It was built with
  `powershell -NoProfile -ExecutionPolicy Bypass -File
  .\mcp-bridge\build.ps1` from this repository root.
- The normal Chiaki-ng application must be open, streaming the PS5,
  visible and not minimized; the helper cannot open a Remote Play
  session or authenticate to PSN. One window can be captured when
  Chiaki is backgrounded using window-specific PrintWindow.
- Default server is **read-only**: `chiaki_screen_status` and
  `chiaki_capture_frame`. `python mcp-bridge/tests/live_mcp_smoke.py`
  verifies live packaged MCP protocol+PNG; add `--image-json` only
  when the connected tool will consume the image response. Do not
  save/log/print the raw image base64 or credentials unnecessarily.
- **Opt-in controller:** pass `--enable-control` to the server for
  `chiaki_press_button` and `chiaki_disable_control`. Prefer the
  short-lived helper `python mcp-bridge/tests/live_control_mcp.py
  --button BUTTON` for a single explicit action. It creates a
  controller-enabled process, queues one button press, disables
  controls and exits. Valid BUTTON: `ps`, `ps_hold`, `up`, `down`,
  `left`, `right`, `cross`, `circle`, `options`,
  `move_forward`, `move_backward`, `move_left`, `move_right`.
  Movement is bounded to 60-1800ms and needs screenshot checking.
  No generic keyboard input,
  mouse control, multi-button sequences, account automation or
  network listener. Never leave input enabled by default.
- The keyboard mapping relies on Chiaki defaults and keyboard
  controller support being enabled. The PS press was verified live
  opening Control Centre; `ps_hold` reached Games Home; Left/Right
  moved the tile selection. Cross/Circle were implemented but
  not yet live-verified (as of 2026-10-30). If mapping differs, stop.
- Current conversation can invoke the executable via the connected
  Local File Editor command tool; there is **no permanently
  installed native MCP tool or continuous screen-sharing feed**.
  Each image is a distinct on-demand screenshot.

## How to drive a test safely

1. Confirm user approved testing the intended PS5 title and action,
   and verify the window exists using the status/screenshot tool.
2. Read the *current* screen. Select only the clearly identified
   target tile with the smallest feasible set of allowed inputs.
   Use `ps_hold` to return to Home only if navigation requires it.
3. After each meaningful button action, request a new screenshot
   and inspect the result. `PostMessageW` success merely means
   Windows queued an event — it does NOT prove PS5 received it.
   If the visual state or controller mapping is uncertain, stop.
4. After selecting the intended KB-RPCS3/game tile, verify loading,
   launch and gameplay visually. Wait for a stable scene before
   collecting emulator logs. The Chiaki bridge does not provide
   reliable emulator FPS; the KB-RPCS3 capture tools do.
5. Before updating PS5 executables, separately prove the running
   title and launcher are closed using an independent PS5 process
   check. **PS5 Home is NOT equivalent to closing the game.**
   Do not attempt to swap live game binaries from this bridge.
6. If Chiaki becomes minimized, disconnected, shows a controller
   reconnect prompt or an unrecognized screen, or cannot navigate
   using the allowlist, do not guess or issue rapid blind presses.
   Request the minimum user intervention needed.

## Controls and tests

| Action | Command from this repository root |
| --- | --- |
| View one frame (metadata only) | `python mcp-bridge/tests/live_mcp_smoke.py` |
| View one frame (image JSON to connected tool) | `python mcp-bridge/tests/live_mcp_smoke.py --image-json` |
| Return to PS5 Home | `python mcp-bridge/tests/live_control_mcp.py --button ps_hold` |
| Move selection right | `python mcp-bridge/tests/live_control_mcp.py --button right` |
| Move selection left | `python mcp-bridge/tests/live_control_mcp.py --button left` |
| Select tile (requires visual check) | `python mcp-bridge/tests/live_control_mcp.py --button cross` |
| Go back (requires visual check) | `python mcp-bridge/tests/live_control_mcp.py --button circle` |
| Rebuild and run tests | `powershell -NoProfile -ExecutionPolicy Bypass -File .\mcp-bridge\build.ps1` |

Input is opt-in per process. If using an extended MCP client instead
of the one-shot helper, call `chiaki_disable_control` as soon as the
single action finishes; terminating the server also disables input.
Default taps are 120 ms; `ps_hold` is 1350 ms. Do not send arbitrary
keystrokes or bypass allowlists. Keep the current GUI and account
session untouched unless the user asks otherwise.

## Performance-test caveats

Video frames can show game start, loading, faults, textures and UI,
but they are **not** substitutes for RPCS3 FPS/frame-time logs.
Remote Play itself may affect PS5/host timing, CPU/network load,
or on-screen overlay. Compare FPS only under matched conditions,
or measure the stream overhead. Coordinate with
`../KB-RPCS3/AGENTS.md` and update the relevant game research
after each test phase.
