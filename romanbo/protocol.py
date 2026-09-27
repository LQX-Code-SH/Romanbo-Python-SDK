"""ROMANBO 舵机总线协议（字节层实现）。

本模块只负责「字节层」：常量、帧构造、校验、帧解析。
高层语义见 `romanbo.servo`（单舵机）与 `romanbo.robot`（整机）。

帧格式
------

    FF FF | ID | LEN | CMD | DATA... | CHK

* ``ID``  : 0 = 控制器板，1..32 = 舵机，224..253 = 传感器，254 = 广播
* ``LEN`` : 整帧字节数（含帧头与校验字节），由发送方决定数据段长度
* ``CMD`` : 命令码，见 `ServoCmd` / `CtrlCmd`
* ``CHK`` : ``(0x100 - (sum(除末字节外的所有字节) & 0xFF)) & 0xFF``
  等价说法：**整帧所有字节求和 ≡ 0 (mod 256)**

基准报文
--------
`tests.test_protocol` 中的断言字节是协议基准报文，用于逐字节回归验证本实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# 常量（协议定义）
# --------------------------------------------------------------------------- #

HEADER = 0xFF

CONTROLLER_ID = 0x00
BROADCAST_ID = 0xFE
SERVO_ID_MIN, SERVO_ID_MAX = 1, 32
SENSOR_ID_MIN, SENSOR_ID_MAX = 224, 253

MIN_FRAME_LEN = 6
MAX_FRAME_LEN = 255

OFF, ON = 0, 1
LED_RED, LED_GREEN, LED_BLUE = 1, 2, 4
RESET_DATA = 0xFC  # 协议保留值（未实际使用）

ADC_MIN, ADC_MAX, ADC_CENTER = 0, 1023, 512

#: 舵机应答数据段的前导字节数。
#: 实测（2026-09-25，舵机 ID=10）：``GetPosition`` 回包 ``FF FF 0A 09 94 00 01 FE 5C``
#: 的数据段为 ``00 01 FE``，其中第 1 个字节恒为 ``0x00``（状态/保留），
#: 真正数值从 ``data[1]`` 开始。
REPLY_PREFIX_LEN = 1

#: 舵机应答延迟（秒）。实测（2026-09-25，ID 8/10，逐命令 6 次取中位）**约 10~17 ms**
#: —— Status/GetPosition/GetLoad/GetTemp/Margin/PID/Motor/PositionLimit/Calibration/
#: FactoryTest 全部如此。早期曾把"应答需 0.3~0.9 s"当成延迟，实为下面的总线静默期
#: 被算进了延迟。
SERVO_RESPONSE_DELAY = 0.02
#: **总线静默期（秒）**。实测：一条**寻址到其它 ID（无设备）**的帧之后，舵机会忽略
#: 后续约 **0.4 s** 内的帧（0.35 s 仍不应答、0.4 s 恢复；而连续两条寻址到它自己的
#: 帧都能正常应答）。因此扫描时每个 ID 之间需等待该静默期。
#: 库内对应策略：探测失败后等这么久再探测下一个 ID；重试间隔也必须不小于它，
#: 否则重试同样会被吃掉。
BUS_QUARANTINE = 0.4
#: 默认应答超时。实测 10~20 ms 即可，留足余量
DEFAULT_ACK_TIMEOUT = 0.3
#: 扫描时每个 ID 的探测等待时间（真实应答 10~20 ms，不必等 0.7 s）
SCAN_PROBE_TIMEOUT = 0.15
#: 扫描时，探测失败后等待的总线静默期
SCAN_QUARANTINE = BUS_QUARANTINE

#: 相邻两帧的**最小发送间隔**（秒）。实测（2026-09-26，ID 8/10，FT230X）：
#: 两帧之间间隔 0 ms 时，**后发的那一帧会被舵机静默丢弃**——交换 ID 顺序后
#: 被丢的永远是第二帧，故与 ID 无关；间隔 2 / 5 / 10 / 20 / 50 ms 时两帧均正常。
#:
#: 多关节下发是**逐关节连发**（``Robot.move`` 的步进逼近、``mode="position"``
#: 的 ``SET_PERIOD``+``SET_POSITION``、``Robot.play``），不兜底就会"只有第一个
#: 关节生效"。因此由 `romanbo.transport.SerialTransport` 在写口强制本间隔。
MIN_FRAME_GAP = 0.002

BAUDRATE_DEFAULT = 115200
BAUDRATE_ALTERNATIVE = 57600


class ServoCmd:
    """舵机命令码。"""

    GET_MODEL = 0x01
    RESET = 0x01            # 与 GET_MODEL 同码，按上下文区分
    GET_VERSION = 0x02
    SET_REBOOT = 0x02       # 与 GET_VERSION 同码，按上下文区分
    STATUS = 0x05
    GET_ID = 0x05
    SET_ID = 0x06
    SET_PID = 0x07
    #: 「PID 不保存」（界面勾选项）。**实测这条才可靠**：``0x47`` 写后立即回读
    #: 即为新值（3/3）；而 ``0x07`` 三次里只有一次生效（见
    #: `romanbo.servo.Servo.set_pid`）。
    SET_PID_NOSAVE = 0x47
    SET_TEMP = 0x08
    SET_POSITION = 0x09
    SET_WHEEL = 0x09        # 与 SET_POSITION 同码，由数据段位域区分
    SET_OFFSET = 0x0A
    SET_PERIOD = 0x0B
    SET_MARGIN = 0x0C
    SET_CURRENT_LIMIT = 0x0D
    #: 加速度设置。**码值由应答表顺序推断**（``ServoAck`` 里 0x89→0x8D 连续对应
    #: 0x0D→0x11，其中 0x8A 即 SET_ACCELERATE，故 0x0E 空位只能是它）。
    #: 真机行为需实测确认。
    SET_ACCELERATE = 0x0E
    SET_POSITION_LIMIT = 0x0F
    GET_MOTION_PERIOD = 0x0F  # 与 SET_POSITION_LIMIT 同码
    SET_TORQUE = 0x10
    SET_LED = 0x11
    GET_PID = 0x12
    GET_DEADZONE = 0x12       # 与 GET_PID 同码
    GET_TEMP = 0x13
    GET_POSITION = 0x14
    GET_CALIBRATION = 0x15
    GET_MOTOR = 0x16
    GET_MARGIN = 0x17
    #: 历史命名为 ``GET_CURRENT_LIMIT``（界面「负荷」）。**实测返回的是
    #: 实测负荷/电流读数**：静止恒为 0，运动中按加速/减速跳动（实测 120→32→28），
    #: 写 ``SET_CURRENT_LIMIT`` 不会改变它。故用 ``GET_LOAD`` 作为主名。
    GET_LOAD = 0x18
    GET_CURRENT_LIMIT = 0x18   # 旧名，语义见上，勿当「限制值」读
    GET_ACCELERATE = 0x19
    GET_POSITION_LIMIT = 0x1A
    SET_SYNC = 0x20
    SET_NEXT_POSITION = 0x21
    SET_NEXT_WHEEL = 0x21
    SET_BAUDRATE = 0x22       # 常量存在，但协议文档未定义发送
    SET_CALIBRATION_CURRPOS = 0x23
    START_FACTORY_TEST = 0x79
    SET_FACTORY_TEST = 0x7A
    GET_FACTORY_TEST = 0x7B


class CtrlCmd:
    """控制器板（整机）命令码。

    实测：``Run`` / ``Stop`` 未定义行为，``Play`` / ``Sequence`` /
    ``DownloadStart`` / ``DownloadEnd`` / ``SaveScene`` 无对应实际报文
    （属兼容接口，动作下载实际走裸数据块直写）。此处保留以保持协议完整。
    """

    CONTROL_STATUS = 0x03
    DOWNLOAD_START = 0x04
    DOWNLOAD_END = 0x05
    SAVE_SCENE = 0x06
    PLAY = 0x07
    SEQUENCE = 0x08
    SET_POSITION = 0x09      # 原实现忽略数据段
    SET_LED = 0x0A
    SET_TORQUE = 0x10
    GET_POSITION = 0x14
    RUN = 0x07               # 原实现为空方法
    STOP = 0x06              # 原实现为空方法


class ServoAck:
    """舵机应答码（协议定义）。

    注意：常量表存在重码（例如 ``STATUS`` 与 ``SET_MOTION_PERIOD`` 都是 0x85），
    因此判定回包类型时建议以 ``ID + 数据长度 + 数据语义`` 为准。

    实测（2026-09-25）：**只有 GET 类与 STATUS 会收到应答**
    （``ACK = 0x80 | cmd``）；SET 类的这些常量在本机固件上未观察到任何回包，
    可能来自控制器板或旧版固件，不要依赖它们做同步。
    """

    ERROR = 0x80
    GET_MODEL = 0x81
    RESET = 0x81
    GET_VERSION = 0x82
    SET_ID = 0x83
    SET_PID = 0x84
    SET_MOTION_PERIOD = 0x85
    STATUS = 0x85
    SET_POSITION = 0x86
    SET_CALIBRATION = 0x87
    SET_DEADZONE = 0x88
    SET_CURRENT_LIMIT = 0x89
    SET_ACCELERATE = 0x8A
    SET_POSITION_LIMIT = 0x8B
    SET_TORQUE = 0x8C
    SET_LED = 0x8D
    GET_PID = 0x92
    GET_TEMP = 0x93
    GET_POSITION = 0x94
    GET_CALIBRATION = 0x95
    GET_MOTOR = 0x96
    GET_MARGIN = 0x97
    GET_CURRENT_LIMIT = 0x98
    GET_ACCELERATE = 0x99
    GET_POSITION_LIMIT = 0x9A
    START_FACTORY_TEST = 0xF9
    GET_FACTORY_TEST = 0xFB


class CtrlAck:
    """控制器板应答码。"""

    ERROR = 0x80
    CONTROL_STATUS = 0x83
    DOWNLOAD_START = 0x84
    DOWNLOAD_END = 0x85
    SAVE_SCENE = 0x86
    PLAY_COMPLETE = 0x87
    SEQUENCE_COMPLETE = 0x88
    SETTORQUE = 0x89
    SETLED = 0x8A
    SETPOSITION = 0x8B
    GETPOSITION = 0x94


class ServoErrorCode:
    """舵机错误码。"""

    NONE = 0
    INSTRUCTION = 1
    CHECKSUM = 2
    OVERLOAD = 3
    LIMIT = 4
    PACKET = 5
    POSITION = 6
    EMPTY_PROGRAM = 7


class CtrlErrorCode:
    """控制器错误码。"""

    NONE = 0
    MOTOR_ERROR = 1
    CHECKSUM = 2
    REQUEST_TIMEOUT = 3
    INSTRUCTION = 4
    NOT_CONNECTION = 5
    DOWNLOAD_OR_POSITION = 6
    EMPTY_PROGRAM = 7


#: GET 类命令的应答码 = ``0x80 | CMD``（实测成立）
_GET_ACKS = {
    ServoCmd.GET_MODEL: ServoAck.GET_MODEL,
    ServoCmd.GET_VERSION: ServoAck.GET_VERSION,
    ServoCmd.STATUS: ServoAck.STATUS,
    ServoCmd.GET_PID: ServoAck.GET_PID,
    ServoCmd.GET_TEMP: ServoAck.GET_TEMP,
    ServoCmd.GET_POSITION: ServoAck.GET_POSITION,
    ServoCmd.GET_CALIBRATION: ServoAck.GET_CALIBRATION,
    ServoCmd.GET_MOTOR: ServoAck.GET_MOTOR,
    ServoCmd.GET_MARGIN: ServoAck.GET_MARGIN,
    ServoCmd.GET_LOAD: ServoAck.GET_CURRENT_LIMIT,
    ServoCmd.SET_ACCELERATE: ServoAck.SET_ACCELERATE,
    ServoCmd.GET_ACCELERATE: ServoAck.GET_ACCELERATE,
    ServoCmd.GET_POSITION_LIMIT: ServoAck.GET_POSITION_LIMIT,
    ServoCmd.GET_FACTORY_TEST: ServoAck.GET_FACTORY_TEST,
}


class ProtocolError(Exception):
    """帧格式/校验错误。"""


class ErrorResponse(Exception):
    """设备回包为错误帧（CMD == 0x80）。"""

    def __init__(self, response: "Response") -> None:
        super().__init__(f"设备返回错误: code=0x{response.data[0]:02X}" if response.data
                         else "设备返回错误")
        self.response = response

    @property
    def code(self) -> int:
        return self.response.data[0] if self.response.data else -1


# --------------------------------------------------------------------------- #
# 校验与帧构造
# --------------------------------------------------------------------------- #

def checksum(frame: Sequence[int]) -> int:
    """计算校验字节：对 ``frame[:-1]`` 求和后取二进制补码。

    ``frame`` 需已包含最后一个占位字节（其值会被忽略）。
    """
    total = sum(frame[:-1]) & 0xFF
    return (0x100 - total) & 0xFF


def verify(frame: Sequence[int], *, check_checksum: bool = True) -> bool:
    """校验一帧：帧头、长度字段与校验和。

    ``LEN`` 字段在两种方向上的含义**不同**（真机实测）：

    * **请求**（上位机 → 设备）：``LEN`` = 整帧字节数
    * **应答**（设备 → 上位机）：``LEN`` = **数据段字节数**，整帧 = ``LEN + 6``
      （实测控制器回包 ``FF FF 00 02 81 00 70 0F``：LEN=2，数据 2 字节，共 8 字节）

    因此这里两种约定都接受。
    """
    if len(frame) < MIN_FRAME_LEN:
        return False
    if frame[0] != HEADER or frame[1] != HEADER:
        return False
    if frame[3] != len(frame) and frame[3] + MIN_FRAME_LEN != len(frame):
        return False
    if check_checksum and (sum(frame) & 0xFF) != 0:
        return False
    return True


def make_frame(
    cmd: int,
    id_: int = CONTROLLER_ID,
    data: bytes = b"",
    *,
    size: Optional[int] = None,
    len_field: Optional[int] = None,
) -> bytes:
    """构造一帧。

    :param cmd: 命令码
    :param id_: 目标地址（0=控制器，1..32=舵机，254=广播）
    :param data: 数据段（从索引 5 开始填充）
    :param size: 整帧长度；默认 ``6 + len(data)``
    :param len_field: 强制指定 ``LEN`` 字段的值。``GetModel`` / ``GetVersion``
        会把 ``LEN`` 写成 0（帧实际长度仍为 6），此处为兼容该行为保留。
    """
    n = size if size is not None else MIN_FRAME_LEN + len(data)
    if not (MIN_FRAME_LEN <= n <= MAX_FRAME_LEN):
        raise ValueError(f"帧长度 {n} 超出 {MIN_FRAME_LEN}..{MAX_FRAME_LEN}")
    if len(data) > n - MIN_FRAME_LEN:
        raise ValueError(f"数据段 {len(data)} 字节放不进帧长 {n}")

    frame = bytearray(n)
    frame[0] = HEADER
    frame[1] = HEADER
    frame[2] = id_ & 0xFF
    frame[3] = n if len_field is None else (len_field & 0xFF)
    frame[4] = cmd & 0xFF
    frame[5:5 + len(data)] = data
    frame[n - 1] = checksum(frame)
    return bytes(frame)


# --------------------------------------------------------------------------- #
# 舵机命令构造（字节打包规则来自协议文档与实测报文）
# --------------------------------------------------------------------------- #

def build_get_model() -> bytes:
    """``GetModel``：LEN 字段为 0、ID 为 0（兼容旧格式）。"""
    return make_frame(ServoCmd.GET_MODEL, CONTROLLER_ID, len_field=0)


def build_get_version() -> bytes:
    """``GetVersion``：LEN 字段为 0、ID 为 0。"""
    return make_frame(ServoCmd.GET_VERSION, CONTROLLER_ID, len_field=0)


def build_status(id_: int) -> bytes:
    return make_frame(ServoCmd.STATUS, id_)


def build_reset(id_: int) -> bytes:
    return make_frame(ServoCmd.RESET, id_)


def build_reboot(id_: int) -> bytes:
    return make_frame(ServoCmd.SET_REBOOT, id_)


def build_set_id(current_id: int, new_id: int) -> bytes:
    """改 ID：帧地址用**旧 ID**，数据段为**新 ID**，LEN=7。"""
    return make_frame(ServoCmd.SET_ID, current_id, bytes([new_id & 0xFF]))


def build_set_pid(id_: int, p: int, i: int, d: int, *, save: bool = True) -> bytes:
    """PID 设置：数据段 3 字节，LEN=9；``save=False`` 用 ``0x47``。"""
    cmd = ServoCmd.SET_PID if save else ServoCmd.SET_PID_NOSAVE
    return make_frame(cmd, id_, bytes([p & 0xFF, i & 0xFF, d & 0xFF]), size=9)


#: ``0x09`` 位置指令里的**出力档位**（2 bit，落在 ``d[5]`` 的 bit3(低)/bit4(高)）。
#:
#: 三个常用档位 High=0 / Middle=1 / Low=2，第 4 个值对应轮子模式（``W``）。
#: 实测（2026-09-25，ID 8，同一段约 40 ADC 位移，只改该字段）：
#:
#: ====== =============== ==================
#: 档位   峰值负荷(``0x18``) 现象
#: ====== =============== ==================
#: 0 = H  178             动，力度最大
#: 1 = M  110             动
#: 2 = L  38              动，力度最小
#: 3 = W  0               **位置指令不生效**
#: ====== =============== ==================
#:
#: 注意：**它不是使能开关**（档位 0 照样能运动）；使能只有 ``0x10 SetTorque``。
LEVEL_HIGH = 0
LEVEL_MIDDLE = 1
LEVEL_LOW = 2
LEVEL_WHEEL = 3


def build_set_position(id_: int, position: int, torque: int = LEVEL_MIDDLE,
                       relative: int = 0, *, level: Optional[int] = None) -> bytes:
    """位置指令（``0x09``，LEN=8）。


        v    = (ledkind << 13) | (torque << 11) | (relative << 10) | position
        d[5] = v >> 8            # 含档位(bit3=bit11, bit4=bit12)、relative(bit2)、位置高位
        d[6] = v & 0xFF

    :param position: 0..1023（10 位 ADC）；越界抛 `ValueError`。
        ``d[5]`` 只有 bit0/bit1 是位置高位，bit2 是 relative、bit3/bit4 是出力档位，
        所以 1024 及以上会**撞上 relative 位**而被固件当成相对运动
        （``1500`` → 相对、``2047`` → 相对 +1023），必须在入口挡掉。
    :param torque: **出力档位**，取值 `LEVEL_HIGH` / `LEVEL_MIDDLE` /
        `LEVEL_LOW` / `LEVEL_WHEEL`（0/1/2/3，见上表）。
        默认 1（= M，兼容历史默认值）；想要最大出力请传 0。
        **不是使能**——使能用 ``0x10``。
    :param level: :paramref:`torque` 的别名（更贴近语义），给出时优先。
    """
    if level is not None:
        torque = int(level)
    if not ADC_MIN <= int(position) <= ADC_MAX:
        raise ValueError(f"position 应为 {ADC_MIN}..{ADC_MAX}，收到 {position}")
    high = ((position >> 8) | (torque << 3) | (relative << 2)) & 0xFF
    return make_frame(ServoCmd.SET_POSITION, id_, bytes([high, position & 0xFF]), size=8)


def build_set_wheel(id_: int, speed: int, *, torque: int = ON, relative: int = 0,
                    free: int = 0, direction: int = 0) -> bytes:
    """轮子模式（LEN=8，与位置指令同码）。


        d[5] = (torque << 3) + (relative << 2) + (free << 1) + direction
        d[6] = speed
    """
    high = ((torque << 3) + (relative << 2) + (free << 1) + direction) & 0xFF
    return make_frame(ServoCmd.SET_WHEEL, id_, bytes([high, speed & 0xFF]), size=8)


def build_set_next_position(id_: int, position: int, *, torque: int = ON,
                            relative: int = 0, freewheel: int = 0,
                            ledkind: int = 0, angle: int = 0) -> bytes:
    """预置下一目标（LEN=8），可用 ``SET_SYNC`` 触发同步执行。

    打包规则（依据协议文档）：

        bit = 1 if position < 512 else 0
        if torque < 3:
            v = (ledkind << 13) | (torque << 11) | (relative << 10) | position
        else:
            v = (ledkind << 13) | (torque << 11) | (relative << 10) \\
                | (freewheel << 9) | (bit << 8) | abs(angle)
        d[5] = v >> 8 ; d[6] = v & 0xFF

    即 ``torque >= 3`` 时走「轮子/角度」分支，此时 ``position`` 只贡献
    一个「是否 < 512」的方向位。
    """
    if torque < 3:
        value = ((ledkind << 13) | (torque << 11) | (relative << 10) | position) & 0xFFFF
    else:
        below = 1 if position < 512 else 0
        value = ((ledkind << 13) | (torque << 11) | (relative << 10)
                 | (freewheel << 9) | (below << 8) | (abs(int(angle)) & 0xFF)) & 0xFFFF
    return make_frame(ServoCmd.SET_NEXT_POSITION, id_,
                      bytes([(value >> 8) & 0xFF, value & 0xFF]), size=8)


def build_set_period(id_: int, period_ms: int) -> bytes:
    """运动周期/速度（LEN=8，16 位**大端**，单位 ms）。"""
    period_ms = int(period_ms) & 0xFFFF
    return make_frame(ServoCmd.SET_PERIOD, id_,
                      bytes([(period_ms >> 8) & 0xFF, period_ms & 0xFF]), size=8)


def build_set_torque(id_: int, on: int) -> bytes:
    """扭矩开关：``on`` 取 0/1。"""
    return make_frame(ServoCmd.SET_TORQUE, id_, bytes([on & 0xFF]))


def build_set_led(id_: int, value: int) -> bytes:
    """LED 原始值（LEN=7）：``d[5] = value << 5``。"""
    return make_frame(ServoCmd.SET_LED, id_, bytes([(value << 5) & 0xFF]))


def build_set_led_color(id_: int, red: bool, green: bool, blue: bool) -> bytes:
    """LED 三色（LEN=7）：

        v = (8 if red else 0) + (4 if green else 0) + (2 if blue else 0)
        d[5] = v << 4
    """
    value = ((8 if red else 0) + (4 if green else 0) + (2 if blue else 0)) & 0xFF
    return make_frame(ServoCmd.SET_LED, id_, bytes([(value << 4) & 0xFF]))


def build_set_margin(id_: int, margin: int) -> bytes:
    return make_frame(ServoCmd.SET_MARGIN, id_, bytes([margin & 0xFF]))


def build_set_load_limit(id_: int, limit: int) -> bytes:
    """负荷（电流）上限，``0x0D``，0..255。

    !!! warning
        实测：写入 8 / 200 后**无法从 ``GET_LOAD`` 观察到任何变化**（同批测试中
        ``SET_MARGIN 0x0C`` 写入立刻可回读，排除读写链路问题），因此本机固件
        是否真的限流**未经验证**。协议文档把这一项标为「负荷」。
    """
    return make_frame(ServoCmd.SET_CURRENT_LIMIT, id_, bytes([limit & 0xFF]))


#: 历史命名（``SetCurrentLimit``）
build_set_current_limit = build_set_load_limit


def build_set_accelerate(id_: int, value: int) -> bytes:
    """加速度设置（``0x0E``，0..255）。

    !!! warning
        码值由应答表顺序推断（见 `ServoCmd.SET_ACCELERATE`），协议文档未定义。
        **实测（2026-09-25，ID 8，交错 A/B 各 2 次）该固件未实现此参数**：
        ``value`` 为 0 与 200 时，同一段 44° 位移的到位时间（0.255 / 0.257 s）与
        逐点轨迹完全一致，峰值负荷差异在运行间噪声内；且 ``0x19`` 读不回来。
        写入本身不破坏通信（写 1/20/200 后 ``ping`` 均 5/5）。
    """
    return make_frame(ServoCmd.SET_ACCELERATE, id_, bytes([value & 0xFF]))


def build_set_temp(id_: int, temp: int) -> bytes:
    return make_frame(ServoCmd.SET_TEMP, id_, bytes([temp & 0xFF]))


def build_set_offset(id_: int, offset: int) -> bytes:
    return make_frame(ServoCmd.SET_OFFSET, id_, bytes([offset & 0xFF]))


def build_set_position_limit(id_: int, minimum: int, maximum: int) -> bytes:
    """位置限值（LEN=10，min/max 各 16 位大端）。"""
    minimum = int(minimum) & 0xFFFF
    maximum = int(maximum) & 0xFFFF
    data = bytes([(minimum >> 8) & 0xFF, minimum & 0xFF,
                  (maximum >> 8) & 0xFF, maximum & 0xFF])
    return make_frame(ServoCmd.SET_POSITION_LIMIT, id_, data, size=10)


def build_set_sync(id_: int) -> bytes:
    """同步触发（ID 可为具体舵机或 254 广播）。"""
    return make_frame(ServoCmd.SET_SYNC, id_)


def build_set_calibration_currpos(id_: int) -> bytes:
    """把**当前位置**设为零点（上位机实际使用的校准命令）。"""
    return make_frame(ServoCmd.SET_CALIBRATION_CURRPOS, id_)


def build_start_factory_test(id_: int) -> bytes:
    return make_frame(ServoCmd.START_FACTORY_TEST, id_)


def build_set_factory_test(id_: int, value: int) -> bytes:
    value = int(value) & 0xFFFF
    return make_frame(ServoCmd.SET_FACTORY_TEST, id_,
                      bytes([(value >> 8) & 0xFF, value & 0xFF]), size=8)


def build_get_pid(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_PID, id_)


def build_get_temp(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_TEMP, id_)


def build_get_position(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_POSITION, id_)


def build_get_calibration(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_CALIBRATION, id_)


def build_get_motor(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_MOTOR, id_)


def build_get_margin(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_MARGIN, id_)


def build_get_load(id_: int) -> bytes:
    """读**实测负荷**（``0x18``；历史命名为 ``GetCurrentLimit``，界面「负荷」）。"""
    return make_frame(ServoCmd.GET_LOAD, id_)


#: 历史命名（``GetCurrentLimit``）——注意它读的是**实测负荷**，不是限制值
build_get_current_limit = build_get_load


def build_get_accelerate(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_ACCELERATE, id_)


def build_get_position_limit(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_POSITION_LIMIT, id_)


def build_get_motion_period(id_: int) -> bytes:
    """读取运动周期（与 ``SET_POSITION_LIMIT`` 同码 ``0x0F``）。

    !!! danger
        **不要发送这条帧。** 实测（2026-09-26，ID 8）它无应答，而且**不带数据的
        ``0x0F`` 会被固件当成 ``SetPositionLimit`` 执行**：固件用解析缓冲里的残留
        4 字节当 min/max 写入限值（实测 `(1,1023)` → `(113,257)`，重复发送结果一致）。
        ID 10 当前限值 `(255,768)` 就是这样在早期联调时被写进去的。
        本函数仅为完整保留协议命令码而存在。
    """
    return make_frame(ServoCmd.GET_MOTION_PERIOD, id_)


def build_get_factory_test(id_: int) -> bytes:
    return make_frame(ServoCmd.GET_FACTORY_TEST, id_)


# --- 控制器板（整机）------------------------------------------------------- #

def build_ctrl_control_status() -> bytes:
    return make_frame(CtrlCmd.CONTROL_STATUS, CONTROLLER_ID)


def build_ctrl_play() -> bytes:
    return make_frame(CtrlCmd.PLAY, CONTROLLER_ID)


def build_ctrl_sequence() -> bytes:
    return make_frame(CtrlCmd.SEQUENCE, CONTROLLER_ID)


def build_ctrl_download_start(data: bytes, *, size: Optional[int] = None) -> bytes:
    return make_frame(CtrlCmd.DOWNLOAD_START, CONTROLLER_ID, data, size=size)


def build_ctrl_download_end(scene_num: int) -> bytes:
    return make_frame(CtrlCmd.DOWNLOAD_END, CONTROLLER_ID, bytes([scene_num & 0xFF]))


def build_ctrl_save_scene(*, size: int = MIN_FRAME_LEN) -> bytes:
    return make_frame(CtrlCmd.SAVE_SCENE, CONTROLLER_ID, size=size)


def build_ctrl_set_torque(id_: int, torque: int) -> bytes:
    return make_frame(CtrlCmd.SET_TORQUE, id_, bytes([torque & 0xFF]))


def build_ctrl_set_led(id_: int, data: int) -> bytes:
    return make_frame(CtrlCmd.SET_LED, id_, bytes([data & 0xFF]))


def build_ctrl_get_position(id_: int) -> bytes:
    return make_frame(CtrlCmd.GET_POSITION, id_)


def build_ctrl_set_position(id_: int, *, size: int = 8) -> bytes:
    """原实现忽略数据段，仅发命令码。"""
    return make_frame(CtrlCmd.SET_POSITION, id_, size=size)


# --------------------------------------------------------------------------- #
# 回包解析
# --------------------------------------------------------------------------- #

@dataclass
class Response:
    """一帧收到的回包。"""

    id: int
    cmd: int
    data: bytes
    raw: bytes = field(repr=False, default=b"")

    @property
    def is_ack(self) -> bool:
        return bool(self.cmd & 0x80)

    @property
    def is_error(self) -> bool:
        return self.cmd == ServoAck.ERROR or self.cmd == CtrlAck.ERROR

    @property
    def is_controller(self) -> bool:
        return self.id == CONTROLLER_ID

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return (f"<Response id={self.id} cmd=0x{self.cmd:02X} "
                f"data={self.data.hex(' ').upper()}>")


def parse_frame(frame: bytes, *, strict: bool = True) -> Response:
    """解析一帧完整报文（不含流式重组）。"""
    if strict and not verify(frame):
        raise ProtocolError(f"非法帧: {frame.hex(' ').upper()}")
    if len(frame) < MIN_FRAME_LEN:
        raise ProtocolError("帧过短")
    return Response(id=frame[2], cmd=frame[4], data=bytes(frame[5:-1]), raw=bytes(frame))


class FrameParser:
    """增量帧解析器（标准帧同步 + 校验流程）。

    按 ``FF FF + LEN`` 同步，并在校验失败时后移一字节重新同步，
    对外表现稳定且健壮。
    """

    def __init__(self, *, verify_checksum: bool = True) -> None:
        self._buf = bytearray()
        self._verify = verify_checksum
        self.errors = 0

    def _candidate_lengths(self) -> List[int]:
        """由 ``LEN`` 字段推出可能的整帧长度。

        ``declared`` 本身已是合法帧长时**优先按「整帧长度」解释**（舵机请求与舵机
        回包都是这个语义，见 `docs/SERVO_SPEC.md` §2.2）；只有它小于最小帧长
        （控制器回包的 ``LEN=2``）才优先按「数据长度」解释。原先的顺序（先
        ``declared + 6``）会把一条完整舵机帧错切成长窗口，恰好凑出校验和时
        （约 1/256）就把后续字节一起吞掉，表现为偶发丢帧/串位。
        """
        declared = self._buf[3]
        order = ((declared, declared + MIN_FRAME_LEN)
                 if declared >= MIN_FRAME_LEN
                 else (declared + MIN_FRAME_LEN, declared))
        out: List[int] = []
        for candidate in order:
            if MIN_FRAME_LEN <= candidate <= MAX_FRAME_LEN and candidate not in out:
                out.append(candidate)
        return out

    def feed(self, chunk: bytes) -> List[Response]:
        """喂入新收到的字节，返回本次能解析出的完整帧列表。

        由于 ``LEN`` 在请求/应答两种语义下含义不同（见 `verify`），
        这里对每个帧头依次尝试「数据长度+6」与「整帧长度」两种截取方式，
        以校验和通过的那个为准。
        """
        self._buf.extend(chunk)
        out: List[Response] = []
        while True:
            # 1. 找到帧头
            start = self._buf.find(b"\xFF\xFF")
            if start < 0:
                # 保留最后一个 0xFF 以便与下一个字节组成帧头
                if len(self._buf) > 1:
                    del self._buf[:-1]
                break
            if start:
                del self._buf[:start]
            if len(self._buf) < 4:
                break

            # 2. 按两种长度语义尝试截取
            candidates = self._candidate_lengths()
            resolved = False
            for length in candidates:
                if len(self._buf) < length:
                    continue
                frame = bytes(self._buf[:length])
                if self._verify and (sum(frame) & 0xFF) != 0:
                    continue
                del self._buf[:length]
                out.append(parse_frame(frame, strict=False))
                resolved = True
                break
            if resolved:
                continue

            # 3. 都还不成立：数据不足则等待，否则丢弃一个字节重新同步
            if len(self._buf) < min(candidates):
                break
            self.errors += 1
            del self._buf[0]
        return out

    def reset(self) -> None:
        self._buf.clear()


def payload(data: bytes, *, prefix: int = REPLY_PREFIX_LEN) -> bytes:
    """去掉应答数据段的前导状态字节，返回真正的数值部分。

    实测 ``GetPID`` 回包数据为 ``00 32 00 05`` → 数值部分 ``32 00 05`` = P50/I0/D5；
    ``GetPositionLimit`` 为 ``00 00 01 03 FF`` → 数值部分 ``00 01 03 FF`` = min0/max1023。
    """
    return bytes(data[prefix:]) if prefix else bytes(data)


def decode_u16_be(data: bytes, offset: int = 0) -> Optional[int]:
    """大端 16 位读取（发送侧的多字节字段统一为大端）。"""
    if len(data) < offset + 2:
        return None
    return (data[offset] << 8) | data[offset + 1]


def decode_u16_le(data: bytes, offset: int = 0) -> Optional[int]:
    if len(data) < offset + 2:
        return None
    return data[offset] | (data[offset + 1] << 8)


def ack_for(cmd: int) -> int:
    """尽力给出命令对应的应答码。

    GET 类为 ``0x80 | cmd``（实测）；SET 类使用另一组顺序编码且存在
    重码，这里只对可确定的部分给出映射，其余返回 ``cmd | 0x80`` 作为参考值。
    """
    if cmd in _GET_ACKS:
        return _GET_ACKS[cmd]
    return cmd | 0x80


def expected_ack(cmd: int) -> Optional[int]:
    """若该命令的应答码是**已实测确认**的，返回它；否则返回 ``None``。

    GET 类满足 ``ACK = 0x80 | cmd``（真机验证：0x14→0x94、0x12→0x92、0x1A→0x9A…）。
    连续多条查询时用它做匹配，可以避免把**上一条命令的迟到回包**错当成当前
    命令的应答（实测中确实出现过这种串位）。
    """
    return _GET_ACKS.get(cmd)


def id_range(start: int = SERVO_ID_MIN, end: int = SERVO_ID_MAX) -> Iterable[int]:
    return range(start, end + 1)


__all__ = [
    "HEADER", "CONTROLLER_ID", "BROADCAST_ID", "SERVO_ID_MIN", "SERVO_ID_MAX",
    "SENSOR_ID_MIN", "SENSOR_ID_MAX", "MIN_FRAME_LEN", "MAX_FRAME_LEN",
    "OFF", "ON", "LEVEL_HIGH", "LEVEL_MIDDLE", "LEVEL_LOW", "LEVEL_WHEEL",
    "LED_RED", "LED_GREEN", "LED_BLUE", "RESET_DATA",
    "ADC_MIN", "ADC_MAX", "ADC_CENTER", "BAUDRATE_DEFAULT", "BAUDRATE_ALTERNATIVE",
    "REPLY_PREFIX_LEN", "SERVO_RESPONSE_DELAY", "BUS_QUARANTINE",
    "DEFAULT_ACK_TIMEOUT", "SCAN_PROBE_TIMEOUT", "SCAN_QUARANTINE",
    "MIN_FRAME_GAP", "payload",
    "ServoCmd", "CtrlCmd", "ServoAck", "CtrlAck",
    "ServoErrorCode", "CtrlErrorCode",
    "ProtocolError", "ErrorResponse", "Response", "FrameParser",
    "checksum", "verify", "make_frame", "parse_frame", "decode_u16_be",
    "decode_u16_le", "ack_for", "expected_ack", "id_range",
]
