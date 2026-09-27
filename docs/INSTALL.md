# 安装与环境

> **本文只讲安装与运行环境**（依赖、权限、平台差异、并发边界）。
> 不记录验证数据与过程；实测结论见[真机实测结论](FINDINGS.md)。

## 依赖

- **Python 3.8+** —— 包内模块全部使用 `from __future__ import annotations`，
  因此 `list[int]` 这类写法不在运行期求值；运行期用到的容器类型一律走 `typing.*`。
- **pyserial**（`>=3.5,<4.0`）—— 只有使用**真实串口**时才需要，且**在打开端口时才惰性导入**。
  未安装时 `--mock`、`selftest`、`info`、`export` 等离线功能仍可正常使用。

```bash
pip install pyserial
```

## 三种用法

均在仓库根目录执行：

```bash
# A. 直接复制目录：把 romanbo/ 拷进你的项目即可（无需安装，可脱离本仓库驱动真机）
# B. 本地可编辑安装（开发用）
pip install -e .                     # 需要开发工具时：pip install -e ".[dev]"
# C. 构建 wheel（分发用；产物含控制台脚本 romanbo）
python -m pip wheel . --no-deps -w dist
#    产物：romanbo-1.1.0-py3-none-any.whl，内含 10 个模块 + py.typed + entry_points.txt
```

也可不安装，直接在仓库根目录以模块方式运行：

```bash
python -m romanbo selftest          # 离线自检
```

## Linux / macOS

代码本身**跨平台**（只依赖 pyserial + 标准库，无 Windows API、无平台分支逻辑），
Linux 上只需注意「端口名 / 权限 / 共享打开」三点：

```bash
sudo apt update && sudo apt install -y python3-pip
pip3 install pyserial
sudo usermod -aG dialout $USER          # 串口权限；执行后需注销重新登录

python3 -m romanbo ports                # 列出设备，确认名字
python3 -m romanbo --port /dev/ttyUSB0 scan
python3 -m romanbo --port /dev/ttyUSB0 play my.rsc --ids 8,10 --speed 15
```

| 差异点 | Windows | Linux / macOS |
|---|---|---|
| 端口名 | `COM3` | `/dev/ttyUSB0`（FTDI）/ `/dev/ttyACM0`（CDC）/ `/dev/tty.usbserial-XXXX` |
| 权限 | 一般无需设置 | 需要 `dialout` 组（否则 `PermissionError`） |
| 共享性 | 打开即独占 | **默认可多进程同时打开** → 本库用 `exclusive=True`（`TIOCEXCL`）排他 |
| 可能被抢占 | 其它上位机 / 串口助手 | 其它上位机 / ModemManager（`sudo systemctl stop ModemManager`） |
| 定时精度 | `time.sleep` ≈ 15 ms 抖动；`time.monotonic()` 在 CPython ≤ 3.12 粒度约 15.6 ms | 更好，步进逼近的角速度更准 |
| 输出编码 | 输出被重定向时 stdout 用区域编码（cp1252/cp936），中文会崩 → CLI 已自动切 UTF-8 | 默认 UTF-8 |

临时放开权限（重插 USB 后失效）：

```bash
sudo chmod 666 /dev/ttyUSB0
```

## 并发边界

`RomanboRobot` 内部的「发—等」过程由可重入锁（`_io_lock`）串行化，所以
**同一实例可以跨线程调用**（例如一个线程只读位置、另一个线程做短动作）；
但**不要用多个线程同时驱动动作**（`play` / `move` 会逐帧交错，动作被打乱）。
总线本身是串行的，多线程不会提高吞吐。
