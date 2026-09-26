"""机型/关节抽象：通道数、ADC 与角度换算、关节命名模板。

常量依据
--------
* 机械中点 ``mCenterValue = 512``
* ADC → 角度比例 ``Ratio = 0.2932551``；下限方向 ``UnderRatio = 0.2636719``；
  上限方向 ``OverRatio = 0.3222656``
* 运行循环默认节流 ``RUNDELAY = 0.8``
* 各机型（无头 / 带头 / 抓取 / 自定义）的电机通道数与关节命名模板
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

ADC_MIN, ADC_MAX, ADC_CENTER = 0, 1023, 512

#: ≈300/1023，ADC → 角度的主换算系数
RATIO_MAIN = 0.2932551
#: ≈270/1024，下限方向
RATIO_UNDER = 0.2636719
#: ≈330/1024，上限方向
RATIO_OVER = 0.3222656

DIRECTION_NORMAL = 0
DIRECTION_REVERSE = 1

WHEEL_CW = 0
WHEEL_CCW = 1

#: 运行循环的默认节流（秒）
DEFAULT_RUN_DELAY = 0.8
#: 串口读缓冲大小
BUFFER_SIZE = 4096
#: 动作数据上限
MAX_MOTION_DATA_SIZE = 131068


class RobotType(IntEnum):
    """机型枚举。"""

    NULL = 0
    HUMAN_NON_HEAD = 1
    HUMAN_HEAD = 2
    CUSTOM = 3
    HUMAN_GRAB = 4


#: 各机型的电机通道数
CHANNEL_COUNT: Dict[int, int] = {
    RobotType.NULL: 0,
    RobotType.HUMAN_NON_HEAD: 17,
    RobotType.HUMAN_HEAD: 18,
    RobotType.CUSTOM: 33,
    RobotType.HUMAN_GRAB: 19,
}


def channels(type_: int) -> int:
    return CHANNEL_COUNT.get(int(type_), 0)


def adc_to_angle(adc: int, *, ratio: float = RATIO_MAIN,
                 center: int = ADC_CENTER) -> float:
    """ADC 原始值 → 角度（度）。

    换算依据 ``Ratio`` / ``mCenterValue`` 常量：``(adc - center) * ratio``。
    """
    return (adc - center) * ratio


def angle_to_adc(angle: float, *, ratio: float = RATIO_MAIN,
                 center: int = ADC_CENTER) -> int:
    """角度（度）→ ADC 原始值，并夹紧到 0..1023。"""
    value = int(round(center + angle / ratio))
    return max(ADC_MIN, min(ADC_MAX, value))


def reflect_adc(adc: int, *, center: int = ADC_CENTER) -> int:
    """镜像一个关节值（左右对称），界面上「对称」功能的等价实现。"""
    return max(ADC_MIN, min(ADC_MAX, 2 * center - adc))


#: 单次运动周期（``SET_PERIOD``）的可用区间，单位 ms。
#: 下限用于保护舵机齿轮箱——周期就是「走完这段距离的时间」，过小等于瞬时跳变。
MIN_PERIOD_MS = 100
MAX_PERIOD_MS = 30000


def period_for_speed(delta_adc: float, dps: float, *,
                     ratio: float = RATIO_MAIN,
                     min_ms: int = MIN_PERIOD_MS,
                     max_ms: int = MAX_PERIOD_MS) -> int:
    """按**目标角速度**反算 ``SET_PERIOD`` 应写入的毫秒数。

    ``SET_PERIOD`` 的原语义是「走完这段距离所需的时间」，因此

    .. math::

        \\text{period\\_ms} = \\frac{|\\Delta_{adc}| \\cdot ratio}{dps} \\times 1000

    :param delta_adc: 角位移（ADC 单位；正负号无关，取绝对值）
    :param dps: 目标角速度，**度/秒**，必须 > 0
    :param ratio: ADC→角度比例，默认常量 :data:`RATIO_MAIN`
    :param min_ms/max_ms: 结果夹紧区间，默认 :data:`MIN_PERIOD_MS` /
        :data:`MAX_PERIOD_MS`
    """
    if dps <= 0:
        raise ValueError(f"角速度必须 > 0 度/秒，收到 {dps!r}")
    ms = abs(float(delta_adc)) * ratio / float(dps) * 1000.0
    return int(max(min_ms, min(max_ms, round(ms))))


def speed_of(delta_adc: float, period_ms: float, *,
             ratio: float = RATIO_MAIN) -> float:
    """由角位移与周期反算**实际角速度**（度/秒），:func:`period_for_speed` 的逆。"""
    period = max(1.0, float(period_ms))
    return abs(float(delta_adc)) * ratio * 1000.0 / period


def frame_period_for_speed(previous: Mapping[int, int], targets: Mapping[int, int],
                           dps: float, *, ratio: float = RATIO_MAIN,
                           min_ms: int = MIN_PERIOD_MS,
                           max_ms: int = MAX_PERIOD_MS,
                           fallback_ms: Optional[int] = None) -> int:
    """多关节帧的周期：取各关节中**最大**角位移反算，保证没有关节超速。

    .. note::
        这是「周期 = 走完时间」假设下的换算。**实测舵机固件会忽略
        ``SET_PERIOD(0x0B)``**（见 :func:`plan_move`），因此真正要控制角速度
        应当使用步进逼近，本函数仅用于换算/报告。

    :param previous: 上一帧（或起始实读）的 ``{舵机ID: ADC}``；缺失的关节会被
        跳过（无从得知角位移）。全部缺失时返回 ``fallback_ms``（默认 ``min_ms``）。
    """
    deltas = [abs(int(targets[i]) - int(previous[i]))
              for i in targets if previous and i in previous]
    if not deltas:
        raw = min_ms if fallback_ms is None else int(fallback_ms)
        return int(max(min_ms, min(max_ms, raw)))
    return period_for_speed(max(deltas), dps, ratio=ratio,
                            min_ms=min_ms, max_ms=max_ms)


#: 步进逼近时相邻两步的默认间隔（ms）
DEFAULT_STEP_INTERVAL_MS = 100
#: 步进间隔上限（ms）——很低的角速度需要拉长间隔，否则每步不足 1 个 ADC
MAX_STEP_INTERVAL_MS = 5000


def pick_step_interval(dps: float, *, ratio: float = RATIO_MAIN,
                       preferred_ms: int = DEFAULT_STEP_INTERVAL_MS,
                       min_step_adc: int = 1) -> int:
    """为给定角速度选一个步进间隔，保证每步至少 ``min_step_adc`` 个 ADC。

    每步位移 = ``dps * interval / 1000 / ratio``（ADC）。角速度很小时需要把
    间隔翻倍，否则四舍五入后每步为 0，动作会完全丢失。
    """
    if dps <= 0:
        raise ValueError(f"角速度必须 > 0 度/秒，收到 {dps!r}")
    interval = max(1.0, float(preferred_ms))
    while (interval < MAX_STEP_INTERVAL_MS
           and dps * interval / 1000.0 / ratio < min_step_adc):
        interval *= 2.0
    return int(round(min(interval, MAX_STEP_INTERVAL_MS)))


def plan_move(start_adc: int, target_adc: int, dps: float, *,
              ratio: float = RATIO_MAIN,
              interval_ms: Optional[int] = None) -> List[Tuple[float, int]]:
    """把 ``start_adc → target_adc`` 拆成 ``[(步长秒, 中间ADC), ...]``。

    .. important::
        **为什么不是靠 ``SET_PERIOD``**：实测该舵机固件忽略运动周期
        （周期 60 / 3000 / 8000 ms 下，44° 位移都在约 0.24 s 内以最大速度走完，
        约 150~180 °/s），因此「角速度」只能靠**按固定节奏下发中间目标**实现：
        每 ``interval_ms`` 走一步，使平均角速度 = ``dps``。多帧动作
        （``Period`` + 每帧小幅位移）也是这个机制。

    步数取 ``round(|Δ| / 每步位移)``，并把余量均摊，保证最后一步正好落在
    ``target_adc``（不过冲）。
    """
    if dps <= 0:
        raise ValueError(f"角速度必须 > 0 度/秒，收到 {dps!r}")
    delta = int(target_adc) - int(start_adc)
    if delta == 0:
        return []
    interval = pick_step_interval(dps, ratio=ratio) if interval_ms is None \
        else max(1, int(interval_ms))
    dt = interval / 1000.0
    step_adc = max(1, int(round(dps * dt / ratio)))
    total_steps = max(1, int(round(abs(delta) / step_adc)))
    return [(dt, int(start_adc) + int(round(delta * k / total_steps)))
            for k in range(1, total_steps + 1)]


def mirror_map(mapping: Dict[int, int], pairs: Sequence[Tuple[int, int]],
               *, center: int = ADC_CENTER) -> Dict[int, int]:
    """按左右对称关节对把一组目标值扩展成整机目标值。"""
    out = dict(mapping)
    for left, right in pairs:
        if left in out and right not in out:
            out[right] = reflect_adc(out[left], center=center)
        elif right in out and left not in out:
            out[left] = reflect_adc(out[right], center=center)
    return out


#: 关节命名模板。协议未约定「通道号 → 关节名」的固定映射，
#: 因此这里给出占位命名，实际名称请在实机示教时逐个核对后覆盖本表。
DEFAULT_JOINT_NAMES: Dict[int, Tuple[str, ...]] = {
    RobotType.HUMAN_NON_HEAD: tuple(f"J{i}" for i in range(1, 18)),
    RobotType.HUMAN_HEAD: tuple(f"J{i}" for i in range(1, 19)),
    RobotType.HUMAN_GRAB: tuple(f"J{i}" for i in range(1, 20)),
    RobotType.CUSTOM: tuple(f"J{i}" for i in range(1, 34)),
}

#: 界面里出现过的关节词（可用来给上面的模板命名）
JOINT_VOCABULARY = ("Hand", "Arm", "Shoulder", "Pelvis", "Leg", "Knee",
                    "Foot", "Ankle")


@dataclass
class JointLayout:
    """某机型的通道布局：``dial_position`` 来自 ``.rsc`` 的 ``DialPosition``。"""

    type: RobotType = RobotType.HUMAN_NON_HEAD
    names: Sequence[str] = ()
    centers: Sequence[int] = ()
    directions: Sequence[int] = ()
    dial_position: Sequence[Tuple[int, int]] = ()

    @classmethod
    def default(cls, type_: RobotType = RobotType.HUMAN_NON_HEAD) -> "JointLayout":
        n = channels(type_)
        return cls(
            type=type_,
            names=DEFAULT_JOINT_NAMES.get(type_, tuple(f"J{i}" for i in range(1, n + 1))),
            centers=tuple([ADC_CENTER] * n),
            directions=tuple([DIRECTION_NORMAL] * n),
        )

    def name(self, index: int) -> str:
        """``index`` 为 1 起的通道号。"""
        if 1 <= index <= len(self.names):
            return self.names[index - 1]
        return f"J{index}"

    def center(self, index: int) -> int:
        if 1 <= index <= len(self.centers):
            return self.centers[index - 1]
        return ADC_CENTER

    def direction(self, index: int) -> int:
        if 1 <= index <= len(self.directions):
            return self.directions[index - 1]
        return DIRECTION_NORMAL


__all__ = [
    "ADC_MIN", "ADC_MAX", "ADC_CENTER", "RATIO_MAIN", "RATIO_UNDER", "RATIO_OVER",
    "DIRECTION_NORMAL", "DIRECTION_REVERSE", "WHEEL_CW", "WHEEL_CCW",
    "DEFAULT_RUN_DELAY", "BUFFER_SIZE", "MAX_MOTION_DATA_SIZE",
    "MIN_PERIOD_MS", "MAX_PERIOD_MS",
    "DEFAULT_STEP_INTERVAL_MS", "MAX_STEP_INTERVAL_MS",
    "RobotType", "CHANNEL_COUNT", "channels", "adc_to_angle", "angle_to_adc",
    "reflect_adc", "mirror_map", "period_for_speed", "speed_of",
    "frame_period_for_speed", "pick_step_interval", "plan_move",
    "DEFAULT_JOINT_NAMES", "JOINT_VOCABULARY", "JointLayout",
]
