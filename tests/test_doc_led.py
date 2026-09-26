"""协议文档《ROMANBO RS485 舵机通信控制协议》示例与本实现的交叉核对。

协议文档给出了 8 条 LED 命令（ID=7）与若干指令示例，这里逐条交叉校验；
顺带把其中 2 处校验和笔误记录下来（实现按算法取值，不照抄文档）。
"""

from __future__ import annotations

import unittest

from romanbo import protocol as P
from romanbo.robot import RomanboRobot
from romanbo.transport import MockTransport

#: 文档「LED控制」表的 8 行：(d[5] 颜色值, 文档给出的完整命令, 颜色名)
DOC_LED = [
    (0x00, "FF FF 07 07 11 00 E3", "全灭"),
    (0x80, "FF FF 07 07 11 80 63", "红"),
    (0x40, "FF FF 07 07 11 40 A3", "绿"),
    (0x20, "FF FF 07 07 11 20 C3", "蓝"),
    (0xE0, "FF FF 07 07 11 E0 03", "白（全亮）"),
    (0xC0, "FF FF 07 07 11 C0 23", "黄（红+绿）"),
    (0xA0, "FF FF 07 07 11 A0 43", "紫（红+蓝）"),
    (0x60, "FF FF 07 07 11 60 83", "青（绿+蓝）"),
]

#: d[5] 值 → (红, 绿, 蓝)
COLOR_BITS = {0x80: (1, 0, 0), 0x40: (0, 1, 0), 0x20: (0, 0, 1),
              0xE0: (1, 1, 1), 0xC0: (1, 1, 0), 0xA0: (1, 0, 1),
              0x60: (0, 1, 1), 0x00: (0, 0, 0)}


class TestOfficialDocLed(unittest.TestCase):
    """LED：文档的 8 条命令 = 我们两种 API 的产物。"""

    def test_raw_frame_matches_doc(self) -> None:
        for value, expected, name in DOC_LED:
            with self.subTest(led=name):
                frame = P.make_frame(P.ServoCmd.SET_LED, 7, bytes([value]))
                self.assertEqual(frame.hex(" ").upper(), expected)

    def test_value_overload_matches_doc(self) -> None:
        # set_led(value) 内部左移 5 位 → value=1/2/4 分别落在 0x20/0x40/0x80
        for value, expected, name in DOC_LED:
            with self.subTest(led=name):
                self.assertEqual(
                    P.build_set_led(7, value >> 5).hex(" ").upper(), expected)

    def test_color_overload_matches_doc(self) -> None:
        # set_led_color(r, g, b) → R=bit7(0x80)、G=bit6(0x40)、B=bit5(0x20)
        for value, expected, name in DOC_LED:
            red, green, blue = COLOR_BITS[value]
            with self.subTest(led=name):
                self.assertEqual(
                    P.build_set_led_color(7, bool(red), bool(green), bool(blue))
                    .hex(" ").upper(),
                    expected)

    def test_color_overload_numbers_in_blue_green_red_order(self) -> None:
        # 用 4 参重载的编号时：1=蓝、2=绿、4=红（与 6 参重载的位序一致但编号不同）
        self.assertEqual(P.build_set_led(7, 1), P.build_set_led_color(7, False, False, True))
        self.assertEqual(P.build_set_led(7, 2), P.build_set_led_color(7, False, True, False))
        self.assertEqual(P.build_set_led(7, 4), P.build_set_led_color(7, True, False, False))
        self.assertEqual(P.build_set_led(7, 7), P.build_set_led_color(7, True, True, True))


class TestOfficialDocOtherExamples(unittest.TestCase):
    """协议文档其它示例（ID=7）交叉校验。"""

    def test_doc_request_examples(self) -> None:
        cases = [
            ("搜索舵机", P.build_status(7), "FF FF 07 06 05 F0"),
            ("设置 ID 7→1", P.build_set_id(7, 1), "FF FF 07 07 06 01 ED"),
            # 文档此处印作 FF FF 07 06 14 E7（沿用了 ID=1 那条的校验和），
            # 按算法正解为 E1，见 test_doc_checksum_typos
            ("读取角度", P.build_get_position(7), "FF FF 07 06 14 E1"),
            ("关扭矩", P.build_set_torque(7, 0), "FF FF 07 07 10 00 E4"),
            ("读零点偏差", P.build_get_calibration(7), "FF FF 07 06 15 E0"),
            ("写零点偏差 0x8A", P.build_set_offset(7, 0x8A), "FF FF 07 07 0A 8A 60"),
            ("置角度 0°（ID 1）", P.build_set_position(1, 0x01FF, torque=0),
             "FF FF 01 08 09 01 FF F0"),
        ]
        for label, frame, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(frame.hex(" ").upper(), expected)

    def test_doc_checksum_typos(self) -> None:
        """文档里有 3 处示例的校验和算错/串位，实现按算法取值。"""
        # ① 文档写 FF FF 07 08 09 03 98 55 → 正解 0x4F
        self.assertEqual(P.build_set_position(7, 0x0398, torque=0).hex(" ").upper(),
                         "FF FF 07 08 09 03 98 4F")
        # ② 文档写 FF FF 07 09 94 00 03 31 30 → 正解 0x2A
        self.assertEqual(
            P.make_frame(0x94, 7, bytes([0x00, 0x03, 0x31])).hex(" ").upper(),
            "FF FF 07 09 94 00 03 31 2A")
        # ③ 文档写 FF FF 07 06 14 E7（ID=1 那条的校验和）→ 正解 0xE1
        self.assertEqual(P.build_get_position(7).hex(" ").upper(),
                         "FF FF 07 06 14 E1")

    def test_doc_reply_frames_parse(self) -> None:
        """文档的回包示例能被我们的解析器正确解析（含 LEN=整帧长度的语义）。"""
        parser = P.FrameParser()
        replies = [
            # (原始帧, id, cmd, 数据段(含状态字节), 状态字节之后的数值)
            ("FF FF 07 07 85 00 6F", 7, 0x85, "00", ""),
            ("FF FF 07 08 95 00 80 DE", 7, 0x95, "00 80", "80"),
            ("FF FF 07 09 94 00 03 31 2A", 7, 0x94, "00 03 31", "03 31"),
        ]
        for text, sid, cmd, data, payload in replies:
            with self.subTest(frame=text):
                raw = bytes(int(b, 16) for b in text.split())
                frames = parser.feed(raw)
                self.assertEqual(len(frames), 1)
                resp = frames[0]
                self.assertEqual((resp.id, resp.cmd), (sid, cmd))
                self.assertEqual(resp.data.hex(" ").upper(), data)
                self.assertEqual(P.payload(resp.data).hex(" ").upper(), payload)
                self.assertEqual(sum(raw) & 0xFF, 0)      # 整帧求和 ≡ 0


class TestCalibrationOffsetBias(unittest.TestCase):
    """文档语义：零点偏差 = 原始字节 − 128。"""

    def setUp(self) -> None:
        self.mock = MockTransport(servo_ids=[1])           # mock 的 calibration = 47
        self.robot = RomanboRobot(transport=self.mock, ack_timeout=0.1).open()

    def tearDown(self) -> None:
        self.robot.close()

    def test_read_offset(self) -> None:
        servo = self.robot.servo(1)
        self.assertEqual(servo.get_calibration(), 47)
        self.assertEqual(servo.get_calibration_offset(), -81)
        self.assertEqual(servo.read_config(timeout=0.1).calibration_offset, -81)

    def test_write_offset_adds_bias(self) -> None:
        self.robot.servo(1).set_calibration_offset(-12)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_offset(1, 116))
        self.robot.servo(1).set_calibration_offset(0)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_offset(1, 128))

    def test_write_offset_is_clamped(self) -> None:
        self.robot.servo(1).set_calibration_offset(-500)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_offset(1, 0))
        self.robot.servo(1).set_calibration_offset(500)
        self.assertEqual(self.robot.sent_frames[-1], P.build_set_offset(1, 255))


if __name__ == "__main__":
    unittest.main()
