"""离线演示：不接硬件，打印每条舵机指令对应的真实报文。

用于核对协议编码是否与基准报文一致（也见 ``python -m romanbo selftest``）。

用法::

    python examples/04_offline_frames.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import golden, protocol as P  # noqa: E402
from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.transport import MockTransport  # noqa: E402


def show(frame: bytes) -> str:
    return frame.hex(" ").upper()


def main() -> int:
    print("== 单条指令 ==")
    print("GetModel           ", show(P.build_get_model()))
    print("Status(1)          ", show(P.build_status(1)))
    print("SetTorque(1,on)    ", show(P.build_set_torque(1, 1)))
    print("SetPosition(1,512) ", show(P.build_set_position(1, 512)))
    print("SetPosition(1,600) ", show(P.build_set_position(1, 600)))
    print("SetPeriod(1,500)   ", show(P.build_set_period(1, 500)))
    print("SetPID(1,50,0,5)   ", show(P.build_set_pid(1, 50, 0, 5)))
    print("SetSync(broadcast) ", show(P.build_set_sync(P.BROADCAST_ID)))

    print("\n== 一次多关节运动（mock）==")
    robot = RomanboRobot(transport=MockTransport(servo_ids=range(1, 18)))
    with robot:
        robot.handshake(verbose=True)
        before = len(robot.sent_frames)
        robot.move({1: 600, 2: 480, 3: 512}, period_ms=500)
        for frame in robot.sent_frames[before:]:
            print("  TX", show(frame))

    print("\n== 黄金向量自检 ==")
    passed, failed, details = golden.run()
    print(f"通过 {passed} / 失败 {failed}")
    for item in details:
        print("  FAIL", item)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
