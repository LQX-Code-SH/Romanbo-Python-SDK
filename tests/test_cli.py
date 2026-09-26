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
