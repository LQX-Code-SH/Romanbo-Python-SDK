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


class TestBrokenPipeHandling(unittest.TestCase):
    """下游提前关掉管道（``... | head``）要安静退出，不能吐栈回溯。

    ``ports --all`` 会打 30 多行，接个 ``head`` / ``grep -q`` 很常见；原先写入撞上
    ``EPIPE`` 会抛 ``BrokenPipeError`` 并打印整段 traceback（实测 2026-09-27）。
    """

    def test_broken_pipe_exits_quietly(self) -> None:
        from unittest import mock

        with mock.patch.object(cli, "_main",
                               side_effect=BrokenPipeError(32, "Broken pipe")):
            self.assertEqual(cli.main(["ports"]), 0)

    def test_other_errors_still_propagate(self) -> None:
        from unittest import mock

        with mock.patch.object(cli, "_main", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cli.main(["ports"])


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


class TestPortsDiagnosis(unittest.TestCase):
    """``ports`` 打不开时的原因必须分开报：**权限不足 ≠ 已被占用**。

    原先两者共用一句提示（"多为权限问题（dialout 组）或已被占用"）。当适配器重枚举、
    节点从 ``ttyUSB0`` 变成 ``ttyUSB1`` 时，新节点退回默认权限 ``0660 root:dialout``
    —— 用户不在 ``dialout`` 组就报 ``PermissionError``，但提示里那句"或已被占用"
    会把人引向"找占用进程"（实测 2026-09-27 就误判过：当时没有任何进程持有它）。
    """

    @staticmethod
    def _render(rows: list, argv: list | None = None) -> tuple:
        import contextlib
        import io

        original = cli.list_serial_ports
        cli.list_serial_ports = lambda *a, **k: list(rows)
        try:
            args = cli.build_parser().parse_args(argv or ["ports"])
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.cmd_ports(None, args)
        finally:
            cli.list_serial_ports = original
        return rc, buf.getvalue()

    @staticmethod
    def _advice_needle() -> str:
        """当前平台上"权限不足"那条建议里的**独特**片段。

        别写死 POSIX 的字样：Windows 分支没有 `dialout` / `lsof`（仓库里
        `test_port_hint_covers_every_platform` 早就为此留了教训）。
        """
        return "加入 dialout 组" if not sys.platform.startswith("win") else "关闭占用它的程序"

    @staticmethod
    def _row(busy: bool, reason: str | None = None, device: str = "/dev/ttyUSB1",
             description: str = "FT230X Basic UART") -> dict:
        row = {"device": device, "description": description, "hwid": "", "busy": busy}
        if busy:
            row["error"] = "SerialException"
            if reason:
                row["reason"] = reason
        return row

    def test_permission_denied_is_not_reported_as_busy(self) -> None:
        rc, text = self._render([self._row(True, "permission")])
        self.assertEqual(rc, 0)
        self.assertIn("权限", text)
        self.assertNotIn("lsof", text, "权限问题不该让人去找占用进程")
        self.assertNotIn("fuser", text)
        if not sys.platform.startswith("win"):        # 组名与命令是 POSIX 专有
            self.assertIn("dialout", text)

    @unittest.skipIf(sys.platform.startswith("win"), "Windows 无 lsof/fuser")
    def test_busy_points_to_the_holder(self) -> None:
        _, text = self._render([self._row(True, "busy")])
        self.assertIn("已被占用", text)
        self.assertIn("lsof", text)

    @unittest.skipIf(sys.platform.startswith("win"), "Windows 分支不涉及 dialout")
    def test_unknown_reason_does_not_point_at_the_group_hint(self) -> None:
        """认不出 errno 时不要硬归类（别把人往"加 dialout 组"上引）。"""
        _, text = self._render([self._row(True, "unknown")])
        self.assertNotIn("dialout", text)

    def test_available_port_gets_no_diagnosis(self) -> None:
        _, text = self._render([self._row(False)])
        self.assertIn("可用", text)
        self.assertNotIn("lsof", text)
        self.assertNotIn("dialout", text)

    def test_advice_is_grouped_not_repeated_per_port(self) -> None:
        """建议只打一次（按原因汇总）。

        真机实测（2026-09-27）：这台机器 pyserial 枚举出 34 个 ``ttyS*`` 占位口，每个都
        各打一份三行的建议——100 多行噪音，把真正的 ``ttyUSB0`` 与建议一起埋掉；而且对
        没有接硬件的 ``ttyS*`` 建议 ``chmod`` 是误导。
        """
        rows = [self._row(True, "permission", f"/dev/ttyS{i}") for i in range(34)]
        rows.append(self._row(True, "permission", "/dev/ttyUSB0"))
        _, text = self._render(rows)
        self.assertEqual(text.count(self._advice_needle()), 1, "建议应只打一次")
        self.assertEqual(text.count("不可用 35 个"), 1)
        self.assertIn("/dev/ttyUSB0", text)          # 汇总里要点出真实设备名

    def test_reasons_are_grouped_separately(self) -> None:
        """两种原因各汇总一次，不混在一起。"""
        rows = [self._row(True, "permission", "/dev/ttyS0"),
                self._row(True, "busy", "/dev/ttyUSB0")]
        _, text = self._render(rows)
        self.assertIn("权限不足", text)
        self.assertIn("已被占用", text)
        if not sys.platform.startswith("win"):
            self.assertIn("dialout", text)
            self.assertIn("lsof", text)

    def test_hardwareless_ports_are_hidden_by_default(self) -> None:
        """没接硬件的占位口默认不列，但**不静默丢弃**——要报隐藏了几个、怎么全看。

        真机实测（2026-09-27）：这台机器枚举出 32 个 ``ttyS*`` 占位口，真正的 FT230X
        排在最后；`ports` 是拿来"找设备 + 排权限"的，不该让它埋在一屏噪音里。
        """
        rows = [self._row(False, device=f"/dev/ttyS{i}", description="n/a")
                for i in range(32)]
        rows.append(self._row(False, device="/dev/ttyUSB0"))
        _, text = self._render(rows)
        device_lines = [line for line in text.splitlines()
                        if line.strip().startswith("/dev/")]
        self.assertEqual(len(device_lines), 1, f"只应列出真适配器：{device_lines}")
        self.assertIn("/dev/ttyUSB0", device_lines[0])
        self.assertIn("已隐藏 32 个", text)
        self.assertIn("--all", text)

    def test_all_flag_lists_hardwareless_ports(self) -> None:
        rows = [self._row(False, device="/dev/ttyS0", description="n/a"),
                self._row(False, device="/dev/ttyUSB0")]
        _, text = self._render(rows, ["ports", "--all"])
        self.assertIn("/dev/ttyS0", text)
        self.assertNotIn("已隐藏", text)

    def test_json_follows_the_same_filter(self) -> None:
        """``--json`` 与文本一致（默认过滤），``--all`` 才全给。"""
        import json

        rows = [self._row(False, device="/dev/ttyS0", description="n/a"),
                self._row(False, device="/dev/ttyUSB0")]
        _, text = self._render(rows, ["ports", "--json"])
        self.assertEqual([r["device"] for r in json.loads(text)], ["/dev/ttyUSB0"])
        _, text_all = self._render(rows, ["ports", "--json", "--all"])
        self.assertEqual([r["device"] for r in json.loads(text_all)],
                         ["/dev/ttyUSB0", "/dev/ttyS0"])

    def test_nothing_left_after_filtering_still_points_at_all(self) -> None:
        rows = [self._row(False, device="/dev/ttyS0", description="n/a")]
        _, text = self._render(rows)
        self.assertIn("未发现串口设备", text)
        self.assertIn("--all", text)

    def test_hardwareless_ports_get_no_permission_advice(self) -> None:
        """``--all`` 看全时，占位口标"可忽略"，不要给 ``chmod`` 建议。

        它们打不开是因为**没有硬件**（``ttyS0..31`` 是主板遗留口），给权限建议会把人
        带偏——真适配器在别处。
        """
        rows = [self._row(True, "permission", f"/dev/ttyS{i}", description="n/a")
                for i in range(32)]
        rows.append(self._row(True, "permission", "/dev/ttyUSB0"))
        _, text = self._render(rows, ["ports", "--all"])
        self.assertIn("没有接硬件的占位口（32 个", text)
        self.assertIn("可忽略", text)
        self.assertEqual(text.count(self._advice_needle()), 1, "只该给真实设备那组一份建议")
        anonymous_block = text.split("没有接硬件的占位口", 1)[1].split("·", 1)[0]
        self.assertNotIn("chmod", anonymous_block)

    def test_windows_wording_avoids_posix_commands(self) -> None:
        """把 ``sys.platform`` 打成 ``win32``：**在任何平台**都能跑到 Windows 分支。

        这条是为了守住上面那个坑——2026-09-27 有两个用例写死了 POSIX 的建议文案
        （``dialout``），只在 ``windows-latest`` 上红，本机 Linux 全绿发现不了。
        """
        from unittest import mock

        rows = [self._row(True, "permission"), self._row(True, "busy", "/dev/ttyUSB0")]
        with mock.patch.object(sys, "platform", "win32"):
            rc, text = self._render(rows)
        self.assertEqual(rc, 0)
        self.assertIn("权限", text)
        self.assertIn("已被占用", text)
        for posix_only in ("dialout", "lsof", "fuser", "chmod"):
            self.assertNotIn(posix_only, text, f"Windows 分支不应出现 {posix_only}")

    def test_identified_devices_are_listed_first(self) -> None:
        """``--all`` 下已识别的适配器仍要排在 ``ttyS*`` 占位口之前（真机上它排第 35）。"""
        rows = [self._row(False, device=f"/dev/ttyS{i}", description="n/a")
                for i in range(34)]
        rows.append(self._row(False, device="/dev/ttyUSB0"))
        _, text = self._render(rows, ["ports", "--all"])
        first_device_line = next(line for line in text.splitlines()
                                 if line.strip().startswith("/dev/"))
        self.assertIn("/dev/ttyUSB0", first_device_line, "已识别设备应排第一")


class TestOpenFailureReason(unittest.TestCase):
    """``_open_failure_reason`` 必须按 ``errno`` 分类，**不能**按异常类型。

    实测 2026-09-27：权限不足时 pyserial 抛的是 ``SerialException: [Errno 13]
    Permission denied``——它是 ``OSError`` 的子类，**类型与"已被占用"完全相同**。
    按类型判断会把"权限不足"报成"已被占用"，把人引向"找占用进程"（当时并没有任何
    进程持有那个设备）。按类型判断的旧写法在真机上是错的。
    """

    def test_errno_mapping(self) -> None:
        import errno as errno_mod

        from romanbo.transport import _open_failure_reason

        cases = {
            errno_mod.EACCES: "permission",
            errno_mod.EPERM: "permission",
            errno_mod.EBUSY: "busy",
            errno_mod.EAGAIN: "busy",
            errno_mod.ENOENT: "unknown",                  # 适配器刚被拔掉
            None: "unknown",                              # errno 缺失，不猜
        }
        for code, expected in cases.items():
            with self.subTest(errno=code):
                exc = OSError(code, "x") if code is not None else OSError("x")
                self.assertEqual(_open_failure_reason(exc), expected)

    def test_pyserial_exception_shape(self) -> None:
        """复刻 pyserial 的真实构造方式（``SerialException(errno, message)``）。"""
        from romanbo.transport import _open_failure_reason

        class SerialException(OSError):                   # pyserial 的基类就是 OSError
            pass

        self.assertEqual(
            _open_failure_reason(
                SerialException(13, "could not open port /dev/ttyUSB1: "
                                    "Permission denied: '/dev/ttyUSB1'")),
            "permission")
        self.assertEqual(
            _open_failure_reason(
                SerialException(16, "Could not exclusively lock port /dev/ttyUSB1")),
            "busy")


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
