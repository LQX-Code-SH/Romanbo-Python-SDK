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
