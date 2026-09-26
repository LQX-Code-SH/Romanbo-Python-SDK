"""CLI 层测试：离线命令与串口占用提示（不需要硬件）。"""

from __future__ import annotations

import os
import sys
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
        self.assertIn("USB", text)            # 拔插适配器的建议


class TestCliPlayOptions(unittest.TestCase):
    def test_play_accepts_ids(self) -> None:
        args = cli.build_parser().parse_args(["play", "x.rsc", "--ids", "8,10"])
        self.assertEqual(args.ids, "8,10")

    def test_play_accepts_speed_and_max_load(self) -> None:
        args = cli.build_parser().parse_args(
            ["play", "x.rsc", "--scene", "1", "--speed", "15", "--max-load", "220"])
        self.assertEqual((args.scene, args.speed, args.max_load), (1, 15.0, 220))


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
