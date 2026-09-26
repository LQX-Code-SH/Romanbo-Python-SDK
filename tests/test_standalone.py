"""独立可用性与并发：离线功能不依赖 pyserial；多线程收发不重叠。"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo.robot import RomanboRobot  # noqa: E402
from romanbo.transport import MockTransport  # noqa: E402

#: 屏蔽 pyserial 后仍应可用的最小用法
NO_SERIAL_SCRIPT = r"""
import importlib.abc
import sys


class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "serial" or fullname.startswith("serial."):
            raise ImportError("serial 被测试屏蔽")
        return None


sys.meta_path.insert(0, _Blocker())
import romanbo

text = romanbo.rsc.dumps_project([{"adc": [512] * 17, "period": 500}])
project = romanbo.RscProject.loads(text)
assert len(list(project.frames())) == 1
assert romanbo.protocol.build_status(1)
try:
    romanbo.RomanboRobot("COM_DOES_NOT_EXIST").open()
except RuntimeError:
    print("OFFLINE_OK", romanbo.__version__)
else:
    raise AssertionError("缺少 pyserial 时应抛出 RuntimeError")
"""


class TestWorksWithoutPyserial(unittest.TestCase):
    """`import romanbo` 与全部离线功能都不应依赖 pyserial。"""

    def test_offline_features_need_no_pyserial(self) -> None:
        result = subprocess.run([sys.executable, "-c", NO_SERIAL_SCRIPT],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OFFLINE_OK", result.stdout)


class TestCrossPlatform(unittest.TestCase):
    """Linux/macOS 差异点：端口命名、权限提示、POSIX 排他打开。"""

    def test_port_hint_is_platform_aware(self) -> None:
        from unittest import mock

        from romanbo import cli

        with mock.patch.object(sys, "platform", "linux"):
            text = cli._port_hint("/dev/ttyUSB0", PermissionError("拒绝访问"))
            self.assertIn("dialout", text)
            self.assertIn("/dev/ttyUSB0", text)
            self.assertIn("lsof", text)
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertIn("lsof", cli._port_hint("/dev/tty.usbserial-1", OSError("x")))
        with mock.patch.object(sys, "platform", "win32"):
            self.assertIn("USB", cli._port_hint("COM3", PermissionError("x")))

    def test_serial_transport_is_exclusive_and_keeps_posix_name(self) -> None:
        from unittest import mock

        from romanbo.transport import SerialTransport

        captured: dict = {}

        class _FakePort:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def __getattr__(self, name):                     # dtr/rts/flush…
                return lambda *args, **kwargs: None

        class _FakeSerial:
            EIGHTBITS, PARITY_NONE, STOPBITS_ONE = 8, "N", 1
            Serial = _FakePort

        with mock.patch.dict(sys.modules, {"serial": _FakeSerial()}):
            transport = SerialTransport("/dev/ttyUSB0", 115200)
            transport.open()
            # Linux 端口名原样传递，绝不能被改写成 COMx
            self.assertEqual(transport.name, "/dev/ttyUSB0")
            self.assertEqual(captured["port"], "/dev/ttyUSB0")
            self.assertEqual(captured["baudrate"], 115200)
            # POSIX 排他打开（Windows 下 pyserial 会忽略该参数）
            self.assertTrue(captured["exclusive"])
            transport.close()


class TestIoLock(unittest.TestCase):
    """同一实例被多线程调用时，收发必须串行（不重叠）。"""

    def test_no_overlapping_io_between_threads(self) -> None:
        class SpyTransport(MockTransport):
            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self._guard = threading.Lock()
                self._in_flight = 0
                self.overlap = False

            def _enter(self) -> None:
                with self._guard:
                    self._in_flight += 1
                    if self._in_flight > 1:
                        self.overlap = True

            def _leave(self) -> None:
                with self._guard:
                    self._in_flight -= 1

            def write(self, frame: bytes) -> None:
                self._enter()
                try:
                    time.sleep(0.001)
                    super().write(frame)
                finally:
                    self._leave()

            def read_available(self, timeout: float) -> bytes:
                self._enter()
                try:
                    time.sleep(0.001)
                    return super().read_available(timeout)
                finally:
                    self._leave()

        transport = SpyTransport(servo_ids=[1, 2, 3])
        robot = RomanboRobot(transport=transport, ack_timeout=0.2).open()
        try:
            errors = []

            def worker(id_: int) -> None:
                try:
                    for _ in range(15):
                        robot.servo(id_).get_position()
                except Exception as exc:                     # noqa: BLE001
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in (1, 2, 3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertFalse(transport.overlap,
                             "多线程下出现收发重叠 —— 锁没生效")
        finally:
            robot.close()


class TestFrameGap(unittest.TestCase):
    """L7-01 / L7-02：连发两帧之间必须留有 ``MIN_FRAME_GAP`` 的间隔。

    实测（2026-09-26，ID 8/10）：两帧间隔 0 ms 时**后发的那一帧会被舵机静默
    丢弃**（交换 ID 顺序后被丢的永远是第二帧），间隔 >= 2 ms 时两帧都正常。
    多关节下发靠 :meth:`SerialTransport.write` 在写口兜底，因此这里用假串口
    验证节流真的生效、且没有把间隔设得过大。

    !!! note
        判定**不读真实秒表**。Windows 上 ``time.monotonic()`` 在 CPython <= 3.12
        走 ``GetTickCount64()``（系统计时器增量，典型约 15.6 ms），拿它量 2 ms 间隔
        只会读到 0.0 或 15.6——本测试就曾在 windows-latest / Python 3.11 上因此失败。
        故这里注入可控时钟，直接断言"节流决策"，任何平台都确定性成立；另留一个
        真实时钟的冒烟用例（只校验宽松下界），防止"注入时钟过了但节流没接上"。
    """

    @staticmethod
    def _install_fake_serial(stamps):
        from unittest import mock

        class _FakePort:
            def __init__(self, **kwargs) -> None:
                pass

            def write(self, frame) -> None:
                # 打点必须用高精度时钟：Windows 的 monotonic() 量不出 2 ms
                stamps.append(time.perf_counter())

            def __getattr__(self, name):                     # dtr/rts/flush…
                return lambda *args, **kwargs: None

        class _FakeSerial:
            EIGHTBITS, PARITY_NONE, STOPBITS_ONE = 8, "N", 1
            Serial = _FakePort

        return mock.patch.dict(sys.modules, {"serial": _FakeSerial()})

    @staticmethod
    @contextlib.contextmanager
    def _fake_clock(start: float = 1000.0):
        """注入可控时钟：``perf_counter()`` 手动推进，``sleep()`` 只记录不真睡。"""
        from unittest import mock

        state = {"now": start, "sleeps": []}

        def perf_counter() -> float:
            return state["now"]

        def sleep(seconds: float) -> None:
            state["sleeps"].append(seconds)
            state["now"] += seconds          # 睡多久时钟就走多久

        with mock.patch.multiple(time, perf_counter=perf_counter, sleep=sleep):
            yield state

    def test_first_write_is_not_delayed(self) -> None:
        from romanbo.transport import SerialTransport

        stamps: list[float] = []
        with self._install_fake_serial(stamps), self._fake_clock() as clock:
            transport = SerialTransport("/dev/ttyUSB0", 115200)
            transport.open()
            transport.write(b"\xFF\xFF\x01\x06\x05\xF6")
            transport.close()
        # 打开端口后的第一帧不应凭空等待
        self.assertEqual(len(stamps), 1)
        self.assertEqual(clock["sleeps"], [])

    def test_second_write_waits_the_remaining_gap(self) -> None:
        from romanbo import protocol as P
        from romanbo.transport import SerialTransport

        stamps: list[float] = []
        with self._install_fake_serial(stamps), self._fake_clock() as clock:
            transport = SerialTransport("/dev/ttyUSB0", 115200)
            transport.open()
            try:
                transport.write(b"\xFF\xFF\x01\x06\x05\xF6")     # t = 1000.0
                clock["now"] += 0.0005                            # 只过了 0.5 ms
                transport.write(b"\xFF\xFF\x01\x06\x05\xF6")
            finally:
                transport.close()

        self.assertEqual(len(stamps), 2)
        self.assertEqual(len(clock["sleeps"]), 1, "第二帧没有等到最小间隔")
        self.assertAlmostEqual(clock["sleeps"][0], P.MIN_FRAME_GAP - 0.0005,
                               places=6)

    def test_no_wait_when_the_gap_is_already_elapsed(self) -> None:
        from romanbo.transport import SerialTransport

        stamps: list[float] = []
        with self._install_fake_serial(stamps), self._fake_clock() as clock:
            transport = SerialTransport("/dev/ttyUSB0", 115200)
            transport.open()
            try:
                transport.write(b"\xFF\xFF\x01\x06\x05\xF6")     # t = 1000.0
                clock["now"] += 0.005                             # 已过 5 ms
                transport.write(b"\xFF\xFF\x01\x06\x05\xF6")
            finally:
                transport.close()

        self.assertEqual(len(stamps), 2)
        self.assertEqual(clock["sleeps"], [], "间隔已够时不应再等待")

    def test_real_clock_gap_is_enforced(self) -> None:
        """真实时钟冒烟：只校验宽松窗口，避开各平台时钟精度与调度抖动。"""
        from romanbo import protocol as P
        from romanbo.transport import SerialTransport

        stamps: list[float] = []
        with self._install_fake_serial(stamps):
            transport = SerialTransport("/dev/ttyUSB0", 115200)
            transport.open()
            try:
                for _ in range(3):
                    transport.write(b"\xFF\xFF\x01\x06\x05\xF6")
            finally:
                transport.close()

        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(gaps), 2)
        for gap in gaps:
            self.assertGreaterEqual(gap, P.MIN_FRAME_GAP * 0.5,
                                    f"帧间隔 {gap * 1000:.2f} ms 过小")
            self.assertLess(gap, 0.05, f"帧间隔 {gap * 1000:.2f} ms 过大")

    def test_gap_constant_matches_measurement(self) -> None:
        from romanbo import protocol as P
        # 实测分界：0 ms 丢帧、2 ms 正常 → 常量取 2 ms
        self.assertEqual(P.MIN_FRAME_GAP, 0.002)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
