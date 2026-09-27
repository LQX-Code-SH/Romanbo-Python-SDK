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

## 四种用法

```bash
# A. 从 PyPI 安装（使用者推荐；控制台脚本 `romanbo` 随包提供）
pip install romanbo
#    国内镜像同步新版本有延迟，急着装可以指官方源：
#    pip install --index-url https://pypi.org/simple romanbo

# B. 直接复制目录：把 romanbo/ 拷进你的项目即可（无需安装，可脱离本仓库驱动真机）

# C. 本地可编辑安装（开发用）
pip install -e .                     # 需要开发工具时：pip install -e ".[dev]"

# D. 构建 wheel（分发用；产物含控制台脚本 romanbo）
python -m pip wheel . --no-deps -w dist
#    产物：romanbo-<版本>-py3-none-any.whl（版本号取自 romanbo/__init__.py），
#    内含 py.typed 与 entry_points.txt
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

临时放开权限（**重插 USB 后失效**，节点名也可能变——先 `python -m romanbo ports` 看当前名字）：

```bash
sudo chmod 666 /dev/ttyUSB1
```

**永久解决**（重插 / 换 USB 口 / 重新枚举都有效）——**一条命令**：

```bash
python -m romanbo ports --fix        # 按设备真实的 VID:PID 生成规则、sudo 装上、复验
```

它会先把**要写入的内容**和**要执行的操作**打出来、问一次，然后走 `sudo`（这是整条流程里
唯一需要 root 的一步——设备节点的权限属于内核/udev，绕不过去），最后把复验结果给你看。
非交互环境（脚本 / CI）加 `--yes`。**已经能用时它什么都不做**，也不会碰系统文件。

不想用这个命令的话，手工等价操作为：

```bash
sudo cp deploy/60-romanbo-usb-serial.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

> **文件名前缀不能乱改**：udev 按**文件名顺序**执行规则，而真正执行 `uaccess` 内建的是
> 系统规则 `73-seat-late.rules`（`TAG=="uaccess" → RUN{builtin}+="uaccess"`）。给
> `TAG+="uaccess"` 的规则必须排在 73 **之前**——写成 `99-` 就会"规则装了、tag 也有了
> （`udevadm info` 里能看到 `CURRENT_TAGS=:uaccess:`），却依然 `Permission denied`"，
> 因为 ACL 永远不会生成。

规则里带 `TAG+="uaccess"`，systemd-logind 会给当前登录的桌面用户补一条 ACL，所以**装完
立刻生效、不必重新登录**。（不想依赖 `uaccess` 的话，把规则里的 `MODE`/`GROUP` 配上
`sudo usermod -aG dialout $USER` 也行——但那需要**注销重新登录**才生效。）

两个操作顺序上的坑：

- **`chmod` 之后别跑 `udevadm trigger`**：trigger 会按规则重新套用权限，把 `chmod 666`
  覆盖回 `0660`（表现为"刚 chmod 过，还是 Permission denied"）。要手动放权就放在 trigger
  **之后**。
- 反过来，如果只做了 `usermod -aG dialout` 而没重新登录，当前会话仍然打不开——这类"装了
  但没生效"多半就是组未生效，用 `id -nG | grep dialout` 一眼能看出来。

规则文件里的两个 16 进制 ID 是 FTDI FT230X（`0403:6015`）的；换别的适配器时先用 `lsusb`
查它的 ID 再改，否则规则不会匹配（同样是"装了没生效"）。

> 脚本里建议用 `/dev/serial/by-id/` 下的**稳定路径**（按适配器序列号命名），避免重插后
> `ttyUSB0` / `ttyUSB1` 变来变去：
>
> ```bash
> ls -l /dev/serial/by-id/
> python -m romanbo --port /dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_DN02AGAB-if00-port0 read --ids 8,10
> ```

## 并发边界

`RomanboRobot` 内部的「发—等」过程由可重入锁（`_io_lock`）串行化，所以
**同一实例可以跨线程调用**（例如一个线程只读位置、另一个线程做短动作）；
但**不要用多个线程同时驱动动作**（`play` / `move` 会逐帧交错，动作被打乱）。
总线本身是串行的，多线程不会提高吞吐。
