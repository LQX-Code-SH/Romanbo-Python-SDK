"""基准向量：协议定义的标准收发帧。

这些字节作为**逐字节基准**，用于离线验证本实现的报文编码是否与协议一致。
"""

from __future__ import annotations

from typing import Callable, List, Tuple

from . import protocol as P

#: (说明, 期望字节, 生成函数)
GOLDEN_CHECKS: List[Tuple[str, str, Callable[[], bytes]]] = [
    # --- 舵机：查询 -------------------------------------------------------
    ("GetModel()", "FF FF 00 00 01 01", P.build_get_model),
    ("GetVersion()", "FF FF 00 00 02 00", P.build_get_version),
    ("Status(1)", "FF FF 01 06 05 F6", lambda: P.build_status(1)),
    ("Status(7)", "FF FF 07 06 05 F0", lambda: P.build_status(7)),
    ("GetPID(7)", "FF FF 07 06 12 E3", lambda: P.build_get_pid(7)),
    ("GetTemp(1)", "FF FF 01 06 13 E8", lambda: P.build_get_temp(1)),
    ("GetPosition(1)", "FF FF 01 06 14 E7", lambda: P.build_get_position(1)),
    ("GetCalibration(1)", "FF FF 01 06 15 E6", lambda: P.build_get_calibration(1)),
    ("GetMotor(1)", "FF FF 01 06 16 E5", lambda: P.build_get_motor(1)),
    ("GetMargin(1)", "FF FF 01 06 17 E4", lambda: P.build_get_margin(1)),
    ("GetCurrentLimit(1)", "FF FF 01 06 18 E3", lambda: P.build_get_current_limit(1)),
    ("GetAccelerate(1)", "FF FF 01 06 19 E2", lambda: P.build_get_accelerate(1)),
    ("GetPositionLimit(1)", "FF FF 01 06 1A E1", lambda: P.build_get_position_limit(1)),
    ("GetMotionPeriod(1)", "FF FF 01 06 0F EC", lambda: P.build_get_motion_period(1)),
    ("GetFactoryTest(1)", "FF FF 01 06 7B 80", lambda: P.build_get_factory_test(1)),
    # --- 舵机：设置 -------------------------------------------------------
    ("Reset(7)", "FF FF 07 06 01 F4", lambda: P.build_reset(7)),
    ("SetReboot(7)", "FF FF 07 06 02 F3", lambda: P.build_reboot(7)),
    ("SetID(1->9)", "FF FF 01 07 06 09 EB", lambda: P.build_set_id(1, 9)),
    ("SetPID(7,50,0,5)", "FF FF 07 09 07 32 00 05 B4",
     lambda: P.build_set_pid(7, 50, 0, 5)),
    ("SetPID_Nosave(7,50,0,5)", "FF FF 07 09 47 32 00 05 74",
     lambda: P.build_set_pid(7, 50, 0, 5, save=False)),
    ("SetTemp(2,60)", "FF FF 02 07 08 3C B5", lambda: P.build_set_temp(2, 60)),
    ("SetPosition(1,pos=0,torque=1)", "FF FF 01 08 09 08 00 E8",
     lambda: P.build_set_position(1, 0)),
    ("SetPosition(1,pos=512,torque=1)", "FF FF 01 08 09 0A 00 E6",
     lambda: P.build_set_position(1, 512)),
    ("SetPosition(1,pos=1023,torque=1)", "FF FF 01 08 09 0B FF E6",
     lambda: P.build_set_position(1, 1023)),
    ("SetWheel(1,speed=100)", "FF FF 01 08 09 08 64 84",
     lambda: P.build_set_wheel(1, 100)),
    ("SetWheel(1,speed=50,t=0,rel=1,free=1,dir=2)", "FF FF 01 08 09 08 32 B6",
     lambda: P.build_set_wheel(1, 50, torque=0, relative=1, free=1, direction=2)),
    ("SetNextPosition(1,512,t=1)", "FF FF 01 08 21 0A 00 CE",
     lambda: P.build_set_next_position(1, 512)),
    ("SetNextPosition(1,led=3,t=1,rel=1,640,90)", "FF FF 01 08 21 6E 80 EA",
     lambda: P.build_set_next_position(1, 640, torque=1, relative=1,
                                       ledkind=3, angle=90)),
    ("SetNextPosition(9,led=3,t=0,free=1,1023,180)", "FF FF 09 08 21 63 FF 6E",
     lambda: P.build_set_next_position(9, 1023, torque=0, freewheel=1,
                                       ledkind=3, angle=180)),
    ("SetPeriod(1,500)", "FF FF 01 08 0B 01 F4 F9",
     lambda: P.build_set_period(1, 500)),
    ("SetPeriod(1,1000)", "FF FF 01 08 0B 03 E8 03",
     lambda: P.build_set_period(1, 1000)),
    ("SetPositionLimit(1,100,900)", "FF FF 01 0A 0F 00 64 03 84 FD",
     lambda: P.build_set_position_limit(1, 100, 900)),
    ("SetPositionLimit(32,0,1023)", "FF FF 20 0A 0F 00 00 03 FF C7",
     lambda: P.build_set_position_limit(32, 0, 1023)),
    ("SetTorque(2,off)", "FF FF 02 07 10 00 E9", lambda: P.build_set_torque(2, 0)),
    ("SetLED(2,value=255)", "FF FF 02 07 11 E0 08", lambda: P.build_set_led(2, 255)),
    ("SetMargin(2,9)", "FF FF 02 07 0C 09 E4", lambda: P.build_set_margin(2, 9)),
    ("SetCurrentLimit(2,200)", "FF FF 02 07 0D C8 24",
     lambda: P.build_set_current_limit(2, 200)),
    ("SetOffSet(2,5)", "FF FF 02 07 0A 05 EA", lambda: P.build_set_offset(2, 5)),
    ("SetSync(7)", "FF FF 07 06 20 D5", lambda: P.build_set_sync(7)),
    ("SetCalibrationCurrpos(7)", "FF FF 07 06 23 D2",
     lambda: P.build_set_calibration_currpos(7)),
    ("StartFactoryTest(1)", "FF FF 01 06 79 82",
     lambda: P.build_start_factory_test(1)),
    # 该项未见实测报文，按协议文档的数据布局（value 16 位大端）推算
    ("SetFactoryTest(1,1234) [协议推算]", "FF FF 01 08 7A 04 D2 A9",
     lambda: P.build_set_factory_test(1, 1234)),
    # --- 控制器板 ---------------------------------------------------------
    ("Ctrl.ControlStatus()", "FF FF 00 06 03 F9", P.build_ctrl_control_status),
    ("Ctrl.Play()", "FF FF 00 06 07 F5", P.build_ctrl_play),
    ("Ctrl.Sequence()", "FF FF 00 06 08 F4", P.build_ctrl_sequence),
    ("Ctrl.DownloadEnd(2)", "FF FF 00 07 05 02 F4",
     lambda: P.build_ctrl_download_end(2)),
    ("Ctrl.SetLED(3,255)", "FF FF 03 07 0A FF EF",
     lambda: P.build_ctrl_set_led(3, 255)),
    ("Ctrl.SetTorque(5,1)", "FF FF 05 07 10 01 E5",
     lambda: P.build_ctrl_set_torque(5, 1)),
    ("Ctrl.GetPosition(5)", "FF FF 05 06 14 E3",
     lambda: P.build_ctrl_get_position(5)),
]


#: 真机采集的**应答帧**：(说明, 原始字节, ID, CMD, 数据段)
#: 采集于 2026-09-25，COM3（FTDI FT230X）上的 ROMANBO 控制器板。
#: 注意：应答帧的 ``LEN`` 字段是**数据长度**（整帧 = LEN + 6），与请求不同。
GOLDEN_REPLIES: List[Tuple[str, str, int, int, str]] = [
    ("控制器 GetModel 回包", "FF FF 00 02 81 00 70 0F", 0x00, 0x81, "00 70"),
    ("控制器 GetVersion 回包", "FF FF 00 02 82 08 01 75", 0x00, 0x82, "08 01"),
    # --- 舵机 ID=10 实测回包（同一次采集，LEN 用的是「整帧长度」语义）---
    ("舵机10 Status 回包", "FF FF 0A 07 85 00 6C", 10, 0x85, "00"),
    ("舵机10 GetMotor 回包", "FF FF 0A 09 96 00 00 00 59", 10, 0x96, "00 00 00"),
    ("舵机10 GetPosition 回包", "FF FF 0A 09 94 00 01 FE 5C", 10, 0x94, "00 01 FE"),
    ("舵机10 GetPID 回包", "FF FF 0A 0A 92 00 32 00 05 25", 10, 0x92, "00 32 00 05"),
    ("舵机10 GetTemp 回包", "FF FF 0A 08 93 00 24 39", 10, 0x93, "00 24"),
    ("舵机10 GetCurrentLimit 回包", "FF FF 0A 08 98 00 00 58", 10, 0x98, "00 00"),
    # 连发 3 次完全一致（2026-09-25 复测）；早期一次采到 `00 00 01 03 FF`
    # 疑为相邻命令的串位，不作为基准
    ("舵机10 GetPositionLimit 回包", "FF FF 0A 0B 9A 00 00 FF 03 00 51",
     10, 0x9A, "00 00 FF 03 00"),
    ("舵机10 GetCalibration 回包", "FF FF 0A 08 95 00 74 E7", 10, 0x95, "00 74"),
    ("舵机10 GetFactoryTest 回包", "FF FF 0A 09 FB 00 06 0E E0", 10, 0xFB, "00 06 0E"),
]


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


def run(verbose: bool = False) -> Tuple[int, int, List[str]]:
    """运行全部黄金向量比对，返回 ``(通过数, 失败数, 失败详情)``。"""
    passed = 0
    failures: List[str] = []
    for name, expected_hex, builder in GOLDEN_CHECKS:
        expected = bytes.fromhex(expected_hex.replace(" ", ""))
        try:
            actual = builder()
        except Exception as exc:  # pragma: no cover
            failures.append(f"{name}: 构造异常 {exc}")
            continue
        if actual == expected:
            passed += 1
            if verbose:
                print(f"  PASS {name:<48} {_hex(actual)}")
        else:
            failures.append(f"{name}: 期望 {_hex(expected)} / 实际 {_hex(actual)}")

    # 应答帧解析校验（真机采集）
    parser = P.FrameParser()
    for name, hex_text, want_id, want_cmd, want_data in GOLDEN_REPLIES:
        raw = bytes.fromhex(hex_text.replace(" ", ""))
        frames = parser.feed(raw)
        if len(frames) != 1:
            failures.append(f"{name}: 未解析出唯一帧（得到 {len(frames)} 帧）")
            continue
        response = frames[0]
        actual = (response.id, response.cmd, response.data.hex(" ").upper())
        expected = (want_id, want_cmd, want_data)
        if actual == expected:
            passed += 1
            if verbose:
                print(f"  PASS {name:<48} {_hex(raw)}")
        else:
            failures.append(f"{name}: 期望 {expected} / 实际 {actual}")

    if verbose:
        for item in failures:
            print("  FAIL " + item)
    return passed, len(failures), failures


def main() -> int:
    passed, failed, _ = run(verbose=True)
    print(f"\n黄金向量自检: 通过 {passed} / 失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["GOLDEN_CHECKS", "GOLDEN_REPLIES", "run", "main"]
