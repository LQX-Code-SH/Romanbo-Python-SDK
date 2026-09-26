"""示教：松扭矩 → 手扳机器人 → 读取位置 → 导出**可直接播放的 `.rsc`**。

对应「扭矩OFF」+「读取位置」流程；导出用 :func:`romanbo.rsc.write_project`
（也可用 CLI 累积多帧：``teach --file`` → ``export``）。

用法::

    python examples/03_teach_and_export.py COM3 --ids 1-17
    python examples/03_teach_and_export.py COM3 --ids 8,10 --out taught.rsc
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.rsc import write_project  # noqa: E402


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    port = args[0]
    ids = list(range(1, 18))
    if "--ids" in args:
        text = args[args.index("--ids") + 1]
        ids = []
        for part in text.split(","):
            if "-" in part:
                start, end = part.split("-", 1)
                ids.extend(range(int(start), int(end) + 1))
            else:
                ids.append(int(part))

    with RomanboRobot(port) as robot:
        robot.handshake(verbose=True)

        print("\n[1/3] 关闭扭矩（机器人会失去支撑，请先扶稳）")
        robot.torque_all(False, ids)
        input("      请把机器人摆成目标姿势，然后按回车继续……")

        print("[2/3] 读取位置")
        positions = robot.capture(ids, timeout=0.3)
        for id_, adc in sorted(positions.items()):
            print(f"      ID {id_:>2}: {adc:>4}  ({(adc - 512) * 0.2932551:>7.2f}°)")

        print("[3/3] 恢复扭矩")
        robot.torque_all(True, ids)
        time.sleep(0.2)

    motor_state = [{"ADC": positions.get(id_, 512), "Direction": 0} for id_ in ids]
    print("\n可直接粘贴进 .rsc 的 Header.MotorState:")
    print(json.dumps(motor_state, ensure_ascii=False, indent=1))

    # 直接生成一个可播放的 .rsc（单帧 = 当前姿势；17 通道，未记录的通道取 512）
    adc = [positions.get(index, 512) for index in range(1, 18)]
    out = args[args.index("--out") + 1] if "--out" in args else "taught.rsc"
    prefix = os.path.splitext(os.path.basename(out))[0]
    path = write_project(out, [{"adc": adc, "period": 500}],
                         motor_count=17, scene_prefix=prefix, home=adc)
    id_text = ",".join(str(i) for i in sorted(positions))
    print(f"\n已导出 {path}（单帧，17 通道）。播放：")
    print(f'  python -m romanbo --port COM3 play "{path}" --ids {id_text} --speed 15')
    print("要多帧动作序列请用 CLI：teach --file taught.json（每扳一个姿势执行一次）"
          "→ export --file taught.json --out taught.rsc")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
