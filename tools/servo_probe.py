"""单舵机联调 / 回包取样工具（不依赖高层解码，只做「原样收发 + 记录」）。

用途
----
库中 `Serve.get_position()` 等**回包解码**尚未完全确定，需要用真机
抓一次原始回包才能定标。本工具把每条命令的请求/应答**原样十六进制**打印并
写入日志文件，供后续把解码逻辑改成实测值。

接线与安全
----------
1. 只连**一个舵机**时，总线仍需供电（控制器板或外接电源），
   并且舵机链末端要保持终端模块（提示 "Loop chip / End chip"）。
2. 运行前请**关闭其它占用串口的程序**，否则串口被占用会打开失败。
3. 默认只做**只读查询**；`move` 子命令必须显式给出 ``--delta``，
   且默认限制在 ±60 ADC，先确认不会撞限位/不会让机器人摔倒。
4. **``0x0F``（GetMotionPeriod）默认不发送**：实测无数据帧也会被固件当成
   ``SetPositionLimit``，用解析缓冲残值改写位置限值（且无应答）。确需发送请加
   ``--unsafe-0f``，并在之前用 ``0x1A`` 记下原值。

用法::

    python tools/servo_probe.py ports
    python tools/servo_probe.py COM3 scan
    python tools/servo_probe.py COM3 probe --id 1
    python tools/servo_probe.py COM3 listen --seconds 10
    python tools/servo_probe.py COM3 move --id 1 --delta 15 --period 800

日志默认写入**仓库根目录**下的 ``logs/probe-<时间戳>.log``（该目录已被 ``.gitignore``
忽略，属临时产物）；需要长期留存的证据请整理进 ``docs/evidence/``（已入库，见
``docs/README.md``）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
import time
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from romanbo import protocol as P                     # noqa: E402
from romanbo.servo import Servo                        # noqa: E402
from romanbo.transport import SerialTransport          # noqa: E402

LOG_DIR = ROOT / "logs"

#: 可读参数清单：(说明, 构造函数 ``f(id) -> bytes``)
READABLE: List[Tuple[str, object]] = [
    ("GetModel       (0x01)", lambda i: P.build_get_model()),
    ("GetVersion     (0x02)", lambda i: P.build_get_version()),
    ("Status         (0x05)", lambda i: P.build_status(i)),
    ("GetMotor       (0x16)", lambda i: P.build_get_motor(i)),
    ("GetPosition    (0x14)", lambda i: P.build_get_position(i)),
    ("GetPID         (0x12)", lambda i: P.build_get_pid(i)),
    ("GetTemp        (0x13)", lambda i: P.build_get_temp(i)),
    ("GetMargin      (0x17)", lambda i: P.build_get_margin(i)),
    ("GetCurrentLimit(0x18)", lambda i: P.build_get_current_limit(i)),
    ("GetAccelerate  (0x19)", lambda i: P.build_get_accelerate(i)),
    ("GetPositionLim (0x1A)", lambda i: P.build_get_position_limit(i)),
    # 0x0F（GetMotionPeriod）**故意不在默认清单里**：实测无数据帧也会被固件当成
    # SetPositionLimit，用解析缓冲残值改写位置限值（且无应答）。确需发送请加
    # ``--unsafe-0f``（会提示先用 0x1A 记下原值）。
    ("GetCalibration (0x15)", lambda i: P.build_get_calibration(i)),
    ("GetFactoryTest (0x7B)", lambda i: P.build_get_factory_test(i)),
]


class Probe:
    """带原样日志的串口探针。"""

    def __init__(self, port: str, baudrate: int, log_path: pathlib.Path) -> None:
        self.port = port
        self.transport = SerialTransport(port, baudrate=baudrate, read_timeout=0.02)
        self.parser = P.FrameParser()
        self._log = log_path.open("w", encoding="utf-8")

    # -- 日志 -------------------------------------------------------------- #

    def out(self, text: str) -> None:
        print(text)
        if not self._log.closed:
            self._log.write(text + "\n")
            self._log.flush()

    def close(self) -> None:
        self.transport.close()
        self._log.close()

    # -- 收发 -------------------------------------------------------------- #

    def rx(self, wait: float) -> Tuple[bytes, List[P.Response]]:
        """读取 ``wait`` 秒内到达的字节；收到数据后按静默间隔提前结束。"""
        deadline = time.monotonic() + wait
        raw = bytearray()
        frames: List[P.Response] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            chunk = self.transport.read_available(min(0.02, remaining))
            if chunk:
                raw.extend(chunk)
                frames.extend(self.parser.feed(chunk))
                deadline = time.monotonic() + 0.06          # 静默 60ms 即认为结束
        if raw:
            parsed = ", ".join(f"id={f.id} cmd=0x{f.cmd:02X} data={f.data.hex(' ').upper()}"
                               for f in frames)
            self.out(f"RX  {bytes(raw).hex(' ').upper()}")
            if parsed:
                self.out(f"    -> {parsed}")
            else:
                # 帧未通过校验/长度不匹配时也要把结构打出来，便于定位
                preview = bytes(raw)[:16]
                self.out(f"    -> 未解析成帧（前 {len(preview)} 字节: "
                         f"{preview.hex(' ').upper()}）")
        else:
            self.out("RX  <超时无响应>")
        return bytes(raw), frames

    def tx(self, frame: bytes, label: str = "", wait: float = 0.25
           ) -> Tuple[bytes, List[P.Response]]:
        self.out(f"TX  {label:<24} {frame.hex(' ').upper()}")
        self.transport.write(frame)
        return self.rx(wait)

    # -- 子命令 ------------------------------------------------------------ #

    def cmd_scan(self, start: int, end: int, wait: float) -> List[int]:
        found: List[int] = []
        self.out(f"== 扫描 ID {start}..{end} ==")
        for id_ in range(start, end + 1):
            _, frames = self.tx(P.build_status(id_), f"Status(id={id_})", wait)
            if any(f.id == id_ for f in frames):
                found.append(id_)
                self.out(f"   [命中] 舵机 ID = {id_}")
        self.out(f"== 扫描结果: {found if found else '无响应'} ==")
        return found

    def cmd_probe(self, id_: int, wait: float, unsafe_0f: bool = False) -> None:
        self.out(f"== 逐条查询 ID {id_}（原样回包，用于标定解码）==")
        items = list(READABLE)
        if unsafe_0f:
            self.out("!! 已启用 --unsafe-0f：即将发送 0x0F。实测该帧会被固件当成"
                     " SetPositionLimit，用缓冲残值改写位置限值（且无应答）；"
                     "请确认已记录 0x1A 的原值以便复原。")
            items.append(("GetMotionPeriod(0x0F)", lambda i: P.build_get_motion_period(i)))
        for label, builder in items:
            self.tx(builder(id_), label, wait)          # type: ignore[operator]
            time.sleep(0.05)
        self.out("== 查询结束；请把上面的 RX 行发回用于解码定标 ==")

    def cmd_detect(self, ids: Sequence[int], wait: float) -> None:
        """探测矩阵：控制器是否在线 + 每种查询命令谁能得到应答。

        用于区分「命令选错」与「接线/供电/终端模块」问题。
        """
        self.out("== [A] 控制器板探测（ID=0，GetModel/GetVersion 走 LEN=0 旧格式）==")
        self.tx(P.build_get_model(), "GetModel(控制器)", wait)
        self.tx(P.build_get_version(), "GetVersion(控制器)", wait)

        self.out("== [B] 舵机探测矩阵 ==")
        for id_ in ids:
            probes = [
                ("Status         0x05", P.build_status(id_)),
                ("GetMotor       0x16", P.build_get_motor(id_)),
                ("GetPosition    0x14", P.build_get_position(id_)),
                # 下面两条用「严格帧」把 GetModel/GetVersion 发给具体舵机 ID
                # （这类查询只发给 ID=0）
                ("GetModel(严格) 0x01", P.make_frame(P.ServoCmd.GET_MODEL, id_)),
                ("GetVersion(严格)0x02", P.make_frame(P.ServoCmd.GET_VERSION, id_)),
            ]
            self.out(f"-- ID {id_}")
            for label, frame in probes:
                self.tx(frame, label, wait)

    def cmd_send(self, id_: int, cmd: int, data: bytes, times: int,
                 wait: float) -> None:
        """发送任意命令（原始帧），用于逐条比对回包。"""
        for index in range(max(1, times)):
            frame = P.make_frame(cmd, id_, data)
            self.tx(frame, f"#{index + 1} cmd=0x{cmd:02X} id={id_}", wait)
            time.sleep(0.1)

    def cmd_listen(self, seconds: float) -> None:
        self.out(f"== 纯监听 {seconds:.0f} 秒（不发任何数据）==")
        self.rx(seconds)
        self.out("== 监听结束 ==")

    def cmd_move(self, id_: int, delta: int, period: int, wait: float,
                 return_home: bool, torque_off: bool) -> None:
        self.out(f"== 单关节小步测试 ID={id_} delta={delta:+d} period={period}ms ==")

        self.out("-- [1] 读取初始位置")
        self.tx(P.build_get_position(id_), "GetPosition(前)", wait)
        self.rx(0.05)
        raw_before, _ = self.tx(P.build_get_position(id_), "GetPosition(前2)", wait)
        try:
            before = Servo._decode_position(_last_data(raw_before))
        except Exception as exc:                                  # noqa: BLE001
            self.out(f"   位置解码失败({exc})，为避免误动作中止。")
            return
        self.out(f"   初始位置(按当前解码) = {before}")

        target = max(P.ADC_MIN, min(P.ADC_MAX, before + delta))
        self.out(f"-- [2] 开扭矩")
        self.tx(P.build_set_torque(id_, 1), "SetTorque(on)", wait)

        self.out(f"-- [3] 缓慢移动到 {target}（周期 {period} ms）")
        self.tx(P.build_set_period(id_, period), "SetPeriod", wait)
        self.tx(P.build_set_position(id_, target, torque=1, relative=0),
                "SetPosition(目标)", wait)
        time.sleep(period / 1000.0 + 0.4)
        self.tx(P.build_get_position(id_), "GetPosition(移动后)", wait)

        if return_home:
            self.out(f"-- [4] 回到 {before}")
            self.tx(P.build_set_position(id_, before, torque=1, relative=0),
                    "SetPosition(回位)", wait)
            time.sleep(period / 1000.0 + 0.4)
            self.tx(P.build_get_position(id_), "GetPosition(回位后)", wait)

        if torque_off:
            self.out("-- [5] 关扭矩（关节将失去保持力）")
            self.tx(P.build_set_torque(id_, 0), "SetTorque(off)", wait)
        self.out("== 测试结束 ==")


def _last_data(raw: bytes) -> bytes:
    """从原始字节里抓最后一个完整帧的数据段（尽力而为，供人工核对）。"""
    frames = P.FrameParser().feed(raw)
    return frames[-1].data if frames else b""


def cmd_ports() -> int:
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError:
        print("需要 pyserial：pip install pyserial")
        return 1
    rows = list(list_ports.comports())
    if not rows:
        print("未发现串口")
        return 1
    print(f"{'设备':<10} {'描述':<28} VID:PID      序列号")
    for port in rows:
        vid_pid = (f"{port.vid:04X}:{port.pid:04X}"
                   if port.vid is not None and port.pid is not None else "-")
        print(f"{port.device:<10} {str(port.description)[:28]:<28} {vid_pid:<12} {port.serial_number or ''}")
    return 0


def _add_common_options(target: argparse.ArgumentParser, *,
                        suppress_defaults: bool = False) -> None:
    """公共选项（主解析器与各子命令共用，参数位置更宽松）。"""
    def default(value: object) -> object:
        return argparse.SUPPRESS if suppress_defaults else value

    target.add_argument("--baud", type=int,
                        default=default(P.BAUDRATE_DEFAULT))
    target.add_argument("--wait", type=float, default=default(0.8),
                        help="每条命令的等待时间（秒）；实测应答只要 10~20ms，"
                             "但无设备 ID 之后有约 0.4s 总线静默期")
    target.add_argument("--log", default=default(None), help="日志文件路径")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ROMANBO 单舵机联调 / 回包取样工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("port", nargs="?", help="串口号，例如 COM3（ports 子命令可省略）")
    _add_common_options(parser)
    sub = parser.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common, suppress_defaults=True)
    _original_add_parser = sub.add_parser

    def _add_parser(name=None, **kwargs):               # type: ignore[no-untyped-def]
        kwargs.setdefault("parents", [common])
        return _original_add_parser(name, **kwargs)

    sub.add_parser = _add_parser                        # type: ignore[assignment]

    sub.add_parser("ports", help="列出本机串口（不打开端口）")

    p = sub.add_parser("scan", help="扫描在线的舵机 ID")
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--end", type=int, default=16)

    p = sub.add_parser("probe", help="对指定 ID 逐条查询并打印原始回包")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--unsafe-0f", action="store_true",
                   help="危险：额外发送 0x0F。实测会被固件当成 SetPositionLimit，"
                        "用缓冲残值改写位置限值且无应答，仅在明确知道后果时使用")

    p = sub.add_parser("detect", help="探测矩阵：控制器 + 各查询命令的应答情况")
    p.add_argument("--ids", default="1,2,3", help="要尝试的舵机 ID，例如 1,2,3")

    p = sub.add_parser("send", help="发送任意原始命令并打印回包")
    p.add_argument("--id", type=int, default=10)
    p.add_argument("--cmd", required=True, help="命令码，支持 0x1A 或 26")
    p.add_argument("--data", default="", help="数据段十六进制，例如 000103FF")
    p.add_argument("--times", type=int, default=1)

    p = sub.add_parser("listen", help="只监听、不发送")
    p.add_argument("--seconds", type=float, default=10.0)

    p = sub.add_parser("move", help="小步移动测试（务必先确认安全）")
    p.add_argument("--id", type=int, required=True)
    p.add_argument("--delta", type=int, required=True,
                   help="相对当前位置的偏移（ADC，建议 5~20）")
    p.add_argument("--period", type=int, default=800, help="运动周期 ms")
    p.add_argument("--no-return", action="store_true", help="结束后不回原位")
    p.add_argument("--keep-torque", action="store_true", help="结束后不关扭矩")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in (None, "ports"):
        return cmd_ports()

    if not args.port:
        parser.error("需要串口号，例如 COM3")

    if args.command == "move" and abs(args.delta) > 60:
        print(f"拒绝执行：delta={args.delta} 超过安全上限 ±60（如确认安全请改脚本）")
        return 2

    LOG_DIR.mkdir(exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = pathlib.Path(args.log) if args.log else LOG_DIR / f"probe-{stamp}.log"

    probe = Probe(args.port, args.baud, log_path)
    probe.out(f"# 端口 {args.port} @ {args.baud} 8N1  日志 {log_path}")
    print(f"提示：请先关闭其它占用串口的程序；单舵机测试需给总线供电。日志 -> {log_path}\n")
    try:
        probe.transport.open()
        probe.out(f"# 串口已打开: {args.port}")
        if args.command == "scan":
            probe.cmd_scan(args.start, args.end, args.wait)
        elif args.command == "detect":
            ids = [int(x) for x in str(args.ids).replace(" ", "").split(",") if x]
            probe.cmd_detect(ids, args.wait)
        elif args.command == "probe":
            probe.cmd_probe(args.id, args.wait, unsafe_0f=args.unsafe_0f)
        elif args.command == "send":
            probe.cmd_send(args.id, int(str(args.cmd), 0),
                           bytes.fromhex(str(args.data)) if args.data else b"",
                           args.times, args.wait)
        elif args.command == "listen":
            probe.cmd_listen(args.seconds)
        elif args.command == "move":
            probe.cmd_move(args.id, args.delta, args.period, args.wait,
                           return_home=not args.no_return,
                           torque_off=not args.keep_torque)
        else:
            parser.error(f"未知子命令 {args.command}")
    except Exception as exc:                                     # noqa: BLE001
        probe.out(f"!! 出错: {type(exc).__name__}: {exc}")
        return 1
    finally:
        probe.close()
    probe.out(f"# 完成，日志: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
