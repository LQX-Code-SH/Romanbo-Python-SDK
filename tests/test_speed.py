"""角速度（--speed，度/秒）实现：步进逼近与周期换算的测试。

实测结论（见 :func:`romanbo.joints.plan_move`）：舵机固件忽略 ``SET_PERIOD``，
因此角速度必须靠按节拍下发中间目标实现，本文件即验证该机制。
"""

from __future__ import annotations

import time
import unittest

from romanbo import joints as J
from romanbo import protocol as P
from romanbo.robot import RomanboRobot
from romanbo.rsc import MotionFrame
from romanbo.transport import MockTransport


def _noop(_seconds: float) -> None:
    return None


def _commands(robot: RomanboRobot, cmd: int) -> list[bytes]:
    return [f for f in robot.sent_frames if f[4] == cmd]


def _positions_for(robot: RomanboRobot, id_: int) -> list[int]:
    """取某关节收到的 SET_POSITION 目标值（d[5] 低 3 位才是位置高位）。"""
    return [((f[5] & 0x07) << 8) | f[6] for f in robot.sent_frames
            if f[4] == P.ServoCmd.SET_POSITION and f[2] == id_]


class TestPeriodSpeedConversion(unittest.TestCase):
    """周期 ↔ 角速度的换算（用于报告与 --period 场景，不决定舵机行为）。"""

    def test_period_matches_angle_over_speed(self) -> None:
        self.assertEqual(J.period_for_speed(51, 15), 997)
        self.assertEqual(J.period_for_speed(-51, 15), 997)

    def test_speed_of_is_inverse(self) -> None:
        self.assertAlmostEqual(J.speed_of(51, 997), 15.0, places=2)

    def test_frame_period_takes_largest_displacement(self) -> None:
        # 多关节帧：取最大角位移，保证没有关节超速（写 .rsc 时挑 Period 用）
        period = J.frame_period_for_speed({1: 512, 2: 512}, {1: 552, 2: 612}, 60)
        self.assertEqual(period, J.period_for_speed(100, 60))
        self.assertEqual(J.frame_period_for_speed({}, {1: 900}, 60, fallback_ms=500),
                         500)

    def test_clamping_and_validation(self) -> None:
        self.assertEqual(J.period_for_speed(0, 60), J.MIN_PERIOD_MS)
        self.assertEqual(J.period_for_speed(1023, 0.1), J.MAX_PERIOD_MS)
        for bad in (0, -1):
            with self.assertRaises(ValueError):
                J.period_for_speed(100, bad)


class TestPlanMove(unittest.TestCase):
    def test_ramp_ends_exactly_on_target(self) -> None:
        # Δ=102 ADC ≈ 29.9°；60°/s、间隔 100ms → 每步约 20 ADC → 5 步
        steps = J.plan_move(512, 614, 60, interval_ms=100)
        self.assertEqual([adc for _, adc in steps], [532, 553, 573, 594, 614])
        self.assertEqual([dt for dt, _ in steps], [0.1] * 5)
        # 平均角速度 = 位移 / 总时长
        # 步数取整会带来少量量化误差（±2% 以内）
        total = sum(dt for dt, _ in steps)
        self.assertAlmostEqual((614 - 512) * J.RATIO_MAIN / total, 60.0, delta=1.0)

    def test_direction_and_zero_delta(self) -> None:
        steps = J.plan_move(614, 512, 60, interval_ms=100)
        self.assertEqual([adc for _, adc in steps], [594, 573, 553, 532, 512])
        self.assertEqual([adc for _, adc in steps][0:1], [614 - 20])
        self.assertEqual(J.plan_move(512, 512, 60), [])

    def test_slower_speed_means_more_steps(self) -> None:
        fast = J.plan_move(512, 614, 120, interval_ms=100)
        slow = J.plan_move(512, 614, 60, interval_ms=100)
        self.assertEqual((len(fast), len(slow)), (2, 5))
        self.assertGreater(len(slow), len(fast))

    def test_interval_grows_for_very_low_speed(self) -> None:
        # 1°/s 时每步位移不足 1 ADC → 间隔需要拉长
        interval = J.pick_step_interval(1)
        self.assertGreater(interval, J.DEFAULT_STEP_INTERVAL_MS)
        self.assertGreaterEqual(1 * interval / 1000.0 / J.RATIO_MAIN, 1)
        self.assertEqual(J.pick_step_interval(120), J.DEFAULT_STEP_INTERVAL_MS)

    def test_rejects_non_positive_speed(self) -> None:
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                J.plan_move(512, 614, bad)


class TestServoStepwise(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=range(1, 6))
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_move_at_speed_sends_ramp_without_period_frames(self) -> None:
        servo = self.robot.servo(1)
        servo.set_position(512, wait=False)
        self.robot.sent_frames.clear()
        sent = servo.move_at_speed(614, 60, current=512, interval_ms=100,
                                   torque=None, sleep=_noop)
        self.assertEqual(sent, [532, 553, 573, 594, 614])
        self.assertEqual(_commands(self.robot, P.ServoCmd.SET_PERIOD), [])
        self.assertEqual(self.mock.positions[1], 614)

    def test_rotate_with_speed_is_stepwise(self) -> None:
        self.robot.servo(1).rotate(30, speed_dps=60, current=512, sleep=_noop)
        self.assertEqual(_commands(self.robot, P.ServoCmd.SET_PERIOD), [])
        frames = _positions_for(self.robot, 1)
        self.assertEqual(frames[-1], 512 + 102)
        self.assertGreater(len(frames), 1)

    def test_period_and_speed_conflict(self) -> None:
        with self.assertRaises(ValueError):
            self.robot.servo(1).rotate(15, period_ms=500, speed_dps=30,
                                       current=512, sleep=_noop)

    def test_sweep_with_speed(self) -> None:
        records = self.robot.servo(1).sweep(512, 614, cycles=1, speed_dps=60,
                                            readback=False, settle=0.0,
                                            interval_ms=100, sleep=_noop)
        self.assertEqual([r["target"] for r in records], [614, 512])
        self.assertEqual(_commands(self.robot, P.ServoCmd.SET_PERIOD), [])
        self.assertEqual(_positions_for(self.robot, 1)[-1], 512)

    def test_step_budget_uses_the_high_resolution_clock(self) -> None:
        """节拍预算必须用 ``perf_counter``，不能用 ``monotonic``。

        Windows 上 ``monotonic()`` 在 CPython <= 3.12 走 ``GetTickCount64()``
        （粒度约 15.6 ms），而一拍才 100 ms —— 预算会偏 ±15%，直接吃掉文档承诺的
        ±4% 角速度。做法：把 ``monotonic`` 换成**每读一次就跨一个 tick** 的粗时钟，
        把 ``perf_counter`` 换成基本不前进的受控时钟；若实现用的是粗时钟，
        第一次预算就会变成 ``dt - 0.015625`` 而不是 ``dt``。
        """
        from unittest import mock

        state = {"mono": 100.0, "perf": 1000.0, "sleeps": []}

        def coarse_monotonic() -> float:
            state["mono"] += 0.015625           # 每次读取前进一个 Windows tick
            return state["mono"]

        def controlled_perf_counter() -> float:
            state["perf"] += 1e-9               # 几乎不动（真实耗时应为 ~0）
            return state["perf"]

        def sleep(seconds: float) -> None:
            state["sleeps"].append(seconds)
            state["perf"] += seconds

        servo = self.robot.servo(1)
        with mock.patch.multiple(time, monotonic=coarse_monotonic,
                                 perf_counter=controlled_perf_counter):
            # max_load 不为 None → 走「扣掉回读耗时」的预算路径
            servo.move_at_speed(614, 60, current=512, interval_ms=100,
                                max_load=255, load_check_every=3, sleep=sleep)

        self.assertTrue(state["sleeps"], "没有走到预算路径")
        for seconds in state["sleeps"]:
            self.assertAlmostEqual(seconds, 0.1, places=6,
                                   msg="预算被粗时钟污染（用了 monotonic？）")

    def test_overshooting_sleep_does_not_accumulate_drift(self) -> None:
        """``sleep`` 的过冲不能**逐拍累积**——按绝对时刻排拍才能压住。

        背景：Windows 的 ``Sleep`` 粒度约 15.6 ms，而一拍才 100 ms。按"每拍睡 dt"实现时，
        每拍都多睡一点，长动作一路慢下去（N 拍就慢 N×过冲，实测最坏约 −13%，正好吃掉文档
        承诺的 ±4% 角速度）。按绝对时刻排拍后，迟到会被下一拍的短睡眠补回来：总时长 ≈
        N×dt **加一次过冲**，不再累积。

        做法：让每次 ``sleep`` 多睡 30 ms 并如实推进受控时钟。
        """
        from unittest import mock

        state = {"perf": 1000.0, "sleeps": []}

        def perf_counter() -> float:
            state["perf"] += 1e-9                # 每次读都前进一点，保证轮询循环能退出
            return state["perf"]

        def sleep(seconds: float) -> None:
            state["sleeps"].append(seconds)
            state["perf"] += seconds + 0.03      # 每次都过冲 30 ms

        with mock.patch.object(time, "perf_counter", perf_counter):
            sent = self.robot.servo(1).move_at_speed(
                614, 60, current=512, interval_ms=100, max_load=255,
                load_check_every=3, sleep=sleep)

        elapsed = state["perf"] - 1000.0
        expected = len(sent) * 0.1
        self.assertGreaterEqual(len(state["sleeps"]), 5, "样本太少")
        self.assertAlmostEqual(
            elapsed, expected, delta=0.06,
            msg=f"过冲被累积了：实测 {elapsed:.3f}s、期望约 {expected:.3f}s"
                f"（逐拍累积的旧实现会是 {len(sent) * 0.13:.3f}s）")

    def test_multi_joint_move_does_not_accumulate_drift(self) -> None:
        """多关节路径（`RomanboRobot.move`）同样按绝对时刻排拍。"""
        from unittest import mock

        state = {"perf": 500.0, "sleeps": []}

        def perf_counter() -> float:
            state["perf"] += 1e-9
            return state["perf"]

        def sleep(seconds: float) -> None:
            state["sleeps"].append(seconds)
            state["perf"] += seconds + 0.03

        with mock.patch.object(time, "perf_counter", perf_counter):
            self.robot.move({1: 614}, speed_dps=60, start={1: 512},
                            step_interval_ms=100, max_load=255,
                            load_check_every=3, sleep=sleep)

        elapsed = state["perf"] - 500.0
        expected = len(state["sleeps"]) * 0.1
        self.assertGreaterEqual(len(state["sleeps"]), 5, "样本太少")
        self.assertAlmostEqual(
            elapsed, expected, delta=0.06,
            msg=f"多关节路径的过冲被累积了：{elapsed:.3f}s vs 期望约 {expected:.3f}s")

    def test_sweep_period_path_uses_injected_sleep(self) -> None:
        """``period_ms`` 分支也必须走注入的 ``sleep``，否则测试会真的睡下去。

        原先该分支写死 ``time.sleep(period/1000 + settle)``：传了 ``sleep=_noop``
        也照样真阻塞（步进分支却尊重它），既拖慢测试也说明参数没生效。
        """
        calls: list[float] = []
        records = self.robot.servo(1).sweep(512, 614, cycles=1, period_ms=500,
                                            readback=False, settle=0.1,
                                            return_home=False, sleep=calls.append)
        self.assertEqual([r["target"] for r in records], [614, 512])
        self.assertEqual(calls, [0.6, 0.6], "period 分支没有用注入的 sleep")


class TestRobotStepwise(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=range(1, 6))
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_multi_joint_ramps_share_one_cadence(self) -> None:
        self.robot.move({1: 614, 2: 563}, speed_dps=60,
                        start={1: 512, 2: 512}, step_interval_ms=100,
                        sleep=_noop)
        # 关节1：Δ102 → 5 步；关节2：Δ51 → 3 步（到位后不再重复下发）
        self.assertEqual(_positions_for(self.robot, 1),
                         [532, 553, 573, 594, 614])
        self.assertEqual(_positions_for(self.robot, 2), [529, 546, 563])
        self.assertEqual(_commands(self.robot, P.ServoCmd.SET_PERIOD), [])

    def test_joint_without_start_is_sent_directly(self) -> None:
        self.robot.move({1: 614, 2: 563}, speed_dps=60,
                        start={1: 512}, step_interval_ms=100, sleep=_noop)
        self.assertEqual(_positions_for(self.robot, 2), [563])
        self.assertEqual(_positions_for(self.robot, 1)[-1], 614)

    def test_rejects_non_positive_speed(self) -> None:
        with self.assertRaises(ValueError):
            self.robot.move({1: 614}, speed_dps=0, sleep=_noop)


class TestPlayWithSpeed(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=range(1, 6))
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()
        self.frames = [
            MotionFrame(0, "s", 0, 500, (512, 512, 512)),
            MotionFrame(0, "s", 1, 500, (614, 614, 614)),
            MotionFrame(0, "s", 2, 250, (614, 614, 614)),   # 保持姿势
        ]

    def tearDown(self) -> None:
        self.robot.close()

    def test_frames_are_played_stepwise(self) -> None:
        slept: list[float] = []
        played = self.robot.play(self.frames, speed_dps=60,
                                 start_positions={1: 512, 2: 512, 3: 512},
                                 step_interval_ms=100, sleep=slept.append)
        self.assertEqual(played, 3)
        self.assertEqual(_commands(self.robot, P.ServoCmd.SET_PERIOD), [])
        # 第二段：3 个关节各 5 步；首段无位移、第三段保持姿势 → 只按文件 Period 停留
        self.assertEqual(len(_commands(self.robot, P.ServoCmd.SET_POSITION)), 15)
        self.assertEqual([_positions_for(self.robot, i)[-1] for i in (1, 2, 3)],
                         [614, 614, 614])
        self.assertIn(0.25, slept)                       # 保持姿势的帧
        # 节拍睡眠：按**绝对时刻**排拍，预算是「到第 i 拍目标时刻还剩多久」。
        # 这里注入的 `slept.append` 只记录、不真的睡（不倒时钟），所以后续预算会逐步累加
        # （0.1、0.2…）——这是正确的：真正"每拍 ≈ 100 ms"的语义由
        # test_overshooting_sleep_does_not_accumulate_drift 用受控时钟守着。
        # 这里只确认节拍路径确实走到了 sleep（第一拍尚未被"不睡"影响）。
        self.assertAlmostEqual(slept[1], 0.1, delta=0.01, msg="第一拍预算应 ≈100 ms")

    def test_missing_start_positions_jump_to_first_frame(self) -> None:
        self.robot.play(self.frames, speed_dps=60, step_interval_ms=100,
                        sleep=_noop)
        self.assertEqual(_positions_for(self.robot, 1)[0], 512)

    def test_period_path_still_uses_file_period(self) -> None:
        self.robot.play(self.frames, speed_scale=2.0, sleep=_noop)
        periods = [(f[5] << 8) | f[6] for f in
                   _commands(self.robot, P.ServoCmd.SET_PERIOD)]
        self.assertTrue(periods)
        # 帧 Period 500/500/250 × 倍率 2.0
        self.assertEqual(set(periods), {1000, 500})

    def test_rejects_non_positive_speed(self) -> None:
        with self.assertRaises(ValueError):
            self.robot.play(self.frames, speed_dps=0)


if __name__ == "__main__":
    unittest.main()
