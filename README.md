# ROMANBO 舵机 / 整机控制库（Python SDK）

[![CI](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml/badge.svg)](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Typing: py.typed](https://img.shields.io/badge/typing-py.typed-blue)](https://peps.python.org/pep-0561/)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-000000.svg)](https://github.com/astral-sh/ruff)

面向 **ROMANBO** 舵机型号的 RS485 总线控制 SDK，完整实现舵机与整机控制协议，
并在**真机**上逐条验证过（验证环境：两个 MOS 系列舵机串联，ID 8 / ID 10，COM3，2026-09-25）。

**文档中每一条结论都区分「协议文档定义」「真机实测」「未验证」**，未验证项绝不写成已验证。

---

## 目录

- [1. 环境与安装](#1-环境与安装)
- [2. 30 秒上手](#2-30-秒上手)
- [3. 命令行参考](#3-命令行参考)
- [4. Python API](#4-python-api)
- [5. 协议要点](#5-协议要点)
- [6. 真机实测结论（**重要**）](#6-真机实测结论重要)
- [7. `.rsc` 工程文件](#7-rsc-工程文件)
- [8. 软件限力](#8-软件限力)
- [9. 测试与自检](#9-测试与自检)
- [10. 安全与已知限制](#10-安全与已知限制)
- [11. 目录结构](#11-目录结构)

---

## 1. 环境与安装

- **Python 3.8+**（与 `requirements.txt` 一致。包内模块全部使用
  `from __future__ import annotations`，因此 `list[int]` 这类写法不在运行期求值；
  运行期用到的容器类型一律走 `typing.*`。开发/实测环境 3.14.3）
- **pyserial**（`>=3.5,<4.0`）：只有使用**真实串口**时才需要，且在打开端口时才惰性导入。
  未安装时 `--mock`、`selftest`、`info` 等离线功能仍可正常使用。

```bash
pip install pyserial
```

**三种用法都验证过**（均在仓库根目录执行）：

```bash
# A. 直接复制目录：把 romanbo/ 拷进你的项目即可（已实测：脱离本仓库可用，
#    包括用副本直接驱动真机）
# B. 本地可编辑安装（开发用）
pip install -e .                     # 需要开发工具时：pip install -e ".[dev]"
# C. 构建 wheel（分发用；产物含控制台脚本 romanbo）
python -m pip wheel . --no-deps -w dist
#    验证产物：romanbo-1.0.0-py3-none-any.whl 内含 10 个模块 + py.typed + entry_points.txt
```

也可不安装，直接在仓库根目录以模块方式运行：

```bash
python -m romanbo selftest          # 离线自检
```

### Ubuntu / Linux 上直接用

代码本身**跨平台**（只依赖 pyserial + 标准库，无 Windows API、无平台分支逻辑），
Linux 上只需注意「端口名 / 权限 / 共享打开」三点：

```bash
sudo apt update && sudo apt install -y python3-pip
pip3 install pyserial
sudo usermod -aG dialout $USER          # 串口权限；执行后需重新登录

python3 -m romanbo ports                # 列出设备，确认名字（/dev/ttyUSB0 或 /dev/ttyACM0）
python3 -m romanbo --port /dev/ttyUSB0 scan
python3 -m romanbo --port /dev/ttyUSB0 play my.rsc --ids 8,10 --speed 15
```

| 差异点 | Windows | Linux / macOS |
|---|---|---|
| 端口名 | `COM3` | `/dev/ttyUSB0`（FTDI）/ `/dev/ttyACM0`（CDC）/ `/dev/tty.usbserial-XXXX` |
| 权限 | 一般无需设置 | 需要 `dialout` 组（否则 `PermissionError`） |
| 共享性 | 打开即独占 | **默认可多进程同时打开** → 本库用 `exclusive=True`（`TIOCEXCL`）排他 |
| 可能被抢占 | 其它上位机 / 串口助手 | 其它上位机 / ModemManager（`sudo systemctl stop ModemManager`） |
| 定时精度 | `time.sleep` ≈ 15 ms 抖动 | 更好，步进逼近的角速度更准 |

**并发边界**：`RomanboRobot` 内部的「发—等」过程由可重入锁（`_io_lock`）串行化，
所以**同一实例可以跨线程调用**（例如一个线程只读位置、另一个线程做短动作）；
但**不要用多个线程同时驱动动作**（`play`/`move` 会逐帧交错，动作会被打乱）。
总线本身是串行的，多线程不会提高吞吐。

---

## 2. 30 秒上手

```bash
# 离线：校验 60 条基准报文（逐字节比对）
python -m romanbo selftest

# 离线：无硬件跑通全流程
python -m romanbo --mock scan
python -m romanbo --mock --json read --ids 1,2

# 真机：扫描在线舵机（探测 0.15 s；探测失败后等 0.4 s 总线静默期）
python -m romanbo --port COM3 scan --start 1 --end 32

# 读位置 / 读全部参数
python -m romanbo --port COM3 read --ids 8,10
python -m romanbo --port COM3 --json config --ids 8,10

# 看实测负荷（静止为 0，运动中跳动）
python -m romanbo --port COM3 load --ids 8 --watch 10 --interval 0.1

# 以 60 °/s 转 +15°，并开启软件限力（阈值 60）
python -m romanbo --port COM3 jog --id 8 --degrees 15 --speed 60 --max-load 60

# 播放 .rsc 动作（以 60 °/s 重放关键帧；examples/data/demo.rsc 是随仓库样例）
python -m romanbo --port COM3 play examples/data/demo.rsc --speed 60

# 查看工程摘要（离线）
python -m romanbo info examples/data/demo.rsc
```

**全局选项**：`-p/--port`、`--mock`、`--baudrate`、`--timeout`（回包超时秒）、`--json`、`--frames`（打印收发原始帧）。

---

## 3. 命令行参考

| 命令 | 说明与主要选项 |
|---|---|
| `selftest` | 离线校验报文编码（黄金向量，60 条） |
| `handshake` | 连接自检（型号 + 固件版本，需控制器板） |
| `scan` | `--start --end --probe-timeout`（默认 0.15 s）、`--quarantine`（探测失败后的总线静默期，默认 0.4 s） |
| `read` | `--ids 8,10`（ADC 与角度） |
| `teach` | `--ids`：批量回读位置（对应「读取位置」）；加 `--file taught.json` 则把当前姿态**追加为一帧**，多次执行累积成动作序列 |
| `export` | `--file taught.json --out taught.rsc`：把示教会话导出为**可播放的 `.rsc` 工程文件**（离线）；配合 `play --ids ...` 即可回放 |
| `config` | `--id/--ids`：位置、PID、限值、**load（实测负荷）**、acceleration、margin、温度、零点 |
| `load` | `--id/--ids --watch N --interval 秒`：实测负荷采样 |
| `jog` | `--id --degrees 度 \| --delta ADC`，速度用 `--speed 度/秒` 或 `--period ms`，可加 `--max-load` |
| `angle` | `--id --degrees`（绝对角度，中点 512 = 0°），速度同上 |
| `sweep` | `--id --degrees/--low/--high --cycles`，速度同上；`--no-readback --no-return --loop` |
| `move` | `--targets 8:600,10:480`；`--speed`（角速度）/`--period`；`--max-load`；`--no-capture` |
| `torque` | `on\|off` `--ids`：力矩使能开关 |
| `led` | `--id/--ids --value N` 或 `--color 1,0,1` |
| `pid` | `--id --p --i --d [--nosave]`：写入后**回读确认**，失败退出码 1 |
| `limit` | `--id --min --max` 位置限值（掉电保存；写入后**回读确认**，失败退出码 1） |
| `param` | `current-limit \| margin \| temp \| offset \| period \| accelerate --value N` |
| `wheel` | `--id --speed 0..255 [--ccw] [--free] [--relative]` |
| `sync` | `--id`（默认 254 广播）发同步触发 |
| `calib` | 把当前位置设为零点（`0x23`） |
| `set-id` / `reset` | 改 ID / 复位 |
| `play` | `文件 [--scene N] [--ids 8,10] [--loop] [--speed 度每秒] [--speed-scale 周期倍率] [--no-capture] [--max-load] [--torque on/off]`；`--ids` 只驱动在线关节（整机文件务必指定，否则向不存在的 ID 发帧会触发 0.4 s 总线静默期） |
| `info` | 打印 `.rsc` 工程摘要（离线） |

退出码：`0` 正常；**`4` 表示因软件限力（`--max-load`）中止**；`130` 用户中断。

---

## 4. Python API

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:          # 或 connect("COM3", mock=True)
    print(robot.scan(1, 32))                 # 在线 ID
    s = robot.servo(8)
    print(s.get_position(), s.angle())       # ADC / 角度
    print(s.get_load())                      # 实测负荷（0x18）

    s.torque(True)                           # 力矩使能
    s.rotate(15, speed_dps=60, current=None) # 以 60 °/s 相对转 15°
    try:
        s.move_at_speed(1023, 120, max_load=60)      # 120 °/s + 软件限力
    except LoadLimitExceeded as exc:
        print(exc.as_dict())                 # {id, load, limit, step, position}

    robot.move({8: 600, 10: 480}, speed_dps=60, start=robot.capture([8, 10]))
    robot.play_rsc("examples/data/demo.rsc", speed_dps=60, max_load=60)
    print(s.read_config().as_dict())          # 含 load / acceleration
```

**主要类**

| 类 | 位置 | 职责 |
|---|---|---|
| `RomanboRobot` | `romanbo/robot.py` | 连接、扫描、多关节同步动作、示教回读、`.rsc` 播放、控制器兼容接口 |
| `Servo` | `romanbo/servo.py` | 单个舵机的全部命令（位置/角度/角速度/轮子/扭矩/周期/PID/限值/负荷/LED/校准/出厂测试/回读） |
| `ServoConfig` | `romanbo/servo.py` | 一次读回的全部参数（字段 `load` 为实测负荷，`current_limit` 为兼容别名） |
| `LoadLimitExceeded` | `romanbo/servo.py` | 软件限力触发（`.as_dict()` 便于上报） |
| `RscProject` / `MotionFrame` | `romanbo/rsc.py` | `.rsc` 工程解析（含重复键兼容） |
| `joints` | `romanbo/joints.py` | ADC↔角度换算、机型通道表、`plan_move`/`pick_step_interval` |
| `protocol` | `romanbo/protocol.py` | 帧编解码、命令码、校验、回包解析 |
| `transport` | `romanbo/transport.py` | `SerialTransport`（pyserial）与 `MockTransport`（离线模拟） |

**与力矩/限力相关的关键接口**

```python
s.torque(True/False)                    # 0x10 力矩使能（真机有效）
s.get_load()                            # 0x18 实测负荷（不是限制值！）
s.set_load_limit(200)                   # 0x0D「负荷」上限（写入无法回读，效果未验证）
s.set_pid(100, 1, 20)                   # 先 0x47（立即生效）再 0x07（持久化）+ 回读确认
s.set_pid(50, 0, 5, save=False)         # 只写 RAM（实测唯一可靠的写入方式）
s.set_accelerate(20)                    # 0x0E 加速度：实测该固件无效（仅存档）
s.move_at_speed(target, dps, max_load=60, load_check_every=1)
robot.move(targets, speed_dps=60, start={...}, max_load=60)
robot.play(frames, speed_dps=60, start_positions={...}, max_load=60)
s.last_peak_load                        # 最近一次带限力运动的峰值负荷
```

---

## 5. 协议要点

> 字节级逐命令参考（请求/回包布局、实例报文、参数语义、未验证项清单）见
> **[`SERVO_SPEC.md`](SERVO_SPEC.md)**；本节只给要点。

### 帧格式（收发同构）

```
FF FF | ID | LEN | CMD | DATA ... | CHK
```

- `LEN` = **整帧字节数**（最短 6）；数据段长度 = `LEN - 6`
- `CHK`：`sum(整帧) & 0xFF == 0`，即 `chk = (~sum(frame[:-1]) + 1) & 0xFF`
- 例：`FF FF 01 08 09 0A 00 E6` → 舵机 1，8 字节，`0x09` 位置指令
- `GetModel`/`GetVersion` 两个查询用 `LEN=0`、`ID=0`（发给控制器）

### 串口与地址

- **115200 8N1**，`DtrEnable=true`、`RtsEnable=true`（另有 57600 备选档）
- 地址分配：`0` = 控制器板；`1..32` = 舵机；`224..253` = 传感器；`254` = 广播
- USB 侧为 FTDI（`driver/CDM21224_Setup.exe`），上位机用普通串口 API

### 舵机命令码（`ServoCmd`）

| CMD | 名称 | 数据段 | 真机状态 |
|---|---|---|---|
| 0x01 | GetModel / **Reset** | — | 查询 ID0 帧无应答；Reset 未验证 |
| 0x02 | GetVersion / **SetReboot** | — | 同上 |
| 0x05 | Status | — | ✅ 应答 `00` |
| 0x06 | SetID | `d[5]=新ID`（帧 ID 用原 ID） | 未验证 |
| 0x07 / 0x47 | SetPID / **不保存** | `P,I,D`（LEN=9） | ⚠️ `0x07` 3 次仅 1 次生效；**`0x47` 3/3 立即生效** → 库先发 `0x47` 再补 `0x07` |
| 0x08 | SetTemp（温度阈值） | `d[5]=阈值` | 无法回读（`0x13` 读的是实测温度） |
| 0x09 | **SetPosition** | `d[5]=(pos>>8)\|(力度档位<<3)\|(relative<<2)`，`d[6]=pos&0xFF`；档位 = **H0/M1/L2/W3**（`d[5]` bit3-4） | ✅ 实测可动；**档位有效**（H226>M152>L118，W 不动） |
| 0x09 | **SetWheel**（同码） | `d[5]=(torque<<3)+(relative<<2)+(free<<1)+dir`，`d[6]=速度` | 未逐个验证 |
| 0x0A | SetOffSet | `d[5]=偏移` | 未验证 |
| 0x0B | SetPeriod | 16 位大端 ms | ⚠️ **固件忽略**（见 6.1） |
| 0x0C | SetMargin（=Deadzone） | `d[5]=margin` | ✅ 写入立即回读（5→7） |
| 0x0D | SetCurrentLimit（界面「负荷」） | `d[5]=值` | ⚠️ 写入后 `0x18` 无变化，限流效果未验证 |
| 0x0E | **SetAccelerate** | `d[5]=值` | ❌ **实测无效果**（0 vs 200 的到位时间/轨迹一致；`0x19` 不可回读） |
| 0x0F | SetPositionLimit | `min,max` 各 16 位大端（LEN=10） | ✅ 可经 `0x1A` 回读 |
| 0x10 | **SetTorque** | `d[5]=0/1` | ✅ 真机有效（关后位置指令不动） |
| 0x11 | SetLED | 4 参版 `d[5]=data<<5`；6 参版 `((r?8)+(g?4)+(b?2))<<4` | ✅ 颜色位：bit7=红、bit6=绿、bit5=蓝（协议文档 + 逐字节交叉校验，见 [`SERVO_SPEC.md`](SERVO_SPEC.md) §5.4） |
| 0x12~0x1A | GetPID/Temp/Position/Calibration/Motor/Margin/**Load**/Accelerate/PositionLimit | — | 见 [6.5 支持矩阵](#65-查询命令支持矩阵) |
| 0x20 | SetSync | — | ❌ 不响应 |
| 0x21 | SetNextPosition / SetNextWheel | 位域见下 | ❌ 不响应 |
| 0x22 | SetBaudrate | `d[5]=档位` | 未验证（协议文档也未定义发送） |
| 0x23 | SetCalibrationCurrpos | — | 未验证（会改零点） |
| 0x79/0x7A/0x7B | 出厂测试 启动/设置/读取 | 16 位大端 | 读取 ✅ `00 06 0E` |

`0x21 SetNextPosition` 位域（协议位域，但真机不响应）：

```
bit = 1 if position < 512 else 0
torque < 3 : v = (ledkind<<13) | (torque<<11) | (relative<<10) | position
torque >= 3: v = (ledkind<<13) | (torque<<11) | (relative<<10) | (freewheel<<9) | (bit<<8) | abs(angle)
```

### 应答（`ServoAck`）

- 实测**只有 GET 类与 `Status` 会应答**，且 `ACK = 0x80 | CMD`（0x14→0x94，0x18→0x98…）
- **所有 SET 类命令都不应答**——`set_*` 默认「只发不等」，要确认结果请回读
- 协议常量表里还有一批顺序编码的 ACK（0x83~0x8D），在本机固件上从未出现，**不要依赖它们做同步**
- 数据段首字节恒为 `0x00`（状态位），数值从第 2 字节开始；错误帧 `CMD=0x80`

### 控制器命令（未验证）

`CtrlCmd`：`0x03` 状态、`0x04/0x05/0x06` 下载相关、`0x07` Play、`0x08` Sequence、`0x0A` SetLED、
`0x10` SetTorque、`0x14` GetPosition、广播 ID 254 的 `SetOrder`。
本机没有控制器板，这些接口**未在真机验证**（`Run`/`Stop` 未定义行为）。

---

## 6. 真机实测结论（**重要**）

> 验证环境：两个舵机（ID 8 / ID 10）串联挂在 COM3，2026-09-25。

### 6.1 `SET_PERIOD(0x0B)` 被固件忽略 → 角速度必须"步进逼近"

同一个 44° 位移，命令不同周期，实测完成时间几乎相同：

| 命令周期 | 完成时刻 | 实测角速度 |
|---|---|---|
| 8000 ms | 0.24 s | ~180 °/s |
| 3000 ms | 0.24 s | ~183 °/s |
| 60 ms | 0.239 s | ~184 °/s |
| **不发周期** | ~0.3 s | ~150 °/s |

结论：舵机**总是以自身最大速度（约 150~180 °/s）走完**，"周期 = 走完时间"不成立。
因此本库用**步进逼近**实现角速度：每 `interval_ms`（默认 100 ms）下发一个中间目标，
使平均角速度 = 期望值（`joints.plan_move`）。真机验证：

| 命令角速度 | 位移 | 实测角速度 | 误差 |
|---|---|---|---|
| 60 °/s | 88° | 57.6 °/s | −4% |
| 15 °/s | 44° | 14.4 °/s | −4% |

> 复核：`jog --id 8 --degrees 15 --speed 60` → 发出周期序列每 100 ms 一步，
> 例：`512 → 532 → 553 → 573 → 594 → 614`。
> 量化误差约 ±2%（步数取整），实测叠加采样偏差后约 4~10%。

### 6.2 `0x21 SET_NEXT_POSITION` / `0x20 SET_SYNC` 不响应

三种变体全部无动作、无应答：规范帧 + 广播 254、原始大端 + 逐 ID、先同步再预置。
对照：同一时刻发 `0x09 SET_POSITION` **立刻生效**。
→ 多关节同步请用 `mode="position"`（本库默认）：逐关节 `SET_PERIOD`+`SET_POSITION`。
**帧间隔是前提**：实测两帧之间无间隔时**后发的那一帧会被静默丢弃**（见 6.8），
所以本库在串口写口强制 `MIN_FRAME_GAP = 2 ms`；17 关节单拍因此增加约 34 ms，
起点偏差 < 40 ms，对百毫秒级节拍仍可视为同步。

### 6.3 `0x18` 是**实测负荷**，不是限制值

| 场景 | `0x18` 读数 |
|---|---|
| 静止 | `0` |
| 运动中 | `120 → 32 → 2 → 28 → 0`（随加/减速跳动） |
| 顶住位置限值 | `0`（到限位后**不再持续出力**） |

这是本硬件上唯一能反映**输出力矩大小**的读数。界面文字「负荷」即此项。
（`GET_CURRENT_LIMIT` 是历史命名，本库已用 `get_load()` 作为主名。）

### 6.4 力矩：**有使能开关，也有高/中/低档位**

| 能力 | 报文 | 实测 |
|---|---|---|
| 力矩使能 | `0x10` = 0/1 | ✅ 置 0 后位置指令**完全不动**；置 1 立刻能动 |
| `0x10` 传 2/3/255 | —— | ❌ 全部等同于 ON（该命令只认布尔） |
| **出力档位 H/M/L/W** | **`0x09` 的 `d[5]` bit3-4** = 0/1/2/3 | ✅ **实测有效**：同起点/同方向/同幅度各 3 次，峰值负荷中位数 **H 226 > M 152 > L 118**；W(3) 位置指令**不生效** |
| 目标力矩/电流闭环 | 协议中不存在 | ❌ |
| 负荷上限 | `0x0D` | ⚠️ 写入 8/200 后 `0x18` 无变化（对照 `0x0C` 立即可回读）→ 限流效果未验证 |
| 温度 | `0x13` | ✅ 实测温度（写 `0x08` 阈值不影响读数） |

出力档位对应关节右键「扭矩设置」的 High/Middle/Low，
取值 0..3（显示 ` H`/` M`/` L`/`W`），并**直接作为 `SetPosition` 的档位实参**下发。
**它不是使能**——档位 0 照样能运动。用法：`--level H|M|L|W` 或 API 的 `level=`。

`.rsc` 里的相关字段是每帧每通道的 `TorqueValue`（键名固定为 `"Torque"`，值 0/1，
663 条记录中 0 出现 488 次、1 出现 175 次）——即"该帧该关节是否通电"。

### 6.5 查询命令支持矩阵

| 命令 | ID 8 回包 | ID 10 回包 | 结论 |
|---|---|---|---|
| `0x05` Status | `00` | `00` | ✅ |
| `0x12` GetPID | `00 64 01 14` | `00 32 00 05` | ✅ P/I/D |
| `0x13` GetTemp | `00 20`（32 ℃） | `00 24`（36 ℃） | ✅ |
| `0x14` GetPosition | `00 03 F4`（1012） | `00 02 31`（561） | ✅ |
| `0x15` GetCalibration | `00 74`（116） | `00 74` | ✅ 偶发丢包，重试可解 |
| `0x16` GetMotor | `00 01` | `00 00` | ✅ 有应答，语义未定（扭矩/运动/空转均不变） |
| `0x17` GetMargin | `00 05` | `00 05` | ✅ |
| `0x18` GetLoad | `00 00` | `00 00` | ✅ 实测负荷 |
| `0x19` GetAccelerate | 超时 | 超时 | ❌ 固件未实现 |
| `0x1A` GetPositionLimit | `00 00 01 03 FF`（1,1023） | `00 00 FF 03 00`（255,768）← **被无数据 `0x0F` 误写，见下** | ✅ |
| `0x7B` GetFactoryTest | `00 06 0E` | `00 06 0E` | ✅ |
| `0x01/0x02`（ID0 帧） | 超时 | 超时 | ❌ 是控制器查询，无控制器板时不回复 |
| `0x0F` GetMotionPeriod | 无应答 | 无应答 | ❌ **禁止发送**：无数据帧也会把缓冲残值写成 min/max（实测 `(1,1023)`→`(113,257)`） |

### 6.6 应答延迟与**总线静默期**（曾长期被误判）

逐命令实测（ID 8，每个命令 6 次取中位）：

| 命令 | 最小 | 中位 | 最大 |
|---|---|---|---|
| Status / GetPosition / GetLoad / GetMargin / GetPID / GetTemp / GetMotor / GetPositionLimit / GetCalibration / GetFactoryTest | 7.5 ms | **约 10 ms** | 12.7 ms |

即**应答本身只要约 10~20 ms**。真正会让人觉得"很慢"的是下面这个现象：

> **一条寻址到无设备 ID 的帧之后，舵机会忽略随后约 0.4 s 内的所有帧。**
> 实测阈值：等 0.35 s 仍**不应答**，等 0.4 / 0.5 / 0.6 s **恢复应答**；
> 而连续两条寻址到它自己的帧都能正常应答（14 字节 = 2 帧）。

这解释了长期以来的两个"怪现象"：

1. 扫描时每个 ID 之间需等待约 0.6 s —— 正是这个静默期；
2. 早期文档写的"应答延迟 0.3~0.9 s" —— 其实是把静默期误当成了应答延迟；
   后果是**用短超时扫描时只能扫到第一个舵机、后面整段漏扫**（探测到无设备 ID
   会让下一个 ID 的应答被吃掉）。

本库的对策：

- `scan()`：探测**失败后**先等 `BUS_QUARANTINE`（0.4 s）再探测下一个 ID；
  探测超时默认降到 0.15 s（应答只要 10~20 ms）
- 统一的 `retry_delay` 默认 `0.45 s`（≥ 静默期），保证重试不会被静默期吃掉
- 全部相关常量在 `romanbo.protocol`：`SERVO_RESPONSE_DELAY`、`BUS_QUARANTINE`、
  `SCAN_PROBE_TIMEOUT`、`SCAN_QUARANTINE`、`MIN_FRAME_GAP`

### 6.7 其它实测事实

- **SET 类命令完全不应答**（不是超时设置问题）
- 连续下发时仍**偶发丢包**（约 1/6 概率，实测 GetPID/GetPositionLimit 各 1 次）
  → 默认重试 2 次
- **越限目标会被夹紧**：ID 10 限值 (255,768)，命令 1000 → 停在 768 且负荷 0（不堵转）
- **到达目标后不再出力**：进入 `margin`（实测 5）死区即视为到位
- 两台的**配置**不同（限值是可写配置、不是型号属性）：ID 8 = PID(100,1,20)、限值(1,1023)；
  ID 10 = PID(50,0,5)、限值(255,768) —— 后者是早期用无数据 `0x0F` 探针误写的
  （日志证明它 9/25 22:13 还是 `(1,1023)`，机制已复现）。**已于 2026-09-26 复原为
  `(1,1023)`**，两台限值现均为全量程（±149.9°）；功能验证：同一命令 620 在窄限值下停在
  580、恢复后到达 621
- `SetCalibration` 未定义行为；真正的零点校准是 `0x23 SetCalibrationCurrpos`

### 6.8 帧间隔：**连发两帧，第二帧会被丢**（`MIN_FRAME_GAP`）

实测（2026-09-26，ID 8/10，FT230X）：**两帧之间没有间隔时，后发的那一帧会被舵机
静默丢弃**，且**与 ID 无关**——交换发送顺序后，被丢的永远是第二帧：

| 操作 | 结果 |
|---|---|
| 先发 ID 8、8 ms 内紧接发 ID 10 | ID 8 到位 ✅ / ID 10 不动 ❌ |
| 先发 ID 10、紧接发 ID 8 | ID 10 到位 ✅ / ID 8 不动 ❌ |

最小安全间隔逐档实测：

| 帧间隔 | ID 8 | ID 10 |
|---|---|---|
| **0 ms** | ✅ | ❌ 丢 |
| 2 / 5 / 10 / 20 / 50 ms | ✅ | ✅ |

**影响面**：多关节动作是**逐关节连发**（`move` 的步进逼近、`mode="position"` 的
`SET_PERIOD`+`SET_POSITION`、`play`），不兜底就会"只有第一个关节生效"。

**对策**：在 `SerialTransport.write()` 写口统一节流到 `MIN_FRAME_GAP = 2 ms`
（而不是在每个调用点各自 `sleep`），因此 `move` / `play` 的多关节路径自动修复；
单帧命令（GET 类）本来就等回包，不受影响。单元回归见
`tests/test_standalone.py::TestFrameGap`，整机回归见 `SERVO_TEST_PLAN.md` L4-01 / L4-02 / L7-01。

---

## 7. `.rsc` 工程文件

`.rsc` 工程文件，JSON 文本，三层结构：

```
Header : FileName / Type / ProjectName / MotorCount / MotorState[] / DialPosition[] / Image(base64 PNG)
Body   : "0".."n" 场景信息（SceneColor/SceneName/MotionCount）+ Motion 数组（扁平化动作帧）
扩展    : Setup[]（每场景一组 PID）、Program（程序块图）、Remocon[]（遥控映射）、KeyIndex[]（程序槽）
```

**生成 `.rsc`（示教 → 导出）**：`Body` 里的场景键（`"0"`/`"1"`…）带
`SceneName`/`MotionCount`/`SceneColor`，`Body.Motion` 是帧记录数组；一帧为
`{SceneNumber, MotionNumber, Period, MotorValue:{ADC×17}, LEDValue:{LED×17}}`。
注意 **`MotorValue`/`LEDValue` 是「重复同名键的对象」而不是数组**（见上方「重复键」说明），
标准 `json.dumps` 生成不了，所以由 `rsc.dumps_project()` / `rsc.write_project()` 手工拼文本；
`Version` 与 `Setup`(PID) 都是可选的（仓库样例 `examples/data/demo.rsc` 里就没有这两个键）。

```bash
# 手扳到位 → 记一帧；重复几次即累积成动作序列
python -m romanbo --port COM3 teach --ids 8,10 --file taught.json --period 500
python -m romanbo export --file taught.json --out taught.rsc            # 离线生成工程文件
python -m romanbo --port COM3 play taught.rsc --ids 8,10 --speed 15     # 回放验证
```

单个动作帧：

```json
{ "SceneNumber": 0, "MotionNumber": 0, "Period": 500,
  "MotorValue": { "ADC": 512, "ADC": 563, "ADC": 205 },
  "LEDValue":   { "LED": 0, "LED": 1, "LED": 2 } }
```

播放（`examples/data/demo.rsc` 为随仓库分发的样例工程，17 通道 / 2 场景 / 3 帧）：

```bash
python -m romanbo --port COM3 play examples/data/demo.rsc --speed 60
python -m romanbo --port COM3 play examples/data/demo.rsc --scene 1 --loop
python -m romanbo info examples/data/demo.rsc               # 只看摘要
```

```python
from romanbo import RscProject, RomanboRobot

project = RscProject.load("examples/data/demo.rsc")
print(project.summary())
with RomanboRobot("COM3") as robot:
    frames = list(project.frames())
    start = robot.capture([i for i in frames[0].targets()])     # 起始位置
    robot.play(frames, speed_dps=60, start_positions=start, max_load=60)
```

**注意事项**

1. 帧里的 `Period` 是**帧间间隔**，不是舵机走完时间（见 6.1）；`--speed` 时会被忽略，
   改为按角速度对关键帧做插值（`--speed-scale` 只在**没给 `--speed`** 时按倍率缩放帧间间隔）。
2. 数组下标 `i` 对应舵机 ID `i + id_offset`（默认 1），即**通道 1..17 → 舵机 1..17**；
   只接了部分舵机时，其余 ID 无设备、无应答（不影响）。
3. 帧里是**绝对 ADC**，首帧会直接跳到该姿态；不确定时先 `--speed` 放慢并观察。
4. 格式缺陷（读取已兼容）：`Program`/`Temp` 节点存在**重复 JSON 键**；`KeyIndex[]` 是
   "字符串里再塞 JSON"的双重序列化；`v1.1.1` 与 `v1.2.0` 的帧字段不同且无版本迁移。

---

## 8. 软件限力

这台硬件**没有可用的力矩环**，唯一可行的限力方式是：运动中轮询 `0x18`（实测负荷），
一旦超过阈值立刻停止继续下发。

```bash
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 120 --max-load 10
```

```
{"aborted": true, "reason": "load_limit", "id": 8, "load": 129,
 "limit": 10, "step": 1, "position": 973}          # 退出码 4
```

- 实现：步进逼近的每个节拍**轮转抽检**一个关节（`load_check_every`），不破坏角速度节拍
- 阈值从实测值里挑：静止为 0；本机快速动作峰值约 110~130 → 取 `60` 左右比较合适，
  `10` 这种低阈值会在起步瞬间就中止
- 触发后各关节**停在最后一个目标位置**保持不动
- API：`Servo.move_at_speed/rotate/set_angle/sweep`、`Robot.move/play` 均支持 `max_load`
- `max_load` 需要与 `speed_dps` 同用；不带 `speed_dps` 时传 `max_load` 会抛 `ValueError`

---

## 9. 测试与自检

```bash
python -m unittest discover -s tests -t .      # 129 项单元测试（全部通过，无跳过）
python -m romanbo selftest                     # 60 条基准报文（逐字节比对）
python -m romanbo --mock scan                  # 离线模拟器冒烟
python examples/04_offline_frames.py           # 打印每条指令的真实报文
```

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_protocol.py` | 帧编解码、校验、命令打包（含协议位域） |
| `tests/test_rsc.py` | `.rsc` 解析（重复键、场景过滤、字段缺失） |
| `tests/test_mock.py` | 整机流程、握手、扫描、示教、播放（离线模拟器） |
| `tests/test_speed.py` | 角速度步进规划、多关节同节拍、play 插值 |
| `tests/test_load.py` | 实测负荷语义、软件限力、别名兼容 |

---

## 10. 安全与已知限制

**会动真机，请注意**

- 所有运动类命令（`jog/angle/sweep/move/play`）**会立即驱动舵机**；下肢/悬臂场景请先做好支撑
- 参数类命令（`pid/limit/param/margin/校准/改 ID/复位`）会**写入舵机内部并掉电保存**，
  建议先 `config` 记下原值；**`SET` 类命令没有 ACK**，写入可能被静默丢弃
  （实测 PID 的 `0x07` 保存式写入 3 次只生效 1 次），所以 `pid` 命令默认回读确认
- `torque off` 后关节会失去保持力（下肢关节会失去支撑而倒下）
- `param period`、`param current-limit`、`param accelerate` 三项在本机固件上**无法验证效果**
  （分别是被忽略、无法回读、无法回读），请勿依赖
- `0x0F` 与 `SetPositionLimit` 同码，**任何情况下都不要发送**：实测**不带数据**也会把解析
  缓冲里的残留 4 字节写成 min/max（ID 8 被改成 `(113,257)`，可重复复现）。
  库层已上保险：`Servo.get_period()` 默认**抛 `ProtocolError` 拒发**，只有显式
  `get_period(unsafe=True)` 才会发送；`tools/servo_probe.py` 也把它移出默认清单
  （需 `--unsafe-0f`）

**明确不支持 / 未验证的清单**

| 项目 | 状态 |
|---|---|
| 力矩闭环 / 设定"目标力矩值" | ❌ 协议中不存在 |
| 出力档位（H/M/L/W） | ✅ 可用（`0x09` 的 `d[5]` bit3-4，`--level`） |
| 电流环 / 电流限流 | ⚠️ `0x0D` 写入无法回读，效果未验证 |
| 运动周期调速（`SET_PERIOD`） | ❌ 固件忽略 |
| 多关节预置 + 同步触发（`0x21`+`0x20`） | ❌ 不响应 |
| 读加速度（`0x19`） | ❌ 无应答 |
| 保存式 PID 写入（`0x07`）是否落盘、开机载入 | ⚠️ 3 次仅 1 次即时生效；落盘/载入未验证（需真断电） |
| 舵机 `GetModel`/`GetVersion` | ❌ 属控制器帧（ID0），无控制器板时不回复 |
| 空转 FreeWheel | ⚠️ `0x21` 位域无法验证；`SET_WHEEL(free=1)` 效果未观测 |
| 控制器板全部命令 | ⚠️ 无板，未验证 |
| 改波特率 / 改 ID / 复位 / 零点校准 | ⚠️ 已封装，未做破坏性验证 |

---

## 11. 目录结构

```
.
├── README.md                    本文件（项目级说明：安装/上手/实测结论/限制）
├── SERVO_SPEC.md                舵机功能・协议・接口规格（字节级逐命令参考）
├── SERVO_TEST_PLAN.md           舵机控制测试方案（分级用例 / 判定门限 / 缺陷回归）
├── LICENSE                      许可证（MIT）
├── CHANGELOG.md                 版本变更记录
├── CONTRIBUTING.md              贡献指南（开发约定 / 测试 / 危险改动）
├── requirements.txt             运行期依赖（pyserial）
├── pyproject.toml               打包元数据 + ruff / mypy 配置
├── .editorconfig / .gitignore   编辑器与忽略规则
├── .github/workflows/ci.yml     CI：Python 3.8~3.13 矩阵测试 + 发行包构建
├── romanbo/
│   ├── protocol.py              帧编解码、命令码、校验、回包解析
│   ├── transport.py             SerialTransport（pyserial）/ MockTransport
│   ├── servo.py                 单舵机 API、ServoConfig、LoadLimitExceeded
│   ├── robot.py                 整机 API（握手/扫描/多关节/示教/播放）
│   ├── rsc.py                   .rsc 工程解析
│   ├── joints.py                机型/通道、ADC↔角度、步进规划
│   ├── cli.py                   命令行接口
│   ├── golden.py                基准报文自检（60 条）
│   ├── py.typed                 PEP 561 类型标记（下游类型检查器据此生效）
│   └── __main__.py              python -m romanbo 入口
├── examples/
│   ├── data/demo.rsc            随仓库分发的样例工程（17 通道 / 2 场景 / 3 帧）
│   ├── 01_scan_and_read.py      py examples/01_scan_and_read.py COM3
│   ├── 02_play_rsc.py           py examples/02_play_rsc.py examples/data/demo.rsc COM3 --speed 60
│   ├── 03_teach_and_export.py   py examples/03_teach_and_export.py COM3 --ids 8,10 --out taught.rsc
│   └── 04_offline_frames.py     py examples/04_offline_frames.py
├── tools/servo_probe.py         单舵机联调 / 回包取样工具（原始十六进制）
├── tests/                       129 项单元测试（含独立可用性 / 收发锁 / 帧间隔）
└── logs/                        真机联调探针日志（作为实测结论的原始证据）
```

**关于本 SDK**

- 本 SDK 面向 **ROMANBO** 舵机型号，独立实现其 RS485 总线控制协议，运行期仅依赖 `pyserial`。
- 协议字段、命令码与常量以 [`SERVO_SPEC.md`](SERVO_SPEC.md) 为准；已真机验证的结论均标注实测环境与日期。
- LED 颜色位映射与零点偏差 ±128 偏置语义依据《ROMANBO RS485 舵机通信控制协议》；
  其示例报文已与本实现逐字节交叉校验（发现 3 处校验和笔误，见 [`SERVO_SPEC.md`](SERVO_SPEC.md) §11）。
- 使用真机前请自行确认设备安全。
- 许可证：MIT（见 [`LICENSE`](LICENSE)）；版本变更见 [`CHANGELOG.md`](CHANGELOG.md)。
- 版本：`romanbo 1.0.0`（见 `romanbo/__init__.py`）。
