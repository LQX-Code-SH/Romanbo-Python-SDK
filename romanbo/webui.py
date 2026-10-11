"""可视化控制台：把舵机总线暴露为一组**本地** HTTP JSON 接口 + 一个单页前端。

设计取舍：

* **只用标准库**（``http.server`` / ``json`` / ``threading`` / ``secrets``），不引入
  Web 框架——与「运行期只依赖 pyserial」的约定一致，拷走 ``romanbo/`` 目录即可用。
* **默认只监听 127.0.0.1**，并要求**访问令牌**与 **Host 校验**：这是一个能驱动
  硬件的服务，即使在本机也不该被任意网页（CSRF / DNS rebinding）调到。
* 所有设备操作经一把可重入锁串行化，与 `RomanboRobot` 内部的收发锁配合，
  保证「多步动作」不会被别的请求插进来打断。
* 报文日志在**传输层**抓取（TX 必为整帧、RX 为本轮读到的字节），与真机串口
  助手看到的一致，便于按 `docs/SERVO_SPEC.md` 逐字节对照。
"""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import (Any, Callable, Deque, Dict, Iterable, List, Mapping, Optional,
                    Set, Tuple, Union)
from urllib.parse import parse_qs, urlparse

from . import joints as J
from . import protocol as P
from .arm import (WEIGHT_BEARING_JOINTS, ArmConfig, UrdfSkeleton, discover)
from .robot import RomanboRobot
from .servo import LoadLimitExceeded, Servo
from .transport import (MockTransport, SerialTransport, Transport,
                        is_anonymous_port, list_serial_ports, port_error_hint)
from .webui_page import PAGE

__all__ = ["FrameLog", "WebConsole", "make_server", "serve"]


# --------------------------------------------------------------------------- #
# 报文日志
# --------------------------------------------------------------------------- #

class FrameLog:
    """有界环形缓冲：给前端按序号增量取报文。"""

    def __init__(self, capacity: int = 800) -> None:
        self._items: Deque[Dict[str, Any]] = deque(maxlen=max(16, capacity))
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, direction: str, data: bytes) -> None:
        now = time.time()
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        with self._lock:
            self._seq += 1
            self._items.append({
                "seq": self._seq,
                "dir": direction,
                "hex": data.hex(" ").upper(),
                "at": f"{stamp}.{int((now % 1) * 1000):03d}",
            })

    def since(self, seq: int) -> Tuple[List[Dict[str, Any]], int]:
        with self._lock:
            return [i for i in self._items if i["seq"] > seq], self._seq


class _LoggingTransport(Transport):
    """包装真实/模拟传输，把原始字节抄一份给控制台。"""

    def __init__(self, inner: Transport, log: FrameLog) -> None:
        self._inner = inner
        self._log = log
        self.name = inner.name

    def open(self) -> None:
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    @property
    def is_open(self) -> bool:
        return self._inner.is_open

    @property
    def lost_reason(self) -> Optional[str]:
        """把内层的断线原因透出去（`WebConsole.state` 要靠它提示重新连接）。"""
        return getattr(self._inner, "lost_reason", None)

    def write(self, frame: bytes) -> None:
        self._log.add("tx", bytes(frame))
        self._inner.write(frame)

    def read_available(self, timeout: float) -> bytes:
        data = self._inner.read_available(timeout)
        if data:
            self._log.add("rx", bytes(data))
        return data


# --------------------------------------------------------------------------- #
# 控制台（设备侧）
# --------------------------------------------------------------------------- #

class WebConsole:
    """把 `RomanboRobot` 的操作整理成前端要用的形状。"""

    #: 扫描轮数：单轮探测可能被总线静默期吃掉，3 轮取并集后再判机械臂离线。
    #: 第 1 轮**同步**返回结果，其余轮在后台线程补扫（见 `scan`）。
    SCAN_ROUNDS = 3

    def __init__(self, *, port: Optional[str] = None, mock: bool = False,
                 baudrate: int = P.BAUDRATE_DEFAULT,
                 ack_timeout: float = P.DEFAULT_ACK_TIMEOUT,
                 log_capacity: int = 800,
                 arms: Optional[Mapping[str, ArmConfig]] = None,
                 skeleton: Optional[UrdfSkeleton] = None) -> None:
        self.frames = FrameLog(log_capacity)
        self._lock = threading.RLock()
        self._robot: Optional[RomanboRobot] = None
        self._mock = bool(mock)
        self._port = port
        self._baudrate = int(baudrate)
        self._ack_timeout = float(ack_timeout)
        self._online: List[int] = []
        #: 机械臂配置（key → ArmConfig）+ URDF 骨架。空则 /api/arm/* 返回空集，
        #: 页面不显示 TF 可视化区（单舵机调试场景不受影响）。
        self.arms: Dict[str, ArmConfig] = dict(arms or {})
        self.skeleton = skeleton
        #: 兜底扫描的代数（新扫描开始时 +1，旧后台线程据此自杀）与已完成轮数；
        #: ``_scan_arms`` 是最近一次扫描（任意轮）的机械臂健康快照，随兜底轮更新。
        self._scan_generation = 0
        self._scan_rounds_done = 0
        self._scan_arms: Optional[Dict[str, Any]] = None
        #: 扫描（含兜底轮）进行中 → 主从跟随整拍让路，不读不写也不计失败
        self._scan_busy = False
        #: 每舵机当前 LED 状态（id → (red, green, blue)）。位置指令 ``0x09`` 的
        #: ``d[5]`` 高三位与 LED 颜色位（bit7/6/5 = 红绿蓝）同域，本库下发时
        #: 恒为 0——固件执行位置指令会**顺带把灯清掉**（跟随只发有变化的关节，
        #: 表现为「动了哪个关节哪个的蓝灯灭」）。因此运动后要按此表重发灯色。
        self._led_state: Dict[int, Tuple[bool, bool, bool]] = {}
        #: 主从跟随（平滑限速版）：**采样 / 控制双线程**解耦——
        #: 采样线程低频读主动臂更新期望目标（``_follow_target``），控制线程
        #: 高频做梯形速度规划插值下发（杜绝「目标一跳变就全速扑过去」的
        #: 冲击电流，即过载报警的来源）。``_follow_data_lock`` 保护三张共享表；
        #: ``_follow_last`` 是控制拍上次下发过的值（值没变就不重发）。
        self._follow_thread: Optional[threading.Thread] = None
        self._follow_ctrl_thread: Optional[threading.Thread] = None
        self._follow_stop_evt = threading.Event()
        self._follow_status: Optional[Dict[str, Any]] = None
        self._follow_data_lock = threading.Lock()
        self._follow_target: Dict[int, int] = {}
        self._follow_cur: Dict[int, float] = {}
        self._follow_vel: Dict[int, float] = {}
        self._follow_last: Dict[int, int] = {}
        #: (max_dps, ramp_ms)：控制拍的限速与起步斜坡（`_follow_tick` 用）
        self._follow_tuning: Tuple[float, int] = (float(self.FOLLOW_MAX_DPS),
                                                  int(self.FOLLOW_RAMP_MS))
        #: 主动臂上一拍读到的 ADC（单个关节丢答时沿用，别停掉整拍）
        self._follow_read: Dict[int, int] = {}

    # -- 连接 -------------------------------------------------------------- #

    def connect(self, port: Optional[str] = None, mock: Optional[bool] = None,
                baudrate: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            self.disconnect()
            use_mock = self._mock if mock is None else bool(mock)
            use_port = port or self._port
            use_baud = int(baudrate or self._baudrate)
            if not use_mock and not use_port:
                raise ValueError("请选择串口，或勾选模拟模式")
            # 模拟器默认 load=100（那是给「测试限力路径」用的）；控制台演示改成 0，
            # 否则每个动作都会被默认阈值 60 判为超限——测试语义不等于演示语义。
            inner: Transport = (MockTransport(servo_ids=range(1, 18), load=0)
                                if use_mock
                                else SerialTransport(use_port, baudrate=use_baud))
            robot = RomanboRobot(transport=_LoggingTransport(inner, self.frames),
                                 ack_timeout=self._ack_timeout)
            try:
                robot.open()
            except OSError as exc:
                # 打不开串口是控制台最常见的失败，而两个入口都只打印 ``str(exc)``：
                # 启动时的自动连接，以及页面上点「连接」（前端拼 "连接失败：" + 消息）。
                # 所以把**可照做的处置**拼进消息本身——否则用户只看得到 errno
                # （实测 2026-09-27：页面上只有 "SerialException: [Errno 13] Permission
                # denied"，看不出该去加 dialout 组）。分类依据是 errno，不是异常类型：
                # pyserial 把权限不足也包成 SerialException。
                raise RuntimeError(f"{type(exc).__name__}: {exc}"
                                   f"；{port_error_hint(use_port or '', exc)}") from exc
            self._robot = robot
            self._mock, self._port, self._baudrate = use_mock, use_port, use_baud
            self._online = []
            return self.state()

    def disconnect(self) -> None:
        # **先等跟随线程真正退出**（join）再关串口：只设停止事件是不够的——
        # 线程可能正在一拍的写操作里，串口一关，在途写就撞上已关闭的 fd，
        # pyserial 报 "write failed: [Errno 9] Bad file descriptor"，
        # 跟随会被当成「连续失败 3 次」而自动停止，报错很吓人。
        if self._follow_status and self._follow_status.get("active"):
            self.follow_stop()
        with self._lock:
            self._follow_stop_evt.set()
            if self._follow_status:
                self._follow_status["active"] = False
            if self._robot is not None:
                try:
                    self._set_led(self._online)   # 断开连接 → 灯灭
                    self._robot.close()
                finally:
                    self._robot = None
            self._online = []
            self._led_state = {}                  # 灯已灭，重连后由扫描重新点亮

    def state(self) -> Dict[str, Any]:
        robot = self._robot
        return {
            "connected": bool(robot is not None and robot.is_open),
            "mock": self._mock,
            "port": self._port,
            "baudrate": self._baudrate,
            "online": list(self._online),
            "arms": self._scan_arms,
            "scan_rounds": self._scan_rounds_done,
            "follow": (dict(self._follow_status) if self._follow_status else None),
            "last_error": (robot.last_error if robot is not None else None),
            #: 设备消失（拔插/重枚举）的原因；前端据此提示"请重新连接"
            "lost_reason": (robot.lost_reason if robot is not None else None),
        }

    @staticmethod
    def ports() -> List[Dict[str, Any]]:
        """枚举串口：**已识别的设备排前面**。

        页面用它填端口下拉；Linux 上 pyserial 还会列出 ``ttyS0..31`` 这类没有接硬件的
        占位口（这台机器上 32 个），排在前面的话真适配器要滚半天才找得到。这里**不隐藏**
        （下拉框里得能选到任何端口），只把有身份的排前面——`ports` 命令那边是直接隐藏。
        """
        rows = list_serial_ports()
        return sorted(rows, key=lambda r: (is_anonymous_port(r), str(r["device"])))

    # -- 发现 -------------------------------------------------------------- #

    def scan(self, start: int = P.SERVO_ID_MIN, end: int = P.SERVO_ID_MAX) -> Dict[str, Any]:
        """扫描在线舵机：**第 1 轮同步返回**，其余轮后台兜底。

        单轮可能被总线静默期吃掉应答，所以共扫 `SCAN_ROUNDS`（3）轮取并集；
        但第 1 轮的结果就足够画界面，HTTP 响应在第 1 轮后立即返回
        （``rounds: 1, pending_rounds: 2``），剩余轮在守护线程里补扫并更新
        `state` 的 ``online`` / ``arms`` / ``scan_rounds``——前端轮询 ``/api/state``
        即可拿到兜底后的最终结论。再次扫描会通过代数让旧线程自杀，不会叠罗汉。

        任一**机械臂**的舵机 3 轮都没应答 → 该臂 ``abnormal: true``（前端提示
        「机械臂异常」并给出离线 ID）。模拟模式无静默期，扫描很快。
        """
        robot = self._require()
        with self._lock:
            self._scan_generation += 1
            generation = self._scan_generation
            self._scan_busy = True       # 扫描期间跟随让路（见 _follow_once）
            started = time.perf_counter()
            quarantine = 0.0 if self._mock else P.SCAN_QUARANTINE
            found: set = set(robot.scan(start, end, quarantine=quarantine))
            self._scan_rounds_done = 1
            self._online = sorted(found)
            self._scan_arms = self._arms_health(found)
            self._set_led(found, green=True)   # 扫描成功 → 通信正常亮绿灯
            result = {"found": self._online,
                      "rounds": 1,
                      "total_rounds": self.SCAN_ROUNDS,
                      "pending_rounds": self.SCAN_ROUNDS - 1,
                      "arms": self._scan_arms,
                      "elapsed_s": round(time.perf_counter() - started, 1)}
        if self.SCAN_ROUNDS > 1:
            threading.Thread(target=self._scan_fallback,
                             args=(start, end, found, generation),
                             daemon=True).start()
        else:
            self._scan_busy = False
        return result

    def _arms_health(self, found: Set[int]) -> Dict[str, Any]:
        """按扫描结果计算各机械臂健康状态（离线 ID / 是否异常）。"""
        return {key: {"label": arm.label,
                      "missing": sorted(set(arm.servo_ids) - found),
                      "abnormal": bool(set(arm.servo_ids) - found)}
                for key, arm in self.arms.items()}

    def _scan_fallback(self, start: int, end: int, found: Set[int],
                       generation: int) -> None:
        """后台兜底扫描：把剩余轮的结果并入在线集合并刷新机械臂健康。"""
        try:
            for _ in range(self.SCAN_ROUNDS - 1):
                robot = self._robot
                if generation != self._scan_generation or robot is None:
                    return                      # 有新扫描或已断开 → 放弃旧结果
                quarantine = 0.0 if self._mock else P.SCAN_QUARANTINE
                try:
                    with self._lock:
                        found.update(robot.scan(start, end, quarantine=quarantine))
                        self._scan_rounds_done += 1
                        self._online = sorted(found)
                        self._scan_arms = self._arms_health(found)
                except Exception:
                    return                      # 串口中途断开等：静默放弃兜底
        finally:
            # 只清自己这代的标志：别把更新一代扫描的 busy 一并清掉
            if generation == self._scan_generation:
                self._scan_busy = False

    # -- 读 ---------------------------------------------------------------- #

    def servo_config(self, id_: int) -> Dict[str, Any]:
        with self._lock:
            cfg = self._servo(id_).read_config(timeout=0.25)
            data = cfg.as_dict()
            data["angle"] = (None if cfg.position is None
                             else round(J.adc_to_angle(cfg.position), 2))
            return data

    def servo_live(self, id_: int) -> Dict[str, Any]:
        """轻量读数：只读位置 / 实测负荷 / 温度，供前端高频刷新。

        全部按**单次超时**（``retries=0`` 语义）读：轮询是高频的，等重试既拖慢
        刷新，又会在舵机掉线时长时间占住总线锁、把运动指令一起卡住。要完整参数
        请走 `servo_config`（它才用默认重试）。
        """
        servo = self._servo(id_)
        out: Dict[str, Any] = {"id": int(id_), "position": None, "load": None,
                               "temperature": None}
        # 读操作**不取设备锁**：goto()/multi_move() 会把锁持有整个运动过程，排队会让
        # 界面读数在运动期间整体冻结——而那一刻正是最需要看读数的时候。单次请求的
        # 原子性已由 RomanboRobot 内部的收发锁保证，插在两步之间读取是安全的
        # （与 CLI 的 move --readback 同理）。
        try:
            out["position"] = servo.get_position(timeout=0.2, retries=0)
        except (TimeoutError, P.ProtocolError, RuntimeError) as exc:
            self._note(f"id={id_} 读取位置失败: {exc}")
        out["load"] = self._quick_byte(servo, P.ServoCmd.GET_LOAD)
        out["temperature"] = self._quick_byte(servo, P.ServoCmd.GET_TEMP)
        return out

    def _quick_byte(self, servo: Servo, cmd: int,
                    timeout: float = 0.2) -> Optional[int]:
        """按命令码单次读一个字节值（不回退重试）。

        走 ``robot.request`` 而不是 ``Servo.get_load`` / ``get_temperature``：
        那两个公开方法不暴露 ``retries``，只能吃实例默认的 2 次重试（舵机不回话时
        每次多等约 0.45 s）。GET 类请求就是 6 字节裸帧，数值取数据段首字节，
        与 ``Servo.get_load`` 的解法一致（见 `romanbo.servo`）。
        """
        try:
            resp = servo.robot.request(P.make_frame(cmd, servo.id),
                                       expect_id=servo.id,
                                       expect_cmd=P.expected_ack(cmd),
                                       timeout=timeout, retries=0)
        except (TimeoutError, P.ProtocolError) as exc:
            self._note(f"id={servo.id} 读取 0x{cmd:02X} 失败: {exc}")
            return None
        body = P.payload(resp.data)
        return body[0] if body else None

    def capture(self) -> Dict[str, Any]:
        robot = self._require()
        with self._lock:
            positions = robot.capture(self._online or None)
            return {"positions": positions}

    # -- 机械臂（URDF / TF 可视化）---------------------------------------- #

    def arm_info(self) -> Dict[str, Any]:
        """静态信息：URDF 骨架（前端算 FK 画 TF 树）+ 各臂关节配置。"""
        return {
            "urdf": self.skeleton.as_dict() if self.skeleton else None,
            "arms": {key: {"label": arm.label,
                           "joints": [{"name": j.name, "urdf_joint": j.urdf_joint,
                                       "servo_id": j.servo_id,
                                       "limits_deg": list(j.limits_deg)}
                                      for j in arm.joints.values()]}
                     for key, arm in self.arms.items()},
        }

    #: mesh 文件上限：真实 STL 每个不超过 ~0.7 MB；挡误引用超大文件
    MESH_MAX_BYTES = 32 << 20

    def mesh(self, rel: str) -> bytes:
        """读 URDF 同目录（含子目录）下的 STL，给前端渲染 mesh 用。

        只放行 URDF 所在目录内的 ``.stl``：路径经 ``realpath`` 归一后必须仍落在
        该目录里（挡 ``../`` 逃逸与绝对路径），扩展名白名单再挡一层。
        """
        sk = self.skeleton
        if sk is None or not sk.path:
            raise ValueError("未加载 URDF，mesh 不可用")
        base = os.path.dirname(sk.path)
        full = os.path.realpath(os.path.join(base, str(rel)))
        if not full.startswith(base + os.sep):
            raise ValueError(f"路径不允许：{rel}")
        if os.path.splitext(full)[1].lower() != ".stl":
            raise ValueError(f"只允许 STL 文件：{rel}")
        if not os.path.isfile(full):
            raise FileNotFoundError(f"mesh 不存在：{rel}")
        if os.path.getsize(full) > self.MESH_MAX_BYTES:
            raise ValueError(f"mesh 文件过大：{rel}")
        with open(full, "rb") as fh:
            return fh.read()

    def arm_state(self) -> Dict[str, Any]:
        """各臂实时关节角（URDF 空间，度）。

        与 `servo_live` 同理**不取设备锁**且单次超时不重试：TF 视图是高频轮询，
        运动期间也不该冻结。舵机不在线（未扫描 / 掉线）时该关节 ``angle`` 为
        ``null``，前端用滑块目标角画示意位姿。
        """
        robot = self._robot
        connected = robot is not None and robot.is_open
        online = set(self._online)
        out: Dict[str, Any] = {}
        for key, arm in self.arms.items():
            joints: List[Dict[str, Any]] = []
            seen = False
            for j in arm.joints.values():
                adc = None
                if connected and j.servo_id in online:
                    try:
                        adc = self._servo(j.servo_id).get_position(timeout=0.2,
                                                                   retries=0)
                    except (TimeoutError, P.ProtocolError, RuntimeError) as exc:
                        self._note(f"id={j.servo_id} 读取位置失败: {exc}")
                if adc is not None:
                    seen = True
                joints.append({"name": j.name, "urdf_joint": j.urdf_joint,
                               "servo_id": j.servo_id, "adc": adc,
                               "angle": None if adc is None
                               else round(j.adc_to_deg(adc), 2)})
            out[key] = {"online": seen, "joints": joints}
        return out

    def arm_goto(self, key: str, targets: Mapping[Any, Any], *,
                 speed_dps: Optional[float] = None, max_load: Optional[int] = None,
                 level: Optional[int] = None,
                 load_check_every: int = 1) -> Dict[str, Any]:
        """按 URDF 关节角（度）整臂运动：夹限位 → 转 ADC → 走 `multi_move`。"""
        arm = self._arm(key)
        wanted = arm.targets_from_deg({str(k): float(v)
                                       for k, v in dict(targets).items()})
        if not wanted:
            raise ValueError("没有目标关节")
        return self.multi_move(wanted, speed_dps=speed_dps, max_load=max_load,
                               level=self._arm_levels(arm, level),
                               load_check_every=load_check_every)

    def arm_home(self, key: str, *, speed_dps: Optional[float] = None,
                 max_load: Optional[int] = None, level: Optional[int] = None,
                 load_check_every: int = 1) -> Dict[str, Any]:
        """整臂回到配置文件里的 home 姿态（直接用存储的 ADC，不过限位换算）。"""
        arm = self._arm(key)
        return self.multi_move(arm.home_adcs(), speed_dps=speed_dps,
                               max_load=max_load, level=self._arm_levels(arm, level),
                               load_check_every=load_check_every)

    @staticmethod
    def _arm_levels(arm: ArmConfig,
                    level: Optional[int]) -> Optional[Dict[int, int]]:
        """整臂运动的逐关节档位：**承重关节恒为 H 档**（要悬住臂身自重），
        其余关节用调用方给的档位（不传则库默认 M 档）。"""
        levels: Dict[int, int] = {}
        if level is not None:
            levels = {j.servo_id: int(level) for j in arm.joints.values()}
        levels.update(arm.default_levels())
        return levels or None

    def arm_torque(self, key: str, on: bool = True) -> Dict[str, Any]:
        """整臂力矩使能 / 断开（``0x10``）。

        开启时对**承重关节**（`WEIGHT_BEARING_JOINTS`：shoulder_lift /
        elbow_flex / wrist_flex）额外下发一条 **H 档**位置指令——目标就是
        当前位置（先回读），把「保持当前位姿」的出力档位锁到最大，否则
        M 档保持力扛不住臂身自重。
        """
        arm = self._arm(key)
        robot = self._require()
        enabled: List[str] = []
        high: List[str] = []
        failures: List[str] = []
        with self._lock:
            for name, j in arm.joints.items():
                servo = robot.servo(j.servo_id)
                try:
                    servo.torque(bool(on))
                    if on and name in WEIGHT_BEARING_JOINTS:
                        adc = servo.get_position(timeout=0.25, retries=0)
                        servo.set_position(adc, level=P.LEVEL_HIGH)
                        high.append(name)
                except (TimeoutError, P.ProtocolError, RuntimeError, OSError) as exc:
                    failures.append(f"{name}(ID {j.servo_id})：{exc}")
                    continue
                enabled.append(name)
            if failures:
                raise RuntimeError("；".join(failures))
            # LED 状态灯：使能 → 蓝，断开 → 切回绿（通信正常色）
            self._set_led(arm.servo_ids, blue=bool(on), green=not on)
            return {"on": bool(on), "enabled": enabled, "high": high}

    def _arm(self, key: str) -> ArmConfig:
        arm = self.arms.get(str(key))
        if arm is None:
            raise ValueError(f"未知机械臂 {key!r}（可用：{sorted(self.arms) or '无'}）")
        return arm

    # -- 主从模式 ---------------------------------------------------------- #

    #: 跟随环里读主动臂位置的单次超时（不重试、不占设备锁）。真机应答 10~20 ms，
    #: 但总线被其它读（面板轮询）或兜底扫描交错时可能拖到几十毫秒——100 ms 留足
    #: 余量；真丢答由「沿用上一拍读数」的逐关节容错兜住，超时大一点只是慢不是停。
    FOLLOW_READ_TIMEOUT = 0.1

    #: 整拍全部关节读取失败连续多少次才自动停止。一拍失败约耗时 0.6 s
    #: （6 关节 × 0.1 s 超时），6 次 ≈ 3~4 s **持续**失联才算真断——瞬时抖动、
    #: 总线拥塞都不会误杀跟随。
    FOLLOW_FAILURE_LIMIT = 6

    #: 主动臂**采样**周期默认值（ms）。读 6 关节约占 96 ms 总线（每个 ~16 ms），
    #: 5 Hz 采样 ≈ 48% 总线占用，给控制拍留足余量——没必要实时追踪主动臂。
    FOLLOW_SAMPLE_MS = 200
    #: 从动臂**插值控制**周期默认值（ms，50 Hz）：只在有位移时发帧（SET 类
    #: 无应答，每帧 <1 ms），运动中 6 关节也只占 ~25% 总线。
    FOLLOW_CONTROL_MS = 20
    #: 从动臂关节限速（°/s）：梯形规划的匀速段上限。
    FOLLOW_MAX_DPS = 60.0
    #: 起步/停步斜坡（ms）：0 → 全速的加速时间，即「平滑插值」的陡峭程度。
    FOLLOW_RAMP_MS = 150
    #: 目标死区（ADC，≈0.7°）：吸收主动臂读数抖动，死区内精确贴目标并停发。
    FOLLOW_DEADBAND_ADC = 2

    def follow_start(self, master: str = "leader", follower: str = "follower",
                     *, sample_ms: int = FOLLOW_SAMPLE_MS,
                     control_ms: int = FOLLOW_CONTROL_MS,
                     max_dps: float = FOLLOW_MAX_DPS,
                     ramp_ms: int = FOLLOW_RAMP_MS,
                     interval_ms: Optional[int] = None) -> Dict[str, Any]:
        """**主从跟随**（平滑限速版）：主动臂断力矩示教，从动臂限速平滑跟随。

        启动时：主动臂全部关节 ``0x10`` 断力矩；从动臂走 `arm_torque(on=True)`
        （承重关节额外发 **H 档**保持）。随后**两条线程**各按各的节拍运行：

        - **采样线程**（低频，``sample_ms`` 默认 200 ms ≈ 5 Hz）：读主动臂各
          关节 ADC → URDF 角换算 → 更新期望目标。单个关节丢答沿用上一拍读数
          （记在 ``state.follow.stale``）；兜底扫描期间整拍让路；整拍全失败
          连续 6 次（约 3~4 s 持续失联）才自动停止；
        - **控制线程**（高频，``control_ms`` 默认 20 ms ≈ 50 Hz）：每拍对每个
          关节做一步**梯形速度规划**——平滑斜坡起步（``ramp_ms`` 内 0 →
          ``max_dps``）→ 匀速段（限速）→ 按距离自然减速收尾（``v=√(2·a·d)``），
          起步锚点是从动臂**当前实际位置**（启动时 capture 一次），因此第一段
          运动本身就是一段平滑插值，不会有「使能瞬间猛跳到主动臂姿态」的冲击。
          只有插值输出有变化的关节才发帧（承重关节恒 H 档）。

        ``interval_ms`` 是旧参数（= 采样周期），仅为兼容保留。
        """
        if interval_ms is not None:             # 旧调用方：interval_ms → 采样周期
            sample_ms = interval_ms
        master_arm = self._arm(master)
        follower_arm = self._arm(follower)
        if master_arm.key == follower_arm.key:
            raise ValueError("主动臂和从动臂不能是同一条")
        robot = self._require()
        if self._follow_status and self._follow_status.get("active"):
            raise RuntimeError("主从跟随已在运行，请先停止")
        sample_ms = max(50, int(sample_ms))
        control_ms = max(10, int(control_ms))
        max_dps = min(180.0, max(5.0, float(max_dps)))
        ramp_ms = min(1000, max(20, int(ramp_ms)))
        with self._lock:
            for j in master_arm.joints.values():
                robot.servo(j.servo_id).torque(False)
            self._set_led(master_arm.servo_ids, green=True)  # 主动臂断力矩 → 绿灯
            self.arm_torque(follower_arm.key, True)          # 从动臂使能 → 蓝灯（内置）
            # 起步平滑插值的锚点：从动臂**当前实际位置**（而不是主动臂姿态），
            # 第一段运动 = 一段梯形速度规划的斜坡，消除使能瞬间的跳变冲击
            seeded = robot.capture(follower_arm.servo_ids, timeout=0.2)
            self._follow_stop_evt = threading.Event()
            self._follow_read = {}
            self._follow_last = {}
            with self._follow_data_lock:
                self._follow_target = {}
                self._follow_cur = {sid: float(adc) for sid, adc in seeded.items()}
                self._follow_vel = {}
            self._follow_tuning = (max_dps, ramp_ms)
            self._follow_status = {"active": True, "master": master_arm.key,
                                   "follower": follower_arm.key,
                                   "sample_ms": sample_ms, "control_ms": control_ms,
                                   "max_dps": max_dps, "ramp_ms": ramp_ms,
                                   "cycles": 0, "ticks": 0, "stale": 0,
                                   "moving": 0, "error": None}
            self._follow_thread = threading.Thread(
                target=self._follow_loop, args=(master_arm, follower_arm),
                name=f"follow-{master_arm.key}->{follower_arm.key}", daemon=True)
            self._follow_ctrl_thread = threading.Thread(
                target=self._follow_ctrl_loop, args=(follower_arm,),
                name=f"follow-ctrl->{follower_arm.key}", daemon=True)
            self._follow_thread.start()
            self._follow_ctrl_thread.start()
            return dict(self._follow_status)

    def follow_stop(self) -> Dict[str, Any]:
        """停止主从跟随。从动臂**保持使能**、停在当前位置；主动臂保持断力矩。"""
        status = self._follow_status
        if status is None:
            return {"active": False}
        self._follow_stop_evt.set()
        for thread in (self._follow_thread, self._follow_ctrl_thread):
            if (thread is not None and thread.is_alive()
                    and thread is not threading.current_thread()):
                thread.join(timeout=2.0)
        status["active"] = False
        return dict(status)

    def _follow_loop(self, master_arm: ArmConfig, follower_arm: ArmConfig) -> None:
        """主动臂**采样线程**（低频）：读关节角 → 换算 → 更新期望目标。"""
        status = self._follow_status or {}
        failures = 0
        while not self._follow_stop_evt.is_set():
            started = time.perf_counter()
            try:
                serviced = self._follow_once(master_arm, follower_arm)
            except Exception as exc:            # noqa: BLE001 —— 任何异常都计数，
                failures += 1                   # 不能让未知异常把线程无声杀掉
                status["error"] = str(exc)
                if failures >= self.FOLLOW_FAILURE_LIMIT:
                    status["active"] = False
                    self._follow_stop_evt.set()  # 让控制线程一起退出
                    self._note(f"主从跟随已自动停止：{exc}")
                    return
            else:
                if serviced:                    # 扫描让路的空拍不算成功也不算失败
                    failures = 0
                    status["cycles"] = int(status.get("cycles", 0)) + 1
                    status["error"] = None
            finally:
                status["last_cycle_ms"] = round(
                    (time.perf_counter() - started) * 1000)
            # 绝对时刻排拍：读耗时会在下一拍补回。sample_ms 是**最小**间隔，
            # 一拍的总线耗时超过它时就背靠背续拍
            delay = (status.get("sample_ms", self.FOLLOW_SAMPLE_MS) / 1000.0
                     - (time.perf_counter() - started))
            if delay > 0:
                self._follow_stop_evt.wait(delay)

    def _follow_ctrl_loop(self, follower_arm: ArmConfig) -> None:
        """从动臂**控制线程**（高频）：梯形速度规划插值，限速 + 平滑起停。"""
        status = self._follow_status or {}
        dt = max(0.005, status.get("control_ms", self.FOLLOW_CONTROL_MS) / 1000.0)
        levels = follower_arm.default_levels() or None
        while not self._follow_stop_evt.is_set():
            started = time.perf_counter()
            try:
                self._follow_tick(follower_arm, dt, levels)
            except Exception as exc:            # noqa: BLE001 —— 写失败（串口断开
                status["error"] = str(exc)      # 等）比读失败致命：直接停跟随
                status["active"] = False
                self._follow_stop_evt.set()     # 让采样线程一起退出
                self._note(f"主从跟随已自动停止：{exc}")
                return
            else:
                status["ticks"] = int(status.get("ticks", 0)) + 1
            finally:
                status["last_tick_ms"] = round(
                    (time.perf_counter() - started) * 1000)
            delay = dt - (time.perf_counter() - started)
            if delay > 0:
                self._follow_stop_evt.wait(delay)

    def _follow_once(self, master_arm: ArmConfig,
                     follower_arm: ArmConfig) -> bool:
        """采样一拍：读主动臂（丢答容错）→ 换算 → 更新期望目标（不下发）。

        返回 ``False`` 表示本拍**让路**（扫描占用总线），不计成功也不计失败；
        整拍全部关节读取失败时抛 `RuntimeError` 交给 `_follow_loop` 计数。
        下发交给高频控制线程 `_follow_tick` 做限速平滑插值——采样拍直接写
        目标会产生「跳变全速扑向目标」的冲击电流（过载报警来源），已废弃。
        """
        robot = self._robot
        if robot is None or not robot.is_open:
            raise RuntimeError("串口已断开")
        if self._scan_busy:
            # 兜底扫描要独占总线约 4~5 s：让路而不是陪着超时——否则整拍失败
            # 会把「扫描期间的正常拥塞」误判成失联，把跟随停掉
            return False
        pairs = [(mj, follower_arm.joints[name])
                 for name, mj in master_arm.joints.items()
                 if name in follower_arm.joints]
        if not pairs:
            raise RuntimeError("两条臂没有同名关节，无法映射")
        targets: Dict[int, int] = {}
        read_ok = 0
        for mj, fj in pairs:
            try:
                adc = robot.servo(mj.servo_id).get_position(
                    timeout=self.FOLLOW_READ_TIMEOUT, retries=0)
            except (TimeoutError, P.ProtocolError, RuntimeError, OSError):
                # 单个关节丢答很常见（总线静默期）：沿用上一拍读数，别停整拍
                adc = self._follow_read.get(mj.servo_id)
                if adc is None:
                    continue
            else:
                self._follow_read[mj.servo_id] = adc
                read_ok += 1
            targets[fj.servo_id] = fj.deg_to_adc(fj.clamp(mj.adc_to_deg(adc)))
        status = self._follow_status
        if status is not None:
            status["stale"] = len(pairs) - read_ok   # 有几个关节在用旧值
        if not read_ok:
            raise RuntimeError("主动臂整拍无应答（全部关节读取失败）")
        with self._follow_data_lock:
            self._follow_target.update(targets)
        return True

    def _follow_tick(self, follower_arm: ArmConfig, dt: float,
                     levels: Optional[Mapping[int, int]] = None) -> None:
        """控制一拍：每个关节按**梯形速度规划**向期望目标逼近一步。

        速度曲线 = 斜坡起步（``ramp_ms`` 内 0 → ``max_dps``，平滑插值）→
        匀速段（限速）→ 按距离自然减速收尾（``v = √(2·a·d)``），从根本上
        消除「目标一跳变就全速扑过去」的冲击电流。只有插值输出与上次下发
        不同的关节才发帧；期望目标由采样线程 `_follow_once` 低频更新。
        """
        robot = self._robot
        if robot is None or not robot.is_open:
            raise RuntimeError("串口已断开")
        if self._scan_busy:
            return                              # 兜底扫描独占总线 → 本拍让路
        max_dps, ramp_ms = self._follow_tuning
        vmax = max_dps / J.RATIO_MAIN                        # °/s → ADC/s
        accel = vmax / max(0.02, ramp_ms / 1000.0)           # ADC/s²（起步斜坡）
        out: Dict[int, int] = {}
        moving = 0
        with self._follow_data_lock:
            for sid, target in self._follow_target.items():
                cur = self._follow_cur.get(sid)
                if cur is None:                 # capture 没读到的关节：从目标出发
                    cur = float(target)
                vel = self._follow_vel.get(sid, 0.0)
                dist = target - cur
                if abs(dist) <= self.FOLLOW_DEADBAND_ADC:
                    cur, vel = float(target), 0.0  # 死区内：吸收抖动，精确贴目标
                else:
                    # 期望速度：远处 = 限速；近处 = √(2·a·d) 平滑减速到 0
                    v_des = math.copysign(
                        min(vmax, math.sqrt(2.0 * accel * abs(dist))), dist)
                    step = accel * dt
                    vel += max(-step, min(step, v_des - vel))
                    cur += vel * dt
                    if (dist > 0) == (cur > target):        # 越过目标 → 夹回
                        cur, vel = float(target), 0.0
                self._follow_cur[sid] = cur
                self._follow_vel[sid] = vel
                if vel != 0.0 or abs(target - cur) > self.FOLLOW_DEADBAND_ADC:
                    moving += 1
                rounded = int(round(cur))
                if rounded != self._follow_last.get(sid):
                    out[sid] = rounded
        status = self._follow_status
        if status is not None:
            status["moving"] = moving
        if out:
            with self._lock:
                robot.move(out, level=levels)
            self._led_reassert(out)             # 运动会清灯 → 补发
            self._follow_last.update(out)

    # -- 写 / 运动 --------------------------------------------------------- #

    def goto(self, id_: int, *, adc: Optional[int] = None,
             angle: Optional[float] = None, speed_dps: Optional[float] = None,
             max_load: Optional[int] = None, level: Optional[int] = None,
             load_check_every: int = 1) -> Dict[str, Any]:
        """单关节定位。给了 ``speed_dps`` 走步进逼近，否则直接下发（最大速度）。

        ``load_check_every`` 见 `servo_live`：真机实测**起步第 1 步的冲击可达
        128~147**（加速涌流，不是卡死），而运动中只有约 85。所以要么把阈值放到
        150 以上，要么把检查间隔调大（默认建议 3）跳过起步阶段。
        """
        if adc is None and angle is None:
            raise ValueError("需要 adc 或 angle")
        target = (int(adc) if adc is not None
                  else J.angle_to_adc(float(angle)))          # type: ignore[arg-type]
        target = max(P.ADC_MIN, min(P.ADC_MAX, target))
        if max_load is not None and speed_dps is None:
            raise ValueError("软件限力需要同时给出速度（限力靠运动中轮询负荷）")
        servo = self._servo(id_)
        with self._lock:
            if speed_dps is None:
                servo.set_position(target, level=level)
                self._led_reassert([id_])          # 运动会清灯 → 补发
                return {"id": int(id_), "target": target, "sent": 0,
                        "peak_load": None, "angle": round(J.adc_to_angle(target), 2)}
            sent = servo.move_at_speed(target, float(speed_dps),
                                       max_load=None if max_load is None else int(max_load),
                                       level=level,
                                       load_check_every=max(1, int(load_check_every)))
            self._led_reassert([id_])              # 运动会清灯 → 补发
            return {"id": int(id_), "target": target, "sent": len(sent),
                    "peak_load": servo.last_peak_load,
                    "angle": round(J.adc_to_angle(target), 2)}

    def multi_move(self, targets: Mapping[Any, Any], *,
                   speed_dps: Optional[float] = None,
                   max_load: Optional[int] = None,
                   level: Optional[Union[int, Mapping[int, int]]] = None,
                   load_check_every: int = 1) -> Dict[str, Any]:
        robot = self._require()
        wanted = {int(k): max(P.ADC_MIN, min(P.ADC_MAX, int(v)))
                  for k, v in targets.items()}
        if not wanted:
            raise ValueError("没有目标关节")
        with self._lock:
            if speed_dps is None:
                robot.move(wanted, level=level)
                self._led_reassert(wanted)         # 运动会清灯 → 补发
                return {"targets": wanted, "joints": len(wanted), "peak_load": None}
            start = robot.capture(list(wanted), timeout=0.2)
            robot.move(wanted, speed_dps=float(speed_dps), start=start,
                       max_load=None if max_load is None else int(max_load),
                       level=level,
                       load_check_every=max(1, int(load_check_every)))
            self._led_reassert(wanted)             # 运动会清灯 → 补发
            return {"targets": wanted, "joints": len(wanted),
                    "peak_load": robot.last_peak_load}

    def torque(self, id_: int, on: bool) -> Dict[str, Any]:
        with self._lock:
            self._servo(id_).torque(bool(on))
            self._set_led([id_], blue=bool(on), green=not on)
            return {"id": int(id_), "torque": bool(on)}

    def set_pid(self, id_: int, p: int, i: int, d: int,
                save: bool = True) -> Dict[str, Any]:
        with self._lock:
            servo = self._servo(id_)
            servo.set_pid(int(p), int(i), int(d), save=bool(save))
            return {"id": int(id_), "pid": [int(p), int(i), int(d)], "save": bool(save)}

    def set_limit(self, id_: int, minimum: int, maximum: int) -> Dict[str, Any]:
        lo = max(P.ADC_MIN, min(P.ADC_MAX, int(minimum)))
        hi = max(P.ADC_MIN, min(P.ADC_MAX, int(maximum)))
        if lo > hi:
            raise ValueError(f"下限 {lo} 不能大于上限 {hi}")
        with self._lock:
            servo = self._servo(id_)
            servo.set_position_limit(lo, hi)
            return {"id": int(id_), "position_limit": [lo, hi]}

    def set_margin(self, id_: int, value: int) -> Dict[str, Any]:
        with self._lock:
            self._servo(id_).set_margin(int(value))
            return {"id": int(id_), "margin": int(value)}

    def set_led(self, id_: int, *, red: Optional[bool] = None,
                green: Optional[bool] = None, blue: Optional[bool] = None,
                value: Optional[int] = None) -> Dict[str, Any]:
        """设 LED。给三色（推荐）走 ``set_led_color``，给 ``value`` 走 ``set_led``。

        协议文档只定义了 8 种组合（**无亮度/闪烁**），三色落在 ``d[5]`` 的
        bit7/bit6/bit5 = 红/绿/蓝；``set_led(value)`` 是「编号方式」的另一种写法
        （``value`` 的低 3 位，4/2/1 = 红/绿/蓝）。见 `docs/SERVO_SPEC.md` §5.4。

        返回 ``led`` 含三色与**实际下发的 ``d[5]`` 字节**，便于对着报文日志核对。
        """
        with self._lock:
            servo = self._servo(id_)
            if red is None and green is None and blue is None:
                if value is None:
                    raise ValueError("需要 red / green / blue，或 value")
                raw = int(value) & 0xFF
                servo.set_led(raw)
                rgb = (bool(raw & 4), bool(raw & 2), bool(raw & 1))
            else:
                rgb = (bool(red), bool(green), bool(blue))
                servo.set_led_color(*rgb)
            byte = (0x80 if rgb[0] else 0) | (0x40 if rgb[1] else 0) | (0x20 if rgb[2] else 0)
            return {"id": int(id_), "led": {"red": rgb[0], "green": rgb[1],
                                            "blue": rgb[2], "byte": byte}}

    def calibrate(self, id_: int) -> Dict[str, Any]:
        """把当前位置设为零点（``0x23``）。**会改写舵机内部标定。**"""
        with self._lock:
            self._servo(id_).set_calibration_currpos()
            return {"id": int(id_), "calibrated": True}

    def _set_led(self, ids: Iterable[int], *, red: bool = False,
                 green: bool = False, blue: bool = False) -> None:
        """批量下发舵机 LED 状态指示（发完即走，异常不扩散）。

        约定（与页面提示一致）：**绿 = 通信正常**（扫描成功后）、
        **蓝 = 力矩使能**（断使能切回绿）、**灭 = 断开连接**。
        SET_LED 是 SET 类命令无应答，不占等待；单个失败（如串口刚断）
        只跳过该舵机，不影响其余——状态灯是锦上添花，不能反过来拖垮主流程。
        """
        robot = self._robot
        if robot is None or not robot.is_open:
            return
        for id_ in ids:
            try:
                robot.servo(int(id_)).set_led_color(red, green, blue)
                self._led_state[int(id_)] = (bool(red), bool(green), bool(blue))
            except (TimeoutError, P.ProtocolError, RuntimeError, OSError):
                continue

    def _led_reassert(self, ids: Iterable[int]) -> None:
        """运动后重发受影响舵机的 LED 状态（固件执行位置指令会清灯）。

        见 `_led_state` 的注释：跟随 / 滑块 / 平滑到位下发 ``0x09`` 后，
        被驱动关节的灯会被固件清掉；这里按记录的状态原样补发，
        从动臂跟随中表现为「蓝灯持续亮、仅短暂闪灭」。
        """
        robot = self._robot
        if robot is None or not robot.is_open:
            return
        for id_ in ids:
            rgb = self._led_state.get(int(id_))
            if rgb is None:
                continue                     # 从没设过灯（未扫描过）→ 不补
            try:
                robot.servo(int(id_)).set_led_color(*rgb)
            except (TimeoutError, P.ProtocolError, RuntimeError, OSError):
                continue

    def stop_all(self) -> Dict[str, Any]:
        """紧急停止：对所有在线舵机断开力矩。

        还没扫描过时退化为「向 1..32 广播断电」——SET 类命令无应答，不必等回包，
        32 帧约 64 ms 就能发完（见 `MIN_FRAME_GAP`）。

        !!! warning
            **故意不取设备锁**：`goto()` / `multi_move()` 会把 ``_lock`` 持有整个
            运动过程（慢速长距离可达数十秒），紧急停止若排队等锁，就要等运动自己
            走完才生效——等于安全按钮失效。这里每帧只短暂占用 `RomanboRobot`
            内部的收发锁，因此能**插进运动节拍之间**生效：力矩一断，后续位置指令
            就不再驱动关节。
        """
        robot = self._require()
        ids = list(self._online) or list(range(P.SERVO_ID_MIN, P.SERVO_ID_MAX + 1))
        for id_ in ids:
            robot.send(P.build_set_torque(id_, P.OFF))
        self._set_led(ids, green=True)       # 断力矩 → 切回绿灯
        # 主从跟随一并停掉：只设事件、不 join——紧急停止必须立即返回，
        # 跟随线程会在下一拍发现事件并退出（从动臂已被上面的断力矩断电）
        if self._follow_status and self._follow_status.get("active"):
            self._follow_stop_evt.set()
            self._follow_status["active"] = False
        return {"ids": ids, "broadcast": not self._online}

    # -- 内部 -------------------------------------------------------------- #

    def _require(self) -> RomanboRobot:
        robot = self._robot
        if robot is None or not robot.is_open:
            raise RuntimeError("尚未连接：请选择串口后点「连接」，或勾选模拟模式")
        return robot

    def _servo(self, id_: int) -> Servo:
        id_ = int(id_)
        if not P.SERVO_ID_MIN <= id_ <= P.SERVO_ID_MAX:
            raise ValueError(f"舵机 ID 越界：{id_}（合法范围 "
                             f"{P.SERVO_ID_MIN}..{P.SERVO_ID_MAX}）")
        return self._require().servo(id_)

    def _note(self, text: str) -> None:
        robot = self._robot
        if robot is not None:
            robot.last_error = text


# --------------------------------------------------------------------------- #
# HTTP 层
# --------------------------------------------------------------------------- #

_ROUTE = re.compile(r"^/api/servo/(\d+)(?:/([a-z_]+))?$")
_ARM_ROUTE = re.compile(r"^/api/arm/([a-z_]+)/(goto|home|torque)$")
_MESH_ROUTE = re.compile(r"^/api/mesh/(.+)$")
_HOSTPORT = re.compile(r"^\[([^\]]+)\]|^([^:]+)")


class _Handler(BaseHTTPRequestHandler):
    """JSON 接口 + 单页前端。所有 /api/* 都要令牌。"""

    server_version = "romanbo-webui"
    protocol_version = "HTTP/1.1"

    console: WebConsole
    token: str = ""
    allowed_hosts: Tuple[str, ...] = ()

    # -- 基础设施 ---------------------------------------------------------- #

    def log_message(self, fmt: str, *args: Any) -> None:      # noqa: A003
        """默认会往 stderr 打一行/请求；前端每 0.7 s 轮询一次，必须闭嘴。"""

    def _host_ok(self) -> bool:
        if not self.allowed_hosts:
            return True
        raw = (self.headers.get("Host") or "").strip()
        match = _HOSTPORT.match(raw)
        host = (match.group(1) or match.group(2) or "") if match else ""
        return host in self.allowed_hosts

    def _send(self, status: int, payload: Any, *, ctype: str = "application/json; charset=utf-8") -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:                                                 # pragma: no cover
            body = bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _authorized(self, query: Dict[str, List[str]]) -> bool:
        supplied = self.headers.get("X-Robot-Token") or query.get("token", [""])[0]
        return bool(self.token) and secrets.compare_digest(str(supplied), self.token)

    #: 请求体上限：本机调试接口的任何合法请求都远小于此
    MAX_BODY_BYTES = 1 << 20

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > self.MAX_BODY_BYTES:
            # 不读：否则线程会按声明值阻塞/分配，成为唯一没有上限的入口
            raise ValueError(f"请求体过大：{length} 字节（上限 {self.MAX_BODY_BYTES}）")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc

    # -- 路由 -------------------------------------------------------------- #

    def do_GET(self) -> None:                                 # noqa: N802
        self._route("GET")

    def do_POST(self) -> None:                                # noqa: N802
        self._route("POST")

    def _lost_hint(self) -> str:
        """串口已被判定消失时，给错误消息补一句「重新连接」。

        补这一句是因为否则用户只看到「未应答（超时）」或一个 IO 错误，**不知道为什么要
        重连**——设备被拔插/重枚举后旧句柄还在，`SerialTransport` 会主动把它标记成断开，
        于是故障表现是"读不到数"而不是"连不上"。
        """
        if not self.console.state().get("lost_reason"):
            return ""
        return ("｜串口已断开（设备被拔插或重新枚举）：请在页面上重新连接；"
                "Linux 上可用 /dev/serial/by-id/ 下的稳定路径避免端口号变化")

    def _route(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        if not self._host_ok():
            self._send(403, {"error": "Host 校验失败：请用 http://127.0.0.1 或 "
                                      "http://localhost 访问本控制台"})
            return
        if path == "/" and method == "GET":
            self._send(200, PAGE.replace("__TOKEN__", self.token), ctype="text/html; charset=utf-8")
            return
        if path == "/favicon.ico":
            self._send(204, b"")
            return
        if not path.startswith("/api/"):
            self._send(404, {"error": f"未知路径 {path}"})
            return
        if not self._authorized(query):
            self._send(401, {"error": "缺少或错误的访问令牌：请用终端里打印的完整链接打开"})
            return

        try:
            body = self._read_body() if method == "POST" else {}
            status, payload = self._dispatch(method, path, query, body)
        except LoadLimitExceeded as exc:
            self._send(409, {"error": f"软件限力触发：实测负荷 {exc.load} 超过阈值 {exc.limit}"
                                      f"（第 {exc.step} 步，停在位置 {exc.position}）",
                             "aborted": True, "detail": exc.as_dict()})
            return
        except TimeoutError as exc:
            self._send(504, {"error": f"舵机未应答（超时）：{exc}" + self._lost_hint()})
            return
        except P.ProtocolError as exc:
            self._send(400, {"error": f"协议错误：{exc}" + self._lost_hint()})
            return
        except OSError as exc:
            # 读写失败（串口断线、设备被拔）：不是服务端故障，标成 409，
            # 并把"请重新连接"的提示带上。
            self._send(409, {"error": f"{type(exc).__name__}: {exc}" + self._lost_hint()})
            return
        except (ValueError, RuntimeError) as exc:
            self._send(409, {"error": str(exc) + self._lost_hint()})
            return
        except Exception as exc:                              # noqa: BLE001
            self._send(500, {"error": f"{type(exc).__name__}: {exc}" + self._lost_hint()})
            return
        self._send(status, payload,
                   ctype="application/octet-stream"
                   if isinstance(payload, (bytes, bytearray))
                   else "application/json; charset=utf-8")

    def _dispatch(self, method: str, path: str, query: Dict[str, List[str]],
                  body: Any) -> Tuple[int, Any]:
        console = self.console
        if method == "GET":
            if path == "/api/state":
                return 200, console.state()
            if path == "/api/ports":
                return 200, console.ports()
            if path == "/api/frames":
                since = int((query.get("since") or ["0"])[0] or 0)
                frames, latest = console.frames.since(since)
                return 200, {"frames": frames, "latest": latest}
            if path == "/api/capture":
                return 200, console.capture()
            if path == "/api/arm/info":
                return 200, console.arm_info()
            if path == "/api/arm/state":
                return 200, console.arm_state()
            mesh_match = _MESH_ROUTE.match(path)
            if mesh_match:
                return 200, console.mesh(mesh_match.group(1))
            match = _ROUTE.match(path)
            if match:
                id_, action = int(match.group(1)), match.group(2)
                if action is None:
                    return 200, console.servo_config(id_)
                if action == "live":
                    return 200, console.servo_live(id_)
            raise ValueError(f"未知路径 {path}")

        if path == "/api/connect":
            return 200, console.connect(body.get("port"), body.get("mock"),
                                        body.get("baudrate"))
        if path == "/api/disconnect":
            console.disconnect()
            return 200, console.state()
        if path == "/api/scan":
            return 200, console.scan(int(body.get("start", P.SERVO_ID_MIN)),
                                     int(body.get("end", P.SERVO_ID_MAX)))
        if path == "/api/capture":
            return 200, console.capture()
        if path == "/api/move":
            return 200, console.multi_move(body.get("targets") or {},
                                           speed_dps=body.get("speed_dps"),
                                           max_load=body.get("max_load"),
                                           level=body.get("level"),
                                           load_check_every=body.get("load_check_every", 1))
        if path == "/api/stop_all":
            return 200, console.stop_all()
        if path == "/api/follow/start":
            legacy = body.get("interval_ms")    # 旧前端：interval_ms = 采样周期
            return 200, console.follow_start(
                str(body.get("master") or "leader"),
                str(body.get("follower") or "follower"),
                sample_ms=int(body.get("sample_ms",
                                       legacy if legacy is not None else 200)),
                control_ms=int(body.get("control_ms", 20)),
                max_dps=float(body.get("max_dps", 60)),
                ramp_ms=int(body.get("ramp_ms", 150)))
        if path == "/api/follow/stop":
            return 200, console.follow_stop()
        arm_match = _ARM_ROUTE.match(path)
        if arm_match:
            key, action = arm_match.group(1), arm_match.group(2)
            if action == "torque":
                return 200, console.arm_torque(key, bool(body.get("on", True)))
            kw: Dict[str, Any] = {"speed_dps": body.get("speed_dps"),
                                  "max_load": body.get("max_load"),
                                  "level": body.get("level"),
                                  "load_check_every": body.get("load_check_every", 1)}
            if action == "goto":
                return 200, console.arm_goto(key, body.get("targets") or {}, **kw)
            return 200, console.arm_home(key, **kw)
        match = _ROUTE.match(path)
        if match:
            id_, action = int(match.group(1)), match.group(2)
            if action == "goto":
                return 200, console.goto(id_, adc=body.get("adc"), angle=body.get("angle"),
                                         speed_dps=body.get("speed_dps"),
                                         max_load=body.get("max_load"),
                                         level=body.get("level"),
                                         load_check_every=body.get("load_check_every", 1))
            if action == "torque":
                return 200, console.torque(id_, bool(body.get("on", True)))
            if action == "pid":
                return 200, console.set_pid(id_, body.get("p", 0), body.get("i", 0),
                                            body.get("d", 0), body.get("save", True))
            if action == "limit":
                return 200, console.set_limit(id_, body.get("min", P.ADC_MIN),
                                              body.get("max", P.ADC_MAX))
            if action == "margin":
                return 200, console.set_margin(id_, body.get("value", 0))
            if action == "led":
                return 200, console.set_led(id_, red=body.get("red"),
                                            green=body.get("green"),
                                            blue=body.get("blue"),
                                            value=body.get("value"))
            if action == "calib":
                return 200, console.calibrate(id_)
        raise ValueError(f"未知路径 {path}")


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #

def _allowed_hosts(host: str) -> Tuple[str, ...]:
    """绑定回环时做 Host 白名单（防 DNS rebinding）；绑别的地址时无法校验。"""
    if host in ("127.0.0.1", "localhost", "::1"):
        return ("127.0.0.1", "localhost", "::1")
    return ()


def make_server(host: str, port: int, console: WebConsole,
                token: str) -> ThreadingHTTPServer:
    """建好 HTTP 服务（端口传 0 表示由系统分配，测试用）。"""

    class _Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    handler = type("_BoundHandler", (_Handler,), {
        "console": console,
        "token": token,
        "allowed_hosts": _allowed_hosts(host),
    })
    return _Server((host, port), handler)                     # type: ignore[arg-type]


def serve(*, host: str = "127.0.0.1", port: int = 8765,
          serial_port: Optional[str] = None, mock: bool = False,
          baudrate: int = P.BAUDRATE_DEFAULT,
          ack_timeout: float = P.DEFAULT_ACK_TIMEOUT,
          token: Optional[str] = None, open_browser: bool = False,
          urdf_path: Optional[str] = None, config_dir: Optional[str] = None,
          printer: Callable[[str], None] = print) -> int:
    """启动控制台并阻塞到 Ctrl+C。返回进程退出码。"""
    skeleton, arms = discover(urdf_path, config_dir)
    console = WebConsole(port=serial_port, mock=mock, baudrate=baudrate,
                         ack_timeout=ack_timeout, arms=arms, skeleton=skeleton)
    token = token or secrets.token_urlsafe(16)
    httpd = make_server(host, port, console, token)
    actual = httpd.server_address[1]
    shown = "127.0.0.1" if host in ("", "0.0.0.0") else host
    url = f"http://{shown}:{actual}/?token={token}"

    if arms or skeleton:
        arm_text = "、".join(f"{key}（{arm.label}）" for key, arm in arms.items())
        printer("机械臂：" + (arm_text or "（无配置）") +
                ("；URDF 已加载，页面提供 TF 树 + mesh 3D 可视化" if skeleton
                 else "；未找到 URDF，TF 树不可用"))
    else:
        printer("未发现机械臂配置 / URDF（TF 可视化关闭）；"
                "在含 urdf/ 与 config/ 的目录启动可自动加载")

    if mock or serial_port:
        try:
            console.connect()
            printer(f"已自动连接：{'模拟器' if mock else serial_port}")
            if mock:
                found = console.scan()["found"]
                printer(f"模拟器在线舵机：{found}")
        except Exception as exc:                              # noqa: BLE001
            printer(f"自动连接失败（可在页面上再连）：{exc}")

    printer("")
    printer("ROMANBO 舵机控制台已启动，请用浏览器打开：")
    printer(f"  {url}")
    if not _allowed_hosts(host):
        printer(f"注意：已监听 {host}，非回环地址下 Host 校验自动失效，"
                f"令牌是唯一防线，请勿暴露到公网。")
    printer("提示：令牌只用于本机保护，别把这条链接发给别人；Ctrl+C 停止。")
    printer("")
    if open_browser:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        printer("\n已停止控制台")
    finally:
        httpd.server_close()
        console.disconnect()
    return 0
