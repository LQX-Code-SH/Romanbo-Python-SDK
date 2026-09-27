"""``0x09`` 出力档位字段（H/M/L/W）的测试。

背景（2026-09-25 真机实测，ID 8，同一段约 40 ADC 位移，只改 ``d[5]`` 的 bit3/bit4）：
档位 0 = H → 峰值负荷 178；1 = M → 110；2 = L → 38；3 = W → **位置指令不生效**。
档位 0 也照样能运动 ⇒ 该字段**不是使能开关**，使能只有 ``0x10``。
"""

from __future__ import annotations

import unittest

from romanbo import protocol as P
from romanbo.robot import RomanboRobot
from romanbo.transport import MockTransport

#: 真机实测帧（2026-09-25 实验的原始 TX；目标值由帧反解，回读值略差 1~3 ADC）
REAL_FRAMES = [
    (P.LEVEL_HIGH, 971, "FF FF 08 08 09 03 CB 1B"),
    (P.LEVEL_MIDDLE, 931, "FF FF 08 08 09 0B A3 3B"),
    (P.LEVEL_LOW, 890, "FF FF 08 08 09 13 7A 5C"),
    (P.LEVEL_WHEEL, 853, "FF FF 08 08 09 1B 55 79"),
]


class TestLevelFrames(unittest.TestCase):
    def test_level_constants(self) -> None:
        self.assertEqual(
            (P.LEVEL_HIGH, P.LEVEL_MIDDLE, P.LEVEL_LOW, P.LEVEL_WHEEL),
            (0, 1, 2, 3))

    def test_real_hardware_frames(self) -> None:
        for level, position, expected in REAL_FRAMES:
            with self.subTest(level=level):
                self.assertEqual(
                    P.build_set_position(8, position, level=level).hex(" ").upper(),
                    expected)

    def test_level_and_torque_are_the_same_argument(self) -> None:
        for level in (0, 1, 2, 3):
            self.assertEqual(P.build_set_position(3, 500, torque=level),
                             P.build_set_position(3, 500, level=level))

    def test_default_is_middle_level(self) -> None:
        # 默认沿用旧行为（torque=ON=1 → M 档），保证既有黄金向量不变
        self.assertEqual(P.build_set_position(1, 512),
                         P.build_set_position(1, 512, level=P.LEVEL_MIDDLE))
        self.assertEqual(P.build_set_position(1, 512).hex(" ").upper(),
                         "FF FF 01 08 09 0A 00 E6")

    def test_low_level_sets_bit4(self) -> None:
        # L 档把 d[5] 的 bit4 置位（= 16 位值里的 bit12 = 档位高位）
        frame = P.build_set_position(8, 890, level=P.LEVEL_LOW)
        self.assertEqual(frame[5] & 0x18, 0x10)          # bit4=1, bit3=0
        high = P.build_set_position(8, 890, level=P.LEVEL_HIGH)
        self.assertEqual(high[5] & 0x18, 0x00)
        middle = P.build_set_position(8, 890, level=P.LEVEL_MIDDLE)
        self.assertEqual(middle[5] & 0x18, 0x08)         # bit3=1, bit4=0

    def test_position_range_is_checked(self) -> None:
        # 位置只占 10 位（d[5] 的 bit0/bit1 + d[6]）；d[5] 的 bit2 是 relative、
        # bit3/bit4 是出力档位。因此 1024 及以上会**撞上 relative 位**——
        # 1500 被固件当成相对运动、2047 变成"相对 +1023"，上限必须是 1023。
        for bad in (-1, 1024, 1500, 2047, 3000):
            with self.assertRaises(ValueError):
                P.build_set_position(1, bad)
        for good in (0, 512, 1023):
            with self.subTest(position=good):
                frame = P.build_set_position(1, good, level=P.LEVEL_MIDDLE)
                self.assertEqual(frame[5] & 0x04, 0x00, "relative 位被污染")
                self.assertEqual(frame[5] & 0x03, (good >> 8) & 0x03)


class TestLevelPlumbing(unittest.TestCase):
    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1, 2])
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()
        self.mock.positions[1] = 512
        self.mock.positions[2] = 512

    def tearDown(self) -> None:
        self.robot.close()

    def _levels(self) -> list[int]:
        return [(f[5] >> 3) & 0x03 for f in self.robot.sent_frames
                if f[4] == P.ServoCmd.SET_POSITION]

    def test_servo_set_position_level(self) -> None:
        self.robot.servo(1).set_position(600, level=P.LEVEL_HIGH, torque=1)
        self.assertEqual(self._levels(), [P.LEVEL_HIGH])

    def test_move_and_stepwise_pass_level(self) -> None:
        self.robot.move({1: 614}, speed_dps=60, start={1: 512}, level=P.LEVEL_LOW,
                        step_interval_ms=100, sleep=lambda _s: None)
        self.assertTrue(self._levels())
        self.assertEqual(set(self._levels()), {P.LEVEL_LOW})


if __name__ == "__main__":
    unittest.main()
