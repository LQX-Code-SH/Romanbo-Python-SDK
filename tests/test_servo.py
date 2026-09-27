"""舵机层的健壮性：畸形/截断回包不能**静默**变成错误数值。

与 `test_protocol.py` 的分工：那边验证「帧怎么解」，这边验证「解不出来时
`Servo` 怎么办」——真实总线上偶发半帧时，返回一个"看着合理"的错值比直接报错危险得多。
"""

from __future__ import annotations

import unittest

from romanbo import protocol as P
from romanbo.robot import RomanboRobot
from romanbo.servo import Servo
from romanbo.transport import MockTransport


class _TruncatingMock(MockTransport):
    """把 ``GetPosition`` 的回包截断成 1 字节数值段，模拟总线上的半帧。"""

    def _servo_reply(self, req: P.Response) -> bytes:
        if req.cmd == P.ServoCmd.GET_POSITION:
            return P.make_frame(P.ServoAck.GET_POSITION, req.id, bytes([0x00, 0x03]))
        return super()._servo_reply(req)


class TestPositionDecode(unittest.TestCase):
    def test_normal_reply_decodes(self) -> None:
        # 实测回包（ID=10）数据段 00 01 FE → 位置 510
        self.assertEqual(Servo._decode_position(bytes([0x00, 0x01, 0xFE])), 510)
        self.assertEqual(Servo._decode_position(bytes([0x00, 0x03, 0xF4])), 1012)

    def test_truncated_reply_raises_instead_of_returning_a_plausible_value(self) -> None:
        """数值段不足 2 字节时报错，而不是把 3 当成"位置 3"。"""
        for data in (bytes([0x00, 0x03]), bytes([0x03]), bytes([0x00])):
            with self.subTest(data=data.hex(" ")):
                with self.assertRaises(P.ProtocolError):
                    Servo._decode_position(data)

    def test_get_position_propagates_the_error(self) -> None:
        """完整链路（含帧解析）也要把异常抛出来，不能被吞成某个数值。"""
        robot = RomanboRobot(transport=_TruncatingMock(servo_ids=[1]),
                             ack_timeout=0.1).open()
        try:
            with self.assertRaises(P.ProtocolError):
                robot.servo(1).get_position(timeout=0.1)
        finally:
            robot.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
