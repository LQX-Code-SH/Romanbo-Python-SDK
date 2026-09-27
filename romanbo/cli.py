"""命令行接口：``python -m romanbo <命令>``。

示例：

    python -m romanbo --port COM3 handshake
    python -m romanbo --port COM3 scan
    python -m romanbo --port COM3 read --ids 1,2,3
    python -m romanbo --port COM3 move --targets 1:600,2:480 --period 500
    python -m romanbo --port COM3 play "../example/chapter 01.rsc" --speed 60
    python -m romanbo --mock selftest
    python -m romanbo info "../example/chapter 07.rsc"
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from typing import Dict, List, Optional, Sequence

from . import golden, joints as J, protocol as P
from .robot import RomanboRobot
from .rsc import DEFAULT_ADC, RscProject, write_project
from .servo import LoadLimitExceeded
from .transport import MockTransport, list_serial_ports

#: 软件限力中止时的退出码
EXIT_LOAD_LIMIT = 4
#: ``move --readback`` 未显式给 ``--settle`` 时，返回前等待到位的默认秒数
DEFAULT_MOVE_SETTLE = 0.3
#: 串口打不开（被占用 / 端口号不对）时的退出码
EXIT_PORT = 5

#: 出力档位：H/M/L/W ↔ 0/1/2/3（``0x09`` 的 ``d[5]`` bit3-4，实测有效）
LEVEL_MAP = {"H": P.LEVEL_HIGH, "M": P.LEVEL_MIDDLE,
             "L": P.LEVEL_LOW, "W": P.LEVEL_WHEEL}


def _parse_level(text: Optional[str]) -> Optional[int]:
    """把 ``H/M/L/W`` 或 ``0..3`` 解析成档位值。"""
    if text is None:
        return None
    key = str(text).strip().upper()
    if key.isdigit():
        return int(key)
    if key not in LEVEL_MAP:
        raise SystemExit(f"--level 只接受 H/M/L/W 或 0..3，收到 {text!r}")
    return LEVEL_MAP[key]


# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #

def _parse_ids(text: Optional[str]) -> Optional[List[int]]:
    if not text:
        return None
    out: List[int] = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            out.extend(range(int(start), int(end) + 1))
        else:
            out.append(int(part))
    return out or None


def _parse_targets(text: str) -> Dict[int, int]:
    """解析 ``"1:512,2:600"`` 形式的目标位置。

    作为 ``--targets`` 的 ``type=`` 使用，因此参数错误交给 argparse 统一报错
    （干净的一行提示 + 退出码 2，而不是栈回溯）。范围也在这里挡住：位置只占
    10 位，越界值会被 `build_set_position` 拒绝，早点报错更清楚。
    """
    out: Dict[int, int] = {}
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if ":" not in part:
            raise argparse.ArgumentTypeError(f"目标格式应为 id:位置，收到 {part!r}")
        id_text, value_text = part.split(":", 1)
        try:
            id_ = int(id_text)
            value = int(value_text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"id 与位置都要是整数，收到 {part!r}") from None
        if not P.SERVO_ID_MIN <= id_ <= P.SERVO_ID_MAX:
            raise argparse.ArgumentTypeError(
                f"舵机 ID 越界：{id_}（应为 {P.SERVO_ID_MIN}..{P.SERVO_ID_MAX}）")
        if not P.ADC_MIN <= value <= P.ADC_MAX:
            raise argparse.ArgumentTypeError(
                f"位置越界：ID {id_} 的目标 {value}（应为 {P.ADC_MIN}..{P.ADC_MAX}）")
        out[id_] = value
    if not out:
        raise argparse.ArgumentTypeError("目标位置为空")
    return out


#: ``--load-every`` 的统一说明
_LOAD_EVERY_HELP = ("每 N 步抽检一次实测负荷（默认 1 = 每步都查）；起步涌流只持续"
                    "几毫秒，取 3 可跳过它（阈值 100 左右即可，取 1 时阈值需 >150）")


def _load_every(args: argparse.Namespace, default: int = 1) -> int:
    """``--load-every`` → ``load_check_every``（未给出时用库默认 ``1``）。"""
    value = getattr(args, "load_every", None)
    return max(1, int(value)) if value is not None else default


def _emit(payload: object, args: argparse.Namespace) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(payload)


def _abort_on_load(exc: LoadLimitExceeded, args: argparse.Namespace) -> int:
    """软件限力中止：打印明细并返回专用退出码。"""
    if args.json:
        _emit({"aborted": True, "reason": "load_limit", **exc.as_dict()}, args)
    else:
        print(f"已中止：舵机 {exc.id} 实测负荷 {exc.load} 超过阈值 {exc.limit}"
              f"（第 {exc.step} 步，停在位置 {exc.position}）", file=sys.stderr)
    return EXIT_LOAD_LIMIT


def _port_hint(port: str, exc: BaseException) -> str:
    """串口打不开时的排查提示（Windows 与 Linux 的成因/处置不同）。"""
    head = f"串口 {port} 打不开：{type(exc).__name__}: {exc}\n"
    common = "  排查：python -m romanbo ports\n"
    if sys.platform.startswith("linux"):
        return (head
                + "  常见原因：① 权限不足（需要 dialout 组）；② 端口名不对"
                  "（Linux 下是 /dev/ttyUSB0 这类，不是 COM3）；③ 被别的程序占用。\n"
                + common
                + "  权限：sudo usermod -aG dialout $USER（之后需重新登录/重启会话）\n"
                + "  占用：sudo lsof /dev/ttyUSB0  或  sudo fuser -v /dev/ttyUSB0\n"
                + "  注意：POSIX 串口默认可被多个进程同时打开，本库用排他打开"
                  "（exclusive）避免互相打乱；若报占用，先关掉占用进程"
                  "（ModemManager 有时会抓串口：sudo systemctl stop ModemManager）")
    if sys.platform == "darwin":
        return (head
                + "  常见原因：端口名不对（macOS 下形如 /dev/tty.usbserial-XXXX）"
                  "或被别的程序占用。\n"
                + common
                + "  排查占用：lsof /dev/tty.usbserial-XXXX")
    return (head
            + "  常见原因：被其它上位机程序、串口助手或**残留 python 进程**占用；"
              "也可能是端口号不对或适配器没插。\n"
            + common
            + "  释放：关闭占用程序；若进程已退出但句柄没回收（Windows 偶发），"
              "拔插一次 USB 串口适配器即可释放。")


def cmd_ports(robot, args) -> int:
    """列出系统串口并**检测是否被占用**（离线命令，不需要 ``--port``）。"""
    try:
        # POSIX 下用排他打开探测，能检出「已被别人独占」的情况
        rows = list_serial_ports()
    except RuntimeError as exc:                              # 未安装 pyserial
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        _emit(rows, args)
        return 0
    if not rows:
        print("未发现串口设备（检查适配器是否插好；Linux 上通常形如 /dev/ttyUSB0）")
        return 0
    posix = not sys.platform.startswith("win")
    for row in rows:
        mark = "不可用" if row["busy"] else "可用"
        print(f"  {row['device']:<22} {mark:<6} {row['description']}")
        if row["busy"]:
            if posix:
                print("           打不开 → 多为权限问题（dialout 组）或已被占用；"
                      "查占用：sudo lsof <设备>")
            else:
                print("           被占用 → 关闭占用程序/串口助手，或拔插 USB 适配器释放")
    if posix:
        print("  提示：POSIX 串口默认可被多个进程同时打开，「可用」不代表独占；"
              "本库以 exclusive 方式打开。")
    return 0


def _show_io(robot: RomanboRobot, sent_before: int, args: argparse.Namespace) -> None:
    """``--frames`` 时打印本次交互的原始帧。"""
    if not args.frames:
        return
    for frame in robot.sent_frames[sent_before:]:
        print(f"  TX  {frame.hex(' ').upper()}")
    for resp in robot.drain(0.05):
        print(f"  RX  {resp.raw.hex(' ').upper()}    (id={resp.id} cmd=0x{resp.cmd:02X})")


# --------------------------------------------------------------------------- #
# 命令实现
# --------------------------------------------------------------------------- #

def cmd_webui(robot: Optional[RomanboRobot], args: argparse.Namespace) -> int:
    """启动本地可视化控制台：在浏览器里扫描、运动、读参数、看原始报文。"""
    from .webui import serve
    try:
        return serve(host=args.http_host, port=args.http_port,
                     serial_port=args.port, mock=args.mock,
                     baudrate=args.baudrate, ack_timeout=args.timeout,
                     open_browser=args.open_browser)
    except OSError as exc:                                   # 端口被占用等
        print(f"无法启动控制台：{exc}", file=sys.stderr)
        return 1


def cmd_selftest(robot: Optional[RomanboRobot], args: argparse.Namespace) -> int:
    passed, failed, details = golden.run(verbose=not args.json)
    if args.json:
        _emit({"passed": passed, "failed": failed, "details": details}, args)
    else:
        print(f"黄金向量自检: 通过 {passed} / 失败 {failed}")
    return 0 if failed == 0 else 1


def cmd_handshake(robot, args) -> int:
    result = robot.handshake(retries=args.retries, verbose=not args.json)
    payload = {
        "ok": result.ok,
        "model_ok": result.model_ok,
        "version_ok": result.version_ok,
        "attempts": result.attempts,
        "message": result.describe(),
        "model": result.model.raw.hex(" ").upper() if result.model else None,
        "version": result.version.raw.hex(" ").upper() if result.version else None,
        "port": robot.transport.name,
    }
    _emit(payload, args)
    return 0 if result.ok else 1


def cmd_scan(robot, args) -> int:
    ids = robot.scan(args.start, args.end, timeout=args.probe_timeout,
                     quarantine=args.quarantine)
    _emit({"found": ids, "count": len(ids),
           "probe_timeout": args.probe_timeout,
           "quarantine": args.quarantine}, args)
    return 0 if ids else 1


def cmd_read(robot, args) -> int:
    """批量读取位置。

    对**不存在的 ID** 只等一次超时（``retries=0``）：未指定 ``--ids`` 时默认
    扫 1..17，若逐个重试，15 个空 ID 会让整个命令多花十几秒。
    """
    ids = _parse_ids(args.ids) or list(range(1, 18))
    out: Dict[str, Dict[str, float]] = {}
    for id_ in ids:
        try:
            adc = robot.servo(id_).get_position(timeout=args.timeout, retries=0)
            out[str(id_)] = {"adc": adc, "angle": round(J.adc_to_angle(adc), 2)}
        except (TimeoutError, P.ProtocolError) as exc:
            out[str(id_)] = {"error": str(exc)}
    _emit(out, args)
    return 0


def cmd_teach(robot, args) -> int:
    """示教：读取当前姿态。

    不带 ``--file`` 时只打印姿态（原行为）；给出 ``--file`` 时把这一帧**追加**到
    会话文件，多次执行即累积成一段动作，再由 ``export`` 导出为 ``.rsc``。
    """
    ids = _parse_ids(args.ids) or list(range(1, 18))
    positions = robot.capture(ids, timeout=args.timeout)
    if not positions:
        print("没有读到任何舵机位置（检查 --ids 与接线）", file=sys.stderr)
        return 1
    payload = {
        "positions": {str(k): v for k, v in sorted(positions.items())},
        "angles": {str(k): round(J.adc_to_angle(v), 2) for k, v in sorted(positions.items())},
        "rsc_motor_state": [{"adc": adc, "direction": J.DIRECTION_NORMAL}
                            for adc in sorted(positions.values())],
    }
    if getattr(args, "file", None):
        session = _teach_load(args.file)
        count = int(session.get("motor_count", 17))
        frames = list(session.get("frames") or [])
        previous = list(frames[-1]["adc"]) if frames else [DEFAULT_ADC] * count
        adc = list(previous)
        for id_, value in positions.items():
            if 1 <= id_ <= count:
                adc[id_ - 1] = int(value)
        frames.append({"adc": adc, "period": int(args.period),
                       "led": [0] * count, "scene": int(args.scene)})
        session["frames"] = frames
        session.setdefault("motor_count", count)
        with open(args.file, "w", encoding="utf-8") as handle:
            json.dump(session, handle, ensure_ascii=False, indent=1)
        payload["frame_index"] = len(frames)
        payload["session_file"] = args.file
    _emit(payload, args)
    return 0


def cmd_move(robot, args) -> int:
    """多关节同步运动。

    ``--speed`` 走**步进逼近**，函数返回时最后一拍刚下发完，舵机仍在运动；
    因此直接回读会读到中间值。``--readback`` 会在返回前等待 ``--settle``
    秒（默认 0.3 s）再回读各关节实际位置与误差，使输出可直接作为判定依据。
    """
    targets = args.targets          # 已由 --targets 的 type= 解析成 {id: adc}
    if args.torque is not None:
        on = args.torque == "on"
        for id_ in targets:
            robot.servo(id_).torque(on)
        time.sleep(0.1)
    start = None
    if args.speed is not None and not args.no_capture:
        print(f"读取起始位置（{len(targets)} 个关节）…", flush=True)
        start = robot.capture(list(targets), timeout=args.timeout)
    try:
        robot.move(targets, period_ms=args.period, mode=args.mode,
                   speed_dps=args.speed, start=start, max_load=args.max_load,
                   load_check_every=_load_every(args),
                   level=_parse_level(args.level),
                   sync=not args.no_sync,
                   sync_id=None if args.per_id_sync else P.BROADCAST_ID)
    except LoadLimitExceeded as exc:
        return _abort_on_load(exc, args)

    settle = args.settle
    if settle is None:
        settle = DEFAULT_MOVE_SETTLE if args.readback else 0.0
    if settle > 0:
        time.sleep(settle)

    result: Dict[str, object] = {
        "targets": {str(k): v for k, v in sorted(targets.items())},
        "speed_dps": args.speed,
        "period_ms": None if args.speed is not None else args.period,
        "mode": args.mode,
        "settle_s": round(float(settle), 3),
        "peak_load": robot.last_peak_load,
    }
    if args.readback:
        actual = robot.capture(list(targets), timeout=args.timeout)
        result["readback"] = {str(k): v for k, v in sorted(actual.items())}
        result["error"] = {str(k): actual[k] - targets[k]
                           for k in sorted(actual) if k in targets}
    _emit(result, args)
    return 0


def cmd_jog(robot, args) -> int:
    servo = robot.servo(args.id)
    servo.torque(True)
    current = servo.get_position(timeout=args.timeout)
    period: Optional[int] = None if args.speed is not None else args.period
    if args.speed is None and period is None:
        period = 1000                      # 旧默认：1 s 走完
    try:
        if args.degrees is not None:
            # 相对转动：以刚读到的实际位置为基准（不要用机械中点）
            _, target = servo.rotate(args.degrees, period_ms=period,
                                     speed_dps=args.speed, current=current,
                                     level=_parse_level(args.level),
                                     max_load=args.max_load,
                                     load_check_every=_load_every(args))
        else:
            target = max(J.ADC_MIN, min(J.ADC_MAX, current + args.delta))
            if args.speed is not None:
                servo.move_at_speed(target, args.speed, current=current,
                                    level=_parse_level(args.level),
                                    max_load=args.max_load,
                                    load_check_every=_load_every(args))
            else:
                servo.set_position(target, period_ms=period)
    except LoadLimitExceeded as exc:
        return _abort_on_load(exc, args)
    moved_deg = abs(target - current) * J.RATIO_MAIN
    result = {
        "id": args.id,
        "from": current,
        "to": target,
        "delta_adc": target - current,
        "delta_deg": round((target - current) * J.RATIO_MAIN, 2),
        "period_ms": period,
        "speed_dps": args.speed,
        "expected_s": round(moved_deg / args.speed, 2) if args.speed else None,
        "peak_load": servo.last_peak_load,
    }
    _emit(result, args)
    return 0


def cmd_angle(robot, args) -> int:
    """绝对角度定位（以机械中点 512 为 0°）。不传 --degrees 时只显示当前角度。"""
    servo = robot.servo(args.id)
    if args.degrees is None:
        adc = servo.get_position(timeout=args.timeout)
        _emit({"id": args.id, "adc": adc,
               "angle": round(J.adc_to_angle(adc), 2)}, args)
        return 0
    servo.torque(True)
    period: Optional[int] = args.period
    try:
        if args.speed is not None:
            # 角速度模式：需要当前位置才能算角位移（步进逼近的起点）
            current = servo.get_position(timeout=args.timeout)
            target = servo.set_angle(args.degrees, speed_dps=args.speed,
                                     current=current,
                                     level=_parse_level(args.level),
                                     max_load=args.max_load,
                                     load_check_every=_load_every(args))
            wait_s = abs(target - current) * J.RATIO_MAIN / args.speed
            period = None
        else:
            if period is None:
                period = 800
            target = servo.set_angle(args.degrees, period_ms=period)
            wait_s = period / 1000.0
    except LoadLimitExceeded as exc:
        return _abort_on_load(exc, args)
    time.sleep(wait_s + 0.3)
    actual = servo.get_position(timeout=args.timeout)
    _emit({"id": args.id, "target": target, "readback": actual,
           "angle_cmd": args.degrees, "angle_read": round(J.adc_to_angle(actual), 2),
           "error": actual - target, "period_ms": period,
           "speed_dps": args.speed, "expected_s": round(wait_s, 2),
           "peak_load": servo.last_peak_load}, args)
    return 0


def cmd_sweep(robot, args) -> int:
    """往复运动演示（对应「反复动作」）。"""
    servo = robot.servo(args.id)
    low, high = args.low, args.high
    if low is None or high is None:
        center = servo.get_position(timeout=args.timeout)
        delta = int(round(args.degrees / J.RATIO_MAIN))
        low, high = center - delta, center + delta
        print(f"以当前位置 {center} 为中心往复 ±{args.degrees:g}° "
              f"→ ADC {low}..{high}")
    else:
        print(f"往复区间 ADC {low}..{high}（摆幅 {(high - low) / 2 * J.RATIO_MAIN:.1f}°）")

    if args.speed is not None:
        leg_seconds = abs(high - low) * J.RATIO_MAIN / args.speed
        speed_text = (f"角速度 {args.speed:g} °/s"
                      f"（单程约 {leg_seconds:.2f}s，按步进逼近）")
    else:
        leg_seconds = (args.period if args.period is not None else 800) / 1000.0
        speed_text = f"单程周期 {leg_seconds * 1000:.0f} ms"
    print(f"来回 {args.cycles} 次，{speed_text}"
          f"{'，循环直到 Ctrl+C' if args.loop else ''}\n")

    all_records: List[Dict[str, object]] = []

    def _report(record: Dict[str, object]) -> None:
        if args.json:
            return
        print(f"  步 {record['step']:>2}: 目标 {record['target']:>4}  "
              f"回读 {record['readback']}  误差 {record['error']}  "
              f"({record['elapsed_s']}s)", flush=True)

    try:
        while True:
            all_records.extend(servo.sweep(
                low, high, cycles=args.cycles, period_ms=args.period,
                speed_dps=args.speed, max_load=args.max_load,
                load_check_every=_load_every(args),
                level=_parse_level(args.level),
                readback=not args.no_readback, return_home=not args.no_return,
                on_step=_report))
            if not args.loop:
                break
    except KeyboardInterrupt:
        print("\n已手动停止（舵机保持在当前位置）")
    except LoadLimitExceeded as exc:
        return _abort_on_load(exc, args)

    if not args.no_return:
        time.sleep(0.2)
        final = servo.get_position(timeout=args.timeout)
        print(f"已回到起始位置附近: ADC {final}")

    errors = [abs(int(r["error"])) for r in all_records if r["error"] is not None]
    started = sum(float(r["elapsed_s"]) for r in all_records)
    summary = {
        "id": args.id,
        "range": [low, high],
        "speed_dps": args.speed,
        "leg_seconds": round(leg_seconds, 2),
        "steps": len(all_records),
        "max_error": max(errors) if errors else None,
        "avg_error": round(sum(errors) / len(errors), 2) if errors else None,
        "total_seconds": round(started, 1),
        "records": all_records,
    }
    if args.json:
        _emit(summary, args)
    else:
        print(f"\n合计 {summary['steps']} 步，最大误差 {summary['max_error']}，"
              f"平均误差 {summary['avg_error']}，耗时 {summary['total_seconds']} s")
    return 0


def cmd_torque(robot, args) -> int:
    ids = _parse_ids(args.ids) or list(range(1, 18))
    robot.torque_all(args.state == "on", ids)
    print(f"扭矩 {args.state.upper()}：{len(ids)} 个关节")
    return 0


def cmd_led(robot, args) -> int:
    ids = _parse_ids(args.ids) or [args.id]
    for id_ in ids:
        if args.color:
            red, green, blue = (bool(int(x)) for x in args.color.split(","))
            robot.servo(id_).set_led_color(red, green, blue)
        else:
            robot.servo(id_).set_led(args.value)
    print(f"LED 已下发到 {ids}")
    return 0


def cmd_config(robot, args) -> int:
    ids = _parse_ids(args.ids) or [args.id]
    out = {}
    for id_ in ids:
        cfg = robot.servo(id_).read_config(timeout=args.timeout)
        out[str(id_)] = cfg.as_dict()
    _emit(out, args)
    return 0


def cmd_pid(robot, args) -> int:
    """写 PID 并**回读确认**（0x07 保存式写入实测不可靠，库里已改为先 0x47）。"""
    for id_ in _parse_ids(args.ids) or [args.id]:
        try:
            got = robot.servo(id_).set_pid(args.p, args.i, args.d,
                                          save=not args.nosave,
                                          timeout=args.timeout)
        except P.ProtocolError as exc:
            print(f"舵机 {id_} PID 写入未生效：{exc}", file=sys.stderr)
            return 1
        print(f"舵机 {id_} PID 已生效并回读确认: "
              f"P={got[0]} I={got[1]} D={got[2]}"
              f"{'（已尝试写入闪存）' if not args.nosave else '（仅 RAM）'}")
    return 0


def cmd_limit(robot, args) -> int:
    """写位置限值并**回读确认**（会掉电保存；SET 类命令可能被静默丢弃）。"""
    for id_ in _parse_ids(args.ids) or [args.id]:
        try:
            got = robot.servo(id_).set_position_limit(args.min, args.max,
                                                     timeout=args.timeout)
        except P.ProtocolError as exc:
            print(f"舵机 {id_} 位置限值写入未生效：{exc}", file=sys.stderr)
            return 1
        print(f"舵机 {id_} 位置限值已生效并回读确认: "
              f"{got[0]}..{got[1]}（掉电保存，越限会被夹紧）")
    return 0


def cmd_param(robot, args) -> int:
    """通用的单字节参数下发（负荷/Margin/温度/偏移/周期/加速度）。"""
    ids = _parse_ids(args.ids) or [args.id]
    for id_ in ids:
        servo = robot.servo(id_)
        if args.what == "current-limit":
            servo.set_load_limit(args.value)
        elif args.what == "margin":
            servo.set_margin(args.value)
        elif args.what == "temp":
            servo.set_temp(args.value)
        elif args.what == "offset":
            servo.set_offset(args.value)
        elif args.what == "period":
            servo.set_period(args.value)
        elif args.what == "accelerate":
            servo.set_accelerate(args.value)
    warning = ""
    if args.what == "current-limit":
        warning = "（注意：实测回读不体现该写入，本机是否真限流未验证）"
    elif args.what == "accelerate":
        warning = "（注意：0x0E 码值为推断，协议文档未定义）"
    print(f"{args.what} = {args.value} 已下发到 {ids}{warning}")
    return 0


def cmd_wheel(robot, args) -> int:
    for id_ in _parse_ids(args.ids) or [args.id]:
        robot.servo(id_).wheel(args.speed,
                               direction=J.WHEEL_CCW if args.ccw else J.WHEEL_CW,
                               free=args.free, relative=args.relative)
    print(f"轮子模式: speed={args.speed} {'CCW' if args.ccw else 'CW'}")
    return 0


def cmd_sync(robot, args) -> int:
    robot.send(P.build_set_sync(args.id))
    print(f"已发送同步触发 (id={args.id})")
    return 0


def cmd_calib(robot, args) -> int:
    for id_ in _parse_ids(args.ids) or [args.id]:
        robot.servo(id_).set_calibration_currpos()
    print("已将当前位置设为零点")
    return 0


def cmd_set_id(robot, args) -> int:
    robot.servo(args.id).set_id(args.new_id)
    print(f"ID {args.id} → {args.new_id} 命令已下发（请用新 ID 重新连接验证）")
    return 0


def cmd_reset(robot, args) -> int:
    for id_ in _parse_ids(args.ids) or [args.id]:
        robot.servo(id_).reset()
    print("复位命令已下发")
    return 0


def cmd_load(robot, args) -> int:
    """读取**实测负荷**（``0x18``，界面「负荷」）。

    静止时恒为 0；运动时随加/减速跳动。``--watch N`` 连续采样 N 次。
    """
    ids = _parse_ids(args.ids) or [args.id]
    count = max(1, int(args.watch))
    samples: List[Dict[str, object]] = []

    def read_one(id_: int):
        try:
            return robot.servo(id_).get_load(timeout=args.timeout)
        except Exception as exc:                              # noqa: BLE001
            return f"读取失败: {exc}"

    try:
        for index in range(count):
            row: Dict[str, object] = {
                "t": round(index * args.interval, 3),
                **{str(i): read_one(i) for i in ids},
            }
            samples.append(row)
            if not args.json:
                print("  ".join(f"id{k}={v}" for k, v in row.items()), flush=True)
            if index + 1 < count:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止采样")

    if count > 1:
        _emit({"samples": samples}, args)
    elif samples:
        _emit(samples[0], args)
    return 0


def cmd_play(robot, args) -> int:
    project = RscProject.load(args.file)
    frames = list(project.frames(scene=args.scene))
    if not frames:
        print("该文件没有可播放的动作帧", file=sys.stderr)
        return 1
    wanted = _parse_ids(args.ids) or sorted(
        {i for frame in frames for i in frame.targets(id_offset=robot.id_offset)})
    if args.speed is not None:
        print(f"播放 {project.project_name}: {len(frames)} 帧，"
              f"角速度 {args.speed:g} °/s（每段按角速度步进逼近，文件 Period 被忽略）")
    else:
        print(f"播放 {project.project_name}: {len(frames)} 帧，"
              f"按文件 Period（周期倍率 {args.speed_scale:g}）")
    if len(wanted) < frames[0].motor_count:
        print(f"只驱动 {len(wanted)} 个在线关节: {wanted}"
              f"（文件是 {frames[0].motor_count} 通道整机动作；"
              f"向不存在的 ID 发帧会触发 0.4 s 总线静默期）")
    if args.torque is not None:
        robot.torque_all(args.torque == "on", wanted)

    start_positions = None
    if args.speed is not None and not args.no_capture:
        joint_ids = sorted(wanted)
        print(f"读取起始位置（{len(joint_ids)} 个关节，用于第一帧的角位移）…",
              flush=True)
        start_positions = robot.capture(joint_ids, timeout=args.timeout)
        if start_positions:
            print("  起始位置 "
                  + ", ".join(f"{k}={v}" for k, v in sorted(start_positions.items())))
        else:
            print("  起始位置读取失败，首帧将退回文件 Period", file=sys.stderr)
    stop_event = threading.Event()

    def _on_frame(frame):
        if args.frames:
            print(f"  frame scene={frame.scene_index} idx={frame.motion_index} "
                  f"period={frame.period_ms}ms")

    try:
        played = robot.play(frames, loop=args.loop, speed_dps=args.speed,
                            speed_scale=args.speed_scale, mode=args.mode,
                            start_positions=start_positions,
                            ids=wanted,
                            level=_parse_level(args.level),
                            max_load=args.max_load,
                            load_check_every=_load_every(args),
                            on_frame=_on_frame, stop_event=stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        print("\n已中断")
        return 130
    except LoadLimitExceeded as exc:
        return _abort_on_load(exc, args)
    print(f"完成，共播放 {played} 帧")
    return 0


def _teach_load(path: str) -> Dict[str, object]:
    """读取示教会话文件；不存在时返回空会话。"""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        # 会话名取**文件名**（不含目录与扩展名），否则场景名会变成整条路径
        stem = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return {"name": stem, "motor_count": 17, "frames": []}


def cmd_export(robot, args) -> int:
    """把示教会话导出为 ``.rsc`` 工程文件（离线命令）。"""
    session = _teach_load(args.file)
    frames = list(session.get("frames") or [])
    if not frames:
        print(f"{args.file} 里还没有帧，先执行 teach", file=sys.stderr)
        return 1
    count = int(session.get("motor_count", 17))
    # 场景名前缀取**会话文件名**（不含目录/扩展名），避免把路径写进 SceneName
    stem = args.file.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    path = write_project(args.out, frames, motor_count=count,
                         scene_prefix=stem or "taught",
                         home=list(frames[0]["adc"]))
    print(f"已导出 {path}：{len(frames)} 帧 / {count} 通道 / "
          f"场景 {sorted({int(f['scene']) for f in frames})}")
    print(f"  播放：python -m romanbo --port COMx play {args.out} "
          f"--ids 8,10 --speed 15")
    return 0


def cmd_info(robot, args) -> int:
    project = RscProject.load(args.file)
    _emit(project.summary(), args)
    return 0


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #

def _add_global_options(target: argparse.ArgumentParser, *,
                        suppress_defaults: bool = False) -> None:
    """注册全局选项（主解析器与各子命令共用，使参数位置更宽松）。

    ``suppress_defaults=True`` 用于挂到子命令上的那一份：不设默认值，避免
    子解析器的默认值把主解析器已经解析到的值（例如写在子命令前的
    ``--mock``）覆盖掉。
    """
    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress_defaults else value

    target.add_argument("-p", "--port", default=default(None),
                        help="串口号：Windows 形如 COM3，Linux 形如 /dev/ttyUSB0，"
                        "macOS 形如 /dev/tty.usbserial-XXXX")
    target.add_argument("--mock", action="store_true", default=default(False),
                        help="使用离线模拟器（无需硬件）")
    target.add_argument("--baudrate", type=int,
                        default=default(P.BAUDRATE_DEFAULT))
    target.add_argument("--timeout", type=float, default=default(0.3),
                        help="回包超时（秒）")
    target.add_argument("--json", action="store_true", default=default(False),
                        help="以 JSON 输出")
    target.add_argument("--frames", action="store_true", default=default(False),
                        help="打印收发的原始帧")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="romanbo",
        description="ROMANBO 舵机/整机控制命令行",
    )
    _add_global_options(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    # 子命令也挂同一组全局选项，因此 `romanbo --mock scan` 与
    # `romanbo scan --mock` 都可用
    common = argparse.ArgumentParser(add_help=False)
    _add_global_options(common, suppress_defaults=True)
    _original_add_parser = sub.add_parser

    def _add_parser(name=None, **kwargs):               # type: ignore[no-untyped-def]
        kwargs.setdefault("parents", [common])
        return _original_add_parser(name, **kwargs)

    sub.add_parser = _add_parser                        # type: ignore[assignment]

    p = sub.add_parser("selftest", help="离线校验报文编码（黄金向量）")
    p.set_defaults(func=cmd_selftest, need_robot=False)

    p = sub.add_parser("ports", help="列出系统串口并检测是否被占用（离线）")
    p.set_defaults(func=cmd_ports, need_robot=False)

    p = sub.add_parser("export", help="把示教会话导出为 .rsc 工程文件（离线）")
    p.add_argument("--file", default="taught.json")
    p.add_argument("--out", default="taught.rsc")
    p.set_defaults(func=cmd_export, need_robot=False)

    p = sub.add_parser("handshake", help="连接自检（型号 + 固件版本）")
    p.add_argument("--retries", type=int, default=2)
    p.set_defaults(func=cmd_handshake)

    p = sub.add_parser("scan", help="扫描在线舵机 ID")
    p.add_argument("--start", type=int, default=P.SERVO_ID_MIN)
    p.add_argument("--end", type=int, default=P.SERVO_ID_MAX)
    p.add_argument("--probe-timeout", type=float, default=P.SCAN_PROBE_TIMEOUT,
                   help="每个 ID 的等待时间（秒）；实测应答只要 10~20ms")
    p.add_argument("--quarantine", type=float, default=P.SCAN_QUARANTINE,
                   help="探测失败后等待的总线静默期（秒）；实测需约 0.4s，"
                        "否则后面会整段漏扫")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("read", help="读取舵机位置")
    p.add_argument("--ids", help="例如 1,2,3 或 1-6")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("teach", help="示教：读取当前姿态（加 --file 则累积为动作序列）")
    p.add_argument("--ids", help="例如 8,10；默认 1..17（未应答的通道沿用上一帧）")
    p.add_argument("--period", type=int, default=500,
                   help="写入会话时的 Period（ms）")
    p.add_argument("--scene", type=int, default=0, help="写入会话时的场景号")
    p.add_argument("--file", default=None,
                   help="示教会话文件；给出即把当前姿态追加为一帧（供 export 使用）")
    p.set_defaults(func=cmd_teach)

    p = sub.add_parser("move", help="多关节同步运动")
    p.add_argument("--targets", required=True, type=_parse_targets,
                   help="例如 1:600,2:480（位置 0..1023）")
    p.add_argument("--period", type=int, default=500, help="运动周期 ms")
    p.add_argument("--speed", type=float, default=None,
                   help="**实际角速度**（度/秒）；给出时忽略 --period，按步进逼近")
    p.add_argument("--level", default=None,
                   help="出力档位 H=最大/M=中/L=小/W=轮子（也可写 0..3）")
    p.add_argument("--max-load", type=int, default=None,
                   help="软件限力阈值（实测负荷）；超限即停止并返回退出码 4")
    p.add_argument("--load-every", type=int, default=None, metavar="N",
                   help=_LOAD_EVERY_HELP)
    p.add_argument("--no-capture", action="store_true",
                   help="--speed 时不预读起始位置（缺失的关节直接跳到目标）")
    p.add_argument("--torque", choices=("on", "off"), default="on")
    p.add_argument("--mode", choices=("position", "next"), default="position",
                   help="position=SET_PERIOD+SET_POSITION（实测可用）；"
                        "next=预置 0x21 + 同步 0x20（实测舵机不响应）")
    p.add_argument("--no-sync", action="store_true",
                   help="仅 mode=next 有效：不发送同步触发")
    p.add_argument("--per-id-sync", action="store_true",
                   help="仅 mode=next 有效：逐 ID 触发同步")
    p.add_argument("--settle", type=float, default=None,
                   help=f"下发后等待到位再返回的秒数"
                        f"（默认：--readback 时 {DEFAULT_MOVE_SETTLE}，否则不等待）")
    p.add_argument("--readback", action="store_true",
                   help="返回前回读各关节实际位置，并输出与目标的误差")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("jog", help="单关节点动（可按 ADC 或角度）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--delta", type=int, default=None, help="相对位移（ADC 单位）")
    p.add_argument("--degrees", type=float, default=None,
                   help="相对转动角度（度），例如 30；与 --delta 二选一")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--period", type=int, default=None,
                   help="走完时间 ms（默认 1000）")
    g.add_argument("--speed", type=float, default=None,
                   help="**实际角速度**（度/秒）；舵机忽略周期参数，按步进逼近实现")
    p.add_argument("--level", default=None,
                   help="出力档位 H=最大/M=中/L=小/W=轮子（也可写 0..3）；见 SERVO_SPEC §5.2.1")
    p.add_argument("--max-load", type=int, default=None,
                   help="软件限力阈值（实测负荷 0..255）；超限即停止，退出码 4")
    p.add_argument("--load-every", type=int, default=None, metavar="N",
                   help=_LOAD_EVERY_HELP)
    p.set_defaults(func=cmd_jog)

    p = sub.add_parser("angle", help="绝对角度定位（中点 512 为 0°）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--degrees", type=float, default=None,
                   help="目标角度，例如 0 表示回到中点；不传则只显示当前角度")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--period", type=int, default=None,
                   help="走完时间 ms（默认 800）")
    g.add_argument("--speed", type=float, default=None,
                   help="**实际角速度**（度/秒）；会先回读当前位置，按步进逼近实现")
    p.add_argument("--level", default=None,
                   help="出力档位 H/M/L/W（也可写 0..3）")
    p.add_argument("--max-load", type=int, default=None,
                   help="软件限力阈值（实测负荷 0..255）；超限即停止，退出码 4")
    p.add_argument("--load-every", type=int, default=None, metavar="N",
                   help=_LOAD_EVERY_HELP)
    p.set_defaults(func=cmd_angle)

    p = sub.add_parser("sweep", help="往复运动演示（对应「反复动作」）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--degrees", type=float, default=30.0,
                   help="以当前位置为中心的摆幅角度（默认 30°）")
    p.add_argument("--low", type=int, default=None, help="下限 ADC（替代 --degrees）")
    p.add_argument("--high", type=int, default=None, help="上限 ADC（替代 --degrees）")
    p.add_argument("--cycles", type=int, default=3, help="来回次数")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--period", type=int, default=None,
                   help="单程运动周期 ms（默认 800）")
    g.add_argument("--speed", type=float, default=None,
                   help="**实际角速度**（度/秒）；两个方向都按步进逼近实现")
    p.add_argument("--level", default=None, help="出力档位 H/M/L/W（也可写 0..3）")
    p.add_argument("--max-load", type=int, default=None,
                   help="软件限力阈值（实测负荷 0..255）；超限即停止，退出码 4")
    p.add_argument("--load-every", type=int, default=None, metavar="N",
                   help=_LOAD_EVERY_HELP)
    p.add_argument("--no-readback", action="store_true",
                   help="不逐步回读位置（更快，但无法验证）")
    p.add_argument("--no-return", action="store_true",
                   help="结束后不回到起始位置")
    p.add_argument("--loop", action="store_true", help="无限循环直到 Ctrl+C")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("torque", help="扭矩开关")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("--ids")
    p.set_defaults(func=cmd_torque)

    p = sub.add_parser("led", help="舵机 LED")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--value", type=int, default=7, help="原始值（内部左移 5 位）")
    p.add_argument("--color", help="三色：红,绿,蓝 取 0/1，例如 1,0,1")
    p.set_defaults(func=cmd_led)

    p = sub.add_parser("config", help="读取舵机参数")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("load", help="读取实测负荷（0x18，不是限制值）")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--watch", type=int, default=1, help="连续采样次数")
    p.add_argument("--interval", type=float, default=0.2, help="采样间隔秒")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("pid", help="设置 PID")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--p", type=int, default=50)
    p.add_argument("--i", type=int, default=0)
    p.add_argument("--d", type=int, default=5)
    p.add_argument("--nosave", action="store_true",
                   help="不写入闪存（0x47；实测这条才可靠）")
    p.set_defaults(func=cmd_pid)

    p = sub.add_parser("limit", help="设置位置限值")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--min", type=int, default=0)
    p.add_argument("--max", type=int, default=1023)
    p.set_defaults(func=cmd_limit)

    p = sub.add_parser("param", help="单字节参数（负荷/Margin/温度/偏移/周期/加速度）")
    p.add_argument("what", choices=("current-limit", "margin", "temp", "offset",
                                    "period", "accelerate"))
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--value", type=int, required=True)
    p.set_defaults(func=cmd_param)

    p = sub.add_parser("wheel", help="轮子模式")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.add_argument("--speed", type=int, default=100)
    p.add_argument("--ccw", action="store_true")
    p.add_argument("--free", action="store_true", help="空转")
    p.add_argument("--relative", action="store_true")
    p.set_defaults(func=cmd_wheel)

    p = sub.add_parser("sync", help="发送同步触发")
    p.add_argument("--id", type=int, default=P.BROADCAST_ID)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("calib", help="把当前位置设为零点")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.set_defaults(func=cmd_calib)

    p = sub.add_parser("set-id", help="修改舵机 ID")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--new-id", type=int, required=True)
    p.set_defaults(func=cmd_set_id)

    p = sub.add_parser("reset", help="舵机复位")
    p.add_argument("--id", type=int, default=1)
    p.add_argument("--ids")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("play", help="播放 .rsc 动作")
    p.add_argument("file")
    p.add_argument("--scene", type=int, default=None)
    p.add_argument("--ids",
                   help="只驱动这些舵机，例如 8,10（默认为文件里的全部通道；"
                        "总线上只有部分关节时务必指定，否则向不存在的 ID 发帧会"
                        "触发 0.4 s 总线静默期）")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--speed", type=float, default=None,
                   help="**实际角速度**（度/秒）；给出时忽略文件 Period，"
                        "每段按该角速度步进逼近")
    p.add_argument("--speed-scale", type=float, default=1.0,
                   help="周期倍率，仅在未给 --speed 时生效；>1 更慢")
    p.add_argument("--no-capture", action="store_true",
                   help="不预读起始位置（首帧将退回文件 Period）")
    p.add_argument("--max-load", type=int, default=None,
                   help="软件限力阈值（实测负荷 0..255）；超限即停止，退出码 4")
    p.add_argument("--load-every", type=int, default=None, metavar="N",
                   help=_LOAD_EVERY_HELP)
    p.add_argument("--level", default=None, help="出力档位 H/M/L/W（也可写 0..3）")
    p.add_argument("--torque", choices=("on", "off"), default="on")
    p.add_argument("--mode", choices=("position", "next"), default="position",
                   help="下发方式，见 move 命令")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("info", help="查看 .rsc 工程摘要")
    p.add_argument("file")
    p.set_defaults(func=cmd_info, need_robot=False)

    p = sub.add_parser("webui", help="启动本地可视化控制/调试界面（浏览器操作）",
                       description="启动一个只监听本机的 Web 控制台：扫描舵机、"
                                   "实时读数、单/多关节运动、参数读写、原始报文日志。"
                                   "默认地址 http://127.0.0.1:8765，需带令牌链接打开。")
    p.add_argument("--http-host", default="127.0.0.1",
                   help="HTTP 监听地址（默认只监听本机回环）")
    p.add_argument("--http-port", type=int, default=8765,
                   help="HTTP 端口（默认 8765；0 = 由系统分配）")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   help="启动后自动打开浏览器")
    p.set_defaults(func=cmd_webui, need_robot=False)

    return parser


def _ensure_utf8_stdio() -> None:
    """确保 stdout/stderr 能输出中文（Windows 管道下是区域编码）。

    Windows 上输出被**重定向**时，Python 按区域编码（en-US 为 cp1252）写管道，
    编码不了中文的 ``print`` 会抛 ``UnicodeEncodeError`` 直接中断命令——CI 里的
    ``python -m romanbo selftest`` 曾因此在 windows-latest 上稳定失败
    （基准向量名含"[协议推算]"）。

    这里只在当前编码**真的表示不了中文**时才切换，避免影响用户的 locale 选择；
    控制台场景 Python 已用 UTF-8（``_WindowsConsoleIO``），因此是 no-op。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:                  # 已被替换成非 TextIOWrapper（测试捕获）
            continue
        try:
            "中文".encode(getattr(stream, "encoding", None) or "utf-8")
            continue                             # 已能表示中文，不动用户的设置
        except (LookupError, UnicodeEncodeError):
            pass
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):            # pragma: no cover - 罕见的环境限制
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in ("selftest", "info", "ports", "export", "webui"):
        return args.func(None, args)

    try:
        if args.mock:
            robot = RomanboRobot(transport=MockTransport(servo_ids=range(1, 18)),
                                 ack_timeout=args.timeout)
        else:
            if not args.port:
                parser.error("需要 --port COMx（或使用 --mock）")
            robot = RomanboRobot(args.port, baudrate=args.baudrate,
                                 ack_timeout=args.timeout)

        with robot:
            before = len(robot.sent_frames)
            rc = args.func(robot, args)
            _show_io(robot, before, args)
    except OSError as exc:
        # pyserial 的 SerialException 继承自 OSError；PermissionError 也走这里
        print(_port_hint(args.port or "", exc), file=sys.stderr)
        return EXIT_PORT
    return rc


def run() -> None:  # pragma: no cover
    raise SystemExit(main())


__all__ = ["main", "build_parser", "run"]
