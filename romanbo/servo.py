"""单个舵机的高层封装：位置/轮子/扭矩/周期/PID/LED/限位/校准/回读。

覆盖单个舵机的全部命令与参数回读功能。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import joints as J
from . import protocol as P

#: 位置值在**剥离前导状态字节后**的数据段内的偏移（实测为 0，两字节大端）
POSITION_DATA_OFFSET = 0

#: 往复运动（``sweep``）在既未给周期也未给角速度时使用的单程周期
DEFAULT_SWEEP_PERIOD_MS = 800


@dataclass
class ServoConfig:
    """一次读回的全部可读参数（部分项失败时为 ``None``）。"""

    id: int
    position: Optional[int] = None
    pid: Optional[Tuple[int, int, int]] = None
    period_ms: Optional[int] = None
    position_limit: Optional[Tuple[int, int]] = None
    #: **实测负荷**（``0x18``，界面「负荷」），非限制值。静止为 0。
    load: Optional[int] = None
    acceleration: Optional[int] = None
    margin: Optional[int] = None
    temperature: Optional[int] = None
    calibration: Optional[int] = None

    @property
    def current_limit(self) -> Optional[int]:
        """旧名，等价于 :attr:`load`（该字段读的是实测负荷）。"""
        return self.load

    @property
    def calibration_offset(self) -> Optional[int]:
        """零点偏差实际值（``calibration − 128``，协议文档语义）。"""
        return None if self.calibration is None else self.calibration - 128

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "position": self.position,
            "pid": self.pid,
            "period_ms": self.period_ms,
            "position_limit": self.position_limit,
            "load": self.load,
            "acceleration": self.acceleration,
            "margin": self.margin,
            "temperature": self.temperature,
            "calibration": self.calibration,
            "calibration_offset": self.calibration_offset,
        }


class LoadLimitExceeded(Exception):
    """运动过程中实测负荷超过阈值（软件限力触发）。

    :param id: 触发限力的舵机 ID
    :param load: 触发时读到的负荷值
    :param limit: 设定的阈值
    :param step: 触发时的步进序号（1 起）
    """

    def __init__(self, id_: int, load: int, limit: int, step: int = 0,
                 position: Optional[int] = None) -> None:
        self.id = id_
        self.load = load
        self.limit = limit
        self.step = step
        self.position = position
        super().__init__(
            f"舵机 {id_} 负荷 {load} 超过阈值 {limit}（第 {step} 步）")

    def as_dict(self) -> Dict[str, object]:
        return {"id": self.id, "load": self.load, "limit": self.limit,
                "step": self.step, "position": self.position}


class Servo:
    """一个舵机的命令集合。

    实测行为（2026-09-25，ID=10，固件 v1.0 舵机）：

    * **GET 类命令（0x05、0x12~0x1A、0x7B）会应答**，应答码 ``0x80|cmd``，
      数据段首字节恒为 ``0x00``（状态位），数值从第 2 字节开始；
      **应答延迟实测仅 10~20 ms**。
      但要注意**总线静默期**：一条寻址到无设备 ID 的帧之后，舵机约 0.4 s 内
      不再应答（见 :data:`romanbo.protocol.BUS_QUARANTINE`）——这才是"看起来
      很慢"的真正原因。
    * **SET 类命令（0x06~0x11、0x47、0x20、0x21、0x23…）不应答**
      （实测 ``SetTorque`` 超时无回包），因此 ``set_*`` 默认「只发不等」，
      与「只发不等」的标准行为一致。

    因此：``set_*`` 传 ``wait=True`` 通常会超时，属正常现象；要确认动作结果
    请用 ``get_position()`` 回读。
    """

    def __init__(self, robot: "RomanboRobot", id_: int) -> None:  # noqa: F821
        if not (P.SERVO_ID_MIN <= id_ <= P.SERVO_ID_MAX):
            raise ValueError(f"舵机 ID 需在 {P.SERVO_ID_MIN}..{P.SERVO_ID_MAX} 之间")
        self._robot = robot
        self._id = id_
        #: 最近一次带 ``max_load`` 的运动中观察到的**峰值负荷**（供报告用）
        self.last_peak_load: Optional[int] = None

    # -- 基础 -------------------------------------------------------------- #

    @property
    def id(self) -> int:
        return self._id

    @property
    def robot(self) -> "RomanboRobot":  # noqa: F821
        return self._robot

    def _send(self, frame: bytes, *, wait: bool, timeout: Optional[float] = None,
              retries: Optional[int] = None):
        # GET 类命令的应答码已实测确认，用它精确匹配，避免把上一条命令的
        # 迟到回包错认成当前应答（真机实测出现过串位）
        expect = P.expected_ack(frame[4]) if len(frame) > 4 else None
        return self._robot.send_command(frame, expect_id=self._id, wait=wait,
                                        timeout=timeout, expect_cmd=expect,
                                        retries=retries)

    def __repr__(self) -> str:
        return f"<Servo #{self._id} on {self._robot.transport.name}>"

    # -- 运动 -------------------------------------------------------------- #

    def set_position(self, position: int, *, period_ms: Optional[int] = None,
                     torque: int = P.LEVEL_MIDDLE, relative: int = 0,
                     level: Optional[int] = None,
                     wait: bool = False) -> None:
        """位置指令（ADC 0..1023，512 为中位）。

        :param level: **出力档位**（:data:`~romanbo.protocol.LEVEL_HIGH`=0 最大、
            ``LEVEL_MIDDLE``=1、``LEVEL_LOW``=2、``LEVEL_WHEEL``=3 不生效）。
            实测同一位移下峰值负荷：H 178 / M 110 / L 38。
            缺省沿用 ``torque`` 参数（默认 1 = M）。
        :param torque: ``level`` 的旧名；**它不是使能开关**——使能用
            :meth:`torque`（``0x10``），档位 0 也照样能运动。
        :param period_ms: 非空时先发 ``SET_PERIOD``（协议文档行为；实测该固件忽略它）。
        """
        if period_ms is not None:
            self.set_period(period_ms, wait=False)
        self._send(P.build_set_position(self._id, position, torque=torque,
                                        relative=relative, level=level),
                   wait=wait)

    #: 与 :meth:`set_position` 同名语义的别名
    move = set_position

    def move_at_speed(self, target: int, dps: float, *,
                      current: Optional[int] = None,
                      interval_ms: Optional[int] = None,
                      torque: Optional[int] = None, wait: bool = True,
                      level: Optional[int] = None,
                      max_load: Optional[int] = None,
                      load_check_every: int = 1,
                      sleep: Callable[[float], None] = time.sleep,
                      on_step: Optional[Callable[[int, int], None]] = None
                      ) -> List[int]:
        """以**实际角速度**（度/秒）运动到 ``target``（ADC），返回中间目标序列。

        .. important::
            实测该舵机固件**忽略 ``SET_PERIOD(0x0B)``**（周期 60 / 3000 / 8000 ms
            下，44° 位移都以最大速度约 0.24 s 走完），所以角速度只能靠
            **步进逼近**实现：每隔 ``interval_ms`` 下发一个中间目标，使平均角速度
            = ``dps``。间隔默认由 :func:`romanbo.joints.pick_step_interval` 按
            角速度选取（保证每步至少 1 个 ADC）。

        :param current: 已知当前位置；为 ``None`` 时先回读一次。
        :param torque: **使能开关**（``0x10``）；非 ``None`` 时先下发。
        :param level: 每步位置指令的**出力档位**（0/1/2，见 :meth:`set_position`）。
        :param wait: 每一步后 ``sleep(interval)``，返回时该次运动已按节奏走完。
        :param max_load: **软件限力阈值**（0..255）。每 ``load_check_every`` 步
            回读一次实测负荷（``0x18``），超过阈值即**停止继续下发**并抛出
            :class:`LoadLimitExceeded`——这台硬件没有可用的硬件力矩环，这是唯一
            的限力手段。注意每次负荷回读会占用 ~10~30 ms 往返（会略微拖慢节奏）。
        """
        if current is None:
            current = self.get_position()
        steps = J.plan_move(current, target, dps, interval_ms=interval_ms)
        if torque is not None:
            self.torque(bool(torque))
        every = max(1, int(load_check_every))
        sent: List[int] = []
        self.last_peak_load = None
        for index, (dt, adc) in enumerate(steps, start=1):
            started = time.monotonic()
            self.set_position(adc, level=level)
            sent.append(adc)
            if on_step is not None:
                on_step(index, adc)
            if max_load is not None and index % every == 0:
                load = self.get_load()
                self.last_peak_load = load if self.last_peak_load is None \
                    else max(self.last_peak_load, load)
                if load > max_load:
                    raise LoadLimitExceeded(self._id, load, int(max_load), index, adc)
            if wait:
                if max_load is None:
                    sleep(dt)                       # 常规路径：严格按节拍
                else:
                    budget = dt - (time.monotonic() - started)
                    sleep(budget if budget > 0 else 0.0)
        return sent

    def set_angle(self, degrees: float, *, period_ms: Optional[int] = None,
                  speed_dps: Optional[float] = None, current: Optional[int] = None,
                  torque: int = P.ON, wait: bool = False,
                  interval_ms: Optional[int] = None,
                  level: Optional[int] = None,
                  max_load: Optional[int] = None,
                  sleep: Callable[[float], None] = time.sleep,
                  ratio: float = J.RATIO_MAIN, center: int = J.ADC_CENTER) -> int:
        """按**绝对角度**定位（以机械中点 512 为 0°，正方向为 ADC 增大）。

        换算比例来自常量 ``Ratio = 0.2932551``（≈300/1023），
        即 30° ≈ 102 个 ADC 单位。返回实际下发的 ADC 目标值。

        :param speed_dps: 目标**角速度（度/秒）**，用**步进逼近**实现
            （见 :meth:`move_at_speed`）；需要当前位置（未传 ``current``
            时先回读一次）。与 ``period_ms`` 互斥。
        :param period_ms: 协议文档的 ``SET_PERIOD`` 参数。注意实测舵机固件会
            忽略它，因此它**并不**决定角速度。
        """
        target = J.angle_to_adc(degrees, ratio=ratio, center=center)
        if speed_dps is None:
            self.set_position(target, period_ms=period_ms, torque=torque,
                              wait=wait, level=level)
        else:
            if period_ms is not None:
                raise ValueError("period_ms 与 speed_dps 只能给出一个")
            self.move_at_speed(target, speed_dps, current=current,
                               interval_ms=interval_ms, torque=torque,
                               level=level, max_load=max_load, sleep=sleep)
        return target

    def rotate(self, degrees: float, *, period_ms: Optional[int] = None,
               speed_dps: Optional[float] = None, torque: int = P.ON,
               wait: bool = False, current: Optional[int] = None,
               interval_ms: Optional[int] = None,
               level: Optional[int] = None,
               max_load: Optional[int] = None,
               sleep: Callable[[float], None] = time.sleep,
               ratio: float = J.RATIO_MAIN) -> Tuple[int, int]:
        """以**当前位置为基准**转动 ``degrees`` 度，返回 ``(原位置, 目标位置)``。

        :param current: 已知的当前位置；为 ``None`` 时自动回读（多一次串口往返）。
            注意**不要**用机械中点代替它，否则基准会偏移。
        :param speed_dps: 目标**角速度（度/秒）**，用步进逼近实现，见
            :meth:`move_at_speed`。

        常规下发顺序：``SET_PERIOD`` → ``SET_POSITION``。
        """
        if current is None:
            current = self.get_position()
        delta = int(round(degrees / ratio))
        target = max(J.ADC_MIN, min(J.ADC_MAX, current + delta))
        if speed_dps is None:
            self.set_position(target, period_ms=period_ms, torque=torque,
                              wait=wait, level=level)
        else:
            if period_ms is not None:
                raise ValueError("period_ms 与 speed_dps 只能给出一个")
            self.move_at_speed(target, speed_dps, current=current,
                               interval_ms=interval_ms, torque=torque,
                               level=level, max_load=max_load, sleep=sleep)
        return current, target

    #: 语义别名：相对转动
    jog_angle = rotate

    def sweep(self, low: int, high: int, *, cycles: int = 3,
              period_ms: Optional[int] = None, speed_dps: Optional[float] = None,
              torque: int = P.ON,
              readback: bool = True, settle: float = 0.3,
              return_home: bool = True,
              interval_ms: Optional[int] = None,
              level: Optional[int] = None,
              max_load: Optional[int] = None,
              sleep: Callable[[float], None] = time.sleep,
              on_step: Optional[Callable[[Dict[str, object]], None]] = None
              ) -> List[Dict[str, object]]:
        """在 ``low``..``high``（ADC）之间往复运动，返回每一步的记录。

        对应「反复动作」功能。实现要点：

        * 先回读起始位置（``return_home=True`` 时用于结束时归位）
        * 开始时置扭矩；``period_ms`` 模式下发一次 ``SET_PERIOD``
        * 每步发 ``SET_POSITION`` → 等 ``period_ms + settle`` → 回读位置
        * 每步记录 ``{step, target, readback, error, elapsed_s}``

        ``speed_dps``（**度/秒**）与 ``period_ms`` 二选一。给出角速度时改用
        **步进逼近**（见 :meth:`move_at_speed`），两个方向的实际角速度一致。

        .. note::
            往复序列是「先到 high 再到 low」，结束时停在 low。为便于连续多次
            运行（否则中心会逐次漂移），默认在结束时回到起始位置。

        .. warning::
            舵机 SET 类命令无应答，因此这里用**回读位置**来确认动作结果；
            回读多占一次串口往返，实测仅约 10~20 ms。
        """
        low, high = sorted((int(low), int(high)))
        low = max(J.ADC_MIN, low)
        high = min(J.ADC_MAX, high)
        if high <= low:
            raise ValueError(f"往复范围无效: {low}..{high}")

        stepwise = speed_dps is not None
        if stepwise and period_ms is not None:
            raise ValueError("period_ms 与 speed_dps 只能给出一个")
        period = int(period_ms) if period_ms else DEFAULT_SWEEP_PERIOD_MS

        home = self.get_position() if (return_home or stepwise) else None
        if torque is not None:
            self.torque(bool(torque))
            time.sleep(0.05)
        if not stepwise:
            self.set_period(period)

        sequence: List[int] = []
        for _ in range(max(1, int(cycles))):
            sequence.extend([high, low])

        records: List[Dict[str, object]] = []
        current = home if home is not None else self.get_position()
        for index, target in enumerate(sequence, start=1):
            started = time.monotonic()
            if stepwise:
                self.move_at_speed(target, float(speed_dps), current=current,
                                   interval_ms=interval_ms, torque=None,
                                   level=level, max_load=max_load, sleep=sleep)
                current = target
                sleep(settle)
            else:
                self.set_position(target, level=level)
                time.sleep(period / 1000.0 + settle)
            actual = self.get_position() if readback else None
            record: Dict[str, object] = {
                "step": index,
                "target": target,
                "readback": actual,
                "error": None if actual is None else int(actual) - target,
                "elapsed_s": round(time.monotonic() - started, 3),
            }
            records.append(record)
            if on_step is not None:
                on_step(record)

        if return_home and home is not None:
            if stepwise:
                self.move_at_speed(home, float(speed_dps), current=current,
                                   interval_ms=interval_ms, torque=None,
                                   level=level, max_load=max_load, sleep=sleep)
                sleep(settle)
            else:
                self.set_position(home, level=level)
                time.sleep(period / 1000.0 + settle)
        return records

    def next_position(self, position: int, *, period_ms: Optional[int] = None,
                      torque: int = P.ON, relative: int = 0, freewheel: int = 0,
                      ledkind: int = 0, angle: int = 0, sync: bool = False,
                      wait: bool = False) -> None:
        """预置下一目标位置（``SET_NEXT_POSITION 0x21``）。

        ``sync=True`` 时紧接着发 ``SET_SYNC``，用于多关节同步执行。
        """
        if period_ms is not None:
            self.set_period(period_ms, wait=False)
        self._send(P.build_set_next_position(
            self._id, position, torque=torque, relative=relative,
            freewheel=freewheel, ledkind=ledkind, angle=angle), wait=wait)
        if sync:
            self.sync()

    def sync(self, *, wait: bool = False) -> None:
        """触发已下发的目标（``SET_SYNC 0x20``）。"""
        self._send(P.build_set_sync(self._id), wait=wait)

    def wheel(self, speed: int, *, direction: int = J.WHEEL_CW,
              free: bool = False, relative: bool = False,
              torque: int = P.ON, wait: bool = False) -> None:
        """轮子模式：``speed`` 0..255，``direction`` 0=CW/1=CCW。"""
        self._send(P.build_set_wheel(self._id, speed, torque=torque,
                                     relative=1 if relative else 0,
                                     free=1 if free else 0,
                                     direction=direction), wait=wait)

    def stop_wheel(self, *, wait: bool = False) -> None:
        """停止轮子（速度 0）。"""
        self.wheel(0, free=True, wait=wait)

    def torque(self, on: bool = True, *, wait: bool = False) -> None:
        """扭矩开关；``False`` 后关节可自由扳动（示教前必须执行）。"""
        self._send(P.build_set_torque(self._id, P.ON if on else P.OFF), wait=wait)

    def jog(self, delta: int, *, period_ms: Optional[int] = None) -> None:
        """以当前位置为基准点动 ``delta`` 个 ADC 单位。"""
        current = self.get_position()
        target = max(J.ADC_MIN, min(J.ADC_MAX, current + delta))
        self.set_position(target, period_ms=period_ms)

    # -- 运动参数 ---------------------------------------------------------- #

    def set_period(self, period_ms: int, *, wait: bool = False) -> None:
        """运动周期/速度（ms，16 位）。"""
        self._send(P.build_set_period(self._id, period_ms), wait=wait)

    def set_pid(self, p: int, i: int, d: int, *, save: bool = True,
                verify: bool = True, retries: int = 4,
                timeout: Optional[float] = None,
                wait: bool = False) -> Optional[Tuple[int, int, int]]:
        """设置 PID（P/I/D 各 0..255），默认**回读确认**。

        .. important::
            实测（2026-09-25，ID 8，隔离读取重复 3 次）：**保存式写入 ``0x07``
            不可靠**——3 次里只有 1 次改变了回读值；``0x47``（界面「PID 不保存」）
            **3/3 立即生效**（写 ``200/3/30`` 后回读即为该值）。因此本方法：

            1. 先发 ``0x47``（RAM，立即生效）；
            2. ``save=True``（默认）时补发 ``0x07`` 尝试持久化到闪存；
            3. ``verify=True``（默认）回读确认，不一致就重试 ``retries`` 次，
               仍不一致抛 :class:`~romanbo.protocol.ProtocolError`。

            SET 类命令没有 ACK，不校验就可能**静默丢写**（本机实测确实会发生）。

        :returns: 回读到的 ``(P, I, D)``；``verify=False`` 时返回 ``None``。
        """
        target = (int(p) & 0xFF, int(i) & 0xFF, int(d) & 0xFF)
        if wait and not verify:
            self._send(P.build_set_pid(self._id, *target, save=save), wait=wait)
            return None
        got: Optional[Tuple[int, int, int]] = None
        for _ in range(max(1, int(retries))):
            drain = getattr(self._robot, "drain", None)
            if drain is not None:
                drain()                    # 丢弃残留应答，避免把旧值当成新值
            self._send(P.build_set_pid(self._id, *target, save=False), wait=False)
            time.sleep(0.05)
            if not verify:
                if save:
                    self._send(P.build_set_pid(self._id, *target, save=True),
                               wait=False)
                return None
            try:
                got = self.get_pid(timeout=timeout)
            except Exception:                                # noqa: BLE001
                got = None
            if got == target:
                if save:
                    self._send(P.build_set_pid(self._id, *target, save=True),
                               wait=False)
                return got
        raise P.ProtocolError(
            f"舵机 {self._id} PID 写入未生效：期望 {target}，回读 {got}")

    def set_position_limit(self, minimum: int, maximum: int,
                           *, verify: bool = True, retries: int = 4,
                           timeout: Optional[float] = None,
                           wait: bool = False) -> Optional[Tuple[int, int]]:
        """位置限值（界面「最大位置值」）。默认**回读确认**。

        .. warning::
            限值是**硬夹紧**（实测：命令越限会被夹到限值处、且不再持续出力），
            而且**掉电保存**。写错范围会限制关节行程，所以这里和 :meth:`set_pid`
            一样带校验：写完回读，不一致重试 ``retries`` 次，仍不一致抛
            :class:`~romanbo.protocol.ProtocolError`——``SET`` 类命令没有 ACK，
            实测确实会**静默丢写**。

        :returns: 回读到的 ``(min, max)``；``verify=False`` 时返回 ``None``。
        """
        target = (int(minimum) & 0xFFFF, int(maximum) & 0xFFFF)
        if wait and not verify:
            self._send(P.build_set_position_limit(self._id, *target), wait=wait)
            return None
        got: Optional[Tuple[int, int]] = None
        for _ in range(max(1, int(retries))):
            drain = getattr(self._robot, "drain", None)
            if drain is not None:
                drain()
            self._send(P.build_set_position_limit(self._id, *target), wait=False)
            time.sleep(0.05)
            if not verify:
                return None
            try:
                got = self.get_position_limit(timeout=timeout)
            except Exception:                                # noqa: BLE001
                got = None
            if got == target:
                return got
        raise P.ProtocolError(
            f"舵机 {self._id} 位置限值写入未生效：期望 {target}，回读 {got}")

    def set_load_limit(self, limit: int, *, wait: bool = False) -> None:
        """设「负荷」上限（``0x0D``，0..255）。

        .. warning::
            实测写入 8 / 200 后 :meth:`get_load` 无任何变化（同批测试里
            :meth:`set_margin` 写入立刻可回读，排除读写链路问题），**本机固件
            是否真的限流未经验证**。要做限力请用软件方案：:meth:`move_at_speed`
            的 ``max_load`` 参数。
        """
        self._send(P.build_set_load_limit(self._id, limit), wait=wait)

    #: 历史命名（``SetCurrentLimit``）
    set_current_limit = set_load_limit

    def set_accelerate(self, value: int, *, wait: bool = False) -> None:
        """设加速度（``0x0E``，0..255）。

        .. warning::
            协议文档未定义该方法（码值由应答表顺序推断）。**实测本机固件不实现**：
            0 与 200 下同一段 44° 位移的到位时间与逐点轨迹一致，且 ``0x19``
            读不回来 ⇒ 这个"加速度维度"在本硬件上不可用。要减速请用主机侧的
            步进逼近（:meth:`move_at_speed` 的 ``interval_ms`` / 更小角速度）。
        """
        self._send(P.build_set_accelerate(self._id, value), wait=wait)

    def set_margin(self, margin: int, *, wait: bool = False) -> None:
        """Margin 数值。"""
        self._send(P.build_set_margin(self._id, margin), wait=wait)

    def set_temp(self, temp: int, *, wait: bool = False) -> None:
        """温度保护阈值。"""
        self._send(P.build_set_temp(self._id, temp), wait=wait)

    def set_offset(self, offset: int, *, wait: bool = False) -> None:
        """零点偏移的**原始字节**（``0x0A``）。

        协议文档《ROMANBO RS485 舵机通信控制协议》说明该字节带 128 偏置：
        ``偏移量 = 原始值 − 128``（``0x80`` = 偏移 0、``0x81`` = +1）。
        要按"实际偏移量"写请用 :meth:`set_calibration_offset`。
        """
        self._send(P.build_set_offset(self._id, offset), wait=wait)

    def set_calibration_offset(self, offset: int, *, wait: bool = False) -> None:
        """按**实际偏移量**写零点（自动 +128 并夹紧到 0..255）。"""
        raw = max(0, min(255, int(offset) + 128))
        self.set_offset(raw, wait=wait)

    def set_baudrate(self, code: int, *, wait: bool = False) -> None:
        """改波特率（``0x22``）。**协议文档未定义该命令的发送，慎用。**"""
        self._send(P.make_frame(P.ServoCmd.SET_BAUDRATE, self._id,
                                bytes([code & 0xFF])), wait=wait)

    # -- 校准 / 出厂 -------------------------------------------------------- #

    def set_calibration_currpos(self, *, wait: bool = False) -> None:
        """把当前位置设为零点。"""
        self._send(P.build_set_calibration_currpos(self._id), wait=wait)

    #: 语义别名
    calibration_zero = set_calibration_currpos

    def start_factory_test(self, *, wait: bool = False) -> None:
        self._send(P.build_start_factory_test(self._id), wait=wait)

    def set_factory_test(self, value: int, *, wait: bool = False) -> None:
        self._send(P.build_set_factory_test(self._id, value), wait=wait)

    def get_factory_test(self, *, timeout: Optional[float] = None):
        return self._send(P.build_get_factory_test(self._id), wait=True,
                          timeout=timeout)

    def reset(self, *, wait: bool = False) -> None:
        """复位（回到出厂参数）。"""
        self._send(P.build_reset(self._id), wait=wait)

    def reboot(self, *, wait: bool = False) -> None:
        """重启舵机固件。"""
        self._send(P.build_reboot(self._id), wait=wait)

    def set_id(self, new_id: int, *, wait: bool = False) -> None:
        """改本舵机 ID（帧地址用旧 ID）。改完后请使用新 ID 建立 :class:`Servo`。"""
        self._send(P.build_set_id(self._id, new_id), wait=wait)

    # -- LED ---------------------------------------------------------------- #

    def set_led(self, value: int, *, wait: bool = False) -> None:
        """LED 原始值（内部会左移 5 位后再发送）。"""
        self._send(P.build_set_led(self._id, value), wait=wait)

    def set_led_color(self, red: bool, green: bool, blue: bool,
                      *, wait: bool = False) -> None:
        """三色 LED。"""
        self._send(P.build_set_led_color(self._id, red, green, blue), wait=wait)

    # -- 读回 --------------------------------------------------------------- #

    def get_position(self, *, timeout: Optional[float] = None) -> int:
        """读取当前位置（ADC 0..1023）。

        实测回包（ID=10，2026-09-25）::

            请求  FF FF 0A 06 14 DE
            应答  FF FF 0A 09 94 00 01 FE 5C   → 数据段 00 01 FE
                  前导 00 为状态字节，数值 = 0x01FE = 510
        """
        resp = self._send(P.build_get_position(self._id), wait=True, timeout=timeout)
        return self._decode_position(resp.data)

    def get_position_raw(self, *, timeout: Optional[float] = None) -> P.Response:
        return self._send(P.build_get_position(self._id), wait=True, timeout=timeout)

    @staticmethod
    def _decode_position(data: bytes) -> int:
        body = P.payload(data)
        value = P.decode_u16_be(body, POSITION_DATA_OFFSET)
        if value is not None:
            return int(value)
        if body:
            return body[0]
        raise P.ProtocolError("GetPosition 回包为空")

    def get_pid(self, *, timeout: Optional[float] = None) -> Tuple[int, int, int]:
        """读 PID。实测回包 ``00 32 00 05`` → (50, 0, 5)。"""
        resp = self._send(P.build_get_pid(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        if len(body) < 3:
            raise P.ProtocolError(f"GetPID 回包过短: {resp.data.hex(' ')}")
        return body[0], body[1], body[2]

    def get_period(self, *, unsafe: bool = False,
                   timeout: Optional[float] = None) -> int:
        """读运动周期（``0x0F``，本机固件无应答）。

        .. danger::
            **默认拒发（:class:`~romanbo.protocol.ProtocolError`）。**
            实测（2026-09-26，ID 8）无数据段的 ``0x0F`` 会被固件当成
            ``SetPositionLimit`` 执行：它拿解析缓冲里的残留 4 字节写入位置限值
            （``(1,1023)`` → ``(113,257)``，重复发送结果一致），而且本身没有应答。
            只有在你**明确接受限值被破坏、并已记下原值可随时用
            :meth:`set_position_limit` 复原**时才传 ``unsafe=True``。

        :param unsafe: 显式确认"我知道这会改写位置限值"。
        """
        if not unsafe:
            raise P.ProtocolError(
                "拒绝发送 0x0F（GetMotionPeriod）：实测该帧（即使不带数据）会被固件"
                "当成 SetPositionLimit，用解析缓冲残值改写位置限值且无应答。"
                "确需发送请显式传入 unsafe=True，并先用 get_position_limit() 记下原值。")
        resp = self._send(P.build_get_motion_period(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        value = P.decode_u16_be(body, 0)
        if value is not None:
            return int(value)
        return body[0] if body else 0

    def get_position_limit(self, *, timeout: Optional[float] = None) -> Tuple[int, int]:
        """读位置限值。实测回包 ``00 00 FF 03 00`` → (min=255, max=768)。"""
        resp = self._send(P.build_get_position_limit(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        if len(body) >= 4:
            return (int(P.decode_u16_be(body, 0) or 0),
                    int(P.decode_u16_be(body, 2) or 0))
        if len(body) == 2:
            return (0, int(P.decode_u16_be(body, 0) or 0))
        raise P.ProtocolError(f"GetPositionLimit 回包异常: {resp.data.hex(' ')}")

    def get_load(self, *, timeout: Optional[float] = None) -> int:
        """读**实测负荷**（``0x18``，界面「负荷」）。

        实测（2026-09-25，ID 8）：静止恒为 ``0``；运动中随加/减速跳动
        （``120 → 32 → 2 → 28 → 0``）；到目标（误差进入 ``margin``）后回 ``0``。
        这是本硬件上**唯一能反映输出力矩大小**的读数，可用来做软件限力。
        """
        resp = self._send(P.build_get_load(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        return body[0] if body else 0

    #: 旧名——注意它读的是**实测负荷**，不是限制值
    get_current_limit = get_load

    def get_accelerate(self, *, timeout: Optional[float] = None) -> int:
        """读加速度（``0x19``）。"""
        resp = self._send(P.build_get_accelerate(self._id), wait=True,
                          timeout=timeout)
        body = P.payload(resp.data)
        return body[0] if body else 0

    def get_margin(self, *, timeout: Optional[float] = None) -> int:
        resp = self._send(P.build_get_margin(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        return body[0] if body else 0

    def get_temperature(self, *, timeout: Optional[float] = None) -> int:
        """读温度（℃）。实测回包 ``00 24`` → 36 ℃。"""
        resp = self._send(P.build_get_temp(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        return body[0] if body else 0

    def get_calibration(self, *, timeout: Optional[float] = None) -> int:
        """读零点偏差的**原始字节**（``0x15``）。实测回包 ``00 74`` → 116。"""
        resp = self._send(P.build_get_calibration(self._id), wait=True, timeout=timeout)
        body = P.payload(resp.data)
        return body[0] if body else 0

    def get_calibration_offset(self, *, timeout: Optional[float] = None) -> int:
        """零点偏差的**实际值** = ``get_calibration() − 128``（协议文档语义）。

        本机两台实测 ``116 → −12``。
        """
        return self.get_calibration(timeout=timeout) - 128

    def get_motor(self, *, timeout: Optional[float] = None) -> bytes:
        return self._send(P.build_get_motor(self._id), wait=True, timeout=timeout).data

    def status(self, *, timeout: Optional[float] = None,
               retries: Optional[int] = None) -> P.Response:
        """状态/ID 查询，可用于判断该 ID 是否存在设备。

        ``retries=0`` 时单次超时即失败（扫描时用，避免每个空 ID 都重试三次）。
        """
        return self._send(P.build_status(self._id), wait=True, timeout=timeout,
                          retries=retries)

    def model(self, *, timeout: Optional[float] = None) -> bytes:
        return self._send(P.build_get_model(), wait=True, timeout=timeout).data

    def version(self, *, timeout: Optional[float] = None) -> bytes:
        return self._send(P.build_get_version(), wait=True, timeout=timeout).data

    def ping(self, *, timeout: float = P.SCAN_PROBE_TIMEOUT) -> bool:
        """是否在线（``Status`` 有回包即在线）。

        .. warning::
            应答本身只需 10~20 ms，但**若上一条帧是发给别人的（无设备 ID），
            本帧约 0.4 s 内会被忽略**（总线静默期）。扫描时若不复位这一状态，
            会出现"只扫到第一个舵机"的假象——请用
            :meth:`~romanbo.robot.RomanboRobot.scan`（它已处理该延时）。
        """
        try:
            # retries=0：扫描时每个不存在的 ID 只等一次，否则 32 个 ID 会慢 3 倍
            self.status(timeout=timeout, retries=0)
            return True
        except (TimeoutError, P.ProtocolError):
            return False

    # -- 汇总 --------------------------------------------------------------- #

    def read_config(self, *, timeout: Optional[float] = 0.2) -> ServoConfig:
        """尽量读回所有可读参数，单项失败不影响其他项。"""
        cfg = ServoConfig(id=self._id)

        def safe(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                return None

        cfg.position = safe(self.get_position, timeout=timeout)
        cfg.pid = safe(self.get_pid, timeout=timeout)
        # 注意：不在这里发 0x0F（读运动周期）。它没有应答，且与
        # SET_POSITION_LIMIT 同码：实测无数据帧也会用缓冲残值改写限值，
        # 所以 :meth:`get_period` 默认拒发，只有 unsafe=True 才发。
        cfg.position_limit = safe(self.get_position_limit, timeout=timeout)
        cfg.load = safe(self.get_load, timeout=timeout)
        cfg.acceleration = safe(self.get_accelerate, timeout=timeout)
        cfg.margin = safe(self.get_margin, timeout=timeout)
        cfg.temperature = safe(self.get_temperature, timeout=timeout)
        cfg.calibration = safe(self.get_calibration, timeout=timeout)
        return cfg

    # -- 换算 --------------------------------------------------------------- #

    def angle(self, *, ratio: float = J.RATIO_MAIN,
              center: int = J.ADC_CENTER) -> float:
        """读取当前位置并换算成角度（度）。"""
        return J.adc_to_angle(self.get_position(), ratio=ratio, center=center)


__all__ = ["Servo", "ServoConfig"]
