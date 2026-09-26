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

## 阈值怎么选

从实测值里挑（见[实测结论 §3](FINDINGS.md)）：

| 场景 | 实测负荷 |
|---|---|
| 静止 | `0` |
| 快速动作峰值（本机） | 约 `110~130` |
| 顶住限位 | `0`（到限位后不再持续出力） |

→ 一般取 **`60` 左右**比较合适；`10` 这种低阈值会在起步瞬间就中止。

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:
    s = robot.servo(8)
    s.torque(True)
    try:
        s.move_at_speed(1023, 120, max_load=60)      # 120 °/s
    except LoadLimitExceeded as exc:
        print(exc.as_dict())                          # {id, load, limit, step, position}
    print(s.last_peak_load)                           # 最近一次带限力运动的峰值负荷
```
