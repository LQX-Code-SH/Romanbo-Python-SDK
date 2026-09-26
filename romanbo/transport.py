"""传输层：真实串口（pyserial）与离线模拟器。

本层负责原始字节的收发与缓冲，向上屏蔽串口与离线模拟的差异。

串口参数：**115200 8N1、DTR/RTS 置位**。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Sequence

from . import protocol as P


class Transport(ABC):
    """字节流传输抽象。"""

    name = "abstract"

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def write(self, frame: bytes) -> None:
        """原样写出一帧。"""

    @abstractmethod
    def read_available(self, timeout: float) -> bytes:
        """在 ``timeout`` 秒内尽量读出已到达的字节（无数据返回 ``b""``）。"""

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SerialTransport(Transport):
    """基于 pyserial 的真实串口（跨平台：Windows ``COM3`` / Linux ``/dev/ttyUSB0``）。

    !!! note
        ``exclusive=True`` 在 **POSIX 上启用排他打开**（``TIOCEXCL``）——Linux 的串口
        默认是**共享**的，两个进程能同时打开同一个 ``/dev/ttyUSB0`` 并互相打乱收发；
        Windows 本身独占，pyserial 会忽略该参数，所以默认开启即可两边都安全。
    """

    def __init__(self, port: str, baudrate: int = P.BAUDRATE_DEFAULT,
                 *, dtr: bool = True, rts: bool = True, exclusive: bool = True,
                 read_timeout: float = 0.05, write_timeout: float = 0.5) -> None:
        self.name = port
        self._port = port
        self._baudrate = baudrate
        self._dtr, self._rts = dtr, rts
        self._exclusive = exclusive
        self._read_timeout = read_timeout
        self._write_timeout = write_timeout
        self._ser = None
        #: 上一帧写完的时刻（高精度单调时钟）；用于强制 ``P.MIN_FRAME_GAP`` 帧间隔
        self._last_write = 0.0

    def open(self) -> None:
        if self._ser is not None:
            return
        try:
            import serial  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("需要 pyserial：pip install pyserial") from exc

        self._ser = serial.Serial(
            port=self._port,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._read_timeout,
            write_timeout=self._write_timeout,
            exclusive=self._exclusive,      # POSIX 排他；Windows 忽略
        )
        self._last_write = 0.0              # 首帧不等待
        try:
            self._ser.dtr = self._dtr
            self._ser.rts = self._rts
        except Exception:  # 某些驱动不支持，忽略
            pass
        try:
            self._ser.reset_input_buffer()
        except Exception:
            pass

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    @property
    def is_open(self) -> bool:
        return bool(self._ser is not None and getattr(self._ser, "is_open", False))

    def write(self, frame: bytes) -> None:
        """写出一帧，并保证与上一帧之间至少间隔 `romanbo.protocol.MIN_FRAME_GAP`。

        !!! important
            实测（2026-09-26，ID 8/10）：**两帧之间没有间隔时，后发的那一帧会被
            舵机静默丢弃**（交换 ID 顺序后被丢的永远是第二帧，与 ID 无关）；
            间隔 >= 2 ms 时两帧均正常。多关节动作是逐关节连发，若不在写口兜底，
            ``move`` / ``play`` 会出现"只有第一个关节生效"——因此这里统一节流，
            而不是在每个调用点各自 ``sleep``。

        !!! note
            计时用 ``time.perf_counter()`` 而**不是** ``time.monotonic()``：Windows 上
            ``monotonic()`` 在 CPython <= 3.12 走 ``GetTickCount64()``，精度受系统
            计时器增量限制（典型约 15.6 ms）——2 ms 的间隔根本量不出来，跨刻度时
            还会误判"已经过了 15.6 ms"而跳过节流，真机上就可能丢帧（3.13 起才改用
            ``QueryPerformanceCounter()``）。本库支持 3.8+，所以统一用
            ``perf_counter()``（Windows 走 QPC、Linux 走 ``CLOCK_MONOTONIC``、
            macOS 走 ``mach_absolute_time``），三平台都能正确度量。
        """
        if not self.is_open:
            raise RuntimeError(f"串口未打开: {self._port}")
        wait = P.MIN_FRAME_GAP - (time.perf_counter() - self._last_write)
        if wait > 0:
            time.sleep(wait)
        self._ser.write(frame)
        self._last_write = time.perf_counter()

    def read_available(self, timeout: float) -> bytes:
        if not self.is_open:
            return b""
        deadline = time.monotonic() + timeout
        while True:
            waiting = self._ser.in_waiting
            if waiting:
                return self._ser.read(waiting)
            if time.monotonic() >= deadline:
                return b""
            time.sleep(0.002)


class MockTransport(Transport):
    """离线模拟器：记录发送的帧，并按协议自动生成应答。

    用于在没有机器人时验证协议编码、跑通上层逻辑与单元测试。
    """

    def __init__(
        self,
        servo_ids: Iterable[int] = (1,),
        *,
        controller: bool = True,
        model: int = 0x70,
        version: int = 8,
        positions: Optional[Dict[int, int]] = None,
        pids: Optional[Dict[int, Sequence[int]]] = None,
        period_ms: int = 500,
        load: int = 100,
        current_limit: Optional[int] = None,
        accelerate: int = 0,
        margin: int = 5,
        temperature: int = 40,
        position_limit: Sequence[int] = (255, 768),
        response_delay: float = 0.0,
        auto_ack: bool = True,
        drop_every: int = 0,
    ) -> None:
        self.name = "mock"
        self.servo_ids = list(servo_ids)
        self.controller = controller
        self.model = model
        self.version = version
        self.positions: Dict[int, int] = dict(positions or {})
        self.pids: Dict[int, Sequence[int]] = {i: (50, 0, 5) for i in self.servo_ids}
        self.pids.update(pids or {})
        self.period_ms = period_ms
        #: 模拟的**实测负荷**（0x18）。测试限力时改这个值即可。
        self.load = load if current_limit is None else current_limit
        self.accelerate = accelerate
        self.margin = margin
        self.temperature = temperature
        self.position_limit = tuple(position_limit)
        self.response_delay = response_delay
        self.auto_ack = auto_ack
        self.drop_every = drop_every          # >0 时每隔 N 帧丢弃一次应答
        self.sent: List[bytes] = []           # 所有发出的帧
        self._rx = bytearray()
        self._open = False

    # -- Transport 接口 ---------------------------------------------------- #

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, frame: bytes) -> None:
        if not self._open:
            raise RuntimeError("mock 未打开")
        self.sent.append(bytes(frame))
        if not self.auto_ack:
            return
        if self.drop_every and len(self.sent) % self.drop_every == 0:
            return
        reply = self._make_reply(frame)
        if reply:
            if self.response_delay:
                time.sleep(self.response_delay)
            self._rx.extend(reply)

    def read_available(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._rx:
                data = bytes(self._rx)
                self._rx.clear()
                return data
            time.sleep(0.001)
        return b""

    # -- 工具 -------------------------------------------------------------- #

    def last_frame(self) -> Optional[bytes]:
        return self.sent[-1] if self.sent else None

    def frames_for(self, id_: int, cmd: int) -> List[bytes]:
        out = []
        for f in self.sent:
            if len(f) >= 5 and f[2] == id_ and f[4] == cmd:
                out.append(f)
        return out

    # -- 应答生成 ---------------------------------------------------------- #

    def _make_reply(self, frame: bytes) -> bytes:
        try:
            req = P.parse_frame(frame, strict=False)
        except P.ProtocolError:
            return b""

        if req.id == P.CONTROLLER_ID:
            if not self.controller:
                return b""
            return self._controller_reply(req)

        if req.id not in self.servo_ids:
            return b""            # 该 ID 无设备 → 不应答（等价于超时）
        return self._servo_reply(req)

    def _controller_reply(self, req: P.Response) -> bytes:
        cmd = req.cmd
        if cmd == P.ServoCmd.GET_MODEL:
            # 控制器型号回包：第 2 个数据字节为型号码（0x70 表示 ROMANBO）
            return P.make_frame(0x81, req.id, bytes([0x00, self.model]))
        if cmd == P.ServoCmd.GET_VERSION:
            return P.make_frame(0x82, req.id, bytes([self.version]))
        if cmd == P.CtrlCmd.CONTROL_STATUS:
            return P.make_frame(P.CtrlAck.CONTROL_STATUS, req.id, bytes([0x00]))
        return P.make_frame(P.ack_for(cmd), req.id, bytes([0x00]))

    @staticmethod
    def _replied(*values: int) -> bytes:
        """按实测格式构造数值：前导状态字节 0x00 + 数值。"""
        return bytes([0x00, *[v & 0xFF for v in values]])

    def _servo_reply(self, req: P.Response) -> bytes:
        cmd, sid = req.cmd, req.id
        if cmd == P.ServoCmd.GET_MODEL:
            return P.make_frame(P.ServoAck.GET_MODEL, sid, self._replied(0x05))
        if cmd == P.ServoCmd.GET_VERSION:
            return P.make_frame(P.ServoAck.GET_VERSION, sid, self._replied(self.version))
        if cmd == P.ServoCmd.STATUS:
            return P.make_frame(P.ServoAck.STATUS, sid, self._replied())
        if cmd == P.ServoCmd.GET_POSITION:
            pos = self.positions.get(sid, P.ADC_CENTER) & 0xFFFF
            return P.make_frame(P.ServoAck.GET_POSITION, sid,
                                self._replied((pos >> 8) & 0xFF, pos & 0xFF))
        if cmd == P.ServoCmd.GET_PID:
            p, i, d = self.pids.get(sid, (50, 0, 5))
            return P.make_frame(P.ServoAck.GET_PID, sid,
                                self._replied(p, i, d))
        if cmd == P.ServoCmd.GET_TEMP:
            return P.make_frame(P.ServoAck.GET_TEMP, sid,
                                self._replied(self.temperature))
        if cmd == P.ServoCmd.GET_MARGIN:
            return P.make_frame(P.ServoAck.GET_MARGIN, sid, self._replied(self.margin))
        if cmd == P.ServoCmd.GET_LOAD:
            return P.make_frame(P.ServoAck.GET_CURRENT_LIMIT, sid,
                                self._replied(self.load))
        if cmd == P.ServoCmd.GET_ACCELERATE:
            return P.make_frame(P.ServoAck.GET_ACCELERATE, sid,
                                self._replied(self.accelerate))
        if cmd == P.ServoCmd.GET_POSITION_LIMIT:
            lo, hi = self.position_limit
            return P.make_frame(P.ServoAck.GET_POSITION_LIMIT, sid,
                                self._replied((lo >> 8) & 0xFF, lo & 0xFF,
                                              (hi >> 8) & 0xFF, hi & 0xFF))
        if cmd == P.ServoCmd.SET_POSITION_LIMIT and len(req.data) >= 4:
            # 0x0F 与 GET_MOTION_PERIOD 同码：真机靠「是否带 4 字节数据」区分，
            # 这里同样处理（带数据 = SetPositionLimit）。
            self.position_limit = ((req.data[0] << 8) | req.data[1],
                                   (req.data[2] << 8) | req.data[3])
            return P.make_frame(P.ServoAck.SET_POSITION_LIMIT, sid)
        if cmd == P.ServoCmd.GET_MOTION_PERIOD:
            return P.make_frame(P.ack_for(cmd), sid,
                                self._replied((self.period_ms >> 8) & 0xFF,
                                              self.period_ms & 0xFF))
        if cmd == P.ServoCmd.SET_POSITION:
            # 位置/轮子指令：更新模拟位置（仅做粗略模拟）
            if len(req.data) >= 2:
                self.positions[sid] = ((req.data[0] & 0x07) << 8) | req.data[1]
            return P.make_frame(P.ServoAck.SET_POSITION, sid)
        if cmd == P.ServoCmd.SET_NEXT_POSITION:
            if len(req.data) >= 2:
                self.positions[sid] = ((req.data[0] & 0x07) << 8) | req.data[1]
            return P.make_frame(P.ack_for(cmd), sid)
        if cmd == P.ServoCmd.SET_PERIOD:
            if len(req.data) >= 2:
                self.period_ms = (req.data[0] << 8) | req.data[1]
            return P.make_frame(P.ServoAck.SET_MOTION_PERIOD, sid)
        if cmd == P.ServoCmd.SET_ACCELERATE:
            if req.data:
                self.accelerate = req.data[0]
            return P.make_frame(P.ServoAck.SET_ACCELERATE, sid)
        if cmd == P.ServoCmd.SET_ID:
            return P.make_frame(P.ServoAck.SET_ID, sid)
        if cmd == P.ServoCmd.SET_TORQUE:
            return P.make_frame(P.ServoAck.SET_TORQUE, sid)
        if cmd == P.ServoCmd.SET_LED:
            return P.make_frame(P.ServoAck.SET_LED, sid)
        if cmd == P.ServoCmd.SET_POSITION_LIMIT:
            # 带数据的情况已在前面（与 GET_MOTION_PERIOD 同码处）处理
            return P.make_frame(P.ServoAck.SET_POSITION_LIMIT, sid)
        if cmd == P.ServoCmd.SET_PID or cmd == P.ServoCmd.SET_PID_NOSAVE:
            if len(req.data) >= 3:
                self.pids[sid] = (req.data[0], req.data[1], req.data[2])
            return P.make_frame(P.ServoAck.SET_PID, sid)
        if cmd == P.ServoCmd.GET_CALIBRATION:
            # 界面里 DefaultCalibration = 47
            return P.make_frame(P.ServoAck.GET_CALIBRATION, sid, self._replied(47))
        if cmd == P.ServoCmd.GET_MOTOR:
            return P.make_frame(P.ServoAck.GET_MOTOR, sid, self._replied(0x00, sid))
        if cmd == P.ServoCmd.SET_SYNC:
            return P.make_frame(P.ack_for(cmd), sid)
        if cmd == P.ServoCmd.SET_CALIBRATION_CURRPOS:
            return P.make_frame(P.ServoAck.SET_CALIBRATION, sid)
        if cmd == P.ServoCmd.SET_REBOOT:
            return P.make_frame(P.ServoAck.GET_VERSION, sid)
        if cmd == P.ServoCmd.RESET:
            return P.make_frame(P.ServoAck.RESET, sid)
        return P.make_frame(P.ack_for(cmd), sid)


__all__ = ["Transport", "SerialTransport", "MockTransport"]
