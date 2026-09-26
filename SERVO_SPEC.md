# ROMANBO 舵机 —— 功能・协议・接口说明

> 适用硬件：ROMANBO 人形机器人所用 MOS 系列总线舵机（本机为 **MOS-S2 / S1**，ID 8 / ID 10 实测）
> 协议基准：《ROMANBO RS485 舵机通信控制协议》——帧格式、命令码与参数语义以此为准
> 本文以 `📄` 标记引用协议文档的内容，凡引用报文都已与本实现逐字节交叉校验
> 验证环境：两个舵机串联、COM3、115200 8N1，2026-09-25
> 配套代码：`romanbo/`（本仓库），项目级说明见 [`README.md`](README.md)

---

## 0. 阅读约定

本文档区分四种证据强度，**未验证的内容绝不写成已验证**：

| 标记 | 含义 |
|---|---|
| ✅ | **真机实测**有效（本文给出实测数据） |
| ⚠️ | 已实现可发送，但**效果无法验证**（固件不回读 / 无合适观测手段） |
| ❌ | **该固件不支持**（实测无应答或无动作） |
| 📖 | 仅来自协议文档 / 常量表，**未在真机验证** |
| 📄 | 来自协议文档《ROMANBO RS485 舵机通信控制协议》；凡引用的报文都已与本实现逐字节交叉校验 |

命名约定：`d[i]` 表示**整帧**第 `i` 个字节（0 起）；`data[i]` 表示**数据段**第 `i` 个字节（数据段从整帧 `d[5]` 开始）。

---

## 1. 系统与链路

### 1.1 拓扑与地址分配

单条 UART 总线上挂控制器板、全部舵机与传感器，靠**地址字段**区分设备：

| 地址 | 设备 | 说明 |
|---|---|---|
| `0` | 控制器板 | 存动作程序、驱动整机、广播同步 |
| `1`–`32` | 舵机 | 本库主要对象（`SERVO_ID_MIN..MAX`） |
| `224`–`253` | 传感器 | 协议定义 |
| `254` | 广播 | 同步触发等（`BROADCAST_ID`） |

上位机侧只有**一个串口对象**同时与所有设备通信。

### 1.2 物理层与串口参数

| 项 | 值 | 依据 |
|---|---|---|
| 波特率 | **115200**（备选 57600） | 协议文档 |
| 数据位/停止位/校验 | 8 / 1 / None | 同上 |
| 流控 | `DtrEnable=true`、`RtsEnable=true` | 同上 |
| USB 转串口 | FTDI（驱动 `CDM21224_Setup.exe`） | 安装包 |
| 上位机 API | 普通串口读写（非 D2XX） | 串口设备文件（115200 8N1） |

### 1.3 总线带宽与刷新率上限（为什么必须避免高频轮询）

| 项 | 数值 |
|---|---|
| 理论字节率 | 115200 / 10 = **11520 B/s** |
| 一条 8 字节指令在线上耗时 | ≈ **0.7 ms** |
| 一次「查询 + 回包」 | 6 + 9 ≈ 15 字节 ≈ 1.3 ms（不含舵机处理） |
| 17 关节各发一条位置指令 | ≈ **12 ms**（起点偏差上限） |
| 若逐关节回读位置 | 每关节约 10~20 ms（见 §3.1） |

结论：**下发指令很便宜，回读很贵**。多关节控制应"一次算好、顺序下发"，不要每帧回读。

---

## 2. 帧格式

### 2.1 通用结构

```
+------+------+------+------+------+-----------+------+
| 0xFF | 0xFF |  ID  | LEN  | CMD  |   DATA…   | CHK  |
+------+------+------+------+------+-----------+------+
   d0     d1     d2     d3     d4    d5..dn-2    d(n-1)
```

| 字段 | 说明 |
|---|---|
| 帧头 | 固定 `FF FF` |
| `ID` | 目标/来源设备地址（见 1.1） |
| `LEN` | 长度字段，**语义见 2.2** |
| `CMD` | 命令码，见 §5；应答帧 = `0x80 \| 请求码`，错误帧 = `0x80` |
| `DATA` | 命令参数（长度 = 整帧长度 − 6） |
| `CHK` | 校验字节 |

最短帧 6 字节，最长 255（`MIN_FRAME_LEN` / `MAX_FRAME_LEN`）。

### 2.2 `LEN` 的两种语义（实测）

| 方向 | `LEN` 含义 | 实测样例 |
|---|---|---|
| **上位机 → 设备**（请求） | **整帧字节数** | `SetPosition(1,512)` = `FF FF 01 08 09 0A 00 E6` → LEN=8，共 8 字节 |
| **舵机 → 上位机**（应答） | **整帧字节数** | `Status` 回包 `FF FF 08 07 85 00 6E` → LEN=7，共 7 字节 |
| **控制器 → 上位机**（应答） | **数据段字节数**（整帧 = LEN+6） | 型号回包 `FF FF 00 02 81 00 70 0F` → LEN=2，共 8 字节 |

本库的 `FrameParser` **同时接受两种解释**（先按 `LEN+6` 再按 `LEN` 试算，配合校验确认），因此两类设备都可直连。

### 2.3 校验算法

```python
def checksum(frame) -> int:                 # frame[-1] 为占位字节，其值被忽略
    return (0x100 - (sum(frame[:-1]) & 0xFF)) & 0xFF
```

等价说法：**整帧所有字节之和 ≡ 0 (mod 256)**。

校验示例（`SetPosition(1,512)`）：

```
0xFF+0xFF+0x01+0x08+0x09+0x0A+0x00 = 0x21A → 低 8 位 0x1A
CHK = (0x100 - 0x1A) & 0xFF = 0xE6
→ FF FF 01 08 09 0A 00 E6        （逐字节求和的低 8 位为 0）
```

### 2.4 应答数据段布局

```
data[0] = 状态字节，实测恒为 0x00
data[1..] = 数值；16 位量一律**大端**（高位在前）
```

| 回包 | 数据段（实测） | 释义 |
|---|---|---|
| `Status(8)` | `00` | 无数据，仅表示在线 |
| `GetPosition(8)` | `00 03 F4` | 位置 = 0x03F4 = **1012** |
| `GetPID(8)` | `00 64 01 14` | P=100, I=1, D=20 |
| `GetTemp(8)` | `00 20` | 32 ℃ |
| `GetMargin(8)` | `00 05` | 5 |
| `GetLoad(8)` | `00 00` / 运动中 `00 78` | 实测负荷 0 / 120 |
| `GetPositionLimit(8)` | `00 00 01 03 FF` | min=1, max=1023 |
| `GetCalibration(8)` | `00 74` | 116 |
| `GetMotor(8)` | `00 01` | 语义未定（见 §5.1.6） |
| `GetFactoryTest(8)` | `00 06 0E` | 0x060E |

### 2.5 完整示例（逐字节）

```
查询位置：  上位机 → FF FF 08 06 14 E0        (ID=8, LEN=6, CMD=0x14, CHK=0xE0)
            舵机  → FF FF 08 09 94 00 03 F4 66   (CMD=0x94=|0x80, data=00 03 F4)
位置指令：  上位机 → FF FF 08 08 09 0E 58 83   (位置 0x258=600, torque=1, relative=1)
错误回包：  舵机  → FF FF 08 07 80 03 70       (CMD=0x80, 错误码 3 = OVERLOAD)
```

---

## 3. 时序、静默期与容错

### 3.1 应答延迟（实测）

逐命令各测 6 次（ID 8，`ack_timeout=1.0`、不重试）：

| 命令 | 最小 | 中位 | 最大 |
|---|---|---|---|
| Status / GetPosition / GetLoad / GetMargin / GetPID / GetTemp / GetMotor / GetPositionLimit / GetCalibration / GetFactoryTest | 7.5 ms | **约 10 ms** | 12.7 ms |

**结论：应答只要 10~20 ms。** 常量：`SERVO_RESPONSE_DELAY = 0.02`，`DEFAULT_ACK_TIMEOUT = 0.3`（留 20 倍余量）。

### 3.2 总线静默期（**最重要的一条时序规则**）

> **一条寻址到"无设备 ID"的帧之后，舵机会忽略随后约 0.4 s 内的所有帧。**

实测（原始字节级）：

| 序列 | 结果 |
|---|---|
| 连续两条 `Status(8)`（都发给它自己） | **两帧都回**（收到 14 字节 = 2×7） |
| `Status(7)`（不存在）→ 立刻 `Status(8)` | **一个字节都不回** |
| `Status(7)` → 等 0.35 s → `Status(8)` | 仍**不回** |
| `Status(7)` → 等 **0.40 s** → `Status(8)` | ✅ 回包 |
| `Status(7)` → 等 0.5 / 0.6 s → `Status(8)` | ✅ 回包 |

这解释了长期以来的两个现象：

1. 扫描时每个 ID 之间需等待约 0.6 s —— 正是这个静默期；
2. 早期文档写的"应答延迟 0.3~0.9 s" —— 把静默期误当成了延迟，后果是**用短超时扫描时只有第一个舵机能被扫到**。
3. **播放整机 `.rsc` 时必须只驱动在线关节**（库/CLI 的 `ids`/`--ids`）：文件是 17 通道
   绝对目标，若总线上只有部分舵机，向不存在的 ID 发帧同样会触发 0.4 s 静默期，
   后续帧会被连续吃掉（实测：整机 17 通道帧里若有 15 个 ID 不在线，节奏完全不可用）。

常量：`BUS_QUARANTINE = 0.4`、`SCAN_QUARANTINE = BUS_QUARANTINE`。

### 3.3 超时 / 重试策略

| 参数 | 默认值 | 说明 |
|---|---|---|
| `DEFAULT_ACK_TIMEOUT` | 0.3 s | 单次回包等待 |
| `retries` | 2 | 超时重试次数 |
| `retry_delay` | **0.45 s** | 重试间隔，**必须 ≥ 静默期**，否则重试同样被吃掉 |
| `SCAN_PROBE_TIMEOUT` | 0.15 s | 扫描时每个 ID 的探测等待 |
| `SCAN_QUARANTINE` | 0.4 s | 探测失败后等这么久再探测下一个 ID |

扫描行为：在线 ID 只花 ~15 ms；探测失败后固定付出 `probe_timeout + quarantine` ≈ 0.55 s。
全范围 1..32 实测 **16.5 s**，且能稳定扫出全部设备（`scan(1,32)` → `[8, 10]`）。

### 3.4 漏答观测

连续采样时仍**偶发漏答**（实测 6 次里出现 1 次，GetPID / GetPositionLimit 各一次）。
注意区分两种成因：① 真随机漏答；② 上一条帧是发给别人的（静默期）。默认重试 2 次可覆盖。

### 3.5 多关节同步精度与帧间隔

位置模式下逐关节下发 `SET_PERIOD` + `SET_POSITION`。
（`SET_SYNC` 广播触发在这批固件上不可用，见 §5.2.4。）

> **连发两帧必须留间隔**。实测（2026-09-26，ID 8/10，FT230X）：两帧之间无间隔时
> **后发的那一帧会被舵机静默丢弃**，且与 ID 无关（交换顺序后被丢的永远是第二帧）。

| 帧间隔 | 结果 |
|---|---|
| 0 ms | 第二帧丢失 |
| 2 / 5 / 10 / 20 / 50 ms | 两帧均正常 |

因此本库在 `SerialTransport.write()` 写口强制 `MIN_FRAME_GAP = 2 ms`。
17 关节单拍由此增加约 34 ms，整体起点偏差 **< 40 ms**，对百毫秒级节拍仍可视为同步。

**真实 `.rsc` 播放实测（2026-09-26，ID 8/10 两台裸舵机）**：

```
play "chapter 25.rsc" --scene 1 --ids 8,10 --speed 15
  播放前实读  ID 8=511  ID 10=510
  文件目标    ID 8=494  ID 10=757 → 774
  完成后实读  ID 8=496  ID 10=772      ← 误差均 2 ADC（≈0.6°）
```

总线序列体现了设计意图：`ID 8` 步进 505→500→494 后**停止重发**，同时 `ID 10` 按每拍
+5 ADC（≈14.7 °/s，目标 15 °/s）继续走到 774；负荷抽检（`0x18`）在两台间轮转。
⇒ **逐关节位置下发即可让多关节按同一节拍协调运动**，无需 `SET_SYNC`。

---

## 4. 功能矩阵

| 功能 | 命令 | Python 接口 | CLI | 状态 |
|---|---|---|---|---|
| 在线检测 | `Status 0x05` | `Servo.ping()` / `Robot.scan()` | `scan` | ✅ |
| 读位置 | `GetPosition 0x14` | `get_position()` / `get_position_raw()` | `read` / `teach` | ✅ |
| 位置定位 | `SetPosition 0x09` | `set_position()` / `move()` | `move` / `jog` | ✅ |
| 绝对角度 | —（软件换算） | `set_angle()` / `angle()` | `angle` | ✅ |
| 恒角速度运动 | —（**步进逼近**） | `move_at_speed()` | `jog/angle/move/sweep/play --speed` | ✅ |
| 轮子模式 | `SetWheel 0x09` | `wheel()` / `stop_wheel()` | `wheel` | 📖（未逐项验证） |
| 力矩使能（开关） | `SetTorque 0x10` | `torque()` | `torque on/off` | ✅ 仅布尔 |
| **出力档位（高/中/低/W）** | `SetPosition 0x09` 的 `d[5]` bit3-4 | `level=` 参数 | `--level H\|M\|L\|W` | ✅ **实测有效**（H226 > M152 > L118；W 位置指令不生效） |
| 实测负荷 | `GetLoad 0x18` | `get_load()` | `load` | ✅ |
| 软件限力 | ——（轮询 0x18） | `max_load=` 参数 | `--max-load` | ✅ |
| PID | `SetPID 0x07` / `0x47` | `set_pid(save=)` / `get_pid()` | `pid` | ✅ 可回读 |
| 位置限值 | `SetPositionLimit 0x0F` | `set_position_limit()` / `get_position_limit()` | `limit` | ✅ |
| 死区/到位判据 | `SetMargin 0x0C` | `set_margin()` / `get_margin()` | `param margin` | ✅ |
| 运动周期 | `SetPeriod 0x0B` | `set_period()` / `get_period(unsafe=True)` | `param period` | ⚠️ **固件忽略**；读周期走 `0x0F` → **默认拒发**（见 §5.3） |
| 温度 | `GetTemp 0x13` | `get_temperature()` | `config` | ✅ |
| 温度阈值 | `SetTemp 0x08` | `set_temp()` | `param temp` | ⚠️ 无法回读 |
| 负荷上限 | `SetLoadLimit 0x0D` | `set_load_limit()` | `param current-limit` | ⚠️ 无法回读 |
| 加速度 | `GetAccelerate 0x19` | `get_accelerate()` | `config` | ❌ 无应答 |
| 加速度设置 | `SetAccelerate 0x0E` | `set_accelerate()` | `param accelerate` | ❌ **实测无效果**（A/B 到位时间与轨迹一致） |
| 零点校准 | `SetCalibrationCurrpos 0x23` | `set_calibration_currpos()` | `calib` | 📖 |
| 读零点 | `GetCalibration 0x15` | `get_calibration()` | `config` | ✅ |
| LED | `SetLED 0x11` | `set_led()` / `set_led_color()` | `led` | ✅ 颜色映射已确认（📄+本实现一致，真机可点亮） |
| 改 ID | `SetID 0x06` | `set_id()` | `set-id` | 📖 |
| 复位/重启 | `Reset 0x01` / `SetReboot 0x02` | `reset()` / `reboot()` | `reset` | 📖 |
| 出厂测试 | `0x79 / 0x7A / 0x7B` | `start_factory_test()` 等 | — | ✅ 读取有效 |
| 波特率 | `SetBaudrate 0x22` | `set_baudrate()` | — | 📖（协议文档也未定义发送） |
| 预置 + 同步触发 | `SetNextPosition 0x21` + `SetSync 0x20` | `next_position()` / `sync()` | `sync` | ❌ **不响应** |
| 空转 FreeWheel | `0x21` 的 freewheel 位 | `next_position(freewheel=)` | `wheel --free` | ❌ 依赖 0x21 |

---

### 4.1 控制模式支持矩阵（P / V / A / 力矩 / 反馈）

把"控制维度"逐条对账（✅ 可用｜⚠️ 已封装未验证｜❌ 该固件不支持）：

| 维度 | 协议字段 | 实测 | 说明 |
|---|---|---|---|
| **P 位置** | `SetPosition 0x09`（绝对/相对 ADC + `relative` 位） | ✅ | 唯一完整可用的闭环量 |
| **出力档位** | `SetPosition 0x09` 的 `d[5]` bit3-4 = H/M/L/W(0/1/2/3) | ✅ | 是**输出力度限值/档位**，不是"力矩给定"（见 §5.2.1） |
| **动力使能** | `SetTorque 0x10` = 0/1 | ✅ | 关掉后位置指令完全不动 |
| **T 时间（走完时间）** | `SetPeriod 0x0B` | ❌ | 固件忽略（60/3000/8000 ms 与不发都约 0.24 s 走完 44°） |
| **A 加速度** | `SetAccelerate 0x0E` / `GetAccelerate 0x19` | ❌ | 写值无任何行为差异，且读不回来 |
| **V 速度（给定）** | 无专用指令；仅 `SetWheel 0x09` 位域（speed 0..255 + 方向） | 📖 | 轮子**开环持续转**，不是"以 X °/s 到 Y"的闭环速度模式；本机未验证 |
| **速度反馈** | —— | ❌ | `0x14` 只回位置，没有速度/转速读数 ⇒ 无法做速度闭环 |
| **力矩/电流给定** | —— | ❌ | 协议中不存在电流环给定 ⇒ **没有转矩模式** |
| **力矩/负荷反馈** | `GetLoad 0x18` | ✅ | 实测负荷（运动中 120→32→28，静止 0），可做限力判据，但不是 N·m 数值 |
| **同步触发** | `SetSync 0x20` + `SetNextPosition 0x21` | ❌ | 不响应 ⇒ 同步靠主机侧顺序下发（起点偏差 <15 ms） |
| **轨迹流式给定（PVT）** | —— | ❌ | 缺 V 字段、T 被忽略、同步不可用；且每点往返 10~30 ms，无法做高频点流 |

**结论**：本硬件是"**位置模式 + 出力档位**"的开环位置伺服。
没有转矩模式、没有电流环给定、没有速度闭环，也没有 PVT；
"以指定角速度运动"这件事是**主机侧用步进逼近模拟出来的**（§6.5）。

---

## 5. 命令详解

### 5.1 查询类（**唯一会应答的一类**）

应答格式统一：`FF FF | ID | LEN | 0x80|CMD | 状态字节 0x00 | 数值… | CHK`
应答帧的 `LEN` = 整帧长度；数值大端。**SET 类命令一律不应答。**

| # | 命令 | 码值 | 请求（示例 ID=8） | 回包数据段（实测） | 状态 |
|---|---|---|---|---|---|
| 5.1.1 | Status | `0x05` | `FF FF 08 06 05 EF` | `00` | ✅ |
| 5.1.2 | GetPosition | `0x14` | `FF FF 08 06 14 E0` | `00 03 F4` → 1012 | ✅ |
| 5.1.3 | GetPID | `0x12` | `FF FF 08 06 12 E2` | `00 64 01 14` → 100/1/20 | ✅ |
| 5.1.4 | GetTemp | `0x13` | `FF FF 08 06 13 E1` | `00 20` → 32 ℃ | ✅ |
| 5.1.5 | GetCalibration | `0x15` | `FF FF 08 06 15 DF` | `00 74` → 116（📄 偏置 ±128 ⇒ 偏移 **−12**） | ✅（偶发漏答） |
| 5.1.6 | GetMotor | `0x16` | `FF FF 08 06 16 DE` | `00 01`（ID 10 为 `00 00`） | ✅ 语义未定 |
| 5.1.7 | GetMargin | `0x17` | `FF FF 08 06 17 DD` | `00 05` | ✅ |
| 5.1.8 | **GetLoad** | `0x18` | `FF FF 08 06 18 DC` | `00 00`（静止）/`00 78`（运动中） | ✅ |
| 5.1.9 | GetAccelerate | `0x19` | `FF FF 08 06 19 DB` | —— | ❌ 无应答 |
| 5.1.10 | GetPositionLimit | `0x1A` | `FF FF 08 06 1A DA` | `00 00 01 03 FF` → (1,1023) | ✅ |
| 5.1.11 | GetMotionPeriod | `0x0F` | `FF FF 08 06 0F E5` | —— | ❌ **无应答，且实测会改写限值**（见 §5.3 的危险说明） |
| 5.1.12 | GetFactoryTest | `0x7B` | `FF FF 08 06 7B 79` | `00 06 0E` | ✅ |
| 5.1.13 | GetModel / GetVersion | `0x01` / `0x02` | `FF FF 00 00 01 01` / `FF FF 00 00 02 00`（ID=0、LEN=0） | —— | ❌ 属控制器查询，无控制器板时不回复 |

**5.1.8 GetLoad 补充**（历史命名为 `GetCurrentLimit`，界面文字「负荷」）：

| 场景 | 读数 |
|---|---|
| 静止 | `0` |
| 运动中 | `120 → 32 → 2 → 28 → 0`（随加速/减速跳动） |
| 顶住位置限值 | `0`（到位后不再出力） |

它是本硬件上**唯一能反映输出力矩大小**的量，也是软件限力的依据。

---

### 5.2 运动类

#### 5.2.1 SetPosition `0x09`（LEN=8）✅ —— **含出力档位**

```
v    = (ledkind << 13) | (torque << 11) | (relative << 10) | position
d[5] = v >> 8                      # 档位(bit3=bit11, bit4=bit12) | relative(bit2) | 位置高位
d[6] = v & 0xFF
```

| 参数 | 取值 | 说明 |
|---|---|---|
| `position` | 0..1023（常规） | 目标 ADC；512 为机械中点。`d[5]` 只留 3 位给位置高位，**≥2048 会污染档位/relative 位**（库会直接拒绝） |
| `torque` / `level` | **0/1/2/3** | **出力档位**（不是使能！见下表） |
| `relative` | 0/1 | 1 = 相对当前位置 |

**出力档位**（对应「扭矩设置」的 High/Middle/Low 与轮子模式；显示为 `" H"/" M"/" L"/"W"`）：

| 档位 | 值 | `d[5]` 位 | 实测峰值负荷（受控对照，各 3 次中位数） | 现象 |
|---|---|---|---|---|
| **High** | 0 | bit3=0, bit4=0 | **226** | 动，出力最大 |
| **Middle**（默认） | 1 | bit3=1 | **152** | 动 |
| **Low** | 2 | bit4=1 | **118** | 动，出力最小 |
| **Wheel** | 3 | bit3=1, bit4=1 | 0 | **位置指令不生效** |

- 实测（2026-09-25，ID 8，起点/方向/幅度相同、交错重复）：`H 226 > M 152 > L 118`，单调且可复现；
  另一组同向大位移对照 `M 212 / L 135`
- **它不是使能开关**：档位 0 照样能运动；使能只有 `0x10 SetTorque`
- 该档位**直接作为 `SetPosition` 的 `d[5]` bit3-4 下发**（与 H/M/L/W 显示一一对应）
- 实例：`SetPosition(1, 512)`(M) → `FF FF 01 08 09 0A 00 E6`；H 档 `FF FF 08 08 09 03 CB 1B`；
  L 档 `FF FF 08 08 09 13 7A 5C`；W 档 `FF FF 08 08 09 1B 55 79`
- API：`Servo.set_position(position, *, level=P.LEVEL_MIDDLE, torque=..., relative=..., period_ms=..., wait=...)`
  以及 `set_angle/rotate/sweep/move_at_speed/robot.move/robot.play` 的 `level=` 参数；
  CLI：`--level H|M|L|W`（或 `0..3`）

#### 5.2.2 SetWheel `0x09`（LEN=8，与位置同码）📖

```
d[5] = (torque << 3) + (relative << 2) + (free << 1) + direction
d[6] = speed (0..255)
```

- 实例：`SetWheel(1, 100, torque=1, free=1, direction=0)` → `FF FF 01 08 09 0A 64 82`
- `direction`：0 = CW，1 = CCW；`free=1` = 空转（不锁死）
- API：`wheel(speed, direction=0, free=False, relative=False, torque=1, wait=False)`、`stop_wheel()`

#### 5.2.3 SetPeriod `0x0B`（LEN=8，16 位大端 ms）⚠️ **固件忽略**

实测：同一 44° 位移，命令周期 8000 / 3000 / 60 ms 与**完全不发**该命令，耗时都是 ~0.24 s
（舵机以自身最大速度 ~150–180 °/s 走完）。**"周期 = 走完时间"不成立**，因此本库用步进逼近实现角速度（§6.6）。

- 实例：`SetPeriod(1, 1000)` → `FF FF 01 08 0B 03 E8 03`
- API：`set_period(period_ms, wait=False)`

#### 5.2.4 SetSync `0x20` 与 SetNextPosition `0x21` ❌ **不响应**

| 变体 | 结果 |
|---|---|
| `SetNextPosition`（规范帧）+ 广播 `SetSync(254)` | 无动作、无应答 |
| `SetNextPosition`（原始大端位置）+ 逐 ID `SetSync(8)` | 无动作 |
| 先 `SetSync` 再 `SetNextPosition` 再 `SetSync`（arm-first） | 无动作 |
| 对照：同一时刻发 `SetPosition 0x09` | ✅ 立即生效 |

`0x21` 的位域（协议位域，仅存档）：

```
bit = 1 if position < 512 else 0
torque < 3 : v = (ledkind<<13) | (torque<<11) | (relative<<10) | position
torque >= 3: v = (ledkind<<13) | (torque<<11) | (relative<<10) | (freewheel<<9) | (bit<<8) | abs(angle)
d[5] = v >> 8 ; d[6] = v & 0xFF
```

- 实例：`SetNextPosition(1, 640, torque=1, relative=1, ledkind=3)` → `FF FF 01 08 21 6E 80 EA`
- 结论：**多关节同步请用逐关节 `SetPosition`**（本库默认），见 §3.5

---

### 5.3 参数类

| 命令 | 码值 | 请求布局 | 实例 | 回读验证 | 状态 |
|---|---|---|---|---|---|
| SetPID | `0x07` | `data = P, I, D`（LEN=9） | `FF FF 01 09 07 64 01 14 78` | `0x12` ✅ | ⚠️ **写入不可靠**：重复 3 次只有 1 次改变回读值（疑似写闪存期间丢帧） |
| **SetPID_Nosave** | `0x47` | 同上 | `FF FF 01 09 47 64 01 14 38` | `0x12` ✅ | ✅ **3/3 立即生效**（库与 CLI 默认先走这条，见 §6.8） |
| SetPositionLimit | `0x0F` | `min,max` 各 16 位大端（LEN=10） | `FF FF 0A 0A 0F 00 FF 03 00 DD`（min=255,max=768） | `0x1A` ✅ | ✅ |
| **SetMargin** | `0x0C` | `data[0] = margin` | `FF FF 01 07 0C 07 E7` | `0x17` ✅（写 7 → 回读 `00 07`） | ✅ |
| SetOffSet（零点偏差） | `0x0A` | `data[0]` = **带偏置的有符号值**（📄 `偏移量 = 原始值 − 128`，`0x80`=0） | `FF FF 01 07 0A 05 EB`；文档例 `0x8A` → +10、`0x81` → +1 | 读 `0x15` ✅ | ✅（📄 语义 + 回读一致） |
| SetTemp（阈值） | `0x08` | `data[0] = 阈值` | `FF FF 01 07 08 2D C5`（45 ℃） | ✖ `0x13` 读的是**实测温度**，无法验证阈值 | ⚠️ |
| **SetLoadLimit** | `0x0D` | `data[0] = 0..255` | `FF FF 01 07 0D C8 25`（200） | ✖ 写 8/200 后 `0x18` 无变化（对照 margin 立即可回读） | ⚠️ |
| SetAccelerate | `0x0E` | `data[0] = 0..255` | `FF FF 01 07 0E 0C E0` | ✖ `0x19` 无应答；**A/B 实测无效果** | ❌ 该固件未实现 |
| SetBaudrate | `0x22` | `data[0] = 档位` | `FF FF 01 07 22 01 D7` | —— | 📖 **高危**：改错波特率将失联 |

> `0x0E` 的依据：应答表 `ServoAck` 中 `0x89→0x8D` 连续对应 `0x0D→0x11`，其中 `0x8A = SET_ACCELERATE`，
> 故 `0x0E` 是唯一空位；协议文档未定义该方法。

> ⚠️ **危险：`0x0F` 的两副面孔（`SetPositionLimit` / `GetMotionPeriod`）**。实测（2026-09-26，ID 8）：
> **不带数据的 `0x0F`（LEN=6，如 `FF FF 08 06 0F E5`）会被固件当成 `SetPositionLimit` 执行**，
> 用解析缓冲里的残留 4 字节写入限值：发送前 `(1,1023)` → 发一次 → 回读 **`(113,257)`**；
> 再发一次仍 `(113,257)`（残留字节固定，结果可重复）。随后用真正的 `SetPositionLimit` 写
> `(100,900)` → 回读 `(100,900)` ✅（写链路正常，可复原）。**任何情况下都不要发送 `0x0F`**
> —— 这正是 ID 10 当前限值 `(255,768)` 的成因（见 §6.2 与 §11）。
>
> **库层防护**：`Servo.get_period()` 默认抛 `ProtocolError` 拒发，须显式 `get_period(unsafe=True)`；
> `tools/servo_probe.py` 已把 `0x0F` 移出默认查询清单（需 `--unsafe-0f`）。

---

### 5.4 使能与指示

| 命令 | 码值 | 请求布局 | 实例 | 状态 |
|---|---|---|---|---|
| **SetTorque** | `0x10` | `data[0] = 0/1` | 关：`FF FF 08 07 10 00 E3`；开：`FF FF 08 07 10 01 E2` | ✅ |
| **SetLED** | `0x11` | `d[5] = 颜色位（见下）` | 红灯（ID 7）`FF FF 07 07 11 80 63` | ✅ 颜色映射已确认 |

**LED 颜色位映射**（📄 协议文档，本库两种 API 产出的字节与之逐字节一致）：

| LED 状态 | `d[5]` | 官方命令（ID=7） | `set_led_color(r,g,b)` | `set_led(value)` |
|---|---|---|---|---|
| 全灭 | `0x00` | `FF FF 07 07 11 00 E3` | `(0,0,0)` | `0` |
| **红** | `0x80` = bit7 | `FF FF 07 07 11 80 63` | `(1,0,0)` | `4` |
| **绿** | `0x40` = bit6 | `FF FF 07 07 11 40 A3` | `(0,1,0)` | `2` |
| **蓝** | `0x20` = bit5 | `FF FF 07 07 11 20 C3` | `(0,0,1)` | `1` |
| 黄（红+绿） | `0xC0` | `FF FF 07 07 11 C0 23` | `(1,1,0)` | `6` |
| 紫（红+蓝） | `0xA0` | `FF FF 07 07 11 A0 43` | `(1,0,1)` | `5` |
| 青（绿+蓝） | `0x60` | `FF FF 07 07 11 60 83` | `(0,1,1)` | `3` |
| 白（全亮） | `0xE0` | `FF FF 07 07 11 E0 03` | `(1,1,1)` | `7` |

- 位序：`bit7=红、bit6=绿、bit5=蓝`；`bit4` 及以下不参与（4 参重载左移 5 位、6 参重载左移 4 位后落到同一批位上）
- 两个重载只是**编号方式不同**：`set_led(1)` = `set_led_color(b=1)` = 蓝
- 真机已确认可点亮（两轮目视，ID 8 / ID 10 均响应）；**LED 状态无法回读**——`Status`/`GetMotor` 在下发 LED 前后完全不变（实测 `GetMotor` 恒为 `00 00 01`/`00 00 00`）
- 文档只定义了这 8 种组合，**无亮度/闪烁指令**

**SetTorque 实测结论**（这是**使能**；出力档位见 §5.2.1）：

| 下发值 | 现象 |
|---|---|
| `0` | 位置指令**完全无效**（906 → 906 不动），关节失去保持力 |
| `1` | 立即恢复，位置指令生效 |
| `2` / `3` / `255` | **全部等同于 ON**（只认布尔，没有力矩幅值语义） |

> 协议文档只收录了 10 条指令（`0x05/0x85`、`0x06`、`0x09`、`0x14/0x94`、`0x10`、`0x15/0x95`、`0x0A`、`0x11`），
> 未涵盖 `0x07` PID、`0x0B` 周期、`0x0C` Margin、`0x0D/0x18` 负荷、`0x0E/0x19` 加速度、
> `0x0F/0x1A` 限值、`0x20/0x21` 同步、出厂测试与波特率——这些以本文（协议文档 + 实测）为准。

---

### 5.5 维护类

| 命令 | 码值 | 请求布局 | 实例 | 备注 |
|---|---|---|---|---|
| SetID | `0x06` | 帧地址 = **旧 ID**，`data[0] = 新 ID` | `1→9`：`FF FF 01 07 06 09 EB` | 之后须用新 ID 连接 |
| Reset | `0x01` | 无数据 | `FF FF 01 06 01 FA` | 回到出厂参数 |
| SetReboot | `0x02` | 无数据 | `FF FF 01 06 02 F9` | 重启（配 Bootloader 升级用） |
| SetCalibrationCurrpos | `0x23` | 无数据 | `FF FF 08 06 23 D1` | **上位机实际使用的零点校准**（改零点，慎用） |
| StartFactoryTest | `0x79` | 无数据 | `FF FF 08 06 79 7B` | 进入出厂测试 |
| SetFactoryTest | `0x7A` | 16 位大端 | `FF FF 01 08 7A 04 D2 A9`（1234） | 参数需实测 |
| GetFactoryTest | `0x7B` | —— | 回包 `00 06 0E` | ✅ |

> `SetCalibration` 未定义行为，真机从未下发过该命令；
> 同理 `SetDeadzone` 也未定义行为，死区设置实际走 `0x0C SetMargin`。

---

### 5.6 空实现 / 同码 / 高风险速查

| 项 | 说明 |
|---|---|
| 未定义行为 | `SetCalibration`、`SetDeadzone`、控制器侧 `Run`/`Stop` |
| 同码（靠上下文区分） | `0x01` GetModel=Reset；`0x02` GetVersion=SetReboot；`0x09` SetPosition=SetWheel；`0x0F` SetPositionLimit=GetMotionPeriod；`0x21` SetNextPosition=SetNextWheel；`0x12` GetPID=GetDeadzone |
| 危险命令 | `0x22 SetBaudrate`（失联风险）、`0x06 SetID`（改地址）、`0x23`（改零点）、`0x01 Reset`、**`0x0F` 一律禁止发送**（实测无数据也会用缓冲残值写限值，见 §5.3） |
| 静态复用缓冲区 | 未定义行为的方法会把上一条报文留在发送缓冲里 —— 不要以为那些功能真的在发命令 |

---

## 6. 数据语义

### 6.1 ADC ↔ 角度

| 常量 | 值 | 含义 |
|---|---|---|
| `ADC_CENTER` | 512 | 机械中点 = 0° |
| `RATIO_MAIN` | 0.2932551 | ≈ 300/1023，度/ADC（主方向） |
| `RATIO_UNDER` | 0.2636719 | ≈ 270/1024 |
| `RATIO_OVER` | 0.3222656 | ≈ 330/1024 |

```
角度 = (adc − 512) × 0.2932551        30° ≈ 102 ADC
adc  = round(512 + 角度 / 0.2932551)
```

API：`Servo.angle()`（读角度）、`set_angle()`（绝对角度）、`rotate()/jog_angle()`（相对角度）。
CLI 的 `read` / `angle` / `jog --degrees` 与 `.rsc` 播放都用同一套换算。

### 6.2 位置限值与夹紧 ✅

| 舵机 | 限值（实测） | 越限行为 |
|---|---|---|
| ID 8 | (1, 1023) | 目标被夹紧；到位（进入 margin）后**负荷回 0，不堵转** |
| ID 10 | (1, 1023)（2026-09-26 已从 `(255,768)` 复原） | 命令 1000 → 停在 **768**（当时限值），负荷保持 0 |

**夹紧是固件行为，与限值寄存器严格一致（同一命令 620 的 A/B 实测）**：

| 限值 | 命令 | 实测 | 结论 |
|---|---|---|---|
| `(540, 580)` | 620 | **580**（+19.9°），负荷 0 | ✅ 被夹在限值上 |
| `(1, 1023)` | 620 | **621**（+32.0°） | ✅ 夹紧解除，到达目标 |

两台舵机的限值现在都是 `(1,1023)`（= ±149.9°，"不限"）。写入接口
`set_position_limit()` 默认回读确认（见 §7.1）。

**限值是"每台舵机各自的、可写的配置"，与型号/固件无关。** 同一固件（MOS-S2 v1.0）的
两台可以完全不同：

- `(1,1023)` ≈ **−149.9°…+149.9°**（覆盖整个 10 位 ADC 量程，即"不限"）
- `(255,768)` = 512±256 ≈ **−75.4°…+75.1°**（像是一个按机械结构设的关节行程）

**ID 10 的 (255,768) 是联调期间被误写进去的，不是出厂差异**（2026-09-26 由日志定案）：

| 证据 | 内容 |
|---|---|
| 最早一次读限值（`logs/probe-20260925-221313.log`，22:13:13） | ID 10 = `00 00 01 03 FF` → **(1, 1023)** |
| 3 分钟后（`logs/probe-20260925-221625.log`，22:16:25） | ID 10 = `00 00 FF 03 00` → **(255, 768)** |
| 该窗口内的全部日志发帧 | **没有任何写帧**（全是读命令 + 一条 `SET_TORQUE`），说明写入来自未记日志的一次操作 |
| 机制复现（2026-09-26，ID 8） | 发无数据 `0x0F`（`FF FF 08 06 0F E5`）→ 限值 `(1,1023)` 变成 **`(113,257)`**；再发仍是 `(113,257)` |

⇒ **无数据的 `0x0F` 会被固件当作 SetPositionLimit，用解析缓冲里的残留 4 字节写限值。**
要恢复全量程：`python -m romanbo --port COM3 limit --id 10 --min 1 --max 1023`（写 flash，
写完用 `config` 回读确认）；若该关节机械行程确实只有 ±75°，则保留现值更安全。

### 6.3 Margin（死区）与"到位" ✅

`margin = 5`（实测两台都是 5）。位置误差进入该范围即视为到位，**之后不再持续出力**
（实测：顶住限位/目标时 `0x18` 恒为 0）。因此不存在"堵转保持"电流。

### 6.4 负荷（实测值）✅

见 §5.1.8。可用性：单次读数在静止时恒为 0，**只在运动中才有意义**；
要做保护请**采样式**监控（本库 `max_load` / `load_check_every`），阈值参考：

| 场景 | 实测负荷 |
|---|---|
| 静止 / 到位后 | 0 |
| 44° 快速动作（120 °/s 级） | 峰值 **110~130** |
| 步进逼近（每 100 ms 一步，小位移） | 一般 < 30 |

### 6.5 速度与角速度 ✅

- 硬件层：`SetPeriod` 无效（§5.2.3），舵机固定以最大速度（约 **150~180 °/s**）奔向目标
- 软件层（本库）：**步进逼近** —— 每 `interval_ms`（默认 100 ms）下发一个中间目标，
  使平均角速度 ≈ 期望值。步数 = `round(|Δadc| / 每步 ADC)`，余量均摊，末步精确落在目标（不过冲）

真机验证：

| 期望角速度 | 位移 | 实测角速度 | 误差 |
|---|---|---|---|
| 60 °/s | 88° | 57.6 °/s | −4% |
| 15 °/s | 44° | 14.4 °/s | −4% |

限制：① 无法超过硬件最大速度；② 步数取整带来约 ±2% 量化误差；
③ 每步至少 1 ADC —— 角速度过低时自动拉长间隔（`pick_step_interval`）。

**多关节实测（2026-09-26，ID 8/10，`chapter 25` scene 1，位移 77.1°）**：

| 期望角速度 | 耗时 | 纯运动理论值 | 实际达成速度 | 最终误差 | 峰值负荷 |
|---|---|---|---|---|---|
| 15 °/s | 6.0 s | 5.1 s | **12.9 °/s** | 7 / −1 ADC | 0 |
| 30 °/s | 3.2 s | 2.6 s | **24.1 °/s** | 0 / 0 | 1 |
| 60 °/s | 1.6 s | 1.3 s | **48.0 °/s** | 0 / 0 | 0 |
| 120 °/s | 0.9 s | 0.6 s | **85.7 °/s** | 0 / 0 | 0 |

⇒ 结论：**到位精度与速度无关（都是 ≤1 ADC ≈0.3°）**，但**实际速度约为期望值的 65~86%**——
每拍固定 100 ms 之外还有通信与主机侧开销（约 15 ms/拍），节拍越短占比越大。
要精确命中某个速度，可按实测比例略微上浮期望值（如 `--speed 18` 得到约 15 °/s）。

### 6.6 Wheel 模式位域 📖

`d[5] = (torque<<3) | (relative<<2) | (free<<1) | direction`，`d[6] = speed`；
`direction` 0=CW / 1=CCW。与位置指令**同码**，靠数据段位域区分。

### 6.7 空转（FreeWheel）

只有 `0x21` 的 `freewheel` 位与 `SetWheel(free=1)` 两处涉及；前者该固件不响应，
后者的效果**未能观测**（SET 类无应答，需要手感/目视确认）。❌/⚠️

---

### 6.8 PID（位置环增益）✅ 实测作用

PID 三字节（P/I/D 各 0..255）是**固件内位置环的唯一可调参数**；它不影响可用控制
模式（§4.1 仍是"位置 + 出力档位"），只改变"怎么追目标"。

**它存在于两个地方**：

| 位置 | 内容 | 实测值 |
|---|---|---|
| 舵机内部（闪存） | `0x12` 可读、`0x07/0x47` 可写 | ID 8 = `(100,1,20)`、ID 10 = `(50,0,5)` |
| 工程文件 `.rsc` | `Body.Setup` = **每通道一组 P/I/D** | 两个工程各 16 条目，全为 `(50,0,5)` |

⇒ 工程下载会把每通道 PID 写进舵机（ID 10 的当前值 = 文件值，ID 8 则等于
`SetPID` 示例帧的值 `FF FF 01 09 07 64 01 14`）。

**写入语义（实测，重要）**：

| 命令 | 语义 | 实测 |
|---|---|---|
| `0x47` SetPID_Nosave | 写 RAM（界面「PID 不保存」） | **3/3 立即生效** |
| `0x07` SetPID | 保存式（写闪存） | **3 次里仅 1 次生效**（回读不变）；闪存值何时载入未验证 |

故 `Servo.set_pid()` 默认**先发 `0x47`，`save=True` 时再补发 `0x07`**，并**回读
确认**（SET 类命令无 ACK，不校验就会静默失败）。

**对响应的影响（实测：同一起点、同一位移 100 ADC ≈ 29.3°，隔离读取）**：

| PID | t=0.10 s 已走 | 到位 | 过冲 | 结论 |
|---|---|---|---|---|
| `(5,1,20)` | ~30（1.3 s 才走 30） | 未到 | — | P 过小 → 几乎走不动 |
| `(20,1,20)` | — | 1.3 s 后仍差 9 | — | P 小 → 明显滞后/稳态误差 |
| `(50,0,5)`（工程值） | 305（51 ADC） | ~0.2 s | +1~+3 | 柔和 |
| `(100,1,20)`（ID 8） | 315~317（61 ADC） | ~0.2 s | +2~+3 | 更快起步 |
| `(255,1,20)` | 316~317 | ~0.2 s | −1 | 已到速度上限，**再大不再更快** |
| `(100,1,0)` D=0 | 314 | 0.35 s→353 | **+5** | 阻尼不足 → 过冲变大 |
| `(100,1,100)` D 大 | 295 | 0.60 s 仍未到 | −1 | 阻尼过大 → 明显变慢 |
| `(100,50,20)` I 大 | 312 | ~0.2 s | +2 | 与基线几乎相同 → **I 基本无用** |

**怎么用**：想让动作柔和/护齿轮 → 降 P 或加 D；想更快 → P 提到 100~150 即可；
**别用 PID 调速**（最大 ~150–180 °/s 是固件上限，速度请用步进逼近 §6.5）；
**I 保持 0**（到位判据是 `margin` 死区，误差进了死区就停止累积，I 无处发挥）。

---

## 7. Python 接口参考

> **完整度核对（2026-09-26）**：逐条遍历 `ServoCmd` 的全部码值（含同码双身份：
> `0x01/0x02/0x05/0x09/0x0F/0x12/0x18/0x21`），**每个码都已有 `protocol.build_*`
> 构造函数 + 对应的 `Servo` 方法**，无遗漏；CLI 覆盖常用命令，出厂测试
> （`0x79/0x7A/0x7B`）、波特率（`0x22`）、重启（`reboot`）与 `GetMotor`（`0x16`）
> 只在 Python API 层（见 §4 功能矩阵）。唯一例外是 `0x0F`：构造函数保留
> （用于复现协议原文），但库层**默认拒发**。

### 7.1 `Servo`（单舵机）

构造：`robot.servo(id)` 或 `robot[id]`；ID 范围 1..32。

**读（会应答）**

| 方法 | 返回 | 说明 |
|---|---|---|
| `get_position(timeout=None)` | `int` | ADC |
| `get_position_raw(timeout=None)` | `Response` | 原始回包 |
| `get_pid()` / `get_temperature()` / `get_margin()` / `get_calibration()` | `int`/`Tuple` | |
| `get_load()` | `int` | **实测负荷**（旧名 `get_current_limit`） |
| `get_position_limit()` | `(min, max)` | |
| `get_accelerate()` | `int` | 本机固件无应答 |
| `get_factory_test()` | `Response` | |
| `get_period(unsafe=True)` | `int` | 与写限值同码：**默认抛 `ProtocolError` 拒发**，须显式 `unsafe=True`（见 §5.3） |
| `angle(ratio=, center=)` | `float` | 当前角度 |
| `status(timeout=, retries=)` / `ping(timeout=0.15)` | `Response`/`bool` | 在线检测 |
| `read_config(timeout=0.2)` | `ServoConfig` | 一次性读全部参数 |

**运动**

| 方法 | 关键参数 | 说明 |
|---|---|---|
| `set_position(position, *, level=None, period_ms=None, torque=1, relative=0, wait=False)` | 别名 `move`；`level` = 出力档位 0/1/2/3（`P.LEVEL_*`） | 位置定位 |
| `set_angle(degrees, period_ms=None, speed_dps=None, current=None, torque=1, wait=False, interval_ms=None, level=None, max_load=None, ...)` | | 绝对角度；给 `speed_dps` 走步进 |
| `rotate(degrees, period_ms=None, speed_dps=None, torque=1, wait=False, current=None, interval_ms=None, level=None, max_load=None, ...)` | 别名 `jog_angle` | 相对角度 |
| `move_at_speed(target, dps, current=None, interval_ms=None, torque=None, wait=True, max_load=None, load_check_every=1, on_step=None)` | 返回下发的中间目标列表 | **恒角速度**核心 |
| `jog(delta, period_ms=None)` | | 相对 ADC 小幅点动 |
| `sweep(low, high, cycles=3, period_ms=None, speed_dps=None, torque=1, readback=True, settle=0.3, return_home=True, interval_ms=None, max_load=None, on_step=None)` | | 往复运动（对应「反复动作」） |
| `wheel(speed, direction=0, free=False, relative=False, torque=1, wait=False)` / `stop_wheel()` | | 轮子模式 |
| `next_position(position, period_ms=None, torque=1, relative=0, freewheel=0, ledkind=0, angle=0, sync=False, wait=False)` | | 本机固件不响应 |
| `sync(wait=False)` | | 本机固件不响应 |

**参数与状态**

| 方法 | 说明 |
|---|---|
| `torque(on=True, wait=False)` | 力矩**使能**（`0x10`，仅布尔）；出力档位用各运动方法的 `level=` |
| `set_pid(p, i, d, save=True, verify=True, retries=4)` | 先 `0x47`（立即生效）再 `0x07`（持久化），**默认回读确认**；失败抛 `ProtocolError`。返回回读到的 `(P,I,D)`（见 §6.8） |
| `set_position_limit(minimum, maximum, verify=True, retries=4)` | **默认回读确认**（限值掉电保存 + 硬夹紧，写错会限制行程；实测 `SET` 会静默丢写）；返回回读到的 `(min,max)`，失败抛 `ProtocolError` |
| `set_margin(margin)` / `set_offset(offset)` | |
| `set_temp(temp)` / `set_load_limit(limit)`（旧名 `set_current_limit`）/ `set_accelerate(value)` | 三项均**无法回读验证** |
| `set_led(value)` / `set_led_color(red, green, blue)` | 颜色位序 bit7=红 / bit6=绿 / bit5=蓝（见 §5.4） |
| `set_calibration_offset(offset)` / `get_calibration_offset()` | 零点偏差**实际值**（内部 ±128 偏置；`0x80`=0） |
| `set_calibration_currpos()`（别名 `calibration_zero`） | 改零点 |
| `set_id(new_id)` / `reset()` / `reboot()` / `set_baudrate(code)` | 高危 |
| `start_factory_test()` / `set_factory_test(value)` | |
| `last_peak_load` | 最近一次带 `max_load` 运动的峰值负荷 |

### 7.2 `RomanboRobot`

| 方法 | 说明 |
|---|---|
| `connect(port, mock=False, handshake=True, **kw)` / `open()` / `close()` | 连接（`mock=True` 离线模拟） |
| `scan(start=1, end=32, timeout=0.15, quarantine=0.4)` | 扫描在线 ID（**已处理静默期**） |
| `capture(ids=None, timeout=0.2)` / `read_positions(...)` | 示教批量回读 |
| `move(positions, period_ms=500, torque=1, relative=0, mode="position", speed_dps=None, start=None, step_interval_ms=None, max_load=None, load_check_every=1, sync=True, sync_id=254, wait=False, led=None)` | 多关节运动（位置模式 / 步进角速度） |
| `move_frame(frame, period_ms=None, torque=1, mode="position", sync=True, id_offset=None)` | 执行一个 `.rsc` 帧 |
| `play(frames, loop=False, speed_dps=None, speed_scale=1.0, torque=1, mode="position", id_offset=None, wait_frame=True, start_positions=None, step_interval_ms=None, max_load=None, load_check_every=1, on_frame=None, stop_event=None)` | 播放关键帧序列 |
| `play_rsc(path, scene=None, ids=None, ...)` | 加载 `.rsc` 并播放；**`ids` 只驱动在线关节**——整机文件（17 通道）直接照发会寻址到不存在的 ID，触发 0.4 s 总线静默期（§3.2），节奏会被打乱 |
| `torque_all(on=True, ids=None)` / `led_all(value, ids=None)` | 批量 |
| `send(frame)` / `request(frame, expect_id=, expect_cmd=, timeout=, retries=, raise_on_error=)` / `poll()` / `flush_input()` / `drain()` | 底层收发 |
| `controller_*` | 控制器板兼容命令（无板无法验证） |

### 7.3 常量

| 常量 | 值 | 位置 |
|---|---|---|
| `CONTROLLER_ID` / `BROADCAST_ID` | 0 / 254 | `protocol` |
| `SERVO_ID_MIN/MAX`、`SENSOR_ID_MIN/MAX` | 1/32、224/253 | `protocol` |
| `HEADER`、`MIN_FRAME_LEN`/`MAX_FRAME_LEN` | 0xFF、6/255 | `protocol` |
| `SERVO_RESPONSE_DELAY` | 0.02 s | `protocol` |
| `BUS_QUARANTINE` | 0.4 s | `protocol` |
| `DEFAULT_ACK_TIMEOUT` / `SCAN_PROBE_TIMEOUT` / `SCAN_QUARANTINE` | 0.3 / 0.15 / 0.4 s | `protocol` |
| `MIN_FRAME_GAP` | 0.002 s | `protocol` |
| `LEVEL_HIGH/MIDDLE/LOW/WHEEL` | 0/1/2/3 | `protocol` |
| `ADC_MIN/MAX/CENTER` | 0/1023/512 | `joints` |
| `RATIO_MAIN/UNDER/OVER` | 0.2932551 / 0.2636719 / 0.3222656 | `joints` |
| `MIN_PERIOD_MS`/`MAX_PERIOD_MS` | 100 / 30000 | `joints` |
| `DEFAULT_STEP_INTERVAL_MS` | 100 ms | `joints` |
| `DIRECTION_NORMAL/REVERSE`、`WHEEL_CW/CCW` | 0/1、0/1 | `joints` |

### 7.4 异常

| 异常 | 触发 |
|---|---|
| `TimeoutError` | 回包超时（重试用尽）。**注意 SET 类命令必然超时**（不应答） |
| `ProtocolError` | 帧格式/校验错误 |
| `ErrorResponse` | 设备返回错误帧（`CMD=0x80`），`.code` 为错误码 |
| `LoadLimitExceeded` | 软件限力触发，`.as_dict()` → `{id, load, limit, step, position}` |

舵机错误码：`1` 指令错误、`2` 校验错误、`3` **过载**、`4` 超限、
`5` 包错误、`6` 下载/位置错误、`7` 程序为空。
控制器错误码：`1` 马达错误、`2` 校验、`3` 请求超时、`4` 指令、
`5` 未连接、`6` 下载/位置、`7` 程序为空。

### 7.5 组合用法

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:                 # 默认超时 0.3s、重试 2 次、重试间隔 0.45s
    ids = robot.scan(1, 32)                         # ✅ 内部已处理 0.4s 静默期
    s = robot.servo(8)

    # 1) 恒角速度 + 软件限力
    s.torque(True)
    try:
        s.move_at_speed(700, 60, max_load=60)       # 60 °/s，负荷 > 60 立即停
    except LoadLimitExceeded as exc:
        print("限力中止:", exc.as_dict())

    # 2) 多关节同步（各自按 60 °/s，取最大位移定节拍，起点偏差 <15ms）
    start = robot.capture([8, 10])
    robot.move({8: 620, 10: 520}, speed_dps=60, start=start, max_load=60)

    # 3) 示教 → 导出
    robot.torque_all(False, [8, 10])                # 松扭矩，手扳到位
    positions = robot.capture([8, 10])
    robot.torque_all(True, [8, 10])

    # 4) 播放 .rsc（忽略文件 Period，按 60 °/s 重放关键帧）
    robot.play_rsc("../example/chapter 01.rsc", speed_dps=60, max_load=60)
```

---

## 8. CLI ↔ 协议对照

| CLI | 协议 | 等价 Python |
|---|---|---|
| `scan --start --end --probe-timeout --quarantine` | `Status 0x05` ×N | `robot.scan(...)` |
| `read --ids` | `GetPosition 0x14` | `servo.get_position()` |
| `teach --ids` | 同上（批量） | `robot.capture(ids)` |
| `config --id/--ids` | `0x12/0x13/0x14/0x15/0x17/0x18/0x19/0x1A` | `servo.read_config()` |
| `load --watch --interval` | `GetLoad 0x18` | `servo.get_load()` |
| `jog --degrees/--delta (--speed\|--period) --max-load` | `0x0B`+`0x09`（或步进） | `rotate()` / `move_at_speed()` |
| `angle --degrees` | `0x09` | `set_angle()` |
| `sweep --low/--high --cycles` | `0x0C`+`0x09` | `sweep()` |
| `move --targets (--speed) --max-load` | `0x0B`+`0x09` 逐关节 | `robot.move(...)` |
| `torque on/off --ids` | `SetTorque 0x10` | `torque()` |
| `led --value/--color` | `SetLED 0x11` | `set_led()` / `set_led_color()` |
| `pid --p --i --d [--nosave]` | `0x07` / `0x47` | `set_pid(..., save=)` |
| `limit --min --max` | `SetPositionLimit 0x0F` | `set_position_limit()`（写入后**回读确认**，失败退出码 1） |
| `teach --ids [--file]` | `GetPosition 0x14` | `robot.capture()`；带 `--file` 时把姿态追加为一帧（示教会话 JSON） |
| `export --file --out` | ——（离线） | `rsc.write_project()` / `rsc.dumps_project()`：生成**重复键风格**的 `.rsc`（`json.dumps` 做不到） |
| `param margin/temp/offset/period/accelerate/current-limit` | `0x0C/0x08/0x0A/0x0B/0x0E/0x0D` | `set_margin()` 等 |
| `wheel --speed [--ccw] [--free] [--relative]` | `SetWheel 0x09` | `wheel()` |
| `sync --id` | `SetSync 0x20` | `sync()`（本机无效） |
| `calib` | `0x23` | `set_calibration_currpos()` |
| `set-id --new-id` | `SetID 0x06` | `set_id()` |
| `reset` | `0x01` | `reset()` |
| `play FILE --speed --max-load --mode` | 步进序列 | `robot.play_rsc(...)` |
| `info FILE` | —（离线） | `RscProject.summary()` |

退出码：`0` 成功、`1` 未找到设备/无帧、`4` **软件限力中止**、`130` 用户中断。

---

## 9. 参考实现（无依赖，可直接移植）

下面这段不依赖本库，展示协议的全部关键点（帧构造、校验、收包、静默期、位置/角度换算），
可原样搬到 C / Arduino / STM32 工程。

```python
from __future__ import annotations

import time

import serial

HEADER, BROADCAST = 0xFF, 0xFE
QUARANTINE = 0.4            # 无设备 ID 之后的总线静默期
RATIO = 0.2932551           # 度 / ADC
CENTER = 512

def checksum(frame: bytes) -> int:
    return (0x100 - (sum(frame[:-1]) & 0xFF)) & 0xFF

def frame(cmd: int, dev: int, data: bytes = b"", size: int | None = None) -> bytes:
    n = size or (6 + len(data))
    f = bytearray(n)
    f[0] = f[1] = HEADER
    f[2], f[3], f[4] = dev & 0xFF, n & 0xFF, cmd & 0xFF
    f[5:5 + len(data)] = data
    f[n - 1] = checksum(f)
    return bytes(f)

class Bus:
    def __init__(self, port: str, baud: int = 115200):
        self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1,
                                 timeout=0.02, write_timeout=0.5,
                                 rtscts=False, dsrdtr=False)
        self.ser.dtr = self.ser.rts = True      # 与设备要求一致
        self.buf = bytearray()

    def write(self, f: bytes) -> None:
        self.ser.write(f)

    def read_frame(self, timeout: float = 0.3):
        """读一帧：FF FF | ID | LEN | CMD | DATA | CHK（回包 LEN = 整帧长度）。"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.buf += self.ser.read(self.ser.in_waiting or 1)
            while len(self.buf) >= 6:
                if self.buf[0] != HEADER or self.buf[1] != HEADER:
                    del self.buf[0]                      # 重新同步
                    continue
                n = self.buf[3]
                if n < 6 or len(self.buf) < n:
                    break                                # 等剩余字节
                raw = bytes(self.buf[:n]); del self.buf[:n]
                if sum(raw) & 0xFF:
                    continue                             # 校验不过，丢弃
                if raw[4] == 0x80:
                    raise RuntimeError(f"设备错误码 {raw[5]}")
                return {"id": raw[2], "cmd": raw[4],
                        "data": raw[5:-1], "raw": raw}
        return None

    def request(self, cmd: int, dev: int, data: bytes = b"",
                size: int | None = None, timeout: float = 0.3):
        self.ser.reset_input_buffer(); self.buf.clear()
        self.write(frame(cmd, dev, data, size))
        return self.read_frame(timeout)

def set_torque(bus, dev, on: bool):                 # 0x10
    bus.write(frame(0x10, dev, bytes([1 if on else 0])))

def set_position(bus, dev, adc, torque=1, relative=0):   # 0x09
    adc = max(0, min(1023, int(adc)))
    high = (adc >> 8) | (torque << 3) | (relative << 2)
    bus.write(frame(0x09, dev, bytes([high & 0xFF, adc & 0xFF]), 8))

def get_position(bus, dev, timeout=0.3) -> int:     # 0x14
    resp = bus.request(0x14, dev, timeout=timeout)
    return (resp["data"][1] << 8) | resp["data"][2]

def get_load(bus, dev):                             # 0x18（实测负荷）
    return bus.request(0x18, dev)["data"][1]

def move_at_speed(bus, dev, target, dps, interval=0.1):
    """恒角速度：按节拍下发中间目标（硬件本身忽略 SetPeriod）。"""
    cur = get_position(bus, dev)
    total = int(round(abs(target - cur) / max(1, round(dps * interval / RATIO))))
    for k in range(1, total + 1):
        set_position(bus, dev, round(cur + (target - cur) * k / total))
        time.sleep(interval)

def scan(bus, start=1, end=32):
    """扫描：探测失败后必须等静默期，否则会漏掉后面的设备。"""
    found, failed = [], False
    for dev in range(start, end + 1):
        if failed:
            time.sleep(QUARANTINE)
        bus.ser.reset_input_buffer(); bus.buf.clear()
        bus.write(frame(0x05, dev))
        resp = bus.read_frame(0.15)
        failed = resp is None
        if not failed:
            found.append(dev)
    return found
```

**移植提示**

- 目标机需一条 UART（115200 8N1）；半双工总线需处理收发方向切换与静默期
- 校验就是一个「整帧求和为 0」的字节，无需查表
- 位置指令的 `d[5]` 同时承载位置高位与 torque/relative 位，**位置务必限制在 0..1023**
- 若要做"平滑慢速运动"，不要指望 `SetPeriod`，请自己按节拍喂中间点（§6.5）

---

## 10. 未支持 / 未验证清单

| 项 | 状态 | 建议验证方法 |
|---|---|---|
| 力矩闭环（设定"目标力矩值"） | ❌ 协议不存在 | ——（需换舵机型号） |
| 出力档位（H/M/L/W） | ✅ 已实测（`0x09` 的 `d[5]` bit3-4，见 §5.2.1） | —— |
| 电流环 / 电流限流 | ⚠️ `0x0D` 写入无法回读；`0x18` 读的是实测负荷 | 加负载对比堵转电流 |
| `SetPeriod` 调速 | ❌ 固件忽略 | 已用三种周期 + 不发送对照验证 |
| `SetNextPosition` + `SetSync` | ❌ 不响应 | 三种变体已试 |
| `GetAccelerate` | ❌ 无应答 | 已测 6 次全超时 |
| 舵机 `GetModel` / `GetVersion` | ❌ 无应答（控制器帧） | 接上控制器板再试 |
| ~~`GetMotionPeriod` (`0x0F`)~~ | **已复现：会写限值** —— 无数据帧也把缓冲残值当 min/max 写入（`(1,1023)` → `(113,257)`，可重复） | 结论已确定：**不要发送**该命令；库层已默认拒发（`get_period(unsafe=True)` 才发，`servo_probe` 需 `--unsafe-0f`） |
| `SetLoadLimit` (`0x0D`) 限流效果 | ⚠️ 无法回读 | 加负载后对比堵转电流/温升 |
| ~~`SetAccelerate` (`0x0E`)~~ | **已测：无效果**（accel 0 vs 200，44° 位移到位时间 0.255/0.257 s、轨迹逐点一致；写入不破坏通信）+ `0x19` 不可回读 | 换固件/型号才能有"加速度"维度 |
| `SetTemp` (`0x08`) 阈值 | ⚠️ 无法回读 | 用热风/冷喷改变实际温度，观察是否触发保护 |
| **`0x07`（保存式 PID）是否真的落盘、并在开机时载入** | ⚠️ 写入本身 3 次仅 1 次即时生效；落盘与载入**均未验证**（`0x02` 重启后 RAM 值不变） | 写 `0x07` 后**真断电重上电**，再读 `0x12` 对比 |
| FreeWheel / 空转 | ⚠️ 效果未观测 | 下发 `SetWheel(free=1)` 后用手扳动对比手感 |
| `SetID` / `Reset` / `SetReboot` / `SetBaudrate` / 零点校准 | 📖 未做破坏性验证 | 逐个在实验室单独验证并备份原参数 |
| LED 亮度 / 闪烁 | 📄 协议文档只定义 8 种颜色组合 | 无对应指令，无需验证 |
| ~~`0x09` 的 `torque` 位=0 能否驱动~~ | **已解决**：档位 0（H）照样能运动，该字段是**出力档位**而非使能（见 §5.2.1） | —— |
| 波特率 **1000000 bps** | 📄 文档称"常见 115200 或 1000000（需实际确认）" | 串口改 1 Mbps 后跑 `scan` |
| 舵机 ID 范围 | 📄 文档称 ID `0–253`（254 广播）；本库采用舵机 `1–32`、传感器 `224–253` | 对 `33–223` 逐个 `scan` 看是否有设备 |
| 零点偏差的物理意义 | 📄 文档给出 ±128 偏置，但单位（度？ADC？）未说明 | 写 +10 后用 `read` 看位置变化量 |
| `GetMotor` (`0x16`) 语义 | ⚠️ 未定 | 对比不同状态下回包（已知与扭矩/运动/空转无关） |
| 控制器板全部命令 | 📖 无板未验证 | 接上控制器板后按 `CtrlCmd` 表逐个试 |

---

## 11. 本版本纠正的旧结论

| 旧结论（错误/不完整） | 实测结论 | 影响 |
|---|---|---|
| "舵机应答延迟 0.3~0.9 s" | 应答仅 **10~20 ms**；0.4 s 是**无设备 ID 之后的总线静默期** | 扫描超时可从 0.7 s 降到 0.15 s；短超时扫描不再漏扫 |
| `GET_CURRENT_LIMIT (0x18)` = 读限制值 | 它是**实测负荷**；写 `0x0D` 不会让它变化 | 库改名 `get_load()`；成为软件限力的唯一依据 |
| "周期 = 走完时间，可调速" | 固件**忽略** `SetPeriod` | 角速度必须用步进逼近实现 |
| "`0x21`+`0x20` 可做多关节同步" | 该固件**不响应** | 多关节改用逐关节 `SetPosition`（起点偏差 <40 ms） |
| "`SET_TORQUE` 可设力矩大小" | `0x10` 只认布尔 0/1（2/3/255 均为 ON）✓ 仍然成立 | 但**出力档位在 `0x09` 的 `d[5]` bit3-4** |
| "`0x09` 的 `torque` 只是布尔使能" | ❌ 错：它是**出力档位**（H/M/L/W 共 4 档），且**确实生效** | 本库新增 `level=`/`--level`；实测 H226 > M152 > L118 |
| "`0x0E` 是加速度设置（码值推断）" | **实测无效果**：accel 0 vs 200，同一段 44° 位移到位时间 **0.255 / 0.257 s**、逐点轨迹一致（负荷差在运行间噪声内），且 `0x19` 不可回读 | **控制维度只剩「位置 + 出力档位」**：减速/柔顺只能靠主机侧步进逼近，不能用舵机自身加速度 |
| "`SetPID 0x07`（保存式）是正常写入路径，`0x47` 只是勾选项" | **写反了**：`0x07` 重复 3 次只有 1 次生效（回读不变），`0x47` **3/3 立即生效** | `set_pid()` 改为先 `0x47` 再 `0x07`，并**默认回读确认**；`SET` 命令无 ACK，静默丢写是真实风险 |
| "PID 只是界面上的三个数字" | 它是**固件位置环增益**：P 决定起步力度/跟踪（P=5 几乎走不动、P=255 已到速度上限）；D 决定阻尼（D=0 过冲 +5、D=100 明显变慢）；I 因 `margin` 死区几乎无用 | 详见 §6.8；工程文件 `Body.Setup` 每通道存一份 PID |
| "两台舵机出厂状态不同（ID 8 限值 `1..1023`、ID 10 限值 `255..768`）" | **错**：日志证明 ID 10 在 2026-09-25 **22:13:13 仍是 `(1,1023)`**，22:16:25 才变成 `(255,768)` —— 是**联调期间被写进去的** | 限值不是型号属性而是可写配置；成因是无数据 `0x0F` 会用缓冲残值写限值（已复现） |
| 扫描用 0.7 s/ID（全范围 23 s） | 0.15 s 探测 + 失败后 0.4 s 静默 | 全范围 **16.5 s** 且更可靠 |
| LED 位偏移"两个重载不一致、需目视标定" | 协议文档确认：`bit7=红、bit6=绿、bit5=蓝`；两个重载只是编号不同（`set_led(1)`=蓝） | LED 从 📖 升为 ✅；接口无需改动，已补 LED 对照测试 |
| 零点偏差含义不明（读数 116） | 协议文档：`偏移量 = 原始值 − 128` ⇒ 本机偏移 **−12** | 新增 `set_calibration_offset()` / `get_calibration_offset()` |
| ——（新增）| **协议文档自身有 3 处校验和笔误**：`0x09` 例（`55`→`4F`）、`0x94` 例（`30`→`2A`）、`0x14` 例（`E7`→`E1`，沿用了 ID=1 的校验和） | 一律以算法为准；已写成回归测试 `tests/test_doc_led.py` |
