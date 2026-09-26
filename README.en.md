# ROMANBO Servo / Robot Control Library (Python SDK)

[English] | [简体中文](README.md)

[![CI](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml/badge.svg)](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Typing: py.typed](https://img.shields.io/badge/typing-py.typed-blue)](https://peps.python.org/pep-0561/)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f)](https://lqx-code-sh.github.io/Romanbo-Python-SDK/)

A Python SDK for **ROMANBO** servo models over an RS485 bus. It implements the full
servo / whole-robot control protocol and **every conclusion has been verified on real
hardware** (two MOS servos on a shared bus, IDs 8 / 10, COM3, 2026-09-25).

**Every statement in the docs is labelled `protocol spec` / `measured on hardware` /
`unverified`** — nothing unverified is presented as verified.

📖 **Documentation site:** <https://lqx-code-sh.github.io/Romanbo-Python-SDK/>

---

## Requirements & installation

- **Python 3.8+** — all modules use `from __future__ import annotations`
- **pyserial** (`>=3.5,<4.0`) — only needed for a **real serial port**, and imported
  lazily when the port is opened. Offline features (`--mock`, `selftest`, `info`, `.rsc`
  parsing, frame encoding) work without it.

```bash
pip install pyserial          # only for real hardware
```

```bash
# A. Copy the directory: drop `romanbo/` into your project (verified standalone)
# B. Editable install (development)
pip install -e .              # with dev tooling: pip install -e ".[dev]"
# C. Build a wheel
python -m pip wheel . --no-deps -w dist
```

No install needed either — run as a module from the repository root:

```bash
python -m romanbo selftest
```

On Linux you must be in the `dialout` group (or the port must be readable):

```bash
python3 -m romanbo ports                      # list serial ports
python3 -m romanbo --port /dev/ttyUSB0 scan   # find servo IDs
```

## Quick start

```bash
# Offline: validate 60 reference frames byte-for-byte
python -m romanbo selftest

# Offline: full workflow without hardware
python -m romanbo --mock scan
python -m romanbo --mock --json read --ids 1,2

# Real hardware
python -m romanbo --port COM3 scan --start 1 --end 32
python -m romanbo --port COM3 read --ids 8,10
python -m romanbo --port COM3 --json config --ids 8,10
python -m romanbo --port COM3 load --ids 8 --watch 10 --interval 0.1

# Rotate +15° at 60 °/s with software load limiting (threshold 60)
python -m romanbo --port COM3 jog --id 8 --degrees 15 --speed 60 --max-load 60

# Play a .rsc motion file; examples/data/demo.rsc ships with the repo
python -m romanbo --port COM3 play examples/data/demo.rsc --speed 60

# Inspect a project file (offline)
python -m romanbo info examples/data/demo.rsc
```

Global options: `-p/--port`, `--mock`, `--baudrate`, `--timeout`, `--json`, `--frames`.

## Command-line reference (condensed)

| Command | Notes |
|---|---|
| `selftest` | Offline: verify 60 reference frames |
| `handshake` | Controller model + firmware version |
| `scan` | `--start --end --probe-timeout --quarantine` |
| `read` | `--ids 8,10` (positions; missing IDs cost a single timeout each) |
| `teach` | Batch position read-back; `--file taught.json` appends a keyframe |
| `export` | `taught.json` → playable `.rsc` (offline) |
| `config` | position / PID / limits / **measured load** / temperature / zero |
| `load` | Sample the measured load (`--watch N --interval s`) |
| `jog` | `--id --degrees` or `--delta`, `--speed` (deg/s) or `--period`, `--max-load` |
| `angle` | Absolute angle (ADC 512 = 0°), same speed options |
| `sweep` | Back-and-forth demo, `--cycles`, `--no-readback`, `--no-return`, `--loop` |
| `move` | `--targets 8:600,10:480`, `--speed`/`--period`, `--settle`, `--readback`, `--max-load` |
| `torque` | `on\|off --ids` (enable switch) |
| `led` | `--value N` or `--color 1,0,1` |
| `pid` / `limit` | Write **and read back to confirm**; exit code 1 on mismatch |
| `param` | `current-limit \| margin \| temp \| offset \| period \| accelerate --value N` |
| `wheel` | `--speed 0..255 [--ccw] [--free] [--relative]` |
| `play` | `.rsc` playback; `--ids` restricts to online joints, `--speed`, `--max-load` |

Exit codes: `0` OK · `4` aborted by software load limit · `5` port error · `130` Ctrl+C.

## Python API

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:          # or connect("COM3", mock=True)
    print(robot.scan(1, 32))                 # online IDs
    s = robot.servo(8)
    print(s.get_position(), s.angle())       # ADC / degrees
    print(s.get_load())                      # measured load (0x18)

    s.torque(True)
    s.rotate(15, speed_dps=60)               # relative +15° at 60 °/s
    try:
        s.move_at_speed(1023, 120, max_load=60)
    except LoadLimitExceeded as exc:
        print(exc.as_dict())

    robot.move({8: 600, 10: 480}, speed_dps=60, start=robot.capture([8, 10]))
    robot.play_rsc("examples/data/demo.rsc", speed_dps=60, max_load=60)
    print(s.read_config().as_dict())
```

| Class / module | Responsibility |
|---|---|
| `RomanboRobot` | connection, scan, multi-joint moves, teach, `.rsc` playback |
| `Servo` | all per-servo commands (position/angle/speed/wheel/torque/PID/limits/LED/calibration) |
| `ServoConfig` | all parameters read back in one call |
| `LoadLimitExceeded` | raised when the software load limit trips |
| `RscProject` / `MotionFrame` | `.rsc` project parsing and generation |
| `joints` | ADC↔angle conversion, channel tables, step planning |
| `protocol` | frame codec, command codes, checksums, timings |
| `transport` | `SerialTransport` (pyserial) / `MockTransport` (offline) |

Full, auto-generated API reference: [`docs/api.md`](docs/api.md) → docs site.

## Protocol essentials

```
FF FF | ID | LEN | CMD | DATA ... | CHK
```

- `CHK`: `sum(whole frame) & 0xFF == 0`
- 115200 8N1, DTR/RTS asserted
- `0` = controller board, `1..32` = servos, `224..253` = sensors, `254` = broadcast
- Responses: only GET-class commands and `Status` reply; `ACK = 0x80 | cmd`.
  **SET commands never reply** — use a read-back to confirm.
- ADC range 0..1023 with **512 = 0°**; `RATIO_MAIN = 0.2932551` (≈300/1023)

Byte-level, per-command spec: [`docs/SERVO_SPEC.md`](docs/SERVO_SPEC.md).

## Key findings from real hardware

| Finding | Consequence |
|---|---|
| `SET_PERIOD (0x0B)` is **ignored by the firmware** — the servo always runs at its own max speed (~150–180 °/s) | Angular speed is implemented by **step-wise approach**: publish an intermediate target every `interval_ms`. Measured error ≈ −4 % |
| `0x18` is the **measured load**, not a limit. 0 at rest, ~110–130 while moving fast | The only way to limit force: poll `0x18` and stop (`--max-load`) |
| **Two frames sent back-to-back lose the second one** (ID-independent) | `SerialTransport.write()` enforces `MIN_FRAME_GAP = 2 ms`; without it multi-joint moves only moved the first joint |
| **Bus quarantine**: after a frame addressed to a non-existent ID, the bus ignores ~0.4 s of traffic | `scan()` waits 0.4 s after a failed probe, otherwise scanning silently misses servos |
| Torque has an **enable switch** (`0x10`) **and power levels** H/M/L/W (`0x09` `d[5]` bit3-4) | Measured peak load: H 226 > M 152 > L 118; W makes position commands ineffective |
| `0x0F` (GetMotionPeriod) shares a code with `SetPositionLimit` and **corrupts limits even with no data** | `get_period()` refuses to send unless `unsafe=True` |

## Software load limiting

This hardware has no usable torque loop, so `--max-load`/`max_load=` polls the measured
load while moving and stops as soon as the threshold is crossed:

```bash
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 120 --max-load 10
```

Picking a threshold: 0 at rest, peaks around 110–130 during fast motion → `60` is a
reasonable starting point; `10` will abort immediately on start-up.

## Safety and known limitations

- Motion commands (`jog`/`angle`/`sweep`/`move`/`play`) **drive the servos immediately**.
  Support or detach the limb first.
- Parameter commands (`pid`/`limit`/`param`/calibration/`set-id`/`reset`) are written to
  the servo and **persist across power cycles**. Back up the current values with
  `config` first. **SET commands have no ACK**, so writes can be silently dropped —
  `pid` and `limit` therefore read back to confirm.
- `torque off` makes the joints go limp (a supported robot may collapse).
- `0x0F` must never be sent: even without data it rewrites the position limits.
- Not supported / unverified: torque closed loop, hardware current limiting, motion
  period as a speed control, multi-joint preset + sync trigger (`0x20`/`0x21`),
  reading acceleration (`0x19`), controller-board commands (no board available).

Details: [`README.md`](README.md) §10 and [`docs/SERVO_SPEC.md`](docs/SERVO_SPEC.md) §10.

## Project layout

```
romanbo/            SDK package (protocol / transport / servo / robot / rsc / joints / cli …)
docs/               protocol spec, test plan, API reference, evidence logs
examples/           runnable examples + data/demo.rsc
tests/              129 unit tests (no hardware required)
tools/servo_probe.py  raw hex probe for bench debugging
```

```bash
python -m unittest discover -s tests -t .   # 129 unit tests
python -m romanbo selftest                  # 60 reference frames
python -m pip install -e ".[docs]" && python -m mkdocs serve   # docs site locally
```

## License

MIT — see [`LICENSE`](LICENSE). Changelog: [`CHANGELOG.md`](CHANGELOG.md).
Contributions: [`CONTRIBUTING.md`](CONTRIBUTING.md).
