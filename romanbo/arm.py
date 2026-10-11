"""机械臂层：URDF 运动学 + 「舵机 ADC ⇄ URDF 关节角」的配置加载。

与 ``config/*.json``（主动臂 / 从动臂）配套：

* 配置文件只存**数据**（ID 映射、零位 ADC、方向、限位、home），换算逻辑在这里，
  两边不会漂移失同步；
* URDF 只解析可视化所需的最小集合：关节的 origin（xyz/rpy）、axis、parent/child
  与限位，外加每个 link 的 ``<visual>``（mesh 文件名 / origin / 材质色）——
  ``inertial`` / ``collision`` 不读（标准库 ``xml.etree``，零新依赖）；
* 正向运动学按 URDF 约定合成：``T(parent→child) = Trans(xyz)·Rot(rpy)·Rot(axis, q)``，
  其中 ``rpy`` 是绕固定轴的 roll-pitch-yaw（``R = Rz(yaw)·Ry(pitch)·Rx(roll)``），
  ``q`` 以**度**传入（与配置文件的关节角口径一致，内部再转弧度）。
"""

from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import joints as J
from . import protocol as P

__all__ = ["JointCfg", "ArmConfig", "UrdfSkeleton", "discover",
           "WEIGHT_BEARING_JOINTS"]

Mat3 = List[List[float]]
Pose = Tuple[Mat3, Tuple[float, float, float]]

#: 配置文件名（去扩展名、去 ``_arm`` 后缀）→ 界面显示名
_LABELS = {"leader": "主动臂", "follower": "从动臂"}

#: 承载机械臂自重的关节（配置文件里的短名）。这些关节使能 / 运动时默认用
#: H 档（`romanbo.protocol.LEVEL_HIGH`）出力——M 档的保持力扛不住臂身自重。
WEIGHT_BEARING_JOINTS = ("shoulder_lift", "elbow_flex", "wrist_flex")


# --------------------------------------------------------------------------- #
# 旋转 / 矩阵（3x3，够用即可，不引入 numpy）
# --------------------------------------------------------------------------- #

def _eye() -> Mat3:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def _rpy(roll: float, pitch: float, yaw: float) -> Mat3:
    """URDF rpy（固定轴 XYZ）→ 旋转矩阵 ``Rz(yaw)·Ry(pitch)·Rx(roll)``。"""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp,     cp * sr,               cp * cr]]


def _axis_rot(axis: Sequence[float], q: float) -> Mat3:
    """Rodrigues 公式：绕 ``axis``（自动归一化）转 ``q`` 弧度。"""
    n = math.sqrt(sum(float(a) ** 2 for a in axis))
    if n < 1e-12:
        return _eye()
    x, y, z = float(axis[0]) / n, float(axis[1]) / n, float(axis[2]) / n
    c, s = math.cos(q), math.sin(q)
    t = 1.0 - c
    return [[t * x * x + c,     t * x * y - s * z,  t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c,      t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x,  t * z * z + c]]


def _mul(a: Mat3, b: Mat3) -> Mat3:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _apply(r: Mat3, v: Sequence[float]) -> Tuple[float, float, float]:
    return (r[0][0] * v[0] + r[0][1] * v[1] + r[0][2] * v[2],
            r[1][0] * v[0] + r[1][1] * v[1] + r[1][2] * v[2],
            r[2][0] * v[0] + r[2][1] * v[1] + r[2][2] * v[2])


def _floats(text: str) -> List[float]:
    return [float(p) for p in text.replace(",", " ").split()]


# --------------------------------------------------------------------------- #
# 臂配置（config/*.json）
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class JointCfg:
    """单关节：把舵机 ADC 与 URDF 关节角接起来。"""

    name: str                    # 配置里的短名（如 shoulder_pan）
    urdf_joint: str              # URDF 关节名（如 Romanbo_shoulder_pan）
    servo_id: int
    zero_adc: int                # 零位标定：该 ADC 对应关节角 0°
    direction: int               # +1：ADC 增大 = 关节角增大；-1 反向
    limits_deg: Tuple[float, float]

    def adc_to_deg(self, adc: int) -> float:
        return (int(adc) - self.zero_adc) * J.RATIO_MAIN * self.direction

    def deg_to_adc(self, deg: float) -> int:
        value = self.zero_adc + float(deg) / (J.RATIO_MAIN * self.direction)
        return max(P.ADC_MIN, min(P.ADC_MAX, int(round(value))))

    def clamp(self, deg: float) -> float:
        lo, hi = self.limits_deg
        return max(lo, min(hi, float(deg)))


def _stem_key(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem[:-4] if stem.endswith("_arm") else stem


@dataclass
class ArmConfig:
    """一份 ``config/*.json``。"""

    key: str
    label: str
    joints: Dict[str, JointCfg]        # 键 = 短名
    home: Dict[str, int]               # 短名 → ADC（回位姿态）
    version: int = 1

    @classmethod
    def load(cls, path: str, *, key: Optional[str] = None) -> "ArmConfig":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        joints: Dict[str, JointCfg] = {}
        for name, jd in (data.get("joints") or {}).items():
            limits = jd.get("limits_deg") or (-120.0, 120.0)
            joints[str(name)] = JointCfg(
                name=str(name),
                urdf_joint=str(jd["urdf_joint"]),
                servo_id=int(jd["servo_id"]),
                zero_adc=int(jd["zero_adc"]),
                direction=int(jd.get("direction", 1)),
                limits_deg=(float(limits[0]), float(limits[1])))
        if not joints:
            raise ValueError(f"{path}: joints 为空")
        home = {str(k): int(v) for k, v in (data.get("home") or {}).items()}
        key = key or _stem_key(path)
        return cls(key=key, label=_LABELS.get(key, key), joints=joints, home=home,
                   version=int(data.get("version", 1)))

    @property
    def servo_ids(self) -> List[int]:
        return [j.servo_id for j in self.joints.values()]

    def joint_for(self, name: str) -> JointCfg:
        """按短名或 URDF 关节名查关节（接口两个口径都收）。"""
        if name in self.joints:
            return self.joints[name]
        for joint in self.joints.values():
            if joint.urdf_joint == name:
                return joint
        raise ValueError(f"未知关节 {name!r}（可用：{sorted(self.joints)}）")

    def angles_from_adc(self, positions: Mapping[int, int]) -> Dict[str, float]:
        """``{servo_id: adc}`` → ``{urdf_joint: 度}``（缺的关节跳过）。"""
        return {j.urdf_joint: round(j.adc_to_deg(positions[j.servo_id]), 2)
                for j in self.joints.values() if j.servo_id in positions}

    def targets_from_deg(self, degs: Mapping[Any, float]) -> Dict[int, int]:
        """``{关节名: 度}`` → ``{servo_id: adc}``，角度夹限位、ADC 夹 0..1023。"""
        out: Dict[int, int] = {}
        for name, deg in dict(degs).items():
            joint = self.joint_for(str(name))
            out[joint.servo_id] = joint.deg_to_adc(joint.clamp(float(deg)))
        return out

    def home_adcs(self) -> Dict[int, int]:
        missing = sorted(n for n in self.joints if n not in self.home)
        if missing:
            raise ValueError(f"home 缺少关节：{missing}")
        return {self.joints[n].servo_id: int(self.home[n]) for n in self.home}

    def default_levels(self) -> Dict[int, int]:
        """承重关节（`WEIGHT_BEARING_JOINTS`）→ H 档（`LEVEL_HIGH`）。

        供整臂运动默认使用：这些关节要悬住臂身自重，出力档位不能低于 H。
        非承重关节不在返回里，由调用方决定（不传则用库默认 M 档）。
        """
        return {j.servo_id: P.LEVEL_HIGH for n, j in self.joints.items()
                if n in WEIGHT_BEARING_JOINTS}


# --------------------------------------------------------------------------- #
# URDF 骨架
# --------------------------------------------------------------------------- #

@dataclass
class UrdfSkeleton:
    """画 TF 树 + mesh 所需的最小 URDF 信息（标准库解析，inertial 不读）。"""

    root: str
    #: 每项：name/parent/child/movable/xyz/rpy/axis/limits_deg
    joints: List[Dict[str, Any]]
    #: link → visual 列表（file/xyz/rpy/scale/rgba），前端渲染 mesh 用
    links: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    #: URDF 文件路径：mesh 的相对路径按它解析，也用于 /api/mesh 的目录约束
    path: Optional[str] = None

    @classmethod
    def parse(cls, path: str) -> "UrdfSkeleton":
        root_el = ET.parse(path).getroot()
        # 全局 <material>（名字 → rgba），visual 里按名字引用
        materials: Dict[str, List[float]] = {}
        for mat in root_el.findall("material"):
            color = mat.find("color")
            if color is not None and mat.get("name"):
                materials[mat.get("name") or ""] = _floats(
                    color.get("rgba", "0.6 0.6 0.6 1"))
        links: Dict[str, List[Dict[str, Any]]] = {}
        for el in root_el.findall("link"):
            visuals: List[Dict[str, Any]] = []
            for vis in el.findall("visual"):
                mesh = vis.find("geometry/mesh")
                if mesh is None or not mesh.get("filename"):
                    continue
                origin = vis.find("origin")
                scale = mesh.get("scale")
                rgba: Optional[List[float]] = None
                mat_el = vis.find("material")
                if mat_el is not None:
                    color = mat_el.find("color")
                    if color is not None:
                        rgba = _floats(color.get("rgba", "0.6 0.6 0.6 1"))
                    elif mat_el.get("name") in materials:
                        rgba = materials[mat_el.get("name") or ""]
                visuals.append({
                    "file": mesh.get("filename") or "",
                    "xyz": _floats(origin.get("xyz", "0 0 0") if origin is not None else "0 0 0"),
                    "rpy": _floats(origin.get("rpy", "0 0 0") if origin is not None else "0 0 0"),
                    "scale": _floats(scale) if scale else [1.0, 1.0, 1.0],
                    "rgba": rgba or [0.6, 0.6, 0.6, 1.0],
                })
            if visuals:
                links[el.get("name") or ""] = visuals
        joints: List[Dict[str, Any]] = []
        children = set()
        for el in root_el.findall("joint"):
            parent = el.find("parent")
            child = el.find("child")
            origin = el.find("origin")
            axis = el.find("axis")
            limit = el.find("limit")
            jtype = (el.get("type") or "").lower()
            movable = jtype in ("revolute", "continuous", "prismatic")
            limits_deg = None
            if movable and limit is not None:
                try:
                    limits_deg = [float(limit.get("lower", 0.0)) * 180.0 / math.pi,
                                  float(limit.get("upper", 0.0)) * 180.0 / math.pi]
                except (TypeError, ValueError):
                    limits_deg = None
            joint = {
                "name": el.get("name") or "",
                "parent": (parent.get("link") if parent is not None else "") or "",
                "child": (child.get("link") if child is not None else "") or "",
                "movable": movable,
                "xyz": _floats(origin.get("xyz", "0 0 0") if origin is not None else "0 0 0"),
                "rpy": _floats(origin.get("rpy", "0 0 0") if origin is not None else "0 0 0"),
                "axis": _floats(axis.get("xyz", "0 0 1") if axis is not None else "0 0 1"),
                "limits_deg": limits_deg,
            }
            joints.append(joint)
            children.add(joint["child"])
        roots = [j["parent"] for j in joints
                 if j["parent"] and j["parent"] not in children]
        return cls(root=roots[0] if roots else "base_link", joints=joints,
                   links=links, path=os.path.abspath(path))

    def as_dict(self) -> Dict[str, Any]:
        """给前端的形状（页面用它算 FK、画坐标系与 mesh）。"""
        return {"root": self.root, "joints": self.joints, "links": self.links}

    def fk(self, angles: Mapping[str, float]) -> Dict[str, Pose]:
        """各 link 的世界位姿 ``{link: (R3x3, t)}``。``angles`` 键 = URDF 关节名，度。"""
        world: Dict[str, Pose] = {self.root: (_eye(), (0.0, 0.0, 0.0))}
        pending = list(self.joints)
        while pending:
            progressed = False
            for joint in list(pending):
                if joint["parent"] not in world:
                    continue
                rp, tp = world[joint["parent"]]
                rot = _mul(rp, _rpy(*joint["rpy"]))
                if joint["movable"]:
                    q = float(angles.get(joint["name"], 0.0)) * math.pi / 180.0
                    rot = _mul(rot, _axis_rot(joint["axis"], q))
                off = _apply(rp, joint["xyz"])
                world[joint["child"]] = (rot, (off[0] + tp[0], off[1] + tp[1],
                                               off[2] + tp[2]))
                pending.remove(joint)
                progressed = True
            if not progressed:            # 环 / 断链：剩下的画不出来，直接放弃
                break
        return world


# --------------------------------------------------------------------------- #
# 自动发现
# --------------------------------------------------------------------------- #

def discover(urdf_path: Optional[str] = None,
             config_dir: Optional[str] = None
             ) -> Tuple[Optional[UrdfSkeleton], Dict[str, ArmConfig]]:
    """找 URDF 与 ``config/`` 下的臂配置；找不到**不报错**（相关功能静默关闭）。

    两个参数都给 ``None`` 时按常见布局探测：``urdf/Romanbo.urdf`` 与 ``config/``
    （再往上退一级，照顾「在子目录里启动」的场景）。
    """
    skeleton: Optional[UrdfSkeleton] = None
    for cand in ([urdf_path] if urdf_path else ("urdf/Romanbo.urdf",
                                                "../urdf/Romanbo.urdf")):
        if cand and os.path.isfile(cand):
            try:
                skeleton = UrdfSkeleton.parse(cand)
            except (OSError, ET.ParseError):
                pass
            break
    arms: Dict[str, ArmConfig] = {}
    for cand in ([config_dir] if config_dir else ("config", "../config")):
        if not cand or not os.path.isdir(cand):
            continue
        for fn in sorted(os.listdir(cand)):
            if not fn.endswith(".json"):
                continue
            try:
                arm = ArmConfig.load(os.path.join(cand, fn))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            arms[arm.key] = arm
        break
    return skeleton, arms
