"""可视化控制台后端：跑真实 HTTP（回环 + 随机端口），不需要浏览器。

覆盖三件容易出错的事：**访问控制**（令牌 / Host）、**报文日志**（TX/RX 成对）、
**异常映射**（限力中止 → 409、未连接 → 409、未知路径 → 409）。
"""

from __future__ import annotations

import errno
import http.client
import json
import os
import re
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import cli  # noqa: E402
from romanbo.webui_page import PAGE  # noqa: E402
from romanbo.servo import LoadLimitExceeded  # noqa: E402
from romanbo.webui import WebConsole, make_server  # noqa: E402

TOKEN = "unit-test-token"


class _ServerMixin:
    """起一个回环服务（端口 0 = 由系统分配），并给出带令牌的调用助手。"""

    console: WebConsole
    httpd: object
    port: int

    @classmethod
    def start_server(cls, console: WebConsole) -> None:
        cls.console = console
        cls.httpd = make_server("127.0.0.1", 0, console, TOKEN)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      kwargs={"poll_interval": 0.05}, daemon=True)
        cls.thread.start()

    @classmethod
    def stop_server(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.console.disconnect()

    def call(self, path: str, body: object = None, *, token: str | None = TOKEN,
             host: str | None = None, raw_body: bytes | None = None
             ) -> tuple[int, object]:
        """返回 ``(状态码, 解析后的 JSON)``；4xx/5xx 不抛异常。"""
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None
        if raw_body is not None:
            data = raw_body
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data,
                                         method="GET" if data is None else "POST")
        if token is not None:
            request.add_header("X-Robot-Token", token)
        if host is not None:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                payload = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8")
            status = exc.code
        try:
            return status, json.loads(payload)
        except json.JSONDecodeError:
            return status, payload


class TestWebConsole(_ServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        console = WebConsole(mock=True)
        console.connect()
        console.scan()
        cls.start_server(console)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def setUp(self) -> None:
        # 每个用例前把「在线集合」恢复到已知状态，避免用例互相影响
        self.console.scan(1, 17)

    # -- 访问控制 ---------------------------------------------------------- #

    def test_page_carries_the_token(self) -> None:
        status, html = self.call("/")                       # 首页不需要令牌
        self.assertEqual(status, 200)
        self.assertIn("舵机控制台", html)
        self.assertIn(TOKEN, html)
        self.assertNotIn("__TOKEN__", html, "占位符没被替换")

    def test_api_requires_token(self) -> None:
        self.assertEqual(self.call("/api/state", token=None)[0], 401)
        self.assertEqual(self.call("/api/state", token="wrong-token")[0], 401)

    def test_api_rejects_foreign_host(self) -> None:
        """防 DNS rebinding：Host 不是回环名就拒绝（令牌正确也不行）。"""
        status, payload = self.call("/api/state", host="evil.example")
        self.assertEqual(status, 403)
        self.assertIn("Host", str(payload))

    def test_token_via_query_string(self) -> None:
        url = f"http://127.0.0.1:{self.port}/api/state?token={TOKEN}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            self.assertEqual(resp.status, 200)

    # -- 状态与发现 -------------------------------------------------------- #

    def test_state_reports_connection_and_online(self) -> None:
        status, payload = self.call("/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(payload["connected"])
        self.assertTrue(payload["mock"])
        self.assertEqual(payload["online"], list(range(1, 18)))

    def test_scan_endpoint(self) -> None:
        status, payload = self.call("/api/scan", {"start": 1, "end": 4})
        self.assertEqual(status, 200)
        self.assertEqual(payload["found"], [1, 2, 3, 4])

    # -- 读数 -------------------------------------------------------------- #

    def test_servo_config_and_live(self) -> None:
        status, cfg = self.call("/api/servo/1")
        self.assertEqual(status, 200)
        for key in ("position", "pid", "position_limit", "load", "margin",
                    "temperature", "angle"):
            self.assertIn(key, cfg)
        self.assertEqual(cfg["position"], 512)
        self.assertEqual(cfg["angle"], 0.0)

        status, live = self.call("/api/servo/1/live")
        self.assertEqual(status, 200)
        self.assertEqual(set(live), {"id", "position", "load", "temperature"})

    def test_capture_reads_back(self) -> None:
        status, payload = self.call("/api/capture")
        self.assertEqual(status, 200)
        self.assertIn("1", payload["positions"])

    # -- 写入与运动 -------------------------------------------------------- #

    def test_goto_moves_and_logs_frames(self) -> None:
        status, payload = self.call("/api/servo/2/goto", {"adc": 640, "max_load": 60})
        self.assertEqual(status, 409, "只给限力不给速度时应当拒绝")
        self.assertIn("速度", str(payload))

        status, payload = self.call("/api/servo/2/goto", {"adc": 640})
        self.assertEqual(status, 200)
        self.assertEqual(payload["target"], 640)
        self.assertEqual(self.call("/api/servo/2/live")[1]["position"], 640)

        status, frames = self.call("/api/frames?since=0")
        self.assertEqual(status, 200)
        hexes = [f["hex"] for f in frames["frames"]]
        self.assertTrue(any(h.startswith("FF FF 02 08 09") for h in hexes),
                        f"没有发往 ID2 的位置指令：{hexes[:5]}")
        self.assertTrue(all(set(f) <= {"seq", "dir", "hex", "at"} for f in frames["frames"]))
        self.assertEqual(frames["latest"], frames["frames"][-1]["seq"])

    def test_frames_incremental_since(self) -> None:
        _, first = self.call("/api/frames?since=0")
        latest = first["latest"]
        self.call("/api/servo/3/torque", {"on": True})
        _, delta = self.call(f"/api/frames?since={latest}")
        self.assertTrue(delta["frames"], "增量查询应拿到新报文")
        self.assertTrue(all(f["seq"] > latest for f in delta["frames"]))

    def test_angle_input_is_converted(self) -> None:
        status, payload = self.call("/api/servo/4/goto", {"angle": 30.0})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(payload["target"], 614, delta=1)

    def test_pid_and_limit_write_then_readback(self) -> None:
        status, payload = self.call("/api/servo/5/pid", {"p": 100, "i": 1, "d": 20})
        self.assertEqual((status, payload["pid"]), (200, [100, 1, 20]))
        status, payload = self.call("/api/servo/5/limit", {"min": 255, "max": 768})
        self.assertEqual((status, payload["position_limit"]), (200, [255, 768]))
        cfg = self.call("/api/servo/5")[1]
        self.assertEqual(cfg["pid"], [100, 1, 20])
        self.assertEqual(cfg["position_limit"], [255, 768])

    # -- LED（8 色）-------------------------------------------------------- #

    #: 与 docs/SERVO_SPEC.md §5.4 的 d[5] 映射一致
    LED_BYTE = {(False, False, False): 0x00, (True, False, False): 0x80,
                (False, True, False): 0x40, (False, False, True): 0x20,
                (True, True, False): 0xC0, (True, False, True): 0xA0,
                (False, True, True): 0x60, (True, True, True): 0xE0}

    def test_led_color_byte_matches_protocol(self) -> None:
        for (red, green, blue), byte in self.LED_BYTE.items():
            with self.subTest(colors=(red, green, blue)):
                status, payload = self.call("/api/servo/9/led",
                                            {"red": red, "green": green, "blue": blue})
                self.assertEqual(status, 200)
                self.assertEqual(payload["led"]["byte"], byte)

    def test_led_color_reaches_the_wire(self) -> None:
        """真正的验收点：线上帧里的 d[5] 才是发给舵机的值。"""
        self.call("/api/servo/9/led", {"red": True, "green": False, "blue": True})
        _, frames = self.call("/api/frames?since=0")
        sent = [f["hex"] for f in frames["frames"]
                if f["dir"] == "tx" and f["hex"].startswith("FF FF 09 07 11")]
        self.assertTrue(sent, "没有捕获到 ID9 的 LED 下发帧")
        self.assertEqual(sent[-1], "FF FF 09 07 11 A0 41")      # 紫

    def test_led_raw_value_is_still_supported(self) -> None:
        """旧的 ``value`` 写法保留：低 3 位 = 红/绿/蓝（4/2/1），不是 d[5] 本体。"""
        status, payload = self.call("/api/servo/9/led", {"value": 5})   # 4+1 = 红+蓝
        self.assertEqual(status, 200)
        self.assertEqual(payload["led"]["byte"], 0xA0)
        self.assertEqual((payload["led"]["red"], payload["led"]["blue"]), (True, True))

    def test_led_needs_a_color_or_value(self) -> None:
        status, payload = self.call("/api/servo/9/led", {})
        self.assertEqual(status, 409)
        self.assertIn("red", str(payload))

    def test_torque_and_multi_move(self) -> None:
        self.assertEqual(self.call("/api/servo/6/torque", {"on": False})[0], 200)
        status, payload = self.call("/api/move", {"targets": {"6": 600, "7": 400}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["joints"], 2)
        # JSON 对象的键必然是字符串
        self.assertEqual({int(k): v for k, v in payload["targets"].items()},
                         {6: 600, 7: 400})
        self.assertEqual(self.call("/api/servo/6/live")[1]["position"], 600)
        self.assertEqual(self.call("/api/servo/7/live")[1]["position"], 400)

    def test_stop_all_uses_online_ids(self) -> None:
        status, payload = self.call("/api/stop_all", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["broadcast"])
        self.assertEqual(payload["ids"], list(range(1, 18)))

    # -- 异常映射 ---------------------------------------------------------- #

    def test_unknown_path_and_bad_body(self) -> None:
        self.assertEqual(self.call("/api/nope")[0], 409)
        self.assertEqual(self.call("/api/servo/1/nope")[0], 409)
        status, _ = self.call("/api/servo/1/goto", raw_body=b"{not json")
        self.assertEqual(status, 409)

    def test_id_out_of_range(self) -> None:
        status, payload = self.call("/api/servo/99/live")
        self.assertEqual(status, 409)
        self.assertIn("越界", str(payload))

    def test_goto_requires_a_target(self) -> None:
        status, payload = self.call("/api/servo/8/goto", {})
        self.assertEqual(status, 409)
        self.assertIn("adc", str(payload))


class TestLoadLimitMapping(_ServerMixin, unittest.TestCase):
    """限力中止要变成 409 + aborted，而不是 500。"""

    class _AbortingConsole(WebConsole):
        def goto(self, id_, **kwargs):                    # noqa: ANN001, ANN003
            raise LoadLimitExceeded(id_, 129, 10, 1, 973)

    @classmethod
    def setUpClass(cls) -> None:
        console = cls._AbortingConsole(mock=True)
        console.connect()
        cls.start_server(console)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def test_load_limit_is_409(self) -> None:
        status, payload = self.call("/api/servo/8/goto", {"adc": 900, "speed_dps": 120})
        self.assertEqual(status, 409)
        self.assertTrue(payload["aborted"])
        self.assertEqual(payload["detail"]["load"], 129)
        self.assertIn("软件限力", payload["error"])


class TestDisconnectedConsole(_ServerMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.start_server(WebConsole(mock=False))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def test_operations_require_connection(self) -> None:
        self.assertFalse(self.call("/api/state")[1]["connected"])
        for path, body in (("/api/scan", {}), ("/api/servo/1/live", None),
                           ("/api/stop_all", {})):
            with self.subTest(path=path):
                status, payload = self.call(path, body)
                self.assertEqual(status, 409)
                self.assertIn("尚未连接", str(payload))

    def test_connect_without_port_is_rejected(self) -> None:
        status, payload = self.call("/api/connect", {"mock": False})
        self.assertEqual(status, 409)
        self.assertIn("串口", str(payload))

    def test_connect_mock_then_operate(self) -> None:
        status, payload = self.call("/api/connect", {"mock": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["connected"])
        self.assertEqual(self.call("/api/scan", {"start": 1, "end": 3})[1]["found"],
                         [1, 2, 3])
        self.assertEqual(self.call("/api/disconnect", {})[1]["connected"], False)


class TestPageApiContract(_ServerMixin, unittest.TestCase):
    """页面里调用的**每个**接口都必须真的存在（防前后端漂移）。

    这张表是按 `romanbo/webui_page.py` 的调用列出来的：前端用 ``api(path)`` 不带
    请求体时发 GET、带请求体时发 POST。历史上就漏在这里——页面用 GET 调
    ``/api/capture`` 与 ``/api/disconnect``，服务端却只挂了 POST。
    """

    #: (方法, 路径, 请求体) —— 与页面里的 api(...) 调用一一对应
    CALLS = (
        ("POST", "/api/connect", {"mock": True}),
        ("POST", "/api/scan", {"start": 1, "end": 4}),
        ("GET", "/api/ports", None),
        ("GET", "/api/state", None),
        ("GET", "/api/servo/1", None),
        ("GET", "/api/servo/1/live", None),
        ("POST", "/api/servo/1/goto", {"adc": 600, "speed_dps": 60,
                                       "max_load": 100, "load_check_every": 3}),
        ("POST", "/api/servo/1/torque", {"on": True}),
        ("POST", "/api/servo/1/pid", {"p": 50, "i": 0, "d": 5}),
        ("POST", "/api/servo/1/limit", {"min": 1, "max": 1023}),
        ("POST", "/api/servo/1/margin", {"value": 5}),
        ("POST", "/api/servo/1/led", {"red": True, "green": False, "blue": False}),
        ("POST", "/api/servo/1/calib", {}),
        ("GET", "/api/capture", None),
        ("POST", "/api/move", {"targets": {"1": 600, "2": 500}}),
        ("POST", "/api/stop_all", {}),
        ("GET", "/api/frames?since=0", None),
        ("POST", "/api/disconnect", {}),
    )

    @classmethod
    def setUpClass(cls) -> None:
        cls.start_server(WebConsole(mock=False))       # 由上面的 connect 自己连

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def test_every_page_call_succeeds(self) -> None:
        for method, path, body in self.CALLS:
            with self.subTest(path=path):
                status, payload = self.call(path, body)
                self.assertEqual(status, 200, f"{method} {path} -> {status} {payload}")

    def test_contract_table_still_matches_page(self) -> None:
        """表里的接口必须仍被页面调用，否则说明页面删了功能、表要同步。

        页面为了拼 id 会写成 ``"/api/servo/" + sel + "/live"``，所以对这类路径按
        **末段**匹配；纯字面量的（如 ``/api/state``）直接整串匹配。
        """
        from romanbo.webui_page import PAGE

        for _method, path, _body in self.CALLS:
            literal = path.split("?")[0]
            if literal in PAGE:
                continue
            tail = literal.rsplit("/", 1)[-1]
            if tail.isdigit():                      # /api/servo/1 —— 页面里是拼接的
                self.assertIn("/api/servo/", PAGE)
                continue
            self.assertTrue(f'"/{tail}"' in PAGE or f'/{tail}"' in PAGE,
                            f"页面已不再调用 {literal}，请同步本表")


class TestCliWiring(unittest.TestCase):
    def test_webui_subcommand_is_offline_and_wired(self) -> None:
        args = cli.build_parser().parse_args(["webui", "--http-port", "0"])
        self.assertIs(args.func, cli.cmd_webui)
        self.assertIs(args.need_robot, False)
        self.assertEqual(args.http_port, 0)
        self.assertEqual(args.http_host, "127.0.0.1")

    def test_webui_accepts_serial_and_mock_flags(self) -> None:
        args = cli.build_parser().parse_args(
            ["--port", "/dev/ttyUSB0", "--mock", "webui", "--open"])
        self.assertEqual(args.port, "/dev/ttyUSB0")
        self.assertTrue(args.mock)
        self.assertTrue(args.open_browser)


class TestPageIntegrity(unittest.TestCase):
    """页面自身的静态一致性——**不需要浏览器**。

    前端最常见的自伤就是「JS 查了一个 HTML 里不存在的 id」或「id 写重了」，
    这两种都能靠源码静态比对抓出来；否则只有在浏览器里点一下才会暴露。
    """

    HTML_IDS = re.compile(r'\sid="([A-Za-z0-9_-]+)"')
    DOLLAR_REF = re.compile(r'\$\("#([A-Za-z0-9_-]+)"\)')
    BY_ID_REF = re.compile(r'getElementById\("([A-Za-z0-9_-]+)"\)')

    def _ids(self) -> set:
        return set(self.HTML_IDS.findall(PAGE))

    def test_no_duplicate_element_ids(self) -> None:
        ids = self.HTML_IDS.findall(PAGE)
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(duplicates, [], f"HTML 里出现重复 id：{duplicates}")

    def test_referenced_elements_exist(self) -> None:
        declared = self._ids()
        referenced = set(self.DOLLAR_REF.findall(PAGE))
        referenced |= set(self.BY_ID_REF.findall(PAGE))
        missing = sorted(referenced - declared)
        self.assertEqual(missing, [], f"JS 引用了不存在的 id：{missing}")

    def test_control_id_list_exists(self) -> None:
        """``CTRL_IDS`` 是「按 id 批量禁用」的清单，写错一个就会静默失效。"""
        declared = self._ids()
        block = re.search(r"const CTRL_IDS = \[(.*?)\];", PAGE, re.S)
        self.assertIsNotNone(block, "找不到 CTRL_IDS 定义")
        listed = set(re.findall(r'"([A-Za-z0-9_-]+)"', block.group(1)))
        self.assertTrue(listed, "CTRL_IDS 是空的")
        self.assertEqual(sorted(listed - declared), [],
                         "CTRL_IDS 里引用了不存在的 id")

    def test_token_placeholder_is_unique(self) -> None:
        self.assertEqual(PAGE.count("__TOKEN__"), 1,
                         "占位符必须恰好出现一次，否则替换会漏或覆盖错位置")

    def test_no_leftover_debug_markers(self) -> None:
        for marker in ("console.log", "TODO", "FIXME", "debugger"):
            self.assertNotIn(marker, PAGE, f"页面里残留了调试标记：{marker}")


class TestConsoleConcurrency(unittest.TestCase):
    """运动期间控制台仍要能响应——紧急停止与读数**不能**排在运动后面。

    回归 `WebConsole.stop_all` / `servo_live`：它们原先与 `goto` / `multi_move`
    共用一把设备锁，而运动会把锁持有整个动作过程（这里约 2 s），于是
    「紧急停止」要等运动自己走完才生效（安全按钮失效）、读数在运动期间整体冻结。
    """

    def setUp(self) -> None:
        self.console = WebConsole(mock=True)
        self.console.connect()
        self.console.scan(1, 3)

    def tearDown(self) -> None:
        self.console.disconnect()

    def _start_slow_move(self) -> threading.Thread:
        """后台跑一次约 2 s 的运动（512→900 @60°/s，19 步 × 100 ms）。"""
        thread = threading.Thread(
            target=self.console.goto,
            kwargs={"id_": 1, "adc": 900, "speed_dps": 60}, daemon=True)
        thread.start()
        time.sleep(0.25)                 # 等它拿到锁并发出若干步
        return thread

    def test_emergency_stop_does_not_wait_for_the_move(self) -> None:
        thread = self._start_slow_move()
        started = time.perf_counter()
        result = self.console.stop_all()
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 1.0,
                        f"紧急停止等了 {elapsed:.2f}s：说明它在排队等设备锁")
        self.assertTrue(thread.is_alive(), "运动应仍在进行（否则测的不是并发场景）")
        self.assertEqual(result["ids"], [1, 2, 3])
        self.assertFalse(result["broadcast"])
        # 断电帧确实发到线上了（ID 1 的 SetTorque=0）
        sent = [f["hex"] for f in self.console.frames.since(0)[0] if f["dir"] == "tx"]
        self.assertIn("FF FF 01 07 10 00 EA", sent)
        thread.join(timeout=10)

    def test_emergency_stop_broadcasts_before_scanning(self) -> None:
        """还没扫描过时退化为向 1..32 广播断电（同样不能等锁）。"""
        console = WebConsole(mock=True)
        console.connect()
        try:
            result = console.stop_all()
            self.assertTrue(result["broadcast"])
            self.assertEqual(len(result["ids"]), 32)
        finally:
            console.disconnect()

    def test_live_reads_work_during_the_move(self) -> None:
        thread = self._start_slow_move()
        started = time.perf_counter()
        live = self.console.servo_live(1)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5,
                        f"运动期间读数等了 {elapsed:.2f}s：读操作不应取设备锁")
        self.assertIsNotNone(live["position"], "运动期间应能读到位置")
        thread.join(timeout=10)


class TestRequestBodyLimit(_ServerMixin, unittest.TestCase):
    """请求体上限：声明超大 ``Content-Length`` 时立即报错，而不是把线程挂住。"""

    @classmethod
    def setUpClass(cls) -> None:
        console = WebConsole(mock=True)
        console.connect()
        cls.start_server(console)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def test_oversized_content_length_is_rejected_immediately(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", "/api/servo/1/goto")
            conn.putheader("X-Robot-Token", TOKEN)
            conn.putheader("Content-Length", str(1 << 21))    # 2 MiB，且故意不发 body
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 409)
            self.assertIn("过大", response.read().decode("utf-8"))
        finally:
            conn.close()


class TestConnectFailureHint(unittest.TestCase):
    """串口打不开时，控制台要给出**可照做的处置**，而不是只丢一个 errno。

    真实场景（2026-09-27）：适配器重枚举成 ``/dev/ttyUSB1`` 后用户不在 ``dialout``
    组，页面上只显示 ``SerialException: [Errno 13] Permission denied``——看不出该去
    加组还是该去关占用程序。启动时的自动连接与页面的「连接」都只打印 ``str(exc)``，
    所以处置必须拼进**消息本身**。
    """

    def _message(self, exc: OSError, port: str = "/dev/ttyUSB1") -> str:
        from unittest import mock as _mock

        from romanbo import webui

        console = WebConsole(port=port)
        # 真实的失败点在 ``robot.open()``：``SerialTransport.__init__`` 是惰性的、不碰
        # 端口，所以**不能**把桩打在构造函数上——那会绕过 connect 里的包装（第一次
        # 就是这么写错的：测试红、复现脚本也显示不出处置）。
        with _mock.patch.object(webui.RomanboRobot, "open", side_effect=exc):
            with self.assertRaises(RuntimeError) as ctx:
                console.connect()
        return str(ctx.exception)

    def test_permission_denied_says_how_to_fix(self) -> None:
        text = self._message(OSError(13, "Permission denied: '/dev/ttyUSB1'"))
        self.assertIn("Permission denied", text, "原始信息不能丢")
        self.assertIn("权限", text)
        if not sys.platform.startswith("win"):
            self.assertIn("dialout", text, "POSIX 下要指出是组权限问题")

    def test_busy_says_where_to_look(self) -> None:
        text = self._message(OSError(16, "Device or resource busy"))
        self.assertIn("占用", text)

    def test_wrong_port_name_points_at_ports(self) -> None:
        """Windows 式的 ``COM3`` 写在 Linux 上 → ``ENOENT``：要指向能查设备名的命令。"""
        text = self._message(OSError(2, "No such file or directory: 'COM3'"), port="COM3")
        self.assertIn("COM3", text)
        self.assertIn("ports", text)


class TestLostPortReporting(unittest.TestCase):
    """串口被拔掉 / 重新枚举后，控制台不能继续自报「已连接」。

    实测 2026-09-27：适配器重枚举成 ``ttyUSB1`` 后，旧控制台仍显示
    ``connected: true, online: [8, 10]``（攥着已删除的 ``ttyUSB0`` 句柄），直到发命令
    才报 ``Input/output error``——这一段时间里用户会以为机器人还能控。断线判据只看
    "设备消失"类 errno：普通读超时、校验和不符都不算断线。
    """

    @staticmethod
    def _dead_handle():
        """一个"设备已消失"的假句柄：``is_open`` 仍为真，读写全部 EIO。"""

        class _Dead:
            is_open = True

            @staticmethod
            def write(_data):
                raise OSError(errno.EIO, "Input/output error")

            @property
            def in_waiting(self):
                raise OSError(errno.EIO, "Input/output error")

            @staticmethod
            def read(_size):
                raise OSError(errno.EIO, "Input/output error")

            @staticmethod
            def close():
                pass

        return _Dead()

    def _lost_transport(self):
        from romanbo.transport import SerialTransport

        port = SerialTransport("/dev/ttyUSB1")
        port._ser = self._dead_handle()          # 直接放一个已消失的句柄
        return port

    def test_write_failure_marks_the_port_lost(self) -> None:
        port = self._lost_transport()
        self.assertTrue(port.is_open)
        with self.assertRaises(OSError):
            port.write(bytes.fromhex("FFFF010614E0"))
        self.assertFalse(port.is_open, "设备消失后 is_open 必须立刻变假")
        self.assertIn("Input/output error", port.lost_reason or "")

    def test_read_failure_marks_the_port_lost(self) -> None:
        port = self._lost_transport()
        with self.assertRaises(OSError):
            port.read_available(0.05)
        self.assertFalse(port.is_open)
        self.assertTrue(port.lost_reason)

    def test_ordinary_timeout_is_not_a_disconnect(self) -> None:
        """读超时（无数据返回 ``b""``）不能被当成断线——舵机偶尔漏答是常态。"""
        from romanbo.transport import SerialTransport

        class _Quiet:
            is_open = True

            @property
            def in_waiting(self):
                return 0

            @staticmethod
            def close():
                pass

        port = SerialTransport("/dev/ttyUSB1")
        port._ser = _Quiet()
        self.assertEqual(port.read_available(0.02), b"")
        self.assertTrue(port.is_open, "没数据 ≠ 断线")
        self.assertIsNone(port.lost_reason)

    def test_console_stops_claiming_connected_after_the_device_vanishes(self) -> None:
        from romanbo.robot import RomanboRobot

        port = self._lost_transport()
        console = WebConsole(port="/dev/ttyUSB1")
        console._robot = RomanboRobot(transport=port)     # 私有注入：只为验证状态口径
        self.assertTrue(console.state()["connected"])
        self.assertIsNone(console.state()["lost_reason"])

        with self.assertRaises(OSError):
            console._robot.send(bytes.fromhex("FFFF010614E0"))

        state = console.state()
        self.assertFalse(state["connected"], "设备消失后不能再报已连接")
        self.assertTrue(state["lost_reason"], "要带上断线原因，前端才能提示重连")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
