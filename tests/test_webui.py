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
from romanbo import joints as J  # noqa: E402
from romanbo import protocol as P  # noqa: E402
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
                payload = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace")
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
        # 30° × 1023/360 ≈ 85 ADC（RATIO_MAIN = 360/1023）
        self.assertAlmostEqual(payload["target"], 597, delta=1)

    def test_scan_returns_after_round1_and_falls_back(self) -> None:
        """扫描第 1 轮即返回，剩余 2 轮后台兜底并更新 /api/state。"""
        _, payload = self.call("/api/scan", {"start": 1, "end": 4})
        self.assertEqual(payload["rounds"], 1)
        self.assertEqual(payload["total_rounds"], 3)
        self.assertEqual(payload["pending_rounds"], 2)
        self.assertEqual(payload["found"], [1, 2, 3, 4])
        self.assertEqual(payload["arms"], {})
        # 等后台兜底轮跑完（mock 全范围每轮 ~2 s，空 ID 各等 0.15 s 探测超时）
        deadline = time.time() + 8
        state = self.call("/api/state")[1]
        while state["scan_rounds"] < 3 and time.time() < deadline:
            time.sleep(0.01)
            state = self.call("/api/state")[1]
        self.assertEqual(state["scan_rounds"], 3)
        self.assertEqual(state["online"], [1, 2, 3, 4])
        self.assertEqual(state["arms"], {})

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
        ("GET", "/api/arm/info", None),
        ("GET", "/api/arm/state", None),
    )

    @classmethod
    def setUpClass(cls) -> None:
        # 机械臂接口（TF 可视化）依赖 config/*.json 与 urdf/——测试环境下按
        # 仓库根目录显式加载；仓库里没有这些文件时只测上面的通用行。
        from romanbo.arm import discover
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skeleton, arms = discover(os.path.join(root, "urdf", "Romanbo.urdf"),
                                  os.path.join(root, "config"))
        cls.start_server(WebConsole(mock=False, arms=arms, skeleton=skeleton))
        if arms:
            key = sorted(arms)[0]
            first = sorted(arms[key].joints)[0]
            rows = [("POST", f"/api/arm/{key}/goto", {"targets": {first: 10.0}}),
                    ("POST", f"/api/arm/{key}/home", {"speed_dps": 60}),
                    ("POST", f"/api/arm/{key}/torque", {"on": True})]
            # 运动类要在 disconnect 之前（连接是上面 connect 行建立的）
            base = list(cls.CALLS)
            cut = base.index(("POST", "/api/disconnect", {}))
            cls.CALLS = tuple(base[:cut]) + tuple(rows) + tuple(base[cut:])
        if skeleton and skeleton.links:
            mesh_file = skeleton.links[sorted(skeleton.links)[0]][0]["file"]
            cls.CALLS = tuple(cls.CALLS) + (("GET", f"/api/mesh/{mesh_file}", None),)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stop_server()

    def test_every_page_call_succeeds(self) -> None:
        for method, path, body in self.CALLS:
            with self.subTest(path=path):
                status, payload = self.call(path, body)
                self.assertEqual(status, 200, f"{method} {path} -> {status} {payload}")

    # -- mesh（STL 文件服务）----------------------------------------------- #

    def _raw_get(self, path: str) -> tuple[int, bytes]:
        """二进制响应不能走 `call`（它按 UTF-8 解码 JSON）。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", path, headers={"X-Robot-Token": TOKEN})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_mesh_serves_binary_stl(self) -> None:
        status, body = self._raw_get("/api/mesh/meshes/Romanbo_base.STL")
        self.assertEqual(status, 200)
        # 二进制 STL 布局自检：84 + 50×三角形数 == 文件长度
        self.assertGreater(len(body), 84)
        self.assertEqual(84 + 50 * int.from_bytes(body[80:84], "little"), len(body))

    def test_mesh_rejects_path_escape_and_wrong_type(self) -> None:
        for path in ("/api/mesh/../romanbo/webui.py",          # ../ 逃逸
                     "/api/mesh/meshes/../../pyproject.toml",  # 逃逸 + 非 STL
                     "/api/mesh/meshes/missing.STL",           # 不存在
                     "/api/mesh/romanbo/webui_page.py"):       # 白名单外类型
            with self.subTest(path=path):
                status, _ = self._raw_get(path)
                self.assertEqual(status, 409)

    def test_mesh_requires_token(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/api/mesh/meshes/Romanbo_base.STL")
            self.assertEqual(conn.getresponse().status, 401)
        finally:
            conn.close()

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
            if literal.startswith("/api/mesh/"):
                # 页面里是 "/api/mesh/" + 文件名拼接（文件名来自 /api/arm/info）
                self.assertIn("/api/mesh/", PAGE)
                continue
            tail = literal.rsplit("/", 1)[-1]
            if tail.isdigit():                      # /api/servo/1 —— 页面里是拼接的
                self.assertIn("/api/servo/", PAGE)
                continue
            self.assertTrue(f'"/{tail}"' in PAGE or f'/{tail}"' in PAGE,
                            f"页面已不再调用 {literal}，请同步本表")


class TestServoLedIndicators(unittest.TestCase):
    """舵机 LED 状态指示：绿 = 通信正常、蓝 = 力矩使能、灭 = 断开连接。

    编码见 `WebConsole._set_led` / 协议 §5.4：``d[5]`` 的 bit6=绿(0x40)、
    bit5=蓝(0x20)，全 0 = 灭。
    """

    @classmethod
    def setUpClass(cls) -> None:
        from romanbo.arm import discover
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skeleton, arms = discover(os.path.join(root, "urdf", "Romanbo.urdf"),
                                  os.path.join(root, "config"))
        if not arms:
            raise unittest.SkipTest("仓库缺少 config/*.json 或 urdf/，跳过")
        cls.arms = arms
        cls.console = WebConsole(mock=True, arms=arms, skeleton=skeleton)
        cls.console.connect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.console.disconnect()

    def setUp(self) -> None:
        self.console.scan()
        self.console._robot.sent_frames.clear()

    # -- 工具 -------------------------------------------------------------- #

    def _led(self, byte: int) -> set[int]:
        """SET_LED 帧（d[5] == byte）→ 舵机 ID 集合。"""
        return {f[2] for f in self.console._robot.sent_frames
                if f[4] == P.ServoCmd.SET_LED and f[5] == byte}

    # -- 用例 -------------------------------------------------------------- #

    def test_scan_success_lights_green(self) -> None:
        self.console._robot.sent_frames.clear()
        self.console.scan(1, 4)
        self.assertEqual(self._led(0x40), {1, 2, 3, 4},
                         "扫描成功应给所有在线舵机亮绿灯（d[5]=0x40）")

    def test_arm_torque_toggles_blue_and_green(self) -> None:
        key = sorted(self.arms)[0]
        ids = set(self.arms[key].servo_ids)
        self.console.arm_torque(key, True)
        self.assertEqual(self._led(0x20), ids, "使能 → 蓝灯（d[5]=0x20）")
        self.console._robot.sent_frames.clear()
        self.console.arm_torque(key, False)
        self.assertEqual(self._led(0x40), ids, "断使能 → 切回绿灯（d[5]=0x40）")

    def test_single_servo_torque_toggles_led(self) -> None:
        self.console.torque(3, True)
        self.assertEqual(self._led(0x20), {3})
        self.console.torque(3, False)
        self.assertEqual(self._led(0x40), {3})

    def test_goto_reasserts_led_after_motion(self) -> None:
        """位置指令会清灯（d[5] 高三位与 LED 颜色位同域）→ 运动后必须补发。"""
        self.console.torque(3, True)                 # 蓝
        self.console._robot.sent_frames.clear()
        self.console.goto(3, adc=600)
        self.assertEqual(self._led(0x20), {3}, "goto 后应补发该舵机的蓝灯")

    def test_arm_goto_reasserts_led_for_moved_joints(self) -> None:
        key = sorted(self.arms)[0]
        arm = self.arms[key]
        self.console.arm_torque(key, True)           # 蓝
        self.console._robot.sent_frames.clear()
        self.console.arm_goto(key, {"shoulder_pan": 15.0})
        moved = {arm.joints["shoulder_pan"].servo_id}
        self.assertEqual(self._led(0x20), moved, "整臂运动只补发被驱动关节的灯")

    def test_follow_cycle_reasserts_follower_led(self) -> None:
        if "leader" not in self.arms or "follower" not in self.arms:
            raise unittest.SkipTest("配置缺少 leader/follower，跳过")
        self.console.arm_torque("follower", True)    # 蓝
        fj = self.arms["follower"].joints["shoulder_pan"]
        with self.console._follow_data_lock:         # 构造一个会让该关节运动的目标
            self.console._follow_target = {fj.servo_id: fj.zero_adc + 30}
            self.console._follow_cur = {fj.servo_id: float(fj.zero_adc)}
            self.console._follow_vel = {fj.servo_id: 0.0}
        self.console._follow_last = {}
        self.console._follow_tuning = (60.0, 20)     # 陡斜坡：首拍就有可见位移
        self.console._robot.sent_frames.clear()
        self.console._follow_tick(self.arms["follower"], 0.02, None)
        self.assertEqual(self._led(0x20), {fj.servo_id},
                         "控制拍下发后应补发该关节的蓝灯")

    def test_stop_all_returns_green(self) -> None:
        self.console.stop_all()
        online = set(self.console.state()["online"])
        self.assertEqual(self._led(0x40), online, "紧急停止（断力矩）→ 回绿灯")

    def test_follow_start_sets_master_green_follower_blue(self) -> None:
        leader, follower = "leader", "follower"
        if leader not in self.arms or follower not in self.arms:
            raise unittest.SkipTest("配置缺少 leader/follower，跳过")
        self.console.follow_start(leader, follower, interval_ms=5000)
        try:
            self.assertEqual(self._led(0x40), set(self.arms[leader].servo_ids),
                             "主动臂断力矩 → 绿灯")
            self.assertEqual(self._led(0x20), set(self.arms[follower].servo_ids),
                             "从动臂使能 → 蓝灯")
        finally:
            self.console.follow_stop()

    def test_disconnect_turns_leds_off(self) -> None:
        online = set(self.console.state()["online"])
        robot = self.console._robot          # disconnect 后 _robot 会被置空
        self.console.disconnect()
        leds = {f[2] for f in robot.sent_frames
                if f[4] == P.ServoCmd.SET_LED and f[5] == 0x00}
        self.assertEqual(leds, online, "断开连接 → 灯灭（d[5]=0x00）")
        self.console.connect()               # 恢复连接给同类的后续用例


class TestArmTorqueAndScanHealth(unittest.TestCase):
    """整臂使能（承重关节 H 档保持）与 3 轮扫描的机械臂健康判定。

    直接调 `WebConsole` 方法（不起 HTTP），用 mock 总线检查下发的帧：
    * ``arm_torque`` 对全关节发 ``0x10``，承重关节额外收到**目标 = 当前位置**
      的 **H 档**位置指令（把保持出力锁到最大）；
    * ``arm_goto`` / ``arm_home`` 对承重关节恒用 H 档，其余关节按调用方档位；
    * ``scan`` 3 轮取并集；任一臂的舵机离线 → ``abnormal: true``。
    """

    @classmethod
    def setUpClass(cls) -> None:
        from romanbo.arm import WEIGHT_BEARING_JOINTS, discover
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skeleton, arms = discover(os.path.join(root, "urdf", "Romanbo.urdf"),
                                  os.path.join(root, "config"))
        if not arms:
            raise unittest.SkipTest("仓库缺少 config/*.json 或 urdf/，跳过")
        cls.weight = WEIGHT_BEARING_JOINTS
        cls.arms = arms
        cls.console = WebConsole(mock=True, arms=arms, skeleton=skeleton)
        cls.console.connect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.console.disconnect()

    def setUp(self) -> None:
        self.console.scan()                      # 恢复在线集合到全在线
        self.console._robot.sent_frames.clear()

    # -- 工具 -------------------------------------------------------------- #

    def _positions(self) -> list[tuple[int, int, int]]:
        """SET_POSITION 帧 → ``(servo_id, adc, 档位)``。"""
        return [(f[2], ((f[5] & 0x07) << 8) | f[6], (f[5] >> 3) & 0x03)
                for f in self.console._robot.sent_frames
                if f[4] == P.ServoCmd.SET_POSITION]

    def _torque_ids(self) -> set[int]:
        return {f[2] for f in self.console._robot.sent_frames
                if f[4] == P.ServoCmd.SET_TORQUE}

    # -- 用例 -------------------------------------------------------------- #

    def test_arm_torque_enables_all_and_high_for_weight_bearing(self) -> None:
        key = sorted(self.arms)[0]
        arm = self.arms[key]
        robot = self.console._robot
        # mock 位置可能被同类的早先用例移动过：断言「目标 = 调用前的当前位置」
        before = {sid: robot.servo(sid).get_position() for sid in arm.servo_ids}
        result = self.console.arm_torque(key, True)
        self.assertEqual(self._torque_ids(), set(arm.servo_ids))
        self.assertEqual(set(result["enabled"]), set(arm.joints))
        expected_high = {n for n in arm.joints if n in self.weight}
        self.assertEqual(set(result["high"]), expected_high)
        positions = self._positions()
        for name in expected_high:
            sid = arm.joints[name].servo_id
            hits = [p for p in positions if p[0] == sid]
            self.assertTrue(hits, f"{name} 未收到 H 档保持指令")
            self.assertEqual(hits[0][1], before[sid], "目标应为当前位置（不动）")
            self.assertEqual(hits[0][2], P.LEVEL_HIGH)

    def test_arm_torque_off_only_disables(self) -> None:
        key = sorted(self.arms)[0]
        arm = self.arms[key]
        result = self.console.arm_torque(key, False)
        self.assertEqual(self._torque_ids(), set(arm.servo_ids))
        self.assertEqual(self._positions(), [], "断开力矩不应附带位置指令")
        self.assertEqual(result["high"], [])

    def test_arm_goto_keeps_high_level_for_weight_bearing(self) -> None:
        key = sorted(self.arms)[0]
        arm = self.arms[key]
        # 显式要求 L 档：承重关节仍必须是 H，其余关节按 L
        self.console.arm_goto(key, {"shoulder_lift": 5.0, "wrist_roll": 5.0},
                              level=P.LEVEL_LOW)
        levels = {sid: lv for sid, _adc, lv in self._positions()}
        self.assertEqual(levels[arm.joints["shoulder_lift"].servo_id],
                         P.LEVEL_HIGH)
        self.assertEqual(levels[arm.joints["wrist_roll"].servo_id], P.LEVEL_LOW)

    def test_scan_union_of_three_rounds_and_arm_abnormal(self) -> None:
        def wait_rounds(n):
            deadline = time.time() + 8
            while self.console.state()["scan_rounds"] < n and time.time() < deadline:
                time.sleep(0.01)
            return self.console.state()

        result = self.console.scan()
        self.assertEqual(result["rounds"], 1)
        self.assertTrue(result["arms"])
        state = wait_rounds(3)
        self.assertEqual(state["scan_rounds"], 3)
        for key, health in state["arms"].items():
            self.assertFalse(health["abnormal"], f"{key} 全在线不应异常")
            self.assertEqual(health["missing"], [])
        # 只有 ID 1、2 应答 → 每条臂都有离线关节 → 全部判异常（兜底后仍如此）
        self.console.scan(1, 2)
        state = wait_rounds(3)
        for key, health in state["arms"].items():
            self.assertTrue(health["abnormal"], f"{key} 应判异常")
        follower = state["arms"].get("follower")
        self.assertIsNotNone(follower)
        self.assertIn(arm_id := self.arms["follower"].joints["shoulder_lift"].servo_id,
                      follower["missing"], f"shoulder_lift(ID {arm_id}) 应在离线表")


class TestFollowMode(unittest.TestCase):
    """主从模式：主动臂断力矩、从动臂使能，实时读主动臂关节角控制从动臂。"""

    @classmethod
    def setUpClass(cls) -> None:
        from romanbo.arm import WEIGHT_BEARING_JOINTS, discover
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        skeleton, arms = discover(os.path.join(root, "urdf", "Romanbo.urdf"),
                                  os.path.join(root, "config"))
        if not {"leader", "follower"} <= set(arms):
            raise unittest.SkipTest("仓库缺少 leader/follower 臂配置，跳过")
        cls.weight = WEIGHT_BEARING_JOINTS
        cls.arms = arms
        cls.console = WebConsole(mock=True, arms=arms, skeleton=skeleton)
        cls.console.connect()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.console.follow_stop()
        cls.console.disconnect()

    def setUp(self) -> None:
        self.console.follow_stop()
        self.leader = self.arms["leader"]
        self.follower = self.arms["follower"]
        # 隔离上一条用例留下的跟随状态（目标/插值/速度/已发值/调参）
        with self.console._follow_data_lock:
            self.console._follow_target = {}
            self.console._follow_cur = {}
            self.console._follow_vel = {}
        self.console._follow_last = {}
        self.console._follow_tuning = (60.0, 20)     # 测试默认：陡斜坡，首拍可见位移

    # -- 工具 --------------------------------------------------------------

    def _settled(self) -> bool:
        """所有期望目标都已精确贴齐且速度归零（梯形规划收敛）。"""
        c = self.console
        with c._follow_data_lock:
            for sid, target in c._follow_target.items():
                cur = c._follow_cur.get(sid)
                if (cur is None or abs(target - cur) > c.FOLLOW_DEADBAND_ADC
                        or c._follow_vel.get(sid, 0.0) != 0.0):
                    return False
        return True

    def _tick_until_settled(self, dt: float = 0.02, limit: int = 5000) -> None:
        """确定性驱动控制拍的纯计算部分直到收敛（不起真线程）。"""
        levels = self.follower.default_levels() or None
        for _ in range(limit):
            self.console._follow_tick(self.follower, dt, levels)
            if self._settled():
                return
        self.fail("控制拍在限次内未收敛到期望目标")

    def _mock(self):
        return self.console._robot.transport._inner

    def _positions(self) -> list:
        """SET_POSITION 帧 → ``(servo_id, adc, 档位)``。"""
        return [(f[2], ((f[5] & 0x07) << 8) | f[6], (f[5] >> 3) & 0x03)
                for f in self.console._robot.sent_frames
                if f[4] == P.ServoCmd.SET_POSITION]

    # -- 用例 -------------------------------------------------------------- #

    def test_same_arm_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.console.follow_start("leader", "leader")

    def test_follow_maps_master_angles_via_smooth_control(self) -> None:
        """采样拍只更新目标（不下发）→ 控制拍平滑收敛到映射目标，承重 H 档。"""
        mock = self._mock()
        for i, j in enumerate(self.leader.joints.values()):
            mock.positions[j.servo_id] = j.zero_adc + (i + 1) * 20
        self.console._robot.sent_frames.clear()
        self.console._follow_once(self.leader, self.follower)
        self.assertFalse(self._positions(),
                         "采样拍不应直接下发位置（由控制拍插值下发）")
        self._tick_until_settled()
        sent = {sid: (adc, lvl) for sid, adc, lvl in self._positions()}
        self.assertEqual(set(sent), set(self.follower.servo_ids))
        for name, mj in self.leader.joints.items():
            fj = self.follower.joints[name]
            adc, lvl = sent[fj.servo_id]
            expected = fj.deg_to_adc(fj.clamp(mj.adc_to_deg(mock.positions[mj.servo_id])))
            self.assertEqual(adc, expected, f"{name} 收敛后应到达 URDF 角映射目标")
            if name in self.weight:
                self.assertEqual(lvl, P.LEVEL_HIGH, f"{name} 承重关节应 H 档")
        # 收敛后：再跑控制拍不重发任何帧（死区 + 值没变 → 停发）
        before = len(self.console._robot.sent_frames)
        for _ in range(3):
            self.console._follow_tick(self.follower, 0.02, None)
        self.assertEqual(len(self.console._robot.sent_frames), before,
                         "已收敛的关节不应继续重发")

        # 只动主动臂一个关节 → 只有对应从动关节重新开始插值下发
        name0 = next(iter(self.leader.joints))
        mj0, fj0 = self.leader.joints[name0], self.follower.joints[name0]
        mock.positions[mj0.servo_id] += 60
        self.console._follow_once(self.leader, self.follower)
        before = len(self._positions())
        for _ in range(3):
            self.console._follow_tick(self.follower, 0.02, None)
        delta = self._positions()[before:]
        self.assertEqual({p[0] for p in delta}, {fj0.servo_id},
                         "只有目标变化了的关节才应重新下发")

    def test_follow_tick_limits_speed_and_ramps_up(self) -> None:
        """限速 + 平滑起步：单拍位移不超限，起步为匀加速斜坡（第2拍≈2×第1拍）。"""
        mock = self._mock()
        name0 = next(iter(self.leader.joints))
        mj0, fj0 = self.leader.joints[name0], self.follower.joints[name0]
        mock.positions[mj0.servo_id] = mj0.zero_adc + 300   # 大位移（远超死区）
        with self.console._follow_data_lock:
            self.console._follow_cur = {fj0.servo_id: float(fj0.zero_adc)}
            self.console._follow_vel = {}
        self.console._follow_once(self.leader, self.follower)
        self.console._follow_tuning = (60.0, 150)           # 60 °/s，150 ms 斜坡
        dt = 0.02
        vmax = 60.0 / J.RATIO_MAIN                          # °/s → ADC/s
        accel = vmax / 0.15
        curs = []
        for _ in range(3):
            self.console._follow_tick(self.follower, dt, None)
            with self.console._follow_data_lock:
                curs.append(self.console._follow_cur[fj0.servo_id])
        d1, d2 = abs(curs[0] - fj0.zero_adc), abs(curs[1] - curs[0])
        self.assertLessEqual(d1, vmax * dt + 1e-6, "单拍位移不得超过限速")
        self.assertAlmostEqual(d2, 2.0 * d1, delta=1e-6,
                               msg="起步应为匀加速斜坡（第 2 拍位移 ≈ 2 × 第 1 拍）")
        # 起步斜坡期间速度远低于限速（平滑插值，不是全速扑向目标）
        self.assertLess(d2 / dt, vmax * 0.5,
                        "150 ms 斜坡内速度不应过早达到限速上限")

    def test_follow_start_seeds_cur_from_capture(self) -> None:
        """起步插值锚点 = 从动臂当前实际位置（capture），而不是主动臂姿态。"""
        mock = self._mock()
        fj = self.follower.joints["shoulder_pan"]
        mock.positions[fj.servo_id] = fj.zero_adc + 40
        try:
            self.console.follow_start("leader", "follower", interval_ms=5000)
            with self.console._follow_data_lock:
                cur = dict(self.console._follow_cur)
            self.assertIsNotNone(cur.get(fj.servo_id))
            self.assertLessEqual(abs(cur[fj.servo_id] - mock.positions[fj.servo_id]),
                                 3.0, "起步锚点应为从动臂当前实际位置（±控制拍位移）")
        finally:
            self.console.follow_stop()

    def test_follow_start_records_tuning(self) -> None:
        r = self.console.follow_start("leader", "follower", sample_ms=250,
                                      control_ms=25, max_dps=45, ramp_ms=200)
        try:
            self.assertEqual((r["sample_ms"], r["control_ms"]), (250, 25))
            self.assertEqual((r["max_dps"], r["ramp_ms"]), (45.0, 200))
            st = self.console.state()["follow"]
            self.assertEqual(st["sample_ms"], 250)
        finally:
            self.console.follow_stop()

    def test_follow_legacy_interval_ms_alias(self) -> None:
        r = self.console.follow_start("leader", "follower", interval_ms=5000)
        try:
            self.assertEqual(r["sample_ms"], 5000, "旧参数 interval_ms 应映射为采样周期")
        finally:
            self.console.follow_stop()

    def test_start_stop_lifecycle_and_torque_frames(self) -> None:
        result = self.console.follow_start("leader", "follower", interval_ms=5000)
        self.assertTrue(result["active"])
        self.assertEqual((result["master"], result["follower"]), ("leader", "follower"))
        deadline = time.time() + 2
        off = on = set()
        while time.time() < deadline:
            frames = self.console._robot.sent_frames
            off = {f[2] for f in frames
                   if f[4] == P.ServoCmd.SET_TORQUE and f[5] == 0}
            on = {f[2] for f in frames
                  if f[4] == P.ServoCmd.SET_TORQUE and f[5] == 1}
            if (set(self.leader.servo_ids) <= off
                    and set(self.follower.servo_ids) <= on):
                break
            time.sleep(0.02)
        self.assertLessEqual(set(self.leader.servo_ids), off, "主动臂应全部断力矩")
        self.assertLessEqual(set(self.follower.servo_ids), on, "从动臂应全部使能")
        self.assertTrue(self.console.state()["follow"]["active"])
        stopped = self.console.follow_stop()
        self.assertFalse(stopped["active"])
        self.assertFalse(self.console.state()["follow"]["active"])

    def test_stop_all_also_stops_follow(self) -> None:
        self.console.follow_start("leader", "follower", interval_ms=5000)
        self.console.stop_all()
        self.assertFalse(self.console.state()["follow"]["active"],
                         "紧急停止应一并停掉主从跟随")

    def test_scan_busy_yields_and_real_outage_fails(self) -> None:
        """整拍无应答：扫描占用总线时让路（不算失败），真失联才抛错计失败。"""
        mock = self._mock()
        mock.auto_ack = False          # 所有应答消失 → 所有读超时
        self.console._follow_read = {j.servo_id: 512
                                     for j in self.leader.joints.values()}
        try:
            self.console._scan_busy = True
            self.assertFalse(self.console._follow_once(self.leader, self.follower),
                             "扫描期间应整拍让路（返回 False，不抛错）")
            self.console._scan_busy = False
            with self.assertRaises(RuntimeError):
                self.console._follow_once(self.leader, self.follower)
        finally:
            self.console._scan_busy = False
            mock.auto_ack = True


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


class TestPortSelectorFiltering(unittest.TestCase):
    """页面下拉框要**过滤**没接硬件的占位口，并如实报告隐藏了几个。

    真机实测（2026-09-27）：不筛的话下拉框 33 项，得在 32 个 ``ttyS*`` 里找 FT230X。
    过滤放在前端做（``/api/ports`` 仍返回全部，数据不丢）。
    """

    def test_dropdown_filters_hardwareless_ports(self) -> None:
        from romanbo.webui_page import PAGE

        self.assertIn('r.description !== "n/a"', PAGE, "缺了过滤条件")
        self.assertIn("已隐藏", PAGE, "隐藏了几个要如实说")
        self.assertIn("没有接硬件的占位口", PAGE)


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

    def test_api_401_is_explained_once(self) -> None:
        """服务重启后旧页面的令牌全部 401（每个按钮都报晦涩错误）。

        页面必须：识别 401 → 给出「用新链接打开」指引 → ``tokenDead`` 防重。
        """
        self.assertIn("res.status === 401", PAGE, "api() 没有单独处理 401")
        self.assertIn("tokenDead", PAGE, "缺了只提示一次的开关")

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
        """后台跑一次约 2.6 s 的运动（512→900 @60°/s，默认 20 ms 节拍）。"""
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


class TestConsolePortsOrder(unittest.TestCase):
    """控制台的端口列表要把**已识别的设备排前面**（下拉框里同理）。

    真机实测（2026-09-27）：这台机器枚举出 32 个 ``ttyS*`` 占位口，FT230X 排在第 35 位；
    GUI 这边**不隐藏**（下拉框得能选到任何端口），但顺序要让真适配器在最前面。
    """

    def test_identified_devices_come_first(self) -> None:
        from unittest import mock as _mock

        rows = [{"device": f"/dev/ttyS{i}", "description": "n/a", "hwid": "", "busy": True}
                for i in range(32)]
        rows.append({"device": "/dev/ttyUSB0", "description": "FT230X Basic UART",
                     "hwid": "", "busy": False})
        with _mock.patch("romanbo.webui.list_serial_ports", return_value=rows):
            ordered = WebConsole.ports()
        self.assertEqual(ordered[0]["device"], "/dev/ttyUSB0")
        self.assertEqual(len(ordered), 33, "GUI 只排序、不隐藏")


class TestLostPortReporting(unittest.TestCase):
    """串口被拔掉 / 重新枚举后，控制台不能继续自报「已连接」。

    实测 2026-09-27：适配器重枚举成 ``ttyUSB1`` 后，旧控制台仍显示
    ``connected: true, online: [8, 10]``（攥着已删除的 ``ttyUSB0`` 句柄），直到发命令
    才报 ``Input/output error``——这一段时间里用户会以为机器人还能控。断线判据只看
    "设备消失"类 errno：普通读超时、校验和不符都不算断线。
    """

    @staticmethod
    def _device_path() -> str:
        """给 ``SerialTransport`` 一个**真实存在**的路径。

        新逻辑（`_ensure_alive`）会检查设备节点还在不在，所以不能再用
        ``"/dev/ttyUSB1"`` 这种测试机上不存在的名字。这里给一个指向 ``/dev/null`` 的
        临时符号链接——只让 ``os.path.exists`` 为真，句柄本身由测试注入的假对象顶替，
        不碰真串口。Windows 分支不做路径检查，随便给个端口名即可（也不去创建符号链接，
        那需要额外权限）。
        """
        import os
        import tempfile

        if sys.platform.startswith("win"):
            return "COM1"
        link = os.path.join(tempfile.mkdtemp(), "fake-tty")
        os.symlink("/dev/null", link)
        return link

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

        port = SerialTransport(self._device_path())
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

        port = SerialTransport(self._device_path())
        port._ser = _Quiet()
        self.assertEqual(port.read_available(0.02), b"")
        self.assertTrue(port.is_open, "没数据 ≠ 断线")
        self.assertIsNone(port.lost_reason)

    @unittest.skipIf(sys.platform.startswith("win"), "节点路径检查是 POSIX 专有")
    def test_vanished_device_node_is_detected_even_when_io_stays_silent(self) -> None:
        """拔掉后读写可能只**静默超时**，所以还得看设备节点是否还在。

        真机日志（2026-09-27）：拔掉适配器后只得到 ``TimeoutError: 等待回包超时``，
        ``is_open`` 仍为真、``lost_reason`` 为空——控制台会一直以为还连着。光靠 errno
        抓不住这种情况，设备节点消失才是确定信号。
        """
        import os
        import tempfile

        from romanbo.transport import SerialTransport

        link = os.path.join(tempfile.mkdtemp(), "fake-tty")
        os.symlink("/dev/null", link)

        class _Silent:
            """读写都不报错、但永远没数据的句柄（模拟"拔掉后静默超时"）。"""
            is_open = True

            @property
            def in_waiting(self):
                return 0

            @staticmethod
            def close():
                pass

        port = SerialTransport(link)
        port._ser = _Silent()
        self.assertEqual(port.read_available(0.02), b"")     # 静默：不报错、也没数据
        self.assertTrue(port.is_open)

        os.remove(link)                                     # 节点消失 == 拔掉
        with self.assertRaises(OSError):
            port.read_available(0.02)
        self.assertFalse(port.is_open, "节点都没了就不能再报已连接")
        self.assertIn("设备节点已不存在", port.lost_reason or "")

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
