# 软件限力

这台硬件**没有可用的力矩环**（协议里不存在目标力矩/电流闭环，`0x0D` 写入也无法回读），
唯一可行的限力方式是：运动中轮询 `0x18`（**实测负荷**），一旦超过阈值立刻停止继续下发。

```bash
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 120 --max-load 10
```

```json
{"aborted": true, "reason": "load_limit", "id": 8, "load": 129,
 "limit": 10, "step": 1, "position": 973}          // 退出码 4
```

## 实现要点

- 实现：步进逼近的每个节拍**轮转抽检**一个关节（`load_check_every`），不破坏角速度节拍
- 触发后各关节**停在最后一个目标位置**保持不动
- API：`Servo.move_at_speed / rotate / set_angle / sweep`、`Robot.move / play` 均支持 `max_load`
- `max_load` 需要与 `speed_dps` 同用；不带 `speed_dps` 时传 `max_load` 会抛 `ValueError`
- 触发时抛出 `LoadLimitExceeded`（CLI 退出码 `4`），`.as_dict()` 便于上报

## 阈值与检查间隔怎么选

从实测值里挑（见[实测结论 §3](FINDINGS.md)）：

| 场景 | 实测负荷 |
|---|---|
| 静止 | `0` |
| **起步第 1 步** | 随**步长**增大（趋势）：5 ADC ≈ `25~38`、10 ADC ≈ `126~153`、20 ADC ≈ `149~159`、30 ADC ≈ `176~192`（加速涌流，**不是**卡死）。注意同一设置的重复测量散布很大（步长 5 ADC 也测到过 `104`），所以这是**量级**不是公式 |
| 运动中（30°/s ≈ 步长 10 ADC） | 约 `85` |
| 快速动作峰值 | 约 `110~130` |
| 顶住限位 | `0`（到限位后不再持续出力） |

注意「起步冲击」取决于**步长**而不是速度标签本身：`--speed` 越小、节拍越长，
单步跨的 ADC 就越少，涌流也越小（30°/s 在默认节拍下步长仅 10 ADC）。

!!! warning "涌流只持续几毫秒——但采样点正好落在那里"
    `load_check_every` 默认是 `1`，即**每一拍都采一次负荷**；而
    `Servo.move_at_speed`（`jog` / `angle` / `sweep` 走的就是它）在
    **发出该步之后立刻**采样，正好落在涌流峰上。2026-09-27 真机复测：阈值取 `60` 时
    30°/s 的运动在第 4 步被中止（负荷 85），改 `80` 仍在**第 1 步**中止（负荷 `147`）。
    这**不是**限力功能坏了，而是采样点选得太早。

    单步实验（从静止发一步，立刻按约 5 ms 间隔回读）说明它是瞬态：
    读数是 `139 → 4 → 5 → 7 → 0 …`——**峰只存在最初十几毫秒**，随后衰减到个位数。
    所以「每拍查一次」量到的基本都是这个瞬态，而它与卡死无关。

!!! note "两条路径的采样点不同，所以同一个阈值表现不同"
    | 入口 | 采样点 | 量到的是 |
    |---|---|---|
    | `jog` / `angle` / `sweep`（`Servo.move_at_speed`） | 发出该步**之后立刻** | **含**起步涌流 |
    | `move` / `play`（`RomanboRobot` 的步进循环） | `sleep(节拍)` **之后** | **持续负荷**（涌流已衰减，天然跳过） |

    实测印证：`move --speed 30 --max-load 100` 通过且 `peak_load` 仅 `9`；
    同样阈值在 `jog` 上则会中止。这不是随机现象，而是采样时刻不同。

两种可用姿势（择一）：

1. **跳过起步阶段（推荐）**：`--load-every 3`（或更大）让首次采样落在起步之后，
   阈值取 `100` 左右即可——实测采样峰值 81，不再误触发；
2. **只做粗保护**：阈值取 `150` 以上，起步不会误报，但只能抓住严重卡死。

```bash
# 阈值 100 + 每 3 拍查一次：跳过起步涌流，同时仍能抓住真正的卡死
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 60 \
    --max-load 100 --load-every 3
```

`--load-every` 在 `move` / `jog` / `angle` / `sweep` / `play` 上都有；不给时用库默认
`1`（不改变既有行为）。对应的库参数是 `load_check_every`，见
`Servo.move_at_speed / set_angle / rotate / sweep` 与 `RomanboRobot.move / play`。
[可视化控制台](WEBUI.md)界面里同样有「负荷检查间隔」输入框（默认 3）。

## 完整用法

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:
    s = robot.servo(8)
    s.torque(True)
    try:
        # 30 °/s，阈值 100，阈值每 3 拍采一次（跳过起步涌流）
        s.move_at_speed(600, 30, max_load=100, load_check_every=3)
    except LoadLimitExceeded as exc:
        print(exc.as_dict())         # {id, load, limit, step, position}
    print(s.last_peak_load)          # 最近一次带限力运动的峰值负荷
```
