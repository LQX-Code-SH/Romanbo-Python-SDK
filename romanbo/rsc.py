"""``.rsc`` 工程文件解析。

格式要点
--------
``.rsc`` 是 JSON 文本，但**用重复键表示数组**，例如::

    "MotorValue": { "ADC": 512, "ADC": 563, ... }   // 17 个关节值

标准 JSON 解析器会把重复键覆盖掉（只剩最后一个），因此这里必须用
``object_pairs_hook`` 保留重复键，否则动作数据会全部丢失。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Dict, Iterable, Iterator, List, Mapping, Optional,
                    Sequence, Tuple)

#: 遥控按键位编码常量
REMOCON_BUTTON_NAMES: Dict[int, str] = {
    1: "Left_Up", 2: "Left_Right", 4: "Left_Down", 8: "Left_Left",
    100: "Right_Up", 200: "Right_Right", 400: "Right_Down", 800: "Right_Left",
    40000: "L", 80000: "R",
    100000: "Control_Up", 200000: "Control_Down",
    300000: "Control_Left", 400000: "Control_Right",
    10000000: "Control_Run", 20000000: "Control_Mode",
}

#: 程序块类型常量
CHIP_KINDS: Dict[int, str] = {
    0: "Start", 10: "While", 20: "Loop", 21: "End", 30: "If", 31: "Else",
    32: "ElseIf", 33: "Until", 40: "Delay", 41: "Index", 50: "Custom",
    51: "Lib", 100: "Mp3Play", 101: "Mp3End", 102: "Mp3Volume",
}


class RscFormatError(Exception):
    """``.rsc`` 结构不符合预期。"""


class _Repeated(list):
    """标记：该列表由「重复键」收集而来。"""


def _pairs_hook(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            if isinstance(out[key], _Repeated):
                out[key].append(value)
            else:
                out[key] = _Repeated([out[key], value])
        else:
            out[key] = value
    return out


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        return _as_int(value[0], default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _explode_repeated(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把「用重复键塞进同一个对象的多条记录」拆成多条记录。

    ``.rsc`` 里一个 ``"Motion": [ { ... } ]`` 块可以包含**多帧**：所有帧的
    ``SceneNumber``/``MotionNumber``/``Period``/``MotorValue``/``LEDValue``
    都作为重复键塞在同一个对象里，如::

        {"SceneNumber": [0,0,0], "MotionNumber": [0,1,2], "Period": [500,500,500],
         "MotorValue": [{"ADC": [...]}, {"ADC": [...]}, {"ADC": [...]}]}

    本函数按重复次数（取最大长度）把它展开成 3 条独立记录。
    """
    counts = [len(value) for value in item.values() if isinstance(value, _Repeated)]
    if not counts:
        return [item]
    rows: List[Dict[str, Any]] = []
    for index in range(max(counts)):
        row: Dict[str, Any] = {}
        for key, value in item.items():
            if isinstance(value, _Repeated):
                row[key] = value[index] if index < len(value) else None
            else:
                row[key] = value
        rows.append(row)
    return rows


def _extract_values(container: Any, key: str) -> List[int]:
    """从 ``MotorValue`` / ``LEDValue`` 这类容器里取出全部值。"""
    if isinstance(container, dict):
        return [_as_int(v) for v in _as_list(container.get(key)) if v is not None]
    if isinstance(container, (list, tuple)):
        out: List[int] = []
        for entry in container:
            out.extend(_extract_values(entry, key))
        return out
    return []


@dataclass(frozen=True)
class MotionFrame:
    """一个动作帧（对应界面上的一列）。"""

    scene_index: int
    scene_name: str
    motion_index: int
    period_ms: int
    adc: Tuple[int, ...]
    led: Tuple[int, ...] = ()

    @property
    def motor_count(self) -> int:
        return len(self.adc)

    def targets(self, *, id_offset: int = 1) -> Dict[int, int]:
        """转成 ``{舵机ID: 目标ADC}``。

        约定：``.rsc`` 数组下标 ``i`` 对应舵机 ID ``i + id_offset``
        （舵机 ID 从 1 开始，故默认偏移为 1）。
        """
        return {i + id_offset: value for i, value in enumerate(self.adc)}

    def led_targets(self, *, id_offset: int = 1) -> Dict[int, int]:
        return {i + id_offset: value for i, value in enumerate(self.led) if value}


@dataclass
class Scene:
    index: int
    name: str
    color: int = 0
    motion_count: int = 0


@dataclass
class RemoconKey:
    key: int
    flag: int
    main_cmd: int
    start_cmd: int
    end_cmd: int

    @property
    def buttons(self) -> Tuple[str, ...]:
        """把按键位编码翻译成按键名（``Flag=1`` 表示组合键）。"""
        names = [name for bit, name in sorted(REMOCON_BUTTON_NAMES.items())
                 if self.key == bit]
        if names:
            return tuple(names)
        # 组合键：位与匹配
        found = tuple(name for bit, name in sorted(REMOCON_BUTTON_NAMES.items())
                      if self.key & bit)
        return found or (f"0x{self.key:X}",)


class RscProject:
    """读取一个 ``.rsc`` 工程。"""

    def __init__(self, raw: Dict[str, Any], path: Optional[str] = None) -> None:
        self.raw = raw
        self.path = path
        self.header: Dict[str, Any] = raw.get("Header", {}) or {}
        self.body: Dict[str, Any] = raw.get("Body", {}) or {}

    # -- 构造 -------------------------------------------------------------- #

    @classmethod
    def load(cls, path: str | Path) -> "RscProject":
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return cls.loads(text, path=str(path))

    @classmethod
    def loads(cls, text: str, path: Optional[str] = None) -> "RscProject":
        try:
            raw = json.loads(text, object_pairs_hook=_pairs_hook)
        except json.JSONDecodeError as exc:
            raise RscFormatError(f"JSON 解析失败: {exc}") from exc
        if not isinstance(raw, dict) or "Header" not in raw or "Body" not in raw:
            raise RscFormatError("缺少 Header/Body，可能不是 .rsc 工程文件")
        return cls(raw, path=path)

    # -- 基本信息 ---------------------------------------------------------- #

    @property
    def version(self) -> Optional[str]:
        block = self.raw.get("Version")
        if isinstance(block, dict):
            value = block.get("Version")
            return str(value) if value is not None else None
        return None

    @property
    def project_name(self) -> str:
        return str(self.header.get("ProjectName", Path(self.path).name if self.path else ""))

    @property
    def type_id(self) -> int:
        return _as_int(self.header.get("Type"), 1)

    @property
    def motor_count(self) -> int:
        return _as_int(self.header.get("MotorCount"), 0)

    @property
    def dial_position(self) -> List[Tuple[int, int]]:
        out = []
        for item in _as_list(self.header.get("DialPosition")):
            if isinstance(item, dict):
                out.append((_as_int(item.get("x")), _as_int(item.get("y"))))
        return out

    @property
    def motor_state(self) -> List[Dict[str, int]]:
        out = []
        for item in _as_list(self.header.get("MotorState")):
            if isinstance(item, dict):
                out.append({"adc": _as_int(item.get("ADC")),
                            "direction": _as_int(item.get("Direction"))})
        return out

    @property
    def scenes(self) -> List[Scene]:
        out: List[Scene] = []
        for key, value in self.body.items():
            if not key.isdigit() or not isinstance(value, dict):
                continue
            out.append(Scene(index=int(key),
                             name=str(value.get("SceneName", f"Scene{key}")),
                             color=_as_int(value.get("SceneColor")),
                             motion_count=_as_int(value.get("MotionCount"))))
        return sorted(out, key=lambda s: s.index)

    @property
    def setup(self) -> List[Dict[str, int]]:
        """每个场景一组 PID（``Setup`` 段）。"""
        out = []
        for item in _as_list(self.body.get("Setup")):
            if isinstance(item, dict):
                out.append({"P": _as_int(item.get("P")), "I": _as_int(item.get("I")),
                            "D": _as_int(item.get("D"))})
        return out

    @property
    def remocon(self) -> List[RemoconKey]:
        out = []
        for item in _as_list(self.body.get("Remocon")):
            if isinstance(item, dict):
                out.append(RemoconKey(key=_as_int(item.get("Key")),
                                      flag=_as_int(item.get("Flag")),
                                      main_cmd=_as_int(item.get("MainCMD")),
                                      start_cmd=_as_int(item.get("StartCMD")),
                                      end_cmd=_as_int(item.get("EndCMD"))))
        return out

    @property
    def key_index(self) -> List[str]:
        """程序槽（0..30）序列化内容，``KeyIndex[n]`` 为嵌套 JSON 字符串。"""
        out: List[str] = []
        for item in _as_list(self.body.get("KeyIndex")):
            if isinstance(item, dict):
                out.append(str(item.get("0", "")))
        return out

    @property
    def key_index_names(self) -> List[str]:
        out: List[str] = []
        for item in _as_list(self.body.get("KeyIndexList")):
            if isinstance(item, dict):
                for key in sorted(item.keys(), key=lambda k: int(k) if k.isdigit() else 0):
                    out.append(str(item[key]))
        return out

    # -- 动作 -------------------------------------------------------------- #

    def frames(self, scene: Optional[int] = None) -> Iterator[MotionFrame]:
        """按顺序产出动作帧；``scene`` 为 ``None`` 时产出全部场景。"""
        names = {s.index: s.name for s in self.scenes}
        # Body 里会出现多个同名 "Motion" 键（每个场景一个块），且单块内也可能
        # 用重复键塞入多帧 —— 这里统一展开成「一条记录 = 一帧」。
        flat: List[Dict[str, Any]] = []
        for block in _as_list(self.body.get("Motion")):
            for item in _as_list(block):
                if isinstance(item, dict):
                    flat.extend(_explode_repeated(item))
        for item in flat:
            scene_index = _as_int(item.get("SceneNumber"))
            if scene is not None and scene_index != scene:
                continue
            adc = _extract_values(item.get("MotorValue"), "ADC")
            led = _extract_values(item.get("LEDValue"), "LED")
            yield MotionFrame(
                scene_index=scene_index,
                scene_name=names.get(scene_index, str(scene_index)),
                motion_index=_as_int(item.get("MotionNumber")),
                period_ms=_as_int(item.get("Period"), 500),
                adc=tuple(adc),
                led=tuple(led),
            )

    def summary(self) -> Dict[str, Any]:
        frames = list(self.frames())
        return {
            "path": self.path,
            "version": self.version,
            "project_name": self.project_name,
            "type": self.type_id,
            "motor_count": self.motor_count,
            "scenes": [{"index": s.index, "name": s.name, "motions": s.motion_count}
                       for s in self.scenes],
            "frame_count": len(frames),
            "remocon": [{"key": r.key, "flag": r.flag, "buttons": list(r.buttons),
                         "main": r.main_cmd, "start": r.start_cmd, "end": r.end_cmd}
                        for r in self.remocon],
            "key_index_count": len(self.key_index),
            "pid_setup": self.setup[:1],
        }


#: 生成 ``.rsc`` 时的默认场景颜色
DEFAULT_SCENE_COLOR = -6082891
#: 生成 ``.rsc`` 时的默认关节值（机械中点）
DEFAULT_ADC = 512


def _indent(depth: int) -> str:
    return "\t" * depth


def dumps_project(frames: Iterable[Mapping[str, Any]], *,
                  motor_count: int = 17,
                  project_name: str = "taught.rsc",
                  file_name: str = "",
                  scene_color: int = DEFAULT_SCENE_COLOR,
                  scene_prefix: str = "taught",
                  home: Optional[Sequence[int]] = None,
                  version: Optional[str] = None) -> str:
    """按 ``.rsc`` 格式生成文本（**重复键风格**，不是标准 JSON）。

    :param frames: 每项 ``{"adc": [...], "period": ms}``，可选 ``"led"`` 与 ``"scene"``
        （场景号，默认全部归入场景 0）。
    :param home: ``Header.MotorState`` 的初始 ADC（长度 ``motor_count``），默认全 512。
    :param scene_prefix: 场景名前缀，生成 ``<prefix>_1``、``<prefix>_2`` …

    .. important::
        ``MotorValue`` / ``LEDValue`` 在文件里是**重复同名键的对象**（不是一个数组），
        标准 ``json.dumps`` 无法生成，因此这里手工拼接文本；缩进用 Tab、字段顺序与
        与既有 ``.rsc`` 文件一致（``Setup``(PID) 一并省略，只写 ``Remocon`` 空表）。
    """
    records: List[Dict[str, Any]] = []
    for index, raw in enumerate(frames):
        adc = [int(v) for v in raw.get("adc", [])]
        if len(adc) != motor_count:
            raise RscFormatError(
                f"第 {index} 帧有 {len(adc)} 个关节值，应为 {motor_count}")
        led = [int(v) for v in raw.get("led", [])] or [0] * motor_count
        if len(led) != motor_count:
            raise RscFormatError(f"第 {index} 帧的 LED 数量应为 {motor_count}")
        records.append({
            "adc": adc, "led": led,
            "period": int(raw.get("period", 500)),
            "scene": int(raw.get("scene", 0)),
        })
    if not records:
        raise RscFormatError("至少要有一帧")

    home_values = ([int(v) for v in home] if home is not None
                   else [DEFAULT_ADC] * motor_count)
    if len(home_values) != motor_count:
        raise RscFormatError("home 长度与 motor_count 不一致")

    scenes = sorted({r["scene"] for r in records})
    out: List[str] = ["{"]
    if version:
        out += [_indent(1) + '"Version": {',
                _indent(2) + f'"Version": {json.dumps(version)},',
                _indent(1) + "},"]
    out += [_indent(1) + '"Header": {',
            _indent(2) + f'"FileName": {json.dumps(file_name)},',
            _indent(2) + '"Type": 1,',
            _indent(2) + f'"ProjectName": {json.dumps(project_name)},',
            _indent(2) + f'"MotorCount": {motor_count},',
            _indent(2) + '"DialPosition": [']
    for i in range(motor_count):
        out += [_indent(3) + "{", _indent(4) + '"x": 0,', _indent(4) + '"y": 0',
                _indent(3) + "}" + ("," if i < motor_count - 1 else "")]
    out += [_indent(2) + "],", _indent(2) + '"MotorState": [']
    for i, value in enumerate(home_values):
        out += [_indent(3) + "{", _indent(4) + f'"ADC": {value},',
                _indent(4) + '"Direction": 0',
                _indent(3) + "}" + ("," if i < motor_count - 1 else "")]
    out += [_indent(2) + "]", _indent(1) + "},"]

    out.append(_indent(1) + '"Body": {')
    for scene in scenes:
        count = sum(1 for r in records if r["scene"] == scene)
        out += [_indent(2) + f'"{scene}": {{',
                _indent(3) + f'"SceneColor": {scene_color},',
                _indent(3) + f'"SceneName": '
                            f'{json.dumps(f"{scene_prefix}_{scene + 1}")},',
                _indent(3) + f'"MotionCount": {count}',
                _indent(2) + "},"]
    out.append(_indent(2) + '"Motion": [')
    seen: Dict[int, int] = {scene: 0 for scene in scenes}
    for index, record in enumerate(records):
        scene = record["scene"]
        out += [_indent(3) + "{",
                _indent(4) + f'"SceneNumber": {scene},',
                _indent(4) + f'"MotionNumber": {seen[scene]},',
                _indent(4) + f'"Period": {record["period"]},',
                _indent(4) + '"MotorValue": {']
        for i, value in enumerate(record["adc"]):
            out.append(_indent(5) + f'"ADC": {value}'
                       + ("," if i < motor_count - 1 else ""))
        out += [_indent(4) + "},", _indent(4) + '"LEDValue": {']
        for i, value in enumerate(record["led"]):
            out.append(_indent(5) + f'"LED": {value}'
                       + ("," if i < motor_count - 1 else ""))
        out += [_indent(4) + "}",
                _indent(3) + "}" + ("," if index < len(records) - 1 else "")]
        seen[scene] += 1
    out += [_indent(2) + "],", _indent(2) + '"Remocon": []', _indent(1) + "}"]
    out.append("}")
    return "\n".join(out) + "\n"


def write_project(path: str | Path, frames: Iterable[Mapping[str, Any]],
                  **kwargs: Any) -> Path:
    """把 :func:`dumps_project` 的结果写入 ``path``（``ProjectName`` 取文件名）。"""
    target = Path(path)
    kwargs.setdefault("project_name", target.name)
    target.write_text(dumps_project(frames, **kwargs), encoding="utf-8")
    return target


__all__ = ["RscProject", "MotionFrame", "Scene", "RemoconKey",
           "RscFormatError", "REMOCON_BUTTON_NAMES", "CHIP_KINDS",
           "dumps_project", "write_project",
           "DEFAULT_SCENE_COLOR", "DEFAULT_ADC"]
