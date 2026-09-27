"""协议层测试：黄金向量、校验和、帧解析、边界条件。

运行： ``python -m unittest discover -s tests -v``
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from romanbo import golden, protocol as P  # noqa: E402


class TestGoldenVectors(unittest.TestCase):
    """与协议基准报文逐字节比对。"""

    def test_all_golden_frames(self) -> None:
        passed, failed, details = golden.run()
        self.assertGreater(passed, 40)
        self.assertEqual(failed, 0, "不一致的报文:\n" + "\n".join(details))


class TestChecksum(unittest.TestCase):
    def test_known_frame(self) -> None:
        frame = bytes.fromhex("FF FF 01 08 09 0A 00 E6")
        self.assertEqual(P.checksum(frame), 0xE6)
        self.assertEqual(sum(frame) & 0xFF, 0)

    def test_every_golden_frame_sums_to_zero(self) -> None:
        for name, hex_text, builder in golden.GOLDEN_CHECKS:
            frame = builder()
            with self.subTest(frame=name):
                self.assertEqual(sum(frame) & 0xFF, 0)
                self.assertEqual(frame[:2], b"\xFF\xFF")

    def test_verify_rejects_bad_length_field(self) -> None:
        frame = bytearray(P.build_status(1))
        frame[3] = 0x09          # LEN 与实际长度不符
        frame[-1] = P.checksum(frame)
        self.assertFalse(P.verify(bytes(frame)))

    def test_make_frame_rejects_overflow(self) -> None:
        with self.assertRaises(ValueError):
            P.make_frame(P.ServoCmd.SET_PID, 1, b"\x01\x02\x03", size=8)
        with self.assertRaises(ValueError):
            P.make_frame(P.ServoCmd.STATUS, 1, size=4)

    def test_get_model_version_use_zero_len_field(self) -> None:
        # 兼容旧格式：LEN 字段写 0（设备端接受该旧行为）
        self.assertEqual(P.build_get_model(), bytes.fromhex("FF FF 00 00 01 01"))
        self.assertEqual(P.build_get_version(), bytes.fromhex("FF FF 00 00 02 00"))
        # 校验时长度字段不匹配，因此这种帧必须用 strict=False 解析
        response = P.parse_frame(P.build_get_model(), strict=False)
        self.assertEqual(response.cmd, P.ServoCmd.GET_MODEL)


class TestBitPacking(unittest.TestCase):
    def test_set_position_layout(self) -> None:
        # d[5] = (pos>>8) | (torque<<3) | (relative<<2)
        self.assertEqual(P.build_set_position(1, 512, torque=1, relative=0)[5], 0x0A)
        self.assertEqual(P.build_set_position(1, 512, torque=0, relative=1)[5], 0x06)
        self.assertEqual(P.build_set_position(1, 256, torque=2, relative=0)[5], 0x11)

    def test_set_wheel_layout(self) -> None:
        # d[5] = (torque<<3) + (relative<<2) + (free<<1) + direction
        frame = P.build_set_wheel(1, 100, torque=1, relative=0, free=0, direction=0)
        self.assertEqual(frame[5], 0x08)
        self.assertEqual(frame[6], 100)
        frame = P.build_set_wheel(1, 50, torque=0, relative=1, free=1, direction=2)
        self.assertEqual(frame[5], 0x08)

    def test_set_next_position_confirms_il_behavior(self) -> None:
        # torque < 3：v = (led<<13)|(torque<<11)|(rel<<10)|position
        self.assertEqual(P.build_set_next_position(1, 640, torque=1, relative=1,
                                                   ledkind=3, angle=90)[5:7],
                         bytes([0x6E, 0x80]))
        self.assertEqual(P.build_set_next_position(9, 1023, torque=0, freewheel=1,
                                                   ledkind=3, angle=180)[5:7],
                         bytes([0x63, 0xFF]))
        # torque >= 3：走「轮子/角度」分支，position 只贡献 <512 的方向位
        # position=1023 ≥ 512 → 方向位 0：
        # v = (3<<13)|(3<<11)|(1<<10)|(1<<9)|(0<<8)|90 = 0x7E5A
        self.assertEqual(P.build_set_next_position(1, 1023, torque=3, relative=1,
                                                   freewheel=1, ledkind=3,
                                                   angle=90)[5:7],
                         bytes([0x7E, 0x5A]))
        # position=100 < 512 → 方向位置 1：v = (3<<11)|(1<<8) = 0x1900
        self.assertEqual(P.build_set_next_position(1, 100, torque=3, ledkind=0,
                                                   angle=0)[5:7],
                         bytes([0x19, 0x00]))

    def test_led_encodings(self) -> None:
        self.assertEqual(P.build_set_led(2, 255)[5], 0xE0)          # <<5
        self.assertEqual(P.build_set_led_color(2, True, False, False)[5], 0x80)  # (8<<4)
        self.assertEqual(P.build_set_led_color(2, False, True, True)[5], 0x60)   # (4+2)<<4


class TestFrameParser(unittest.TestCase):
    def test_single_frame(self) -> None:
        parser = P.FrameParser()
        frames = parser.feed(bytes.fromhex("FF FF 07 06 05 F0"))   # Status 请求
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].id, 7)
        self.assertEqual(frames[0].cmd, P.ServoCmd.STATUS)
        self.assertFalse(frames[0].is_ack)
        self.assertFalse(frames[0].is_controller)

        ack = parser.feed(P.make_frame(P.ServoAck.STATUS, 7, bytes([7])))
        self.assertEqual(len(ack), 1)
        self.assertTrue(ack[0].is_ack)
        self.assertEqual(ack[0].data, bytes([7]))

    def test_chunked_and_noisy_stream(self) -> None:
        parser = P.FrameParser()
        stream = b"\x00\x13" + bytes.fromhex("FF FF 01 06 14 E7") + \
                 bytes.fromhex("FF FF 00 06 03 F9")
        out = []
        for byte in bytes(stream):
            out.extend(parser.feed(bytes([byte])))
        self.assertEqual([f.id for f in out], [1, 0])
        self.assertEqual([f.cmd for f in out], [0x14, 0x03])

    def test_bad_checksum_is_dropped_and_resyncs(self) -> None:
        parser = P.FrameParser()
        broken = bytearray(P.build_status(3))
        broken[-1] ^= 0xFF
        self.assertEqual(parser.feed(bytes(broken)), [])
        self.assertGreater(parser.errors, 0)
        good = parser.feed(P.build_get_position(3))
        self.assertEqual(len(good), 1)

    def test_error_frame_flag(self) -> None:
        parser = P.FrameParser()
        err = P.make_frame(P.ServoAck.ERROR, 5, bytes([P.ServoErrorCode.OVERLOAD]))
        frames = parser.feed(err)
        self.assertTrue(frames[0].is_error)
        with self.assertRaises(P.ErrorResponse):
            raise P.ErrorResponse(frames[0])

    def test_length_candidates_prefer_whole_frame_semantics(self) -> None:
        """舵机的 ``LEN`` 是「整帧长度」，必须优先按它截取。

        原先顺序是先试 ``declared + 6``：缓冲里恰好够长时会把一条完整帧错切成
        跨帧窗口，凑出校验和（约 1/256）就把后续字节一起吞掉，表现为偶发丢帧/串位。
        """
        parser = P.FrameParser()
        parser.feed(bytes([0xFF, 0xFF, 0x01, 0x08]))
        self.assertEqual(parser._candidate_lengths(), [8, 14])
        # 控制器回包的 LEN 是「数据长度」（2 → 整帧 8），此时没有歧义
        parser.reset()
        parser.feed(bytes([0xFF, 0xFF, 0x00, 0x02]))
        self.assertEqual(parser._candidate_lengths(), [8])

    def test_back_to_back_servo_replies_are_not_merged(self) -> None:
        """连续两条舵机回包必须各自成帧（错切会把后一条吞进前一条）。

        两帧都是逐字节核验过的：``FFFF0109940001FE65`` 是 ID1 的位置回包
        （510），``FFFF080898000 05A`` 是 2026-09-27 真机采集的 ID8 负荷回包。
        """
        parser = P.FrameParser()
        frames = parser.feed(bytes.fromhex("FFFF0109940001FE65")
                             + bytes.fromhex("FFFF08089800005A"))
        self.assertEqual([(f.id, f.cmd) for f in frames], [(1, 0x94), (8, 0x98)])
        self.assertEqual(parser.errors, 0)


class TestDeviceReplies(unittest.TestCase):
    """真机采集的应答帧（2026-09-25，COM3 / FTDI FT230X 上的控制器板）。"""

    MODEL_REPLY = "FFFF00028100700F"
    VERSION_REPLY = "FFFF000282080175"

    def test_reply_len_field_is_data_length(self) -> None:
        frame = bytes.fromhex(self.MODEL_REPLY)
        self.assertEqual(frame[3], 2)                  # 数据长度，不是整帧长度
        self.assertEqual(len(frame), 2 + P.MIN_FRAME_LEN)
        self.assertTrue(P.verify(frame))

    def test_parser_decodes_two_replies_in_one_stream(self) -> None:
        parser = P.FrameParser()
        frames = parser.feed(bytes.fromhex(self.MODEL_REPLY + self.VERSION_REPLY))
        self.assertEqual([f.id for f in frames], [P.CONTROLLER_ID] * 2)
        self.assertEqual([f.cmd for f in frames], [0x81, 0x82])
        self.assertEqual(frames[0].data, b"\x00\x70")
        self.assertEqual(frames[1].data, b"\x08\x01")

    def test_handshake_fields_match_original_rules(self) -> None:
        # 协议约定：cmd==0x81 取 data[1]==0x70；cmd==0x82 取 data[0]==8
        model = P.parse_frame(bytes.fromhex(self.MODEL_REPLY), strict=False)
        version = P.parse_frame(bytes.fromhex(self.VERSION_REPLY), strict=False)
        self.assertEqual(model.data[1], 0x70)
        self.assertEqual(version.data[0], 8)

    def test_zero_data_reply(self) -> None:
        frame = bytearray([0xFF, 0xFF, 0x03, 0x00, 0x86, 0x00])
        frame[-1] = P.checksum(frame)
        frames = P.FrameParser().feed(bytes(frame))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].data, b"")
        self.assertEqual(frames[0].cmd, P.ServoAck.SET_POSITION)

    def test_request_style_frame_still_parses(self) -> None:
        # 请求语义（LEN=整帧长度）不能被新逻辑破坏
        frames = P.FrameParser().feed(bytes.fromhex("FF FF 01 08 09 0A 00 E6"))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].cmd, P.ServoCmd.SET_POSITION)


class TestServoReplies(unittest.TestCase):
    """舵机 ID=10 实测回包（2026-09-25）：解码定标。

    数据段第 1 字节是状态/保留字节（实测恒为 0x00），数值从 data[1] 开始。
    """

    POSITION = "FF FF 0A 09 94 00 01 FE 5C"
    PID = "FF FF 0A 0A 92 00 32 00 05 25"
    LIMIT = "FF FF 0A 0B 9A 00 00 FF 03 00 51"
    TEMP = "FF FF 0A 08 93 00 24 39"
    CALIB = "FF FF 0A 08 95 00 74 E7"

    @staticmethod
    def _resp(hex_text: str) -> P.Response:
        frames = P.FrameParser().feed(bytes.fromhex(hex_text.replace(" ", "")))
        assert len(frames) == 1, hex_text
        return frames[0]

    def test_all_servo_replies_parse(self) -> None:
        for name, hex_text, sid, cmd, data_hex in golden.GOLDEN_REPLIES:
            if sid == 0:
                continue
            with self.subTest(frame=name):
                resp = self._resp(hex_text)
                self.assertEqual(resp.id, sid)
                self.assertEqual(resp.cmd, cmd)
                self.assertEqual(resp.data.hex(" ").upper(), data_hex)
                self.assertTrue(resp.is_ack)

    def test_position_decode(self) -> None:
        from romanbo.servo import Servo
        self.assertEqual(Servo._decode_position(self._resp(self.POSITION).data), 510)

    def test_payload_strips_status_byte(self) -> None:
        self.assertEqual(P.payload(self._resp(self.PID).data), bytes([0x32, 0x00, 0x05]))
        self.assertEqual(P.payload(self._resp(self.LIMIT).data),
                         bytes([0x00, 0xFF, 0x03, 0x00]))
        self.assertEqual(P.payload(self._resp(self.TEMP).data), bytes([0x24]))

    def test_pid_matches_rsc_default(self) -> None:
        # .rsc 里每台马达的 PID 默认值就是 P=50 / I=0 / D=5
        body = P.payload(self._resp(self.PID).data)
        self.assertEqual((body[0], body[1], body[2]), (50, 0, 5))

    def test_position_limit_decode(self) -> None:
        # 真机实测（连发 3 次一致）：min = 0x00FF = 255，max = 0x0300 = 768
        body = P.payload(self._resp(self.LIMIT).data)
        self.assertEqual((P.decode_u16_be(body, 0), P.decode_u16_be(body, 2)), (255, 768))

    def test_temperature_and_calibration(self) -> None:
        self.assertEqual(P.payload(self._resp(self.TEMP).data)[0], 36)
        self.assertEqual(P.payload(self._resp(self.CALIB).data)[0], 0x74)

    def test_status_reply_has_only_status_byte(self) -> None:
        resp = self._resp("FF FF 0A 07 85 00 6C")
        self.assertEqual(resp.data, b"\x00")
        self.assertEqual(P.payload(resp.data), b"")

    def test_two_len_conventions_coexist(self) -> None:
        # 控制器回包 LEN=数据长度（2 → 整帧 8），舵机回包 LEN=整帧长度（9 → 整帧 9）
        controller = self._resp("FF FF 00 02 81 00 70 0F")
        servo = self._resp(self.POSITION)
        self.assertEqual(controller.raw[3] + P.MIN_FRAME_LEN, len(controller.raw))
        self.assertEqual(servo.raw[3], len(servo.raw))


class TestAddressing(unittest.TestCase):
    def test_constants_match_original(self) -> None:
        self.assertEqual(P.CONTROLLER_ID, 0x00)
        self.assertEqual(P.BROADCAST_ID, 0xFE)
        self.assertEqual((P.SERVO_ID_MIN, P.SERVO_ID_MAX), (1, 32))
        self.assertEqual((P.SENSOR_ID_MIN, P.SENSOR_ID_MAX), (224, 253))
        self.assertEqual(P.ADC_CENTER, 512)

    def test_broadcast_sync_frame(self) -> None:
        self.assertEqual(P.build_set_sync(P.BROADCAST_ID).hex(" ").upper(),
                         "FF FF FE 06 20 DE")

    def test_ack_for_get_commands(self) -> None:
        self.assertEqual(P.ack_for(P.ServoCmd.GET_POSITION), 0x94)
        self.assertEqual(P.ack_for(P.ServoCmd.GET_PID), 0x92)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
