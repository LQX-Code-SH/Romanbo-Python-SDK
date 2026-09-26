"""整机封装：连接握手、ID 扫描、多关节同步动作、示教回读、播放 ``.rsc`` 动作。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from . import joints as J
from . import protocol as P
from .rsc import MotionFrame, RscProject
from .servo import LoadLimitExceeded, Servo
from .transport import MockTransport, SerialTransport, Transport

#: 控制器固件所需的版本字节（回包第 1 个数据字节；v0.8 → 8）
REQUIRED_FIRMWARE_VERSION = 8
#: 识别为 ROMANBO 控制器的型号码（回包第 2 个数据字节）
REQUIRED_MODEL_CODE = 0x70
#: 握手期间的等待时间（秒）
HANDSHAKE_STEP_DELAY = 0.01
HANDSHAKE_SETTLE_DELAY = 0.5


@dataclass
class HandshakeResult:
    """连接自检结果。"""

    model_ok: bool = False
    version_ok: bool = False
    attempts: int = 0
    model: Optional[P.Response] = None
    version: Optional[P.Response] = None

    @property
    def ok(self) -> bool:
        return self.model_ok and self.version_ok

    def describe(self) -> str:
        if not self.model_ok:
            return "连接的设备不是 ROMANBO（控制器未应答或型号不符）"
        if not self.version_ok:
            return "固件与软件不兼容，请下载固件（控制器版本不符）"
        return "ROMANBO 控制器已连接且固件版本匹配"


class RomanboRobot:
    """一条总线上的全部设备（控制器 + 舵机）。

    :param port: 串口名（如 ``COM3``）；使用 ``transport`` 时可为 ``None``
    :param transport: 自定义传输层（例如 `romanbo.transport.MockTransport`）
    """

    def __init__(self, port: Optional[str] = None, *,
                 transport: Optional[Transport] = None,
                 baudrate: int = P.BAUDRATE_DEFAULT,
                 ack_timeout: float = P.DEFAULT_ACK_TIMEOUT,
                 retries: int = 2, retry_delay: float = P.BUS_QUARANTINE + 0.05,
                 id_offset: int = 1) -> None:
        if transport is None:
            if not port:
                raise ValueError("需要 port 或 transport 之一")
            transport = SerialTransport(port, baudrate=baudrate)
        self.transport: Transport = transport
        self.ack_timeout = ack_timeout
        #: 应答超时后的重试次数。实测舵机偶尔会漏答一条查询（尤其连续下发时），
        #: 默认重试 2 次，避免整个动作流程被单次超时打断。
        self.retries = max(0, int(retries))
        #: 重试间隔必须 ≥ 总线静默期，否则重试同样会被舵机忽略（见 BUS_QUARANTINE）
        self.retry_delay = retry_delay
        self.id_offset = id_offset
        self.parser = P.FrameParser()
        self.sent_frames: List[bytes] = []
        self.received_frames: List[P.Response] = []
        self.last_error: Optional[str] = None
        #: 最近一次软件限力运动中的峰值负荷（``max_load`` 启用时更新）
        self.last_peak_load: Optional[int] = None
        #: 串口收发锁。同一实例可以跨线程使用，但**同一时刻只允许一次收发** ——
        #: 总线本身是串行的，没有锁时多线程会把「请求/应答」配错。可重入，
        #: 所以 ``request`` 内部再调 ``poll``/``send`` 不会自锁。
        self._io_lock = threading.RLock()
        self._servos: Dict[int, Servo] = {}
        self._unsolicited: List[P.Response] = []

    # -- 生命周期 ---------------------------------------------------------- #

    def open(self) -> "RomanboRobot":
        self.transport.open()
        return self

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "RomanboRobot":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    # -- 收发 -------------------------------------------------------------- #

    def send(self, frame: bytes) -> None:
        """只发不等（SET 类命令无应答，发完即返回）。"""
        with self._io_lock:
            self.sent_frames.append(bytes(frame))
            self.transport.write(frame)

    def flush_input(self) -> None:
        with self._io_lock:
            self.parser.reset()

    def poll(self, timeout: float = 0.0) -> List[P.Response]:
        """读取并解析回包（非阻塞或最多等待 ``timeout`` 秒）。"""
        with self._io_lock:
            chunk = self.transport.read_available(timeout)
            frames = self.parser.feed(chunk) if chunk else []
            if frames:
                self.received_frames.extend(frames)
            return frames

    def request(self, frame: bytes, *, expect_id: Optional[int] = None,
                expect_cmd: Optional[int] = None,
                timeout: Optional[float] = None,
                raise_on_error: bool = True,
                retries: Optional[int] = None) -> P.Response:
        """发一帧并等待匹配的回包（线程安全：整个「发—等」过程持锁）。"""
        with self._io_lock:
            return self._request_locked(frame, expect_id=expect_id,
                                        expect_cmd=expect_cmd, timeout=timeout,
                                        raise_on_error=raise_on_error,
                                        retries=retries)

    def _request_locked(self, frame: bytes, *, expect_id: Optional[int] = None,
                        expect_cmd: Optional[int] = None,
                        timeout: Optional[float] = None,
                        raise_on_error: bool = True,
                        retries: Optional[int] = None) -> P.Response:
        """发一帧并等待匹配的回包（超时自动重试，默认 2 次）。

        实测舵机偶尔会漏答一条查询（尤其连续下发时），重试可避免整个动作流程
        被单次超时打断。

        匹配规则：``ID`` 相同（默认取发送帧的 ID），且若给了 ``expect_cmd``
        则要求 ``cmd == expect_cmd``；否则接受任意「高位为 1」的应答帧。
        """
        attempts = self.retries if retries is None else max(0, int(retries))
        last_error: Optional[TimeoutError] = None
        for attempt in range(attempts + 1):
            try:
                return self._request_once(frame, expect_id=expect_id,
                                          expect_cmd=expect_cmd, timeout=timeout,
                                          raise_on_error=raise_on_error)
            except TimeoutError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.retry_delay)
        if last_error is not None:
            raise last_error
        raise TimeoutError("等待回包失败")

    def _request_once(self, frame: bytes, *, expect_id: Optional[int] = None,
                      expect_cmd: Optional[int] = None,
                      timeout: Optional[float] = None,
                      raise_on_error: bool = True) -> P.Response:
        """发一帧并等待一次匹配回包（不重试）。"""
        timeout = self.ack_timeout if timeout is None else timeout
        request = P.parse_frame(frame, strict=False)
        target_id = request.id if expect_id is None else expect_id
        self.flush_input()
        self.send(frame)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"等待回包超时: id={target_id} cmd=0x{request.cmd:02X}")
            for resp in self.poll(remaining):
                if resp.id != target_id:
                    self._unsolicited.append(resp)
                    continue
                # 错误帧（CMD=0x80）不受期望码过滤，必须能浮上来
                if (expect_cmd is not None and resp.cmd != expect_cmd
                        and not resp.is_error):
                    self._unsolicited.append(resp)
                    continue
                if resp.is_error:
                    if raise_on_error:
                        raise P.ErrorResponse(resp)
                    return resp
                return resp

    def send_command(self, frame: bytes, *, expect_id: Optional[int] = None,
                     wait: bool = False, timeout: Optional[float] = None,
                     expect_cmd: Optional[int] = None,
                     retries: Optional[int] = None,
                     raise_on_error: bool = True) -> Optional[P.Response]:
        """统一的「发（可选等）」入口，供 `romanbo.servo.Servo` 使用。"""
        if wait:
            return self.request(frame, expect_id=expect_id, expect_cmd=expect_cmd,
                                timeout=timeout, retries=retries,
                                raise_on_error=raise_on_error)
        self.send(frame)
        return None

    def drain(self, timeout: float = 0.05) -> List[P.Response]:
        """清空并返回当前所有已到达的回包。"""
        out = self.poll(timeout)
        out.extend(self._unsolicited)
        self._unsolicited = []
        return out

    # -- 舵机对象 ---------------------------------------------------------- #

    def servo(self, id_: int) -> Servo:
        if id_ not in self._servos:
            self._servos[id_] = Servo(self, id_)
        return self._servos[id_]

    def __getitem__(self, id_: int) -> Servo:
        return self.servo(id_)

    @property
    def servos(self) -> Dict[int, Servo]:
        return dict(self._servos)

    # -- 连接自检 ---------------------------------------------------------- #

    def _ask_model(self, timeout: float) -> Optional[P.Response]:
        try:
            return self.request(P.build_get_model(), expect_id=P.CONTROLLER_ID,
                                timeout=timeout, raise_on_error=False)
        except (TimeoutError, P.ProtocolError):
            return None

    def _ask_version(self, timeout: float) -> Optional[P.Response]:
        try:
            return self.request(P.build_get_version(), expect_id=P.CONTROLLER_ID,
                                timeout=timeout, raise_on_error=False)
        except (TimeoutError, P.ProtocolError):
            return None

    def handshake(self, *, retries: int = 2, timeout: float = 0.3,
                  settle: bool = True, verbose: bool = False) -> HandshakeResult:
        """连接自检：问型号 + 问版本，失败重试。"""
        result = HandshakeResult()
        for attempt in range(retries + 1):
            result.attempts = attempt + 1
            model = self._ask_model(timeout)
            time.sleep(HANDSHAKE_STEP_DELAY)
            version = self._ask_version(timeout)
            time.sleep(HANDSHAKE_STEP_DELAY)
            result.model, result.version = model, version
            if model is not None and version is not None:
                break
        if settle:
            time.sleep(HANDSHAKE_SETTLE_DELAY)

        result.model_ok = bool(result.model is not None
                               and len(result.model.data) >= 2
                               and result.model.data[1] == REQUIRED_MODEL_CODE)
        result.version_ok = bool(result.version is not None
                                 and len(result.version.data) >= 1
                                 and result.version.data[0] == REQUIRED_FIRMWARE_VERSION)
        if verbose:
            print(result.describe())
            if result.model is not None:
                print(f"  型号回包: {result.model}")
            if result.version is not None:
                print(f"  版本回包: {result.version}")
        return result

    def scan(self, start: int = P.SERVO_ID_MIN, end: int = P.SERVO_ID_MAX, *,
             timeout: float = P.SCAN_PROBE_TIMEOUT,
             quarantine: float = P.SCAN_QUARANTINE) -> List[int]:
        """扫描在线的舵机 ID（用 ``Status 0x05`` 查询）。

        传入越界范围时会被夹紧到 1..32（这一段才是舵机 ID 空间），
        不会抛异常。

        !!! important
            **必须处理总线静默期**。实测：舵机本身应答只要 10~20 ms，但一条
            **寻址到无设备 ID** 的帧之后，舵机会忽略随后约 ``0.4 s`` 内的帧
            （见 `romanbo.protocol.BUS_QUARANTINE`）。因此本方法在**探测失败
            后**先等 ``quarantine`` 秒再探测下一个 ID，否则会出现"只扫到第一个
            舵机、后面全部漏掉"的假象——扫描时每个 ID 之间
            ``Thread.Sleep(600)`` 正是为此。

            ``quarantine=0`` 可用于离线模拟器（测试用）。
        """
        start = max(P.SERVO_ID_MIN, int(start))
        end = min(P.SERVO_ID_MAX, int(end))
        found: List[int] = []
        previous_failed = False
        for id_ in range(start, end + 1):
            if previous_failed and quarantine > 0:
                time.sleep(quarantine)
            ok = self.servo(id_).ping(timeout=timeout)
            previous_failed = not ok
            if ok:
                found.append(id_)
        return found

    # -- 整机动作 ---------------------------------------------------------- #

    def torque_all(self, on: bool = True,
                   ids: Optional[Iterable[int]] = None) -> None:
        """整机扭矩开关。示教前先 ``False``，运动前 ``True``。"""
        for id_ in (list(ids) if ids is not None else self._known_ids()):
            self.servo(id_).torque(on)

    def led_all(self, value: int, ids: Optional[Iterable[int]] = None) -> None:
        for id_ in (list(ids) if ids is not None else self._known_ids()):
            self.servo(id_).set_led(value)

    def move(self, positions: Mapping[int, int], *, period_ms: int = 500,
             torque: int = P.ON, relative: int = 0, mode: str = "position",
             speed_dps: Optional[float] = None,
             start: Optional[Mapping[int, int]] = None,
             step_interval_ms: Optional[int] = None,
             level: Optional[int] = None,
             max_load: Optional[int] = None,
             load_check_every: int = 1,
             sleep: Callable[[float], None] = time.sleep,
             sync: bool = True, sync_id: int = P.BROADCAST_ID,
             wait: bool = False,
             led: Optional[Mapping[int, int]] = None) -> None:
        """多关节运动。速度用 ``speed_dps``（实际角速度）或 ``period_ms`` 表达。

        ``speed_dps`` —— **实际角速度（度/秒）**
            用**步进逼近**实现（见 `Servo.move_at_speed`：实测舵机固件忽略
            ``SET_PERIOD``，速度只能靠按节拍下发中间目标来控制）。需要 ``start``
            给出各关节当前 ADC 才能插值；未出现在 ``start`` 里的关节直接下发目标。

        ``period_ms`` / ``mode`` —— 协议文档的周期参数路径
            ``mode="position"``（默认，**实测可用**）：逐关节先 ``SET_PERIOD``
            再 ``SET_POSITION``；``mode="next"``：预置 + 同步触发的做法
            （``SET_NEXT_POSITION`` + ``SET_SYNC``），**实测该固件不响应**。
            注意 ``SET_PERIOD`` 本身被固件忽略，它并不决定实际速度。

        !!! note
            同步触发的具体 ID 在协议文档中未明确；这里默认用**广播 ID 254**，
            如需逐 ID 触发请传 ``sync_id=None``。
        """
        if mode not in ("position", "next"):
            raise ValueError(f"mode 只能是 'position' 或 'next'，收到 {mode!r}")
        if led:
            for id_, value in led.items():
                if value:
                    self.servo(id_).set_led(value)
        if speed_dps is not None:
            if mode != "position":
                raise ValueError("speed_dps 仅支持 mode='position'")
            self._move_stepwise(positions, speed_dps, start=start,
                                interval_ms=step_interval_ms, torque=torque,
                                level=level, max_load=max_load,
                                load_check_every=load_check_every, sleep=sleep)
            return
        if max_load is not None:
            raise ValueError("max_load（软件限力）需要与 speed_dps 一起使用")
        for id_, position in positions.items():
            if mode == "position":
                self.servo(id_).set_position(int(position), period_ms=period_ms,
                                             torque=torque, relative=relative,
                                             level=level)
            else:
                self.servo(id_).next_position(int(position), period_ms=period_ms,
                                              torque=torque, relative=relative,
                                              sync=False)
        if mode == "next" and sync:
            if sync_id is None:
                for id_ in positions:
                    self.servo(id_).sync()
            else:
                self.send(P.build_set_sync(sync_id))
        if wait:
            timeout = max(0.1, period_ms / 1000.0)
            for id_ in positions:
                self._await_any(id_, timeout=timeout)

    def _move_stepwise(self, positions: Mapping[int, int], dps: float, *,
                       start: Optional[Mapping[int, int]] = None,
                       interval_ms: Optional[int] = None, torque: int = P.ON,
                       level: Optional[int] = None,
                       max_load: Optional[int] = None,
                       load_check_every: int = 1,
                       sleep: Callable[[float], None] = time.sleep) -> None:
        """按同一节拍把多关节目标拆成步进序列，使各关节以 ``dps`` 度/秒运动。

        先到目标的关节保持不动（不会重复下发相同值）。没有参照位置的关节
        （``start`` 中缺失）直接下发目标，不做插值。

        ``max_load`` 为**软件限力**阈值：每 ``load_check_every`` 拍**轮转**抽检
        一个关节的实测负荷（``0x18``），超过阈值即抛出 `LoadLimitExceeded`
        并停止下发（各关节停在最后一个目标位置）。轮转是为了让总线上每拍最多
        只多一次查询，不破坏节拍。
        """
        if dps <= 0:
            raise ValueError(f"角速度必须 > 0 度/秒，收到 {dps!r}")
        interval = interval_ms or J.pick_step_interval(dps)
        dt = interval / 1000.0
        plans: Dict[int, List[int]] = {}
        for id_, position in positions.items():
            if start is not None and id_ in start:
                plans[id_] = [adc for _, adc in J.plan_move(
                    start[id_], int(position), dps, interval_ms=interval)]
            else:
                plans[id_] = []
                self.servo(id_).set_position(int(position), torque=torque,
                                             level=level)
        moving = {id_: plan for id_, plan in plans.items() if plan}
        if not moving:
            return
        ticks = max(len(plan) for plan in moving.values())
        last_sent: Dict[int, int] = {}
        every = max(1, int(load_check_every))
        watch_ids = sorted(moving)
        peaks: Dict[int, int] = {}
        for tick in range(ticks):
            for id_, plan in moving.items():
                adc = plan[tick] if tick < len(plan) else plan[-1]
                if last_sent.get(id_) == adc:
                    continue
                self.servo(id_).set_position(adc, torque=torque, level=level)
                last_sent[id_] = adc
            sleep(dt)
            if max_load is not None and (tick + 1) % every == 0:
                watch = watch_ids[(tick // every) % len(watch_ids)]
                load = self.servo(watch).get_load()
                peaks[watch] = max(peaks.get(watch, 0), load)
                self.last_peak_load = max(peaks.values())
                if load > max_load:
                    raise LoadLimitExceeded(watch, load, int(max_load), tick + 1,
                                            last_sent.get(watch))

    def move_frame(self, frame: MotionFrame, *, period_ms: Optional[int] = None,
                   torque: int = P.ON, mode: str = "position", sync: bool = True,
                   id_offset: Optional[int] = None) -> None:
        """执行 ``.rsc`` 里的一个动作帧。"""
        self.move(frame.targets(id_offset=self.id_offset if id_offset is None else id_offset),
                  period_ms=period_ms or frame.period_ms, torque=torque,
                  mode=mode, sync=sync, led=frame.led_targets(
                      id_offset=self.id_offset if id_offset is None else id_offset))

    def capture(self, ids: Optional[Iterable[int]] = None, *,
                timeout: float = 0.2) -> Dict[int, int]:
        """示教读取：批量回读各关节位置（ADC）。"""
        out: Dict[int, int] = {}
        for id_ in (list(ids) if ids is not None else self._known_ids()):
            try:
                out[id_] = self.servo(id_).get_position(timeout=timeout)
            except (TimeoutError, P.ProtocolError) as exc:
                self.last_error = f"id={id_} 读取失败: {exc}"
        return out

    #: ``capture`` 的别名，语义上更贴近「读取位置」
    read_positions = capture

    def play(self, frames: Iterable[MotionFrame], *, loop: bool = False,
             speed_dps: Optional[float] = None, speed_scale: float = 1.0,
             torque: int = P.ON, mode: str = "position",
             id_offset: Optional[int] = None, wait_frame: bool = True,
             start_positions: Optional[Mapping[int, int]] = None,
             step_interval_ms: Optional[int] = None,
             ids: Optional[Iterable[int]] = None,
             level: Optional[int] = None,
             max_load: Optional[int] = None,
             load_check_every: int = 1,
             sleep: Callable[[float], None] = time.sleep,
             on_frame: Optional[Callable[[MotionFrame], None]] = None,
             stop_event: Optional[threading.Event] = None) -> int:
        """播放一串动作帧，返回播放帧数。速度有两种给法（``speed_dps`` 优先）。

        ``speed_dps`` —— **实际角速度（度/秒）**
            忽略文件里的 ``Period``：每一段（上一帧 → 本帧）都按该角速度用
            **步进逼近**走完（见 `move`），因此关节真的以该角速度运动。
            需要参照位置：``start_positions`` 给出起始时各关节的 ADC（例如播放前
            `capture` 的结果）；缺失的关节在第一段会直接跳到该帧目标。

        ``speed_scale`` —— 周期倍率，仅在未给 ``speed_dps`` 时生效。
            **> 1 更慢**（周期变长），``1.0`` 为文件原始速度。注意舵机固件会忽略
            ``SET_PERIOD``，所以这个倍率只改变**帧间节奏**，不改变单帧内的速度。

        ``max_load`` —— **软件限力**阈值，仅在给了 ``speed_dps`` 时可用；运动中
            轮转抽检各关节实测负荷，超限抛 `romanbo.servo.LoadLimitExceeded`。

        ``ids`` —— **只驱动这些舵机**（例如总线上只有 ID 8/10 时传 ``{8, 10}``）。
            ``.rsc`` 是整机 17 通道的绝对目标，直接照发会寻址到 15 个**不存在的 ID**，
            而实测"寻址到无设备 ID 之后，后续约 0.4 s 内的帧会被舵机忽略"
            （总线静默期），节奏会被彻底打乱；因此只驱动在线的关节时务必给出
            ``ids``。``None``（默认）表示不做过滤、按文件全量下发。

        :param mode: 见 `move`（默认 ``"position"``，实测可用）。
        """
        offset = self.id_offset if id_offset is None else id_offset
        frame_list = list(frames)
        if not frame_list:
            return 0
        if speed_dps is not None and speed_dps <= 0:
            raise ValueError(f"角速度必须 > 0 度/秒，收到 {speed_dps!r}")
        if speed_dps is not None and mode != "position":
            raise ValueError("speed_dps 仅支持 mode='position'")
        wanted: Optional[set] = None if ids is None else {int(i) for i in ids}
        if torque is not None:
            for id_ in frame_list[0].targets(id_offset=offset):
                if wanted is not None and id_ not in wanted:
                    continue
                self.servo(id_).torque(bool(torque))
            time.sleep(0.1)

        previous: Dict[int, int] = {int(k): int(v)
                                    for k, v in (start_positions or {}).items()}
        played = 0
        while True:
            for frame in frame_list:
                if stop_event is not None and stop_event.is_set():
                    return played
                targets = frame.targets(id_offset=offset)
                led = frame.led_targets(id_offset=offset)
                if wanted is not None:
                    targets = {k: v for k, v in targets.items() if k in wanted}
                    led = {k: v for k, v in led.items() if k in wanted}
                    if not targets:
                        played += 1
                        continue
                if speed_dps is None:
                    period = max(1, int(round(frame.period_ms * speed_scale)))
                    self.move(targets, period_ms=period, torque=torque,
                              mode=mode, level=level, sync=True, led=led)
                    if wait_frame:
                        sleep(period / 1000.0)
                elif any(id_ not in previous or previous[id_] != value
                         for id_, value in targets.items()):
                    # 本段按角速度步进走完（内部已按节拍 sleep，无需再等）
                    self.move(targets, speed_dps=speed_dps, start=previous,
                              step_interval_ms=step_interval_ms,
                              max_load=max_load, level=level,
                              load_check_every=load_check_every,
                              torque=torque, sleep=sleep, led=led)
                elif wait_frame:
                    # 该帧没有任何关节需要动（保持姿势）：沿用文件 Period 作为停留时间
                    sleep(max(1, int(round(frame.period_ms))) / 1000.0)
                previous = targets
                if on_frame is not None:
                    on_frame(frame)
                played += 1
            if not loop:
                return played

    def play_rsc(self, path: str, *, scene: Optional[int] = None,
                 loop: bool = False, speed_dps: Optional[float] = None,
                 speed_scale: float = 1.0,
                 torque: int = P.ON, mode: str = "position",
                 start_positions: Optional[Mapping[int, int]] = None,
                 step_interval_ms: Optional[int] = None,
                 ids: Optional[Iterable[int]] = None,
                 level: Optional[int] = None,
                 max_load: Optional[int] = None,
                 load_check_every: int = 1,
                 sleep: Callable[[float], None] = time.sleep,
                 on_frame: Optional[Callable] = None,
                 stop_event: Optional[threading.Event] = None) -> int:
        """加载 ``.rsc`` 并播放其中的动作帧（各参数含义见 `play`）。"""
        project = RscProject.load(path)
        return self.play(project.frames(scene=scene), loop=loop,
                         speed_dps=speed_dps, speed_scale=speed_scale,
                         torque=torque, mode=mode,
                         start_positions=start_positions,
                         step_interval_ms=step_interval_ms,
                         ids=ids, level=level,
                         max_load=max_load, load_check_every=load_check_every,
                         sleep=sleep,
                         on_frame=on_frame, stop_event=stop_event)

    # -- 控制器板（整机）兼容接口 ------------------------------------------ #

    def controller_status(self, *, timeout: Optional[float] = None) -> P.Response:
        return self.request(P.build_ctrl_control_status(),
                            expect_id=P.CONTROLLER_ID, timeout=timeout)

    def controller_play(self, *, timeout: Optional[float] = None) -> P.Response:
        """命令控制器执行已存储的程序。"""
        return self.request(P.build_ctrl_play(), expect_id=P.CONTROLLER_ID,
                            timeout=timeout)

    def controller_sequence(self, *, timeout: Optional[float] = None) -> P.Response:
        return self.request(P.build_ctrl_sequence(), expect_id=P.CONTROLLER_ID,
                            timeout=timeout)

    def controller_set_led(self, id_: int, value: int) -> None:
        self.send(P.build_ctrl_set_led(id_, value))

    def controller_set_torque(self, id_: int, torque: int) -> None:
        self.send(P.build_ctrl_set_torque(id_, torque))

    def controller_download_end(self, scene_num: int) -> None:
        """动作下载结束标记。"""
        self.send(P.build_ctrl_download_end(scene_num))

    # -- 内部 -------------------------------------------------------------- #

    def _known_ids(self) -> List[int]:
        if self._servos:
            return sorted(self._servos)
        return list(range(P.SERVO_ID_MIN, P.SERVO_ID_MIN + 17))

    def _await_any(self, id_: int, timeout: float) -> Optional[P.Response]:
        try:
            return self.request(P.build_status(id_), expect_id=id_, timeout=timeout,
                                raise_on_error=False)
        except (TimeoutError, P.ProtocolError):
            return None

    def __repr__(self) -> str:
        return f"<RomanboRobot on {self.transport.name} open={self.is_open}>"


def connect(port: Optional[str] = None, *, mock: bool = False,
            handshake: bool = True, verbose: bool = False,
            **kwargs) -> RomanboRobot:
    """便捷入口：打开连接（``mock=True`` 时使用离线模拟器）。"""
    if mock:
        transport: Transport = MockTransport(servo_ids=range(1, 18))
        robot = RomanboRobot(transport=transport, **kwargs)
    else:
        robot = RomanboRobot(port, **kwargs)
    robot.open()
    if handshake and not mock:
        robot.handshake(verbose=verbose)
    return robot


__all__ = ["RomanboRobot", "HandshakeResult", "connect",
           "REQUIRED_MODEL_CODE", "REQUIRED_FIRMWARE_VERSION"]
