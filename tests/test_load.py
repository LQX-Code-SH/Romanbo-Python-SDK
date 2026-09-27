"""实测负荷（0x18）、加速度（0x0E/0x19）与软件限力的测试。

背景（2026-09-25 真机实测）：
* ``0x18`` 历史命名为 ``GetCurrentLimit``、界面叫「负荷」，实际返回的是**实测
  负荷**：静止 0，运动时跳动（120→32→28），到目标后回 0。
* ``0x0D`` 写入无法从 ``0x18`` 观察到变化（对照 ``0x0C`` 立即可回读）。
* 硬件没有力矩环，限力只能用软件方案：运动中轮询 ``0x18``。
"""

from __future__ import annotations

import unittest

from romanbo import joints as J
from romanbo import protocol as P
from romanbo.robot import RomanboRobot
from romanbo.rsc import MotionFrame
from romanbo.servo import LoadLimitExceeded, Servo, ServoConfig
from romanbo.transport import MockTransport


def _noop(_seconds: float) -> None:
    return None


def _positions_for(robot: RomanboRobot, id_: int) -> list[int]:
    return [((f[5] & 0x07) << 8) | f[6] for f in robot.sent_frames
            if f[4] == P.ServoCmd.SET_POSITION and f[2] == id_]


class TestLoadSemantics(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1], load=37)
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_get_load_reads_measured_load(self) -> None:
        self.assertEqual(self.robot.servo(1).get_load(), 37)

    def test_old_name_is_alias(self) -> None:
        # 旧名仍可用，但读的是实测负荷
        self.assertEqual(self.robot.servo(1).get_current_limit(), 37)
        self.assertIs(Servo.get_load, Servo.get_current_limit)

    def test_config_exposes_load_and_acceleration(self) -> None:
        cfg = self.robot.servo(1).read_config(timeout=0.1)
        self.assertEqual(cfg.load, 37)
        self.assertEqual(cfg.current_limit, 37)          # 别名属性
        data = cfg.as_dict()
        self.assertEqual(data["load"], 37)
        self.assertIn("acceleration", data)
        self.assertNotIn("current_limit", data)

    def test_servo_config_alias_default(self) -> None:
        self.assertIsNone(ServoConfig(id=1).current_limit)


class TestAccelerate(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1])
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_set_accelerate_frame(self) -> None:
        self.robot.servo(1).set_accelerate(12)
        self.assertEqual(self.robot.sent_frames[-1],
                         P.build_set_accelerate(1, 12))
        self.assertEqual(P.build_set_accelerate(1, 12).hex(" ").upper(),
                         "FF FF 01 07 0E 0C E0")
        self.assertEqual(self.robot.servo(1).get_accelerate(), 12)


class TestLoadCheckInterval(unittest.TestCase):
    """抽检间隔（``load_check_every``）必须在**每个**入口都生效。

    真机实测（2026-09-27）：起步涌流只持续几毫秒，而 `Servo.move_at_speed` 的采样点
    在发出该步**之后立刻**，所以间隔 ``1`` 会把涌流当成卡死（步长 10 ADC 时约
    130~150、30 ADC 时约 180）。原先只有 `move_at_speed` 能设这个间隔，
    `set_angle` / `rotate` / `sweep` 根本没有该参数，命令行也没有开关——
    只能靠抬高阈值规避。
    """

    THRESHOLD = 100
    COMMON = dict(torque=None, sleep=_noop, interval_ms=100)

    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1], load=200)        # 恒超阈值
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()
        self.mock.positions[1] = 512

    def tearDown(self) -> None:
        self.robot.close()

    def _abort_step(self, call) -> int:
        with self.assertRaises(LoadLimitExceeded) as ctx:
            call()
        return ctx.exception.step

    def test_every_entry_point_forwards_the_interval(self) -> None:
        servo = self.robot.servo(1)
        cases = {
            "move_at_speed": lambda every: servo.move_at_speed(
                700, 60, current=512, max_load=self.THRESHOLD,
                load_check_every=every, **self.COMMON),
            "set_angle": lambda every: servo.set_angle(
                45, speed_dps=60, current=512, max_load=self.THRESHOLD,
                load_check_every=every, **self.COMMON),
            "rotate": lambda every: servo.rotate(
                45, speed_dps=60, current=512, max_load=self.THRESHOLD,
                load_check_every=every, **self.COMMON),
            "sweep": lambda every: servo.sweep(
                480, 620, cycles=1, speed_dps=60, readback=False, settle=0.0,
                return_home=False, max_load=self.THRESHOLD,
                load_check_every=every, **self.COMMON),
            # 注意 Robot.move 的 torque 不接受 None（不像 Servo 那边可以直接透传）
            "move": lambda every: self.robot.move(
                {1: 700}, speed_dps=60, start={1: 512}, step_interval_ms=100,
                max_load=self.THRESHOLD, load_check_every=every, sleep=_noop),
        }
        for name, call in cases.items():
            for every in (1, 3):
                with self.subTest(api=name, every=every):
                    # 中止步数 == 间隔：说明参数真的传到了库，而不是被默认值吞掉
                    self.assertEqual(self._abort_step(lambda: call(every)), every)

    def test_no_abort_when_the_threshold_is_above_the_load(self) -> None:
        servo = self.robot.servo(1)
        servo.move_at_speed(700, 60, current=512, max_load=255,
                            load_check_every=3, **self.COMMON)
        self.assertEqual(servo.last_peak_load, 200)
        self.assertEqual(self.mock.positions[1], 700, "运动应照常走完")


class TestSoftwareLoadLimit(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1, 2], load=0)
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()
        self.mock.positions[1] = 512
        self.mock.positions[2] = 512

    def tearDown(self) -> None:
        self.robot.close()

    def test_move_at_speed_completes_when_load_is_low(self) -> None:
        sent = self.robot.servo(1).move_at_speed(
            614, 60, current=512, interval_ms=100, max_load=50, sleep=_noop)
        self.assertEqual(sent, [532, 553, 573, 594, 614])
        self.assertEqual(self.robot.servo(1).last_peak_load, 0)

    def test_move_at_speed_aborts_on_high_load(self) -> None:
        self.mock.load = 200
        with self.assertRaises(LoadLimitExceeded) as ctx:
            self.robot.servo(1).move_at_speed(614, 60, current=512,
                                              interval_ms=100, max_load=50,
                                              sleep=_noop)
        exc = ctx.exception
        self.assertEqual((exc.id, exc.load, exc.limit, exc.step), (1, 200, 50, 1))
        self.assertEqual(exc.as_dict()["position"], 532)
        self.assertEqual(self.robot.servo(1).last_peak_load, 200)

    def test_robot_move_aborts(self) -> None:
        self.mock.load = 99
        with self.assertRaises(LoadLimitExceeded):
            self.robot.move({1: 614, 2: 563}, speed_dps=60,
                            start={1: 512, 2: 512}, step_interval_ms=100,
                            max_load=30, sleep=_noop)

    def test_move_without_speed_rejects_load_limit(self) -> None:
        with self.assertRaises(ValueError):
            self.robot.move({1: 614}, max_load=30)

    def test_play_aborts(self) -> None:
        self.mock.load = 150
        frames = [MotionFrame(0, "s", 0, 500, (614, 614)),
                  MotionFrame(0, "s", 1, 500, (700, 700))]
        with self.assertRaises(LoadLimitExceeded):
            self.robot.play(frames, speed_dps=60,
                            start_positions={1: 512, 2: 512},
                            step_interval_ms=100, max_load=40, sleep=_noop)

    def test_play_records_peak_load(self) -> None:
        frames = [MotionFrame(0, "s", 0, 500, (614, 614))]
        self.robot.play(frames, speed_dps=60, start_positions={1: 512, 2: 512},
                        step_interval_ms=100, max_load=200, sleep=_noop)
        self.assertEqual(self.robot.last_peak_load, 0)

    def test_sweep_aborts(self) -> None:
        self.mock.load = 77
        with self.assertRaises(LoadLimitExceeded):
            self.robot.servo(1).sweep(512, 614, cycles=1, speed_dps=60,
                                      readback=False, settle=0.0,
                                      interval_ms=100, max_load=10, sleep=_noop)


if __name__ == "__main__":
    unittest.main()
