# 协议速览

> 本节只给**要点**。字节级逐命令参考（请求/回包布局、逐条实例报文、参数语义、
> 全量未验证项清单）见 [字节级协议规格](SERVO_SPEC.md)。

## 帧格式（收发同构）

```
FF FF | ID | LEN | CMD | DATA ... | CHK
```

- `LEN` = **整帧字节数**（最短 6）；数据段长度 = `LEN - 6`
- `CHK`：`sum(整帧) & 0xFF == 0`，即 `chk = (~sum(frame[:-1]) + 1) & 0xFF`
- 例：`FF FF 01 08 09 0A 00 E6` → 舵机 1，8 字节，`0x09` 位置指令
- `GetModel` / `GetVersion` 两个查询用 `LEN=0`、`ID=0`（发给控制器）
- 应答方向 `LEN` 语义不同（实测）：**请求**为整帧长度，**应答**为数据段长度
  （整帧 = `LEN + 6`）

## 串口与地址

- **115200 8N1**，`DtrEnable=true`、`RtsEnable=true`（另有 57600 备选档）
- 地址分配：`0` = 控制器板；`1..32` = 舵机；`224..253` = 传感器；`254` = 广播
- USB 侧为 FTDI；上位机用普通串口 API（非 D2XX）

## 舵机命令码（`ServoCmd`）

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
| 0x0B | SetPeriod | 16 位大端 ms | ⚠️ **固件忽略**（见[实测结论 §1](FINDINGS.md)） |
| 0x0C | SetMargin（=Deadzone） | `d[5]=margin` | ✅ 写入立即回读（5→7） |
| 0x0D | SetCurrentLimit（界面「负荷」） | `d[5]=值` | ⚠️ 写入后 `0x18` 无变化，限流效果未验证 |
| 0x0E | **SetAccelerate** | `d[5]=值` | ❌ **实测无效果**（0 vs 200 的到位时间/轨迹一致；`0x19` 不可回读） |
| 0x0F | SetPositionLimit | `min,max` 各 16 位大端（LEN=10） | ✅ 可经 `0x1A` 回读 |
| 0x10 | **SetTorque** | `d[5]=0/1` | ✅ 真机有效（关后位置指令不动）；只认布尔 |
| 0x11 | SetLED | 4 参版 `d[5]=data<<5`；6 参版 `((r?8)+(g?4)+(b?2))<<4` | ✅ 颜色位：bit7=红、bit6=绿、bit5=蓝（协议文档 + 逐字节交叉校验，见 [规格 §5.4](SERVO_SPEC.md)） |
| 0x12~0x1A | GetPID/Temp/Position/Calibration/Motor/Margin/**Load**/Accelerate/PositionLimit | — | 见 [实测结论 §5](FINDINGS.md) |
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

## 应答（`ServoAck`）

- 实测**只有 GET 类与 `Status` 会应答**，且 `ACK = 0x80 | CMD`（0x14→0x94，0x18→0x98…）
- **所有 SET 类命令都不应答** —— `set_*` 默认「只发不等」，要确认结果请回读
- 协议常量表里还有一批顺序编码的 ACK（0x83~0x8D），在本机固件上从未出现，**不要依赖它们做同步**
- 数据段首字节恒为 `0x00`（状态位），数值从第 2 字节开始；错误帧 `CMD=0x80`

## 控制器命令（未验证）

`CtrlCmd`：`0x03` 状态、`0x04/0x05/0x06` 下载相关、`0x07` Play、`0x08` Sequence、
`0x0A` SetLED、`0x10` SetTorque、`0x14` GetPosition、广播 ID 254 的 `SetOrder`。
本机没有控制器板，这些接口**未在真机验证**（`Run`/`Stop` 未定义行为）。
