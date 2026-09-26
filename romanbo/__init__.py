"""ROMANBO 舵机/整机控制库（SDK）。

面向 ROMANBO 舵机型号的 RS485 总线协议实现与功能封装：

* :mod:`romanbo.protocol`  —— 字节层：帧格式、命令码、校验、解析
* :mod:`romanbo.transport` —— 串口 / 离线模拟器
* :mod:`romanbo.servo`     —— 单个舵机 API
* :mod:`romanbo.robot`     —— 整机 API（握手、扫描、同步动作、示教、播放）
* :mod:`romanbo.rsc`       —— ``.rsc`` 工程（动作数据）解析
* :mod:`romanbo.joints`    —— 机型/关节与 ADC↔角度换算
* :mod:`romanbo.golden`    —— 基准报文自检

最小用法::

    from romanbo import RomanboRobot

    with RomanboRobot("COM3") as robot:
        print(robot.handshake().describe())
        robot.servo(1).torque(True)
        robot.move({1: 600, 2: 480}, period_ms=500)
        print(robot.capture())
"""

from . import golden, joints, protocol, rsc, servo, transport  # noqa: F401
from .joints import JointLayout, RobotType, adc_to_angle, angle_to_adc  # noqa: F401
from .protocol import (  # noqa: F401
    BROADCAST_ID,
    CONTROLLER_ID,
    CtrlCmd,
    ErrorResponse,
    ProtocolError,
    Response,
    ServoAck,
    ServoCmd,
)
from .robot import HandshakeResult, RomanboRobot, connect  # noqa: F401
from .rsc import MotionFrame, RscProject  # noqa: F401
from .servo import LoadLimitExceeded, Servo, ServoConfig  # noqa: F401
from .transport import MockTransport, SerialTransport, Transport  # noqa: F401

__version__ = "1.0.0"

__all__ = [
    "RomanboRobot", "connect", "HandshakeResult",
    "Servo", "ServoConfig", "LoadLimitExceeded",
    "RscProject", "MotionFrame",
    "ServoCmd", "CtrlCmd", "ServoAck", "Response",
    "ProtocolError", "ErrorResponse",
    "CONTROLLER_ID", "BROADCAST_ID",
    "Transport", "SerialTransport", "MockTransport",
    "RobotType", "JointLayout", "adc_to_angle", "angle_to_adc",
    "protocol", "transport", "servo", "robot", "rsc", "joints", "golden",
    "__version__",
]
