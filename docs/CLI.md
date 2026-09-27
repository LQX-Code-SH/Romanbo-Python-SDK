# 命令行参考

> **本文只讲命令行怎么用**（子命令、选项、退出码、输出契约）。
> 命令背后的硬件行为与实测依据见[真机实测结论](FINDINGS.md)。

```bash
python -m romanbo [全局选项] <命令> [命令选项]
```

**全局选项**：`-p/--port`、`--mock`、`--baudrate`、`--timeout`（回包超时秒）、
`--json`（结构化输出）、`--frames`（打印收发原始帧）。

## 命令一览

| 命令 | 说明与主要选项 |
|---|---|
| `selftest` | 离线校验报文编码（基准向量，60 条） |
| `ports` | 枚举串口并**实测能否打开**（离线，不需要 `-p`）。打不开时按 `errno` 区分两种成因并给出处置：`EACCES` → 权限不足（`dialout` 组）；`EBUSY` → 已被别的进程排他持有（`lsof`/`fuser` 查） |
| `handshake` | 连接自检（型号 + 固件版本，需控制器板） |
| `scan` | `--start --end --probe-timeout`（默认 0.15 s）、`--quarantine`（探测失败后的总线静默期，默认 0.4 s） |
| `read` | `--ids 8,10`（ADC 与角度）；对不存在的 ID 只等一次超时 |
| `teach` | `--ids`：批量回读位置；加 `--file taught.json` 则把当前姿态**追加为一帧**，多次执行累积成动作序列 |
| `export` | `--file taught.json --out taught.rsc`：把示教会话导出为**可播放的 `.rsc` 工程文件**（离线） |
| `config` | `--id/--ids`：位置、PID、限值、**load（实测负荷）**、acceleration、margin、温度、零点 |
| `load` | `--id/--ids --watch N --interval 秒`：实测负荷采样 |
| `jog` | `--id --degrees 度 \| --delta ADC`；速度用 `--speed 度/秒` 或 `--period ms`；可加 `--max-load`、`--load-every N`、`--level` |
| `angle` | `--id --degrees`（绝对角度，中点 512 = 0°），速度选项同上 |
| `sweep` | `--id --degrees/--low/--high --cycles`；`--max-load`、`--load-every N`；`--no-readback --no-return --loop` |
| `move` | `--targets 8:600,10:480`；`--speed`（角速度）/`--period`；`--max-load`、`--load-every N`；`--no-capture`；`--settle 秒`（下发后等到位再返回，`--readback` 时默认 0.3）；`--readback`（回读实际位置与误差） |
| `torque` | `on\|off` `--ids`：力矩使能开关 |
| `led` | `--id/--ids --value N` 或 `--color 1,0,1` |
| `pid` | `--id --p --i --d [--nosave]`：写入后**回读确认**，失败退出码 1 |
| `limit` | `--id --min --max` 位置限值（掉电保存；写入后**回读确认**，失败退出码 1） |
| `param` | `current-limit \| margin \| temp \| offset \| period \| accelerate --value N` |
| `wheel` | `--id --speed 0..255 [--ccw] [--free] [--relative]` |
| `sync` | `--id`（默认 254 广播）发同步触发 |
| `calib` | 把当前位置设为零点（`0x23`） |
| `set-id` / `reset` | 改 ID / 复位 |
| `play` | `文件 [--scene N] [--ids 8,10] [--loop] [--speed 度每秒] [--speed-scale 周期倍率] [--no-capture] [--max-load] [--load-every N] [--torque on/off]`；`--ids` 只驱动在线关节（整机文件务必指定，否则向不存在的 ID 发帧会触发 0.4 s 总线静默期） |
| `info` | 打印 `.rsc` 工程摘要（离线） |
| `webui` | 启动**本地可视化控制台**（浏览器操作）：`--http-host`（默认 `127.0.0.1`）、`--http-port`（默认 `8765`）、`--open`（自动开浏览器）；配合全局 `-p/--port` 或 `--mock` 会自动连接。见[可视化控制台](WEBUI.md) |

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 正常 |
| `1` | 参数错误 / 回读确认失败 |
| `4` | **因软件限力（`--max-load`）中止**（见 [软件限力](LOAD_LIMITING.md)） |
| `5` | 串口/通信错误：打不开端口、**设备未应答（超时）**、设备返回错误帧 |
| `130` | 用户中断（Ctrl+C） |

## `--json` 的输出契约

**stdout 只有结果**：`--json` 时进度与日志行（`读取起始位置…`、`sweep` 的每步报告、
`play` 的逐帧行、`--frames` 的原始帧）一律改走 **stderr**，因此可以直接消费：

```bash
python -m romanbo --port COM3 --json read --ids 8,10 | jq '.["8"].adc'
python -m romanbo --port COM3 --json move --targets 8:600 --speed 30 --readback \
    | jq .readback
```

`ports --json` 回一个**数组**：每项含 `device` / `description` / `hwid` / `busy`；打不开时
再加 `error`（异常类名）与 `reason`（`permission` / `busy` / `unknown`，由 `errno` 推出）：

```bash
python -m romanbo --json ports | jq '.[] | select(.busy)'
# {"device":"/dev/ttyUSB1","description":"FT230X Basic UART","hwid":"...",
#  "busy":true,"error":"SerialException","reason":"permission"}
```

> 结果 JSON 是**缩进美化**的多行文档。**所有命令**在 `--json` 下都有结果，包括确认类
> 命令：`torque` / `calib` / `reset` / `wheel` / `led` 回 `ids`（及各自的实际下发值），
> `sync` / `set-id` 回 `id`（后者还有 `new_id`），`pid` / `limit` 回**回读确认**后的值。
> 多 ID 命令汇总成**一份** JSON，键是 ID 字符串：

```bash
python -m romanbo --port COM3 --json torque off --ids 8,10 | jq .ids
python -m romanbo --port COM3 --json limit --ids 8,10 --min 100 --max 900 | jq .limit
# {"8": [100, 900], "10": [100, 900]}
```

## 常用组合

```bash
# 离线：无硬件跑通全流程
python -m romanbo --mock scan
python -m romanbo --mock --json read --ids 1,2

# 真机：扫描 → 读参数 → 看负荷
python -m romanbo --port COM3 scan --start 1 --end 32
python -m romanbo --port COM3 --json config --ids 8,10
python -m romanbo --port COM3 load --ids 8 --watch 10 --interval 0.1

# 运动：相对 +15° @60°/s，带软件限力（阈值 100 + 间隔 3 可跳过起步涌流）
python -m romanbo --port COM3 jog --id 8 --degrees 15 --speed 60 --max-load 100 --load-every 3

# 多关节 + 到位确认
python -m romanbo --port COM3 move --targets 8:600,10:480 --speed 30 --readback

# 示教 → 导出 → 回放
python -m romanbo --port COM3 teach --ids 8,10 --file taught.json --period 500
python -m romanbo export --file taught.json --out taught.rsc
python -m romanbo --port COM3 play taught.rsc --ids 8,10 --speed 15

# 可视化控制台（浏览器里操作，见 docs/WEBUI.md）
python -m romanbo webui --mock                  # 无需硬件
python -m romanbo webui --port /dev/ttyUSB0     # 真机
```

> 详细的 `.rsc` 播放语义见 [工程文件](RSC.md)；限力参数怎么选见 [软件限力](LOAD_LIMITING.md)。
