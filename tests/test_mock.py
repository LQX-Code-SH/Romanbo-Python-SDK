"""整机逻辑测试：使用离线模拟器，无需硬件。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import protocol as P  # noqa: E402
from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.rsc import MotionFrame  # noqa: E402
from romanbo.transport import MockTransport  # noqa: E402


class TestMockEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=range(1, 18))
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.2)
        self.robot.open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_handshake_ok(self) -> None:
        result = self.robot.handshake(settle=False)
        self.assertTrue(result.model_ok)
        self.assertTrue(result.version_ok)
        self.assertTrue(result.ok)
        self.assertIn("ROMANBO", result.describe())

    def test_handshake_reports_wrong_device(self) -> None:
        mock = MockTransport(servo_ids=[], controller=True, model=0x00, version=0)
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        result = robot.handshake(retries=0, settle=False)
        self.assertFalse(result.ok)
        self.assertIn("不是 ROMANBO", result.describe())

    def test_scan_finds_servos(self) -> None:
        # quarantine=0：模拟器不需要总线静默期，避免测试白等
        self.assertEqual(self.robot.scan(1, 18, quarantine=0), list(range(1, 18)))
        self.assertEqual(self.robot.scan(33, 40, quarantine=0), [])

    def test_set_position_sends_period_then_position(self) -> None:
        self.robot.servo(3).set_position(600, period_ms=500, torque=1)
        sent = self.robot.sent_frames
        self.assertEqual(sent[-2], P.build_set_period(3, 500))
        self.assertEqual(sent[-1], P.build_set_position(3, 600))
        for frame in sent:
            self.assertEqual(sum(frame) & 0xFF, 0)

    def test_move_default_uses_set_position(self) -> None:
        # 默认 mode="position"：逐关节 SET_PERIOD + SET_POSITION（实测可用路径）
        self.robot.move({1: 600, 2: 480}, period_ms=300)
        frames = self.robot.sent_frames
        self.assertEqual([f[4] for f in frames],
                         [P.ServoCmd.SET_PERIOD, P.ServoCmd.SET_POSITION,
                          P.ServoCmd.SET_PERIOD, P.ServoCmd.SET_POSITION])
        self.assertEqual(frames[0], P.build_set_period(1, 300))
        self.assertEqual(frames[1], P.build_set_position(1, 600))
        self.assertEqual(frames[3], P.build_set_position(2, 480))

    def test_move_next_mode_uses_broadcast_sync(self) -> None:
        self.robot.move({1: 600, 2: 480}, period_ms=300, mode="next")
        frames = self.robot.sent_frames
        # 每关节先 SET_PERIOD 再 SET_NEXT_POSITION，最后广播同步
        self.assertEqual([f[4] for f in frames[:-1]],
                         [P.ServoCmd.SET_PERIOD, P.ServoCmd.SET_NEXT_POSITION,
                          P.ServoCmd.SET_PERIOD, P.ServoCmd.SET_NEXT_POSITION])
        self.assertEqual(frames[-1], P.build_set_sync(P.BROADCAST_ID))
        self.assertEqual(frames[0], P.build_set_period(1, 300))
        self.assertEqual(frames[1], P.build_set_next_position(1, 600))

    def test_move_next_mode_can_sync_per_id(self) -> None:
        self.robot.move({1: 600}, period_ms=100, mode="next", sync_id=None)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_sync(1))

    def test_move_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            self.robot.move({1: 600}, mode="speed")

    def test_capture_reads_positions(self) -> None:
        self.mock.positions[1] = 700
        self.mock.positions[2] = 300
        positions = self.robot.capture([1, 2, 3], timeout=0.1)
        self.assertEqual(positions, {1: 700, 2: 300, 3: 512})

    def test_torque_all(self) -> None:
        self.robot.torque_all(True, [1, 2])
        frames = [f for f in self.robot.sent_frames
                  if f[4] == P.ServoCmd.SET_TORQUE]
        self.assertEqual([f[2] for f in frames], [1, 2])
        self.assertTrue(all(f[5] == P.ON for f in frames))

    def test_set_pid_writes_nosave_first_and_verifies(self) -> None:
        # 实测 0x07（保存式）不可靠 → 必须先发 0x47（RAM 立即生效），再补 0x07
        got = self.robot.servo(1).set_pid(10, 2, 3)
        self.assertEqual(got, (10, 2, 3))
        self.assertEqual(self.robot.servo(1).get_pid(), (10, 2, 3))
        cmds = [f[4] for f in self.robot.sent_frames
                if f[4] in (P.ServoCmd.SET_PID, P.ServoCmd.SET_PID_NOSAVE)]
        self.assertEqual(cmds, [P.ServoCmd.SET_PID_NOSAVE, P.ServoCmd.SET_PID])
        self.assertEqual(self.mock.pids[1], (10, 2, 3))

    def test_set_pid_can_skip_verify(self) -> None:
        self.assertIsNone(self.robot.servo(1).set_pid(9, 9, 9, verify=False))
        self.assertEqual(self.mock.pids[1], (9, 9, 9))

    def test_set_pid_raises_when_write_is_ignored(self) -> None:
        # 模拟"写入被静默丢弃"（SET 类命令无 ACK，本机实测会偶发丢写）
        class _ReadOnlyPids(dict):
            def __setitem__(self, key, value) -> None:   # noqa: D105
                pass                                    # 吞掉写入

        mock = MockTransport(servo_ids=range(1, 18))
        mock.pids = _ReadOnlyPids()
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            with self.assertRaises(P.ProtocolError):
                robot.servo(1).set_pid(11, 1, 1, retries=2)
        finally:
            robot.close()

    def test_get_period_refuses_by_default_and_sends_nothing(self) -> None:
        # 0x0F 实测会被固件当成 SetPositionLimit 改写限值且无应答 → 库层默认拒发
        before = list(self.robot.sent_frames)
        with self.assertRaises(P.ProtocolError):
            self.robot.servo(1).get_period()
        self.assertEqual(list(self.robot.sent_frames), before)

    def test_get_period_unsafe_opt_in_sends_0f(self) -> None:
        self.mock.period_ms = 800
        self.assertEqual(self.robot.servo(1).get_period(unsafe=True), 800)
        cmds = [f[4] for f in self.robot.sent_frames
                if f[4] == P.ServoCmd.GET_MOTION_PERIOD]
        self.assertEqual(cmds, [P.ServoCmd.GET_MOTION_PERIOD])

    def test_play_filters_to_online_ids(self) -> None:
        # 文件是整机动作，但只应驱动指定的在线关节（避免触发总线静默期）
        frames = [MotionFrame(scene_index=0, scene_name="s", motion_index=0,
                              period_ms=100, adc=(500,) * 17)]
        robot = self.robot
        robot.play(frames, ids=[8, 10], wait_frame=False)
        touched = [f[2] for f in robot.sent_frames
                   if f[4] == P.ServoCmd.SET_POSITION]
        self.assertEqual(sorted(set(touched)), [8, 10])

    def test_play_without_ids_sends_all_channels(self) -> None:
        frames = [MotionFrame(scene_index=0, scene_name="s", motion_index=0,
                              period_ms=100, adc=(500,) * 17)]
        self.robot.play(frames, wait_frame=False)
        touched = sorted({f[2] for f in self.robot.sent_frames
                          if f[4] == P.ServoCmd.SET_POSITION})
        self.assertEqual(touched, list(range(1, 18)))

    def test_set_position_limit_verifies(self) -> None:
        # 限值会掉电保存且是硬夹紧，写入必须回读确认
        got = self.robot.servo(1).set_position_limit(1, 1023)
        self.assertEqual(got, (1, 1023))
        self.assertEqual(self.robot.servo(1).get_position_limit(), (1, 1023))

    def test_set_position_limit_raises_when_no_reply(self) -> None:
        mock = MockTransport(servo_ids=[1], drop_every=1)      # 所有应答都丢
        robot = RomanboRobot(transport=mock, ack_timeout=0.05).open()
        try:
            with self.assertRaises(P.ProtocolError):
                robot.servo(1).set_position_limit(1, 1023, retries=2)
        finally:
            robot.close()

    def test_read_config(self) -> None:
        cfg = self.robot.servo(1).read_config(timeout=0.1)
        self.assertEqual(cfg.id, 1)
        self.assertIsNotNone(cfg.position)
        self.assertEqual(cfg.pid, (50, 0, 5))
        self.assertEqual(cfg.position_limit, (255, 768))

    def test_led_and_wheel(self) -> None:
        self.robot.servo(1).set_led(255)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_led(1, 255))
        self.robot.servo(1).wheel(120, direction=1, free=True)
        self.assertEqual(self.robot.sent_frames[-1],
                         P.build_set_wheel(1, 120, free=1, direction=1))

    def test_timeout_without_reply(self) -> None:
        mock = MockTransport(servo_ids=[1], auto_ack=False)
        robot = RomanboRobot(transport=mock, ack_timeout=0.05).open()
        with self.assertRaises(TimeoutError):
            robot.servo(1).get_position()

    def test_error_response_raises(self) -> None:
        class ErrorMock(MockTransport):
            def _servo_reply(self, req: P.Response) -> bytes:
                if req.cmd == P.ServoCmd.GET_POSITION:
                    return P.make_frame(P.ServoAck.ERROR, req.id,
                                        bytes([P.ServoErrorCode.LIMIT]))
                return super()._servo_reply(req)

        robot = RomanboRobot(transport=ErrorMock(servo_ids=[1]), ack_timeout=0.1).open()
        with self.assertRaises(P.ErrorResponse) as ctx:
            robot.servo(1).get_position()
        self.assertEqual(ctx.exception.code, P.ServoErrorCode.LIMIT)

    def test_play_motion_frames(self) -> None:
        frames = [
            MotionFrame(0, "s0", 0, 50, (512,) * 17),
            MotionFrame(0, "s0", 1, 50, (600,) * 17),
        ]
        played = self.robot.play(frames, speed_scale=0.05, wait_frame=True)
        self.assertEqual(played, 2)
        positions = self.mock.positions
        self.assertEqual(positions.get(1), 600)
        for frame in self.robot.sent_frames:
            self.assertEqual(sum(frame) & 0xFF, 0)

    def test_joint_math(self) -> None:
        from romanbo import joints as J
        self.assertEqual(J.angle_to_adc(0.0), 512)
        self.assertAlmostEqual(J.adc_to_angle(512), 0.0)
        self.assertEqual(J.reflect_adc(600), 424)
        self.assertEqual(J.mirror_map({1: 600}, [(1, 2)]), {1: 600, 2: 424})


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
