"""CLI 层测试：离线命令与串口占用提示（不需要硬件）。"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import cli  # noqa: E402


class TestCliOfflineCommands(unittest.TestCase):
    def test_ports_is_offline(self) -> None:
        args = cli.build_parser().parse_args(["ports"])
        self.assertIs(args.func, cli.cmd_ports)
        self.assertIs(args.need_robot, False)

    def test_selftest_is_offline(self) -> None:
        args = cli.build_parser().parse_args(["selftest"])
        self.assertIs(args.need_robot, False)

    def test_port_hint_points_to_remedies(self) -> None:
        text = cli._port_hint("COM3", PermissionError("拒绝访问。"))
        self.assertIn("COM3", text)
        self.assertIn("ports", text)          # 指向诊断命令

    def test_port_hint_covers_every_platform(self) -> None:
        """三个平台分支都要覆盖：端口名形态不同，断言不能写死单个平台的字样。

        原先这里断言 ``"USB" in text``（想验证"拔插适配器"的建议）——Windows/Linux
        的文案恰好含 ``USB``，而 macOS 分支只写 ``/dev/tty.usbserial-XXXX``，
        于是只在 macOS 上失败。
        """
        from unittest import mock

        exc = PermissionError("拒绝访问。")
        cases = (
            ("linux", "/dev/ttyUSB0", "dialout"),
            ("darwin", "/dev/tty.usbserial-XXXX", "lsof"),
            ("win32", "拔插", "USB"),
        )
        for platform, *expected in cases:
            with self.subTest(platform=platform):
                with mock.patch.object(sys, "platform", platform):
                    text = cli._port_hint("COM3", exc)
                self.assertIn("COM3", text)
                self.assertIn("python -m romanbo ports", text)
                for needle in expected:
                    self.assertIn(needle, text, f"{platform} 分支缺少 {needle!r}")


class TestStdioEncoding(unittest.TestCase):
    """stdout 编码不足时 CLI 不能崩。

    Windows 上输出被重定向（CI 的管道）时 Python 用区域编码，en-US 是 cp1252，
    编码不了中文的 ``print`` 会抛 ``UnicodeEncodeError`` 中断命令。这里用
    ``PYTHONIOENCODING`` 把子进程的 stdout 换成 cp1252 来复现，与真机一致——
    修复前 ``selftest`` 会在 windows-latest 上稳定失败。
    """

    def test_selftest_survives_non_utf8_stdout(self) -> None:
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        proc = subprocess.run(
            [sys.executable, "-m", "romanbo", "selftest"],
            cwd=root, env=env, capture_output=True, text=True, errors="replace")
        self.assertEqual(proc.returncode, 0,
                         f"非 UTF-8 stdout 下命令中断：\n{proc.stderr[-600:]}")
        self.assertIn("60", proc.stdout)          # 60 条向量全部跑完


class TestLoadEveryFlag(unittest.TestCase):
    """``--load-every`` 必须存在并透传到库。

    注入 `MockTransport`（负荷恒为 100）+ 阈值 50：中止的 ``step`` 就等于间隔，
    因此既证明开关被接受（否则 argparse 直接报错），也证明它真的传到了库
    （而不是被默认值吞掉）。在**进程内**调用，不为每个用例起一个解释器。
    """

    def _abort_step(self, argv: list[str], func: str) -> int:
        import contextlib
        import io
        import json

        from romanbo.robot import RomanboRobot
        from romanbo.transport import MockTransport

        mock = MockTransport(servo_ids=[1], load=100)
        mock.positions[1] = 512
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            args = cli.build_parser().parse_args(["--json"] + argv)
            buffer = io.StringIO()
            # stderr 也要收走：--json 时进度行改走 stderr，否则会漏进测试输出
            with contextlib.redirect_stdout(buffer), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = getattr(cli, func)(robot, args)
            self.assertEqual(rc, 4, "阈值 50 < 负荷 100，应触发限力中止")
            text = buffer.getvalue()
            # JSON 是美化过的多行，前面还可能有进度行 → 从第一个 { 起整段解析
            return json.loads(text[text.index("{"):])["step"]
        finally:
            robot.close()

    def test_interval_reaches_the_library(self) -> None:
        cases = (
            ("cmd_move", ["move", "--targets", "1:700", "--speed", "60"]),
            ("cmd_jog", ["jog", "--id", "1", "--degrees", "45", "--speed", "60"]),
            ("cmd_angle", ["angle", "--id", "1", "--degrees", "45", "--speed", "60"]),
            ("cmd_sweep", ["sweep", "--id", "1", "--low", "480", "--high", "620",
                           "--speed", "60"]),
        )
        for func, argv in cases:
            with self.subTest(cmd=argv[0]):
                step = self._abort_step(
                    argv + ["--max-load", "50", "--load-every", "3"], func)
                self.assertEqual(step, 3)

    def test_default_is_the_library_default(self) -> None:
        """不给 ``--load-every`` 时仍是库默认 1（不改变既有行为）。"""
        step = self._abort_step(
            ["jog", "--id", "1", "--degrees", "45", "--speed", "60", "--max-load", "50"],
            "cmd_jog")
        self.assertEqual(step, 1)

    def test_flag_is_accepted_by_every_load_command(self) -> None:
        """五个带 ``--max-load`` 的子命令都要认这个开关。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        demo = os.path.join(root, "examples", "data", "demo.rsc")
        for argv in (
            ["move", "--targets", "1:700"],
            ["jog", "--id", "1", "--degrees", "45"],
            ["angle", "--id", "1", "--degrees", "45"],
            ["sweep", "--id", "1", "--low", "480", "--high", "620"],
            ["play", demo, "--ids", "1"],
        ):
            with self.subTest(cmd=argv[0]):
                args = cli.build_parser().parse_args(argv + ["--load-every", "2"])
                self.assertEqual(args.load_every, 2)


class TestJsonOutputIsClean(unittest.TestCase):
    """``--json`` 时 stdout 必须**只有**一份可解析的 JSON。

    原先进度行（「读取起始位置…」、`sweep` 的每步报告、`play` 的逐帧行）也打在
    stdout 上，`python -m romanbo --json … | jq` 这类消费方式直接失败。现在它们走
    stderr，stdout 只留结果。
    """

    def _run_json(self, argv: list[str], func: str):
        import contextlib
        import io
        import json

        from romanbo.robot import RomanboRobot
        from romanbo.transport import MockTransport

        mock = MockTransport(servo_ids=[1])
        mock.positions[1] = 512
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            args = cli.build_parser().parse_args(["--json"] + argv)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = getattr(cli, func)(robot, args)
            self.assertEqual(rc, 0, err.getvalue()[-300:])
            return json.loads(out.getvalue()), err.getvalue()
        finally:
            robot.close()

    def test_move_progress_goes_to_stderr(self) -> None:
        payload, err = self._run_json(
            ["move", "--targets", "1:600", "--speed", "60"], "cmd_move")
        self.assertEqual(payload["targets"], {"1": 600})
        self.assertIn("读取起始位置", err)

    def test_sweep_stdout_holds_only_the_summary(self) -> None:
        payload, err = self._run_json(
            ["sweep", "--id", "1", "--low", "500", "--high", "520", "--cycles", "1",
             "--speed", "60"], "cmd_sweep")
        self.assertEqual(payload["range"], [500, 520])
        self.assertIn("步 ", err)                    # 每步报告进了 stderr

    def test_play_emits_a_result(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        demo = os.path.join(root, "examples", "data", "demo.rsc")
        # 大角速度 → 每帧一步，测试很快；这里验证结果与输出分流
        payload, err = self._run_json(
            ["play", demo, "--ids", "1", "--speed", "600"], "cmd_play")
        self.assertGreaterEqual(payload["frames"], 1)
        self.assertEqual(payload["ids"], [1])
        self.assertIn("播放 ", err)


class TestJsonResultOfConfirmCommands(unittest.TestCase):
    """确认类命令在 ``--json`` 下也要出结果。

    原先它们只打一行中文，`--json` 下 stdout 不是 JSON。多 ID 命令（`pid` / `limit`）
    还必须**汇总成一份** JSON——每个 ID 打一份就破坏了「stdout 只有一份文档」的契约。
    """

    def _payload(self, argv: list[str], func: str) -> dict:
        import contextlib
        import io
        import json

        from romanbo.robot import RomanboRobot
        from romanbo.transport import MockTransport

        mock = MockTransport(servo_ids=[8, 10])
        mock.positions[8] = 512
        mock.positions[10] = 512
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            args = cli.build_parser().parse_args(["--json"] + argv)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = getattr(cli, func)(robot, args)
            self.assertEqual(rc, 0)
            return json.loads(out.getvalue())        # 整段必须是一份 JSON
        finally:
            robot.close()

    def test_every_confirm_command_emits_one_result(self) -> None:
        cases = (
            ("cmd_torque", ["torque", "on", "--ids", "8,10"],
             {"ids": [8, 10], "torque": True}),
            ("cmd_led", ["led", "--id", "8", "--color", "1,0,1"], {"ids": [8]}),
            ("cmd_param", ["param", "margin", "--id", "8", "--value", "7"],
             {"what": "margin", "value": 7, "ids": [8]}),
            ("cmd_wheel", ["wheel", "--id", "8", "--speed", "50"], {"speed": 50}),
            ("cmd_sync", ["sync", "--id", "8"], {"id": 8}),
            ("cmd_calib", ["calib", "--ids", "8,10"], {"ids": [8, 10]}),
            ("cmd_set_id", ["set-id", "--id", "8", "--new-id", "9"],
             {"id": 8, "new_id": 9}),
            ("cmd_reset", ["reset", "--id", "8"], {"ids": [8]}),
            ("cmd_pid", ["pid", "--ids", "8,10", "--p", "50"], {"saved": True}),
            ("cmd_limit", ["limit", "--ids", "8,10", "--min", "10", "--max", "1000"],
             {"limit": {"8": [10, 1000], "10": [10, 1000]}}),
        )
        for func, argv, expected in cases:
            with self.subTest(cmd=argv[0]):
                payload = self._payload(argv, func)
                for key, value in expected.items():
                    self.assertEqual(payload[key], value,
                                     f"{argv[0]} 的结果里 {key} 不符")

    def test_multi_id_pid_is_aggregated(self) -> None:
        payload = self._payload(["pid", "--ids", "8,10", "--p", "50"], "cmd_pid")
        self.assertEqual(sorted(payload["pid"]), ["10", "8"])


class TestMainErrorMessages(unittest.TestCase):
    """`main` 的异常分支不能误导人。

    `TimeoutError` 是 `OSError` 的子类：若不先拦，设备没应答会被报成
    「串口打不开 → 去查 dialout 权限 / 端口号」，而端口其实好好地打开了。
    """

    def test_timeout_is_not_reported_as_a_port_problem(self) -> None:
        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            # mock 里只有 ID 1；`angle --speed` 会先回读当前位置 → 读不存在的 ID
            # 必然超时，且该路径不吞异常（`config`/`load` 会把超时吞成 None）
            rc = cli.main(["--mock", "angle", "--id", "30", "--speed", "30",
                           "--degrees", "10", "--timeout", "0.1"])
        self.assertEqual(rc, cli.EXIT_PORT)
        text = err.getvalue()
        self.assertIn("未应答", text)
        self.assertNotIn("dialout", text, "别把人引去查权限——端口是好的")
        self.assertNotIn("打不开", text)

    def test_protocol_error_becomes_a_one_line_message(self) -> None:
        import contextlib
        import io
        from unittest import mock

        from romanbo import protocol as P

        class _Boom:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self):
                raise P.ProtocolError("设备返回错误帧（过载）")

            def __exit__(self, *exc) -> bool:
                return False

        err = io.StringIO()
        with mock.patch.object(cli, "RomanboRobot", _Boom), \
             contextlib.redirect_stderr(err):
            rc = cli.main(["--port", "/dev/null", "handshake"])
        self.assertEqual(rc, cli.EXIT_PORT)
        self.assertIn("过载", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class TestTargetsValidation(unittest.TestCase):
    """``--targets`` 的位置/ID 范围要在参数层挡住。

    位置只占 10 位（0..1023）：越界值会被 `build_set_position` 拒绝，而在修复前
    1024..2047 会**撞上 relative 位**被固件当成相对运动（1500 → 相对、
    2047 → 相对 +1023）——静默的非预期运动，比报错危险得多。
    """

    @staticmethod
    def _parse(argv: list[str]):
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()):     # 屏蔽 argparse 的用法输出
            return cli.build_parser().parse_args(argv)

    def test_valid_targets_become_int_keys(self) -> None:
        args = self._parse(["move", "--targets", "8:600,10:480"])
        self.assertEqual(args.targets, {8: 600, 10: 480})

    def test_out_of_range_position_is_a_clean_parser_error(self) -> None:
        for bad in ("1:1024", "1:1500", "1:2047", "1:-1"):
            with self.subTest(targets=bad):
                with self.assertRaises(SystemExit) as ctx:
                    self._parse(["move", "--targets", bad])
                self.assertEqual(ctx.exception.code, 2)

    def test_out_of_range_servo_id_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._parse(["move", "--targets", "99:600"])
        self.assertEqual(ctx.exception.code, 2)

    def test_malformed_targets_are_rejected(self) -> None:
        for bad in ("8", "8:abc"):
            with self.subTest(targets=bad):
                with self.assertRaises(SystemExit) as ctx:
                    self._parse(["move", "--targets", bad])
                self.assertEqual(ctx.exception.code, 2)


class TestCliPlayOptions(unittest.TestCase):
    def test_play_accepts_ids(self) -> None:
        args = cli.build_parser().parse_args(["play", "x.rsc", "--ids", "8,10"])
        self.assertEqual(args.ids, "8,10")

    def test_play_accepts_speed_and_max_load(self) -> None:
        args = cli.build_parser().parse_args(
            ["play", "x.rsc", "--scene", "1", "--speed", "15", "--max-load", "220"])
        self.assertEqual((args.scene, args.speed, args.max_load), (1, 15.0, 220))


class TestCliMoveSettleAndReadback(unittest.TestCase):
    """``move --settle/--readback``：返回前等待到位并回读实际位置。

    背景：``--speed`` 走步进逼近，函数返回时最后一拍刚下发完、舵机仍在运动，
    直接回读会读到中间值。
    """

    def test_parser_accepts_settle_and_readback(self) -> None:
        args = cli.build_parser().parse_args(
            ["move", "--targets", "8:600", "--settle", "0.5", "--readback"])
        self.assertEqual(args.settle, 0.5)
        self.assertTrue(args.readback)

    def test_parser_defaults_do_not_change_behaviour(self) -> None:
        args = cli.build_parser().parse_args(["move", "--targets", "8:600"])
        self.assertIsNone(args.settle)
        self.assertFalse(args.readback)

    def test_readback_reports_positions_and_error(self) -> None:
        import contextlib
        import io
        import json

        from romanbo.robot import RomanboRobot
        from romanbo.transport import MockTransport

        mock = MockTransport(servo_ids=[8, 10])
        mock.positions[8] = 512
        mock.positions[10] = 512
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            args = cli.build_parser().parse_args(
                ["move", "--targets", "8:600,10:480", "--period", "10",
                 "--settle", "0", "--readback", "--json"])
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                rc = cli.cmd_move(robot, args)
            self.assertEqual(rc, 0)
            payload = json.loads(buffer.getvalue())
            self.assertEqual(payload["readback"], {"8": 600, "10": 480})
            self.assertEqual(payload["error"], {"8": 0, "10": 0})
            self.assertEqual(payload["settle_s"], 0.0)
            self.assertEqual(payload["targets"], {"8": 600, "10": 480})
        finally:
            robot.close()

    def test_default_settle_applies_only_with_readback(self) -> None:
        import contextlib
        import io
        import json

        from romanbo.robot import RomanboRobot
        from romanbo.transport import MockTransport

        mock = MockTransport(servo_ids=[8])
        robot = RomanboRobot(transport=mock, ack_timeout=0.1).open()
        try:
            args = cli.build_parser().parse_args(
                ["move", "--targets", "8:600", "--period", "10", "--json"])
            buffer = io.StringIO()
            started = time.monotonic()
            with contextlib.redirect_stdout(buffer):
                cli.cmd_move(robot, args)
            self.assertLess(time.monotonic() - started, cli.DEFAULT_MOVE_SETTLE)
            self.assertEqual(json.loads(buffer.getvalue())["settle_s"], 0.0)
        finally:
            robot.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
