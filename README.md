# ROMANBO 舵机 / 整机控制库（Python SDK）

[简体中文] | [English](README.en.md)

[![CI](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml/badge.svg)](https://github.com/LQX-Code-SH/Romanbo-Python-SDK/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Typing: py.typed](https://img.shields.io/badge/typing-py.typed-blue)](https://peps.python.org/pep-0561/)
[![Lint: ruff](https://img.shields.io/badge/lint-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Docs](https://img.shields.io/badge/docs-online-2ea44f)](https://lqx-code-sh.github.io/Romanbo-Python-SDK/)

面向 **ROMANBO** 舵机型号的 RS485 总线控制 SDK，完整实现舵机与整机控制协议，
并在**真机**上逐条验证过（验证环境：两个 MOS 系列舵机串联，ID 8 / ID 10，COM3，2026-09-25）。

**文档中每一条结论都区分「协议文档定义」「真机实测」「未验证」**，未验证项绝不写成已验证。

📖 文档站 <https://lqx-code-sh.github.io/Romanbo-Python-SDK/> ·
[协议规格](docs/SERVO_SPEC.md) · [API 参考](docs/api.md) · [文档索引](docs/README.md)

---

## 特性

- **协议全覆盖**：扫描 / 位置 / 角度 / 角速度 / 轮子 / 扭矩档位 / PID / 限值 / 实测负荷 / LED / 零点校准 / 参数回读
- **角速度是真的角速度**：固件忽略 `SET_PERIOD`，本库用**步进逼近**实现（实测误差约 −4%）
- **软件限力**：轮询实测负荷，超阈值立即停止（硬件没有可用的力矩环）
- **真机验证过的时序**：总线静默期、帧间隔、重试策略都有实测依据，不是猜的
- **离线可用**：`.rsc` 解析、报文编码、`--mock` 模拟器、60 条基准报文自检全部零依赖
- **工程化**：129 项单元测试（无需硬件）+ CI（Python 3.8~3.13）+ 类型标注（`py.typed`）

## 安装

```bash
pip install pyserial                 # 仅真实串口需要；离线功能零依赖
pip install -e .                     # 或把 romanbo/ 目录直接拷进你的项目
```

**Python 3.8+**。Linux 下需要串口权限：

```bash
sudo usermod -aG dialout $USER       # 执行后需重新登录
python3 -m romanbo ports             # 确认端口名，如 /dev/ttyUSB0
```

平台差异、并发边界等细节见 [安装与环境](docs/INSTALL.md)。

## 30 秒上手

```bash
# 离线：校验 60 条基准报文
python -m romanbo selftest

# 真机：扫描 → 读参数 → 看载荷
python -m romanbo --port /dev/ttyUSB0 scan
python -m romanbo --port /dev/ttyUSB0 --json config --ids 8,10
python -m romanbo --port /dev/ttyUSB0 load --ids 8 --watch 10 --interval 0.1

# 运动：相对 +15° @ 60°/s，阈值 60 的软件限力
python -m romanbo --port /dev/ttyUSB0 jog --id 8 --degrees 15 --speed 60 --max-load 60

# 多关节（--readback 会等到位再回读实际位置与误差）
python -m romanbo --port /dev/ttyUSB0 move --targets 8:600,10:480 --speed 30 --readback

# 播放工程文件（examples/data/demo.rsc 是随仓库样例）
python -m romanbo --port /dev/ttyUSB0 play examples/data/demo.rsc --speed 60
```

完整命令与退出码见 [命令行参考](docs/CLI.md)。

## Python API

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("/dev/ttyUSB0") as robot:      # 或 connect("COM3", mock=True)
    print(robot.scan(1, 32))                     # 在线 ID
    s = robot.servo(8)
    print(s.get_position(), s.angle())           # ADC / 角度
    print(s.get_load())                          # 实测负荷（0x18）

    s.torque(True)                               # 力矩使能
    s.rotate(15, speed_dps=60)                   # 以 60 °/s 相对转 15°
    try:
        s.move_at_speed(1023, 120, max_load=60)  # 120 °/s + 软件限力
    except LoadLimitExceeded as exc:
        print(exc.as_dict())

    robot.move({8: 600, 10: 480}, speed_dps=60, start=robot.capture([8, 10]))
    robot.play_rsc("examples/data/demo.rsc", speed_dps=60, max_load=60)
```

| 类 / 模块 | 职责 |
|---|---|
| `RomanboRobot` | 连接、扫描、多关节同步动作、示教回读、`.rsc` 播放 |
| `Servo` | 单个舵机的全部命令（位置/角度/角速度/轮子/扭矩/周期/PID/限值/负荷/LED/校准/回读） |
| `ServoConfig` | 一次读回的全部参数（字段 `load` 为实测负荷） |
| `LoadLimitExceeded` | 软件限力触发（`.as_dict()` 便于上报） |
| `RscProject` / `MotionFrame` | `.rsc` 工程解析与生成 |
| `joints` / `protocol` / `transport` | 换算与步进规划 / 帧编解码与命令码 / 串口与离线模拟 |

完整签名与字段说明见 **[API 参考](docs/api.md)**（由 docstring 自动生成）。

## 关键实测结论（摘要）

| 结论 | 影响 |
|---|---|
| `SET_PERIOD(0x0B)` **被固件忽略**，舵机总以最大速度（约 150~180 °/s）走完 | 角速度改用**步进逼近**（每 100 ms 一个中间目标） |
| `0x18` 是**实测负荷**，不是限制值；静止 0、快速动作峰值约 110~130 | 它是硬件上唯一反映输出力矩的读数，[软件限力](docs/LOAD_LIMITING.md)基于它 |
| **连发两帧会丢第二帧**（与 ID 无关，间隔 ≥2 ms 才稳） | `SerialTransport.write()` 强制 `MIN_FRAME_GAP = 2 ms`，否则多关节只动第一个 |
| **总线静默期**：向无设备 ID 发帧后，约 0.4 s 内的帧被忽略 | 扫描必须留隔离期，否则整段漏扫；`scan` 探测超时降到 0.15 s |
| 力矩有**使能开关**（`0x10`，仅布尔）**与出力档位**（`0x09` `d[5]` bit3-4） | 实测 H(226) > M(152) > L(118)，W 档位置指令不生效 |
| `0x0F` 与 `SetPositionLimit` 同码，**无数据也会改写限值** | `get_period()` 默认拒发，需显式 `unsafe=True` |

完整数据、实测报文与证据日志见 **[真机实测结论](docs/FINDINGS.md)**。

## 安全提示

- 运动类命令（`jog/angle/sweep/move/play`）**会立即驱动舵机**；下肢/悬臂请先做好支撑。
- 参数类命令（`pid/limit/param/calib/set-id/reset`）**写入舵机并掉电保存**，
  建议先 `config` 记录原值。**SET 类命令没有 ACK**，写入可能被静默丢弃。
- `torque off` 后关节失去保持力（下肢关节会倒下）。
- **任何情况下都不要发送 `0x0F`**（同码于 `SetPositionLimit`，会误写限值）。

完整限制清单见 **[安全与已知限制](docs/SAFETY.md)**。

## 文档

| 文档 | 内容 |
|---|---|
| [安装与环境](docs/INSTALL.md) | 依赖、三种用法、Linux/macOS 权限、平台差异、并发边界 |
| [命令行参考](docs/CLI.md) | 全部子命令、全局选项、退出码、常用组合 |
| [协议速览](docs/PROTOCOL.md) | 帧格式、地址分配、命令码表、应答约定 |
| [字节级协议规格](docs/SERVO_SPEC.md) | 逐命令请求/回包布局、时序、数据语义、未验证清单 |
| [真机实测结论](docs/FINDINGS.md) | 全部实测数据与结论（角速度、负荷、时序、帧间隔…） |
| [工程文件 `.rsc`](docs/RSC.md) | 三层结构、示教导出、播放语义与格式缺陷 |
| [软件限力](docs/LOAD_LIMITING.md) | 原理、阈值选择、API 与退出码 |
| [安全与已知限制](docs/SAFETY.md) | 危险命令、不支持/未验证清单、软件边界 |
| [测试与自检](docs/TESTING.md) | 单元测试、基准报文自检、MkDocs 构建 |
| [测试方案](docs/SERVO_TEST_PLAN.md) | L0~L7 分级用例、判定门限、结果模板、缺陷回归 |
| [API 参考](docs/api.md) | 由 docstring 自动生成，不会与代码脱节 |
| [证据日志](docs/evidence/README.md) | 真机探针原始收发字节 |

在线文档站：<https://lqx-code-sh.github.io/Romanbo-Python-SDK/>（由 `docs/` 构建）

## 项目结构

```
romanbo/            核心包（protocol / transport / servo / robot / rsc / joints / cli / golden）
docs/               文档（协议规格、实测结论、API 参考、证据日志）
examples/           示例脚本 + data/demo.rsc 样例工程
tests/              129 项单元测试（不需要硬件）
tools/servo_probe.py  单舵机原始十六进制联调工具
```

```bash
python -m unittest discover -s tests -t .   # 单元测试
python -m romanbo selftest                  # 60 条基准报文
```

## 许可

MIT，见 [`LICENSE`](LICENSE)。版本变更见 [`CHANGELOG.md`](CHANGELOG.md)，
贡献指南见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
