"""可视化控制台：把舵机总线暴露为一组**本地** HTTP JSON 接口 + 一个单页前端。

设计取舍：

* **只用标准库**（``http.server`` / ``json`` / ``threading`` / ``secrets``），不引入
  Web 框架——与「运行期只依赖 pyserial」的约定一致，拷走 ``romanbo/`` 目录即可用。
* **默认只监听 127.0.0.1**，并要求**访问令牌**与 **Host 校验**：这是一个能驱动
  硬件的服务，即使在本机也不该被任意网页（CSRF / DNS rebinding）调到。
* 所有设备操作经一把可重入锁串行化，与 `RomanboRobot` 内部的收发锁配合，
  保证「多步动作」不会被别的请求插进来打断。
* 报文日志在**传输层**抓取（TX 必为整帧、RX 为本轮读到的字节），与真机串口
  助手看到的一致，便于按 `docs/SERVO_SPEC.md` 逐字节对照。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import joints as J
from . import protocol as P
from .robot import RomanboRobot
from .servo import LoadLimitExceeded, Servo
from .transport import (MockTransport, SerialTransport, Transport,
                        is_anonymous_port, list_serial_ports, port_error_hint)
from .webui_page import PAGE

__all__ = ["FrameLog", "WebConsole", "make_server", "serve"]


# --------------------------------------------------------------------------- #
# 报文日志
# --------------------------------------------------------------------------- #

class FrameLog:
    """有界环形缓冲：给前端按序号增量取报文。"""

    def __init__(self, capacity: int = 800) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=max(16, capacity))
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, direction: str, data: bytes) -> None:
        now = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        with self._lock:
            self._seq += 1
            self._items.append({
                "seq": self._seq,
                "dir": direction,
                "hex": data.hex(" ").upper(),
                "at": f"{stamp}.{int((now % 1) * 1000):03d}",
            })

    def since(self, seq: int) -> Tuple[List[Dict[str, Any]], int]:
        with self._lock:
            return [i for i in self._items if i["seq"] > seq], self._seq


class _LoggingTransport(Transport):
    """包装真实/模拟传输，把原始字节抄一份给控制台。"""

    def __init__(self, inner: Transport, log: FrameLog) -> None:
        self._inner = inner
        self._log = log
        self.name = inner.name

    def open(self) -> None:
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    @property
    def is_open(self) -> bool:
        return self._inner.is_open

    @property
    def lost_reason(self) -> Optional[str]:
        """把内层的断线原因透出去（`WebConsole.state` 要靠它提示重新连接）。"""
        return getattr(self._inner, "lost_reason", None)

    def write(self, frame: bytes) -> None:
        self._log.add("tx", bytes(frame))
        self._inner.write(frame)

    def read_available(self, timeout: float) -> bytes:
        data = self._inner.read_available(timeout)
        if data:
            self._log.add("rx", bytes(data))
        return data


# --------------------------------------------------------------------------- #
# 控制台（设备侧）
# --------------------------------------------------------------------------- #

class WebConsole:
    """把 `RomanboRobot` 的操作整理成前端要用的形状。"""

    def __init__(self, *, port: Optional[str] = None, mock: bool = False,
                 baudrate: int = P.BAUDRATE_DEFAULT,
                 ack_timeout: float = P.DEFAULT_ACK_TIMEOUT,
                 log_capacity: int = 800) -> None:
        self.frames = FrameLog(log_capacity)
        self._lock = threading.RLock()
        self._robot: Optional[RomanboRobot] = None
        self._mock = bool(mock)
        self._port = port
        self._baudrate = int(baudrate)
        self._ack_timeout = float(ack_timeout)
        self._online: List[int] = []

    # -- 连接 -------------------------------------------------------------- #

    def connect(self, port: Optional[str] = None, mock: Optional[bool] = None,
                baudrate: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            self.disconnect()
            use_mock = self._mock if mock is None else bool(mock)
            use_port = port or self._port
            use_baud = int(baudrate or self._baudrate)
            if not use_mock and not use_port:
                raise ValueError("请选择串口，或勾选模拟模式")
            # 模拟器默认 load=100（那是给「测试限力路径」用的）；控制台演示改成 0，
            # 否则每个动作都会被默认阈值 60 判为超限——测试语义不等于演示语义。
            inner: Transport = (MockTransport(servo_ids=range(1, 18), load=0)
                                if use_mock
                                else SerialTransport(use_port, baudrate=use_baud))
            robot = RomanboRobot(transport=_LoggingTransport(inner, self.frames),
                                 ack_timeout=self._ack_timeout)
            try:
                robot.open()
            except OSError as exc:
                # 打不开串口是控制台最常见的失败，而两个入口都只打印 ``str(exc)``：
                # 启动时的自动连接，以及页面上点「连接」（前端拼 "连接失败：" + 消息）。
                # 所以把**可照做的处置**拼进消息本身——否则用户只看得到 errno
                # （实测 2026-09-27：页面上只有 "SerialException: [Errno 13] Permission
                # denied"，看不出该去加 dialout 组）。分类依据是 errno，不是异常类型：
                # pyserial 把权限不足也包成 SerialException。
                raise RuntimeError(f"{type(exc).__name__}: {exc}"
                                   f"；{port_error_hint(use_port or '', exc)}") from exc
            self._robot = robot
            self._mock, self._port, self._baudrate = use_mock, use_port, use_baud
            self._online = []
            return self.state()

    def disconnect(self) -> None:
        with self._lock:
            if self._robot is not None:
                try:
                    self._robot.close()
                finally:
                    self._robot = None
            self._online = []

    def state(self) -> Dict[str, Any]:
        robot = self._robot
        return {
            "connected": bool(robot is not None and robot.is_open),
            "mock": self._mock,
            "port": self._port,
            "baudrate": self._baudrate,
            "online": list(self._online),
            "last_error": (robot.last_error if robot is not None else None),
            #: 设备消失（拔插/重枚举）的原因；前端据此提示"请重新连接"
            "lost_reason": (robot.lost_reason if robot is not None else None),
        }

    @staticmethod
    def ports() -> List[Dict[str, Any]]:
        """枚举串口：**已识别的设备排前面**。

        页面用它填端口下拉；Linux 上 pyserial 还会列出 ``ttyS0..31`` 这类没有接硬件的
        占位口（这台机器上 32 个），排在前面的话真适配器要滚半天才找得到。这里**不隐藏**
        （下拉框里得能选到任何端口），只把有身份的排前面——`ports` 命令那边是直接隐藏。
        """
        rows = list_serial_ports()
        return sorted(rows, key=lambda r: (is_anonymous_port(r), str(r["device"])))

    # -- 发现 -------------------------------------------------------------- #

    def scan(self, start: int = P.SERVO_ID_MIN, end: int = P.SERVO_ID_MAX) -> Dict[str, Any]:
        robot = self._require()
        with self._lock:
            started = time.perf_counter()
            found = robot.scan(start, end,
                               quarantine=0.0 if self._mock else P.SCAN_QUARANTINE)
            self._online = list(found)
            return {"found": found,
                    "elapsed_s": round(time.perf_counter() - started, 1)}

    # -- 读 ---------------------------------------------------------------- #

    def servo_config(self, id_: int) -> Dict[str, Any]:
        with self._lock:
            cfg = self._servo(id_).read_config(timeout=0.25)
            data = cfg.as_dict()
            data["angle"] = (None if cfg.position is None
                             else round(J.adc_to_angle(cfg.position), 2))
            return data

    def servo_live(self, id_: int) -> Dict[str, Any]:
        """轻量读数：只读位置 / 实测负荷 / 温度，供前端高频刷新。

        全部按**单次超时**（``retries=0`` 语义）读：轮询是高频的，等重试既拖慢
        刷新，又会在舵机掉线时长时间占住总线锁、把运动指令一起卡住。要完整参数
        请走 `servo_config`（它才用默认重试）。
        """
        servo = self._servo(id_)
        out: Dict[str, Any] = {"id": int(id_), "position": None, "load": None,
                               "temperature": None}
        # 读操作**不取设备锁**：goto()/multi_move() 会把锁持有整个运动过程，排队会让
        # 界面读数在运动期间整体冻结——而那一刻正是最需要看读数的时候。单次请求的
        # 原子性已由 RomanboRobot 内部的收发锁保证，插在两步之间读取是安全的
        # （与 CLI 的 move --readback 同理）。
        try:
            out["position"] = servo.get_position(timeout=0.2, retries=0)
        except (TimeoutError, P.ProtocolError, RuntimeError) as exc:
            self._note(f"id={id_} 读取位置失败: {exc}")
        out["load"] = self._quick_byte(servo, P.ServoCmd.GET_LOAD)
        out["temperature"] = self._quick_byte(servo, P.ServoCmd.GET_TEMP)
        return out

    def _quick_byte(self, servo: Servo, cmd: int,
                    timeout: float = 0.2) -> Optional[int]:
        """按命令码单次读一个字节值（不回退重试）。

        走 ``robot.request`` 而不是 ``Servo.get_load`` / ``get_temperature``：
        那两个公开方法不暴露 ``retries``，只能吃实例默认的 2 次重试（舵机不回话时
        每次多等约 0.45 s）。GET 类请求就是 6 字节裸帧，数值取数据段首字节，
        与 ``Servo.get_load`` 的解法一致（见 `romanbo.servo`）。
        """
        try:
            resp = servo.robot.request(P.make_frame(cmd, servo.id),
                                       expect_id=servo.id,
                                       expect_cmd=P.expected_ack(cmd),
                                       timeout=timeout, retries=0)
        except (TimeoutError, P.ProtocolError) as exc:
            self._note(f"id={servo.id} 读取 0x{cmd:02X} 失败: {exc}")
            return None
        body = P.payload(resp.data)
        return body[0] if body else None

    def capture(self) -> Dict[str, Any]:
        robot = self._require()
        with self._lock:
            positions = robot.capture(self._online or None)
            return {"positions": positions}

    # -- 写 / 运动 --------------------------------------------------------- #

    def goto(self, id_: int, *, adc: Optional[int] = None,
             angle: Optional[float] = None, speed_dps: Optional[float] = None,
             max_load: Optional[int] = None, level: Optional[int] = None,
             load_check_every: int = 1) -> Dict[str, Any]:
        """单关节定位。给了 ``speed_dps`` 走步进逼近，否则直接下发（最大速度）。

        ``load_check_every`` 见 `servo_live`：真机实测**起步第 1 步的冲击可达
        128~147**（加速涌流，不是卡死），而运动中只有约 85。所以要么把阈值放到
        150 以上，要么把检查间隔调大（默认建议 3）跳过起步阶段。
        """
        if adc is None and angle is None:
            raise ValueError("需要 adc 或 angle")
        target = (int(adc) if adc is not None
                  else J.angle_to_adc(float(angle)))          # type: ignore[arg-type]
        target = max(P.ADC_MIN, min(P.ADC_MAX, target))
        if max_load is not None and speed_dps is None:
            raise ValueError("软件限力需要同时给出速度（限力靠运动中轮询负荷）")
        servo = self._servo(id_)
        with self._lock:
            if speed_dps is None:
                servo.set_position(target, level=level)
                return {"id": int(id_), "target": target, "sent": 0,
                        "peak_load": None, "angle": round(J.adc_to_angle(target), 2)}
            sent = servo.move_at_speed(target, float(speed_dps),
                                       max_load=None if max_load is None else int(max_load),
                                       level=level,
                                       load_check_every=max(1, int(load_check_every)))
            return {"id": int(id_), "target": target, "sent": len(sent),
                    "peak_load": servo.last_peak_load,
                    "angle": round(J.adc_to_angle(target), 2)}

    def multi_move(self, targets: Mapping[Any, Any], *,
                   speed_dps: Optional[float] = None,
                   max_load: Optional[int] = None,
                   level: Optional[int] = None,
                   load_check_every: int = 1) -> Dict[str, Any]:
        robot = self._require()
        wanted = {int(k): max(P.ADC_MIN, min(P.ADC_MAX, int(v)))
                  for k, v in targets.items()}
        if not wanted:
            raise ValueError("没有目标关节")
        with self._lock:
            if speed_dps is None:
                robot.move(wanted, level=level)
                return {"targets": wanted, "joints": len(wanted), "peak_load": None}
            start = robot.capture(list(wanted), timeout=0.2)
            robot.move(wanted, speed_dps=float(speed_dps), start=start,
                       max_load=None if max_load is None else int(max_load),
                       level=level,
                       load_check_every=max(1, int(load_check_every)))
            return {"targets": wanted, "joints": len(wanted),
                    "peak_load": robot.last_peak_load}

    def torque(self, id_: int, on: bool) -> Dict[str, Any]:
        with self._lock:
            self._servo(id_).torque(bool(on))
            return {"id": int(id_), "torque": bool(on)}

    def set_pid(self, id_: int, p: int, i: int, d: int,
                save: bool = True) -> Dict[str, Any]:
        with self._lock:
            servo = self._servo(id_)
            servo.set_pid(int(p), int(i), int(d), save=bool(save))
            return {"id": int(id_), "pid": [int(p), int(i), int(d)], "save": bool(save)}

    def set_limit(self, id_: int, minimum: int, maximum: int) -> Dict[str, Any]:
        lo = max(P.ADC_MIN, min(P.ADC_MAX, int(minimum)))
        hi = max(P.ADC_MIN, min(P.ADC_MAX, int(maximum)))
        if lo > hi:
            raise ValueError(f"下限 {lo} 不能大于上限 {hi}")
        with self._lock:
            servo = self._servo(id_)
            servo.set_position_limit(lo, hi)
            return {"id": int(id_), "position_limit": [lo, hi]}

    def set_margin(self, id_: int, value: int) -> Dict[str, Any]:
        with self._lock:
            self._servo(id_).set_margin(int(value))
            return {"id": int(id_), "margin": int(value)}

    def set_led(self, id_: int, *, red: Optional[bool] = None,
                green: Optional[bool] = None, blue: Optional[bool] = None,
                value: Optional[int] = None) -> Dict[str, Any]:
        """设 LED。给三色（推荐）走 ``set_led_color``，给 ``value`` 走 ``set_led``。

        协议文档只定义了 8 种组合（**无亮度/闪烁**），三色落在 ``d[5]`` 的
        bit7/bit6/bit5 = 红/绿/蓝；``set_led(value)`` 是「编号方式」的另一种写法
        （``value`` 的低 3 位，4/2/1 = 红/绿/蓝）。见 `docs/SERVO_SPEC.md` §5.4。

        返回 ``led`` 含三色与**实际下发的 ``d[5]`` 字节**，便于对着报文日志核对。
        """
        with self._lock:
            servo = self._servo(id_)
            if red is None and green is None and blue is None:
                if value is None:
                    raise ValueError("需要 red / green / blue，或 value")
                raw = int(value) & 0xFF
                servo.set_led(raw)
                rgb = (bool(raw & 4), bool(raw & 2), bool(raw & 1))
            else:
                rgb = (bool(red), bool(green), bool(blue))
                servo.set_led_color(*rgb)
            byte = (0x80 if rgb[0] else 0) | (0x40 if rgb[1] else 0) | (0x20 if rgb[2] else 0)
            return {"id": int(id_), "led": {"red": rgb[0], "green": rgb[1],
                                            "blue": rgb[2], "byte": byte}}

    def calibrate(self, id_: int) -> Dict[str, Any]:
        """把当前位置设为零点（``0x23``）。**会改写舵机内部标定。**"""
        with self._lock:
            self._servo(id_).set_calibration_currpos()
            return {"id": int(id_), "calibrated": True}

    def stop_all(self) -> Dict[str, Any]:
        """紧急停止：对所有在线舵机断开力矩。

        还没扫描过时退化为「向 1..32 广播断电」——SET 类命令无应答，不必等回包，
        32 帧约 64 ms 就能发完（见 `MIN_FRAME_GAP`）。

        !!! warning
            **故意不取设备锁**：`goto()` / `multi_move()` 会把 ``_lock`` 持有整个
            运动过程（慢速长距离可达数十秒），紧急停止若排队等锁，就要等运动自己
            走完才生效——等于安全按钮失效。这里每帧只短暂占用 `RomanboRobot`
            内部的收发锁，因此能**插进运动节拍之间**生效：力矩一断，后续位置指令
            就不再驱动关节。
        """
        robot = self._require()
        ids = list(self._online) or list(range(P.SERVO_ID_MIN, P.SERVO_ID_MAX + 1))
        for id_ in ids:
            robot.send(P.build_set_torque(id_, P.OFF))
        return {"ids": ids, "broadcast": not self._online}

    # -- 内部 -------------------------------------------------------------- #

    def _require(self) -> RomanboRobot:
        robot = self._robot
        if robot is None or not robot.is_open:
            raise RuntimeError("尚未连接：请选择串口后点「连接」，或勾选模拟模式")
        return robot

    def _servo(self, id_: int) -> Servo:
        id_ = int(id_)
        if not P.SERVO_ID_MIN <= id_ <= P.SERVO_ID_MAX:
            raise ValueError(f"舵机 ID 越界：{id_}（合法范围 "
                             f"{P.SERVO_ID_MIN}..{P.SERVO_ID_MAX}）")
        return self._require().servo(id_)

    def _note(self, text: str) -> None:
        robot = self._robot
        if robot is not None:
            robot.last_error = text


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #

_ROUTE = re.compile(r"^/api/servo/(\d+)(?:/([a-z_]+))?$")
_HOSTPORT = re.compile(r"^\[([^\]]+)\]|^([^:]+)")


class _Handler(BaseHTTPRequestHandler):
    """JSON 接口 + 单页前端。所有 /api/* 都要令牌。"""

    server_version = "romanbo-webui"
    protocol_version = "HTTP/1.1"

    console: WebConsole
    token: str = ""
    allowed_hosts: Tuple[str, ...] = ()

    # -- 基础设施 ---------------------------------------------------------- #

    def log_message(self, fmt: str, *args: Any) -> None:      # noqa: A003
        """默认会往 stderr 打一行/请求；前端每 0.7 s 轮询一次，必须闭嘴。"""

    def _host_ok(self) -> bool:
        if not self.allowed_hosts:
            return True
        raw = (self.headers.get("Host") or "").strip()
        match = _HOSTPORT.match(raw)
        host = (match.group(1) or match.group(2) or "") if match else ""
        return host in self.allowed_hosts

    def _send(self, status: int, payload: Any, *, ctype: str = "application/json; charset=utf-8") -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:                                                 # pragma: no cover
            body = bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorized(self, query: Dict[str, List[str]]) -> bool:
        supplied = self.headers.get("X-Robot-Token") or query.get("token", [""])[0]
        return bool(self.token) and secrets.compare_digest(str(supplied), self.token)

    #: 请求体上限：本机调试接口的任何合法请求都远小于此
    MAX_BODY_BYTES = 1 << 20

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > self.MAX_BODY_BYTES:
            # 不读：否则线程会按声明值阻塞/分配，成为唯一没有上限的入口
            raise ValueError(f"请求体过大：{length} 字节（上限 {self.MAX_BODY_BYTES}）")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc

    # -- 路由 -------------------------------------------------------------- #

    def do_GET(self) -> None:                                 # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:                                # noqa: N802
        self._route("POST")

    def _lost_hint(self) -> str:
        """串口已被判定消失时，给错误消息补一句「重新连接」。

        补这一句是因为否则用户只看到「未应答（超时）」或一个 IO 错误，**不知道为什么要
        重连**——设备被拔插/重枚举后旧句柄还在，`SerialTransport` 会主动把它标记成断开，
        于是故障表现是"读不到数"而不是"连不上"。
        """
        if not self.console.state().get("lost_reason"):
            return ""
        return ("｜串口已断开（设备被拔插或重新枚举）：请在页面上重新连接；"
                "Linux 上可用 /dev/serial/by-id/ 下的稳定路径避免端口号变化")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        if not self._host_ok():
            self._send(403, {"error": "Host 校验失败：请用 http://127.0.0.1 或 "
                                      "http://localhost 访问本控制台"})
            return
        if path == "/" and method == "GET":
            self._send(200, PAGE.replace("__TOKEN__", self.token), ctype="text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"")
            return
        if not path.startswith("/api/"):
            self._send(404, {"error": f"未知路径 {path}"})
            return
        if not self._authorized(query):
            self._send(401, {"error": "缺少或错误的访问令牌：请用终端里打印的完整链接打开"})
            return

        try:
            body = self._read_body() if method == "POST" else {}
            status, payload = self._dispatch(method, path, query, body)
        except LoadLimitExceeded as exc:
            self._send(409, {"error": f"软件限力触发：实测负荷 {exc.load} 超过阈值 {exc.limit}"
                                      f"（第 {exc.step} 步，停在位置 {exc.position}）",
                             "aborted": True, "detail": exc.as_dict()})
            return
        except TimeoutError as exc:
            self._send(504, {"error": f"舵机未应答（超时）：{exc}" + self._lost_hint()})
            return
        except P.ProtocolError as exc:
            self._send(400, {"error": f"协议错误：{exc}" + self._lost_hint()})
            return
        except OSError as exc:
            # 读写失败（串口断线、设备被拔）：不是服务端故障，标成 409，
            # 并把"请重新连接"的提示带上。
            self._send(409, {"error": f"{type(exc).__name__}: {exc}" + self._lost_hint()})
            return
        except (ValueError, RuntimeError) as exc:
            self._send(409, {"error": str(exc) + self._lost_hint()})
            return
        except Exception as exc:                              # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}" + self._lost_hint()})
            return
        self._send(status, payload)

    def _dispatch(self, method: str, path: str, query: Dict[str, List[str]],
                  body: Any) -> Tuple[int, Any]:
        console = self.console
        if method == "GET":
            if path == "/api/state":
                return 200, console.state()
            if path == "/api/ports":
                return 200, console.ports()
            if path == "/api/frames":
                since = int((query.get("since") or ["0"])[0] or 0)
                frames, latest = console.frames.since(since)
                return 200, {"frames": frames, "latest": latest}
            if path == "/api/capture":
                return 200, console.capture()
            match = _ROUTE.match(path)
            if match:
                id_, action = int(match.group(1)), match.group(2)
                if action is None:
                    return 200, console.servo_config(id_)
                if action == "live":
                    return 200, console.servo_live(id_)
            raise ValueError(f"未知路径 {path}")

        if path == "/api/connect":
            return 200, console.connect(body.get("port"), body.get("mock"),
                                        body.get("baudrate"))
        if path == "/api/disconnect":
            console.disconnect()
            return 200, console.state()
        if path == "/api/scan":
            return 200, console.scan(int(body.get("start", P.SERVO_ID_MIN)),
                                     int(body.get("end", P.SERVO_ID_MAX)))
        if path == "/api/capture":
            return 200, console.capture()
        if path == "/api/move":
            return 200, console.multi_move(body.get("targets") or {},
                                           speed_dps=body.get("speed_dps"),
                                           max_load=body.get("max_load"),
                                           level=body.get("level"),
                                           load_check_every=body.get("load_check_every", 1))
        if path == "/api/stop_all":
            return 200, console.stop_all()
        match = _ROUTE.match(path)
        if match:
            id_, action = int(match.group(1)), match.group(2)
            if action == "goto":
                return 200, console.goto(id_, adc=body.get("adc"), angle=body.get("angle"),
                                         speed_dps=body.get("speed_dps"),
                                         max_load=body.get("max_load"),
                                         level=body.get("level"),
                                         load_check_every=body.get("load_check_every", 1))
            if action == "torque":
                return 200, console.torque(id_, bool(body.get("on", True)))
            if action == "pid":
                return 200, console.set_pid(id_, body.get("p", 0), body.get("i", 0),
                                            body.get("d", 0), body.get("save", True))
            if action == "limit":
                return 200, console.set_limit(id_, body.get("min", P.ADC_MIN),
                                              body.get("max", P.ADC_MAX))
            if action == "margin":
                return 200, console.set_margin(id_, body.get("value", 0))
            if action == "led":
                return 200, console.set_led(id_, red=body.get("red"),
                                            green=body.get("green"),
                                            blue=body.get("blue"),
                                            value=body.get("value"))
            if action == "calib":
                return 200, console.calibrate(id_)
        raise ValueError(f"未知路径 {path}")


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def _allowed_hosts(host: str) -> Tuple[str, ...]:
    """绑定回环时做 Host 白名单（防 DNS rebinding）；绑别的地址时无法校验。"""
    if host in ("127.0.0.1", "localhost", "::1"):
        return ("127.0.0.1", "localhost", "::1")
    return ()


def make_server(host: str, port: int, console: WebConsole,
                token: str) -> ThreadingHTTPServer:
    """建好 HTTP 服务（端口传 0 表示由系统分配，测试用）。"""

    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    handler = type("_BoundHandler", (_Handler,), {
        "console": console,
        "token": token,
        "allowed_hosts": _allowed_hosts(host),
    })
    return _Server((host, port), handler)                     # type: ignore[arg-type]


def serve(*, host: str = "127.0.0.1", port: int = 8765,
          serial_port: Optional[str] = None, mock: bool = False,
          baudrate: int = P.BAUDRATE_DEFAULT,
          ack_timeout: float = P.DEFAULT_ACK_TIMEOUT,
          token: Optional[str] = None, open_browser: bool = False,
          printer: Callable[[str], None] = print) -> int:
    """启动控制台并阻塞到 Ctrl+C。返回进程退出码。"""
    console = WebConsole(port=serial_port, mock=mock, baudrate=baudrate,
                         ack_timeout=ack_timeout)
    token = token or secrets.token_urlsafe(16)
    httpd = make_server(host, port, console, token)
    actual = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("", "0.0.0.0") else host
    url = f"http://{shown}:{actual}/?token={token}"

    if mock or serial_port:
        try:
            console.connect()
            printer(f"已自动连接：{'模拟器' if mock else serial_port}")
            if mock:
                found = console.scan()["found"]
                printer(f"模拟器在线舵机：{found}")
        except Exception as exc:                              # noqa: BLE001
            printer(f"自动连接失败（可在页面上再连）：{exc}")

    printer("")
    printer("ROMANBO 舵机控制台已启动，请用浏览器打开：")
    printer(f"  {url}")
    if not _allowed_hosts(host):
        printer(f"注意：已监听 {host}，非回环地址下 Host 校验自动失效，"
                f"令牌是唯一防线，请勿暴露到公网。")
    printer("提示：令牌只用于本机保护，别把这条链接发给别人；Ctrl+C 停止。")
    printer("")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        printer("\n已停止控制台")
    finally:
        httpd.server_close()
        console.disconnect()
    return 0
