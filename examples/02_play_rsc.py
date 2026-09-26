"""播放 ``.rsc`` 工程里的动作（对应动作运行 / 程序下载播放）。

用法::

    python examples/02_play_rsc.py "../example/chapter 01.rsc"
    python examples/02_play_rsc.py "../example/chapter 01.rsc" COM3 --speed 60 --loop
    python examples/02_play_rsc.py "../example/chapter 01.rsc" --dry-run

``--speed`` 为**实际角速度（度/秒）**：每帧周期由该帧角位移反算；不填则直接用
文件自带的 ``Period``。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.rsc import RscProject  # noqa: E402
from romanbo.transport import MockTransport  # noqa: E402


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2

    path = args[0]
    rest = args[1:]
    dry_run = "--dry-run" in rest
    loop = "--loop" in rest
    speed: Optional[float] = None                 # 实际角速度，度/秒
    if "--speed" in rest:
        speed = float(rest[rest.index("--speed") + 1])
    port = next((a for a in rest if not a.startswith("-") and not _is_number(a)),
                None)

    project = RscProject.load(path)
    frames = list(project.frames())
    print(f"工程: {project.project_name}  (版本 {project.version})")
    print(f"电机数: {project.motor_count}，场景 {len(project.scenes)} 个，动作帧 {len(frames)} 帧")
    for scene in project.scenes:
        print(f"  场景 {scene.index}《{scene.name}》 {scene.motion_count} 帧")

    if dry_run:
        robot = RomanboRobot(transport=MockTransport(servo_ids=range(1, 18)))
    else:
        if not port:
            print("缺少串口参数，或使用 --dry-run")
            return 2
        robot = RomanboRobot(port)

    stop = threading.Event()
    with robot:
        if not dry_run:
            robot.handshake(verbose=True)
            robot.torque_all(True, range(1, project.motor_count + 1))
        start_positions = None
        if speed is not None and not dry_run:
            joint_ids = sorted({i for frame in frames for i in frame.targets()})
            print(f"读取起始位置（{len(joint_ids)} 个关节，用于首帧角位移）…")
            start_positions = robot.capture(joint_ids, timeout=1.2)
        try:
            played = robot.play(
                frames, loop=loop, speed_dps=speed,
                start_positions=start_positions,
                on_frame=lambda f: print(f"  帧 scene={f.scene_index} "
                                         f"idx={f.motion_index} period={f.period_ms}ms",
                                         flush=True),
                stop_event=stop)
        except KeyboardInterrupt:
            stop.set()
            print("\n已中断，正在结束……")
            return 130
        if dry_run:
            print(f"\n[mock] 共生成 {len(robot.sent_frames)} 帧报文，"
                  f"前 3 帧：")
            for frame in robot.sent_frames[:3]:
                print("  TX", frame.hex(" ").upper())
    print(f"播放完成，共 {played} 帧")
    time.sleep(0.1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
