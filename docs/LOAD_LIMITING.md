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
| **起步第 1 步（30°/s）** | **`128~147`**（加速涌流，**不是**卡死） |
| 运动中（30°/s） | 约 `85` |
| 快速动作峰值 | 约 `110~130` |
| 顶住限位 | `0`（到限位后不再持续出力） |

!!! warning "默认每步都查，会先撞上起步涌流"
    `load_check_every` 默认是 `1`，也就是**第一拍就采一次负荷**——而那正是加速涌流
    最大的时刻。2026-09-27 真机复测：阈值取 `60` 时 30°/s 的运动在第 4 步被中止
    （负荷 85），改 `80` 仍在**第 1 步**中止（负荷 **147**）。这**不是**限力功能坏了，
    而是采样点选得太早。

两种可用姿势（择一）：

1. **跳过起步阶段（推荐）**：`load_check_every=3`（或更大）让首次采样落在起步之后，
   阈值取 `100` 左右即可——实测采样峰值 81，不再误触发；
2. **只做粗保护**：阈值取 `150` 以上，起步不会误报，但只能抓住严重卡死。

!!! note "命令行暂未暴露 `--load-every`"
    `jog` / `angle` / `sweep` / `move` / `play` 的 `--max-load` 走的是默认检查间隔，
    因此**命令行下建议阈值取 150 以上**；要精细控制请用 Python API 或
    [可视化控制台](WEBUI.md)（界面上直接有「负荷检查间隔」）。

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
