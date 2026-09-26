"""扫描在线舵机并读取位置（对应「搜索马达」+「读取位置」）。

用法::

    python examples/01_scan_and_read.py COM3
    python examples/01_scan_and_read.py --mock      # 无硬件演示
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import JointLayout, RobotType  # noqa: E402
from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.transport import MockTransport  # noqa: E402


def main(argv: list[str]) -> int:
    mock = "--mock" in argv
    port = next((a for a in argv[1:] if not a.startswith("-")), None)

    if mock:
        robot = RomanboRobot(transport=MockTransport(
            servo_ids=range(1, 18), positions={1: 512, 2: 640, 3: 400}))
    else:
        if not port:
            print("用法: python 01_scan_and_read.py <COMx> | --mock")
            return 2
        robot = RomanboRobot(port)

    layout = JointLayout.default(RobotType.HUMAN_NON_HEAD)

    with robot:
        result = robot.handshake(verbose=True)
        if not mock and not result.ok:
            print("连接自检未通过，继续扫描……")

        found = robot.scan(1, 32)
        print(f"\n在线舵机: {found if found else '（无）'}")

        print(f"\n{'ID':>3} {'关节':<6} {'ADC':>5} {'角度(°)':>9}")
        print("-" * 28)
        for id_ in found:
            try:
                adc = robot.servo(id_).get_position(timeout=0.2)
            except Exception as exc:                     # noqa: BLE001
                print(f"{id_:>3} {layout.name(id_):<6} 读取失败: {exc}")
                continue
            print(f"{id_:>3} {layout.name(id_):<6} {adc:>5} {adc and (adc - 512) * 0.2932551:>9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
