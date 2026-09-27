# 软件限力

这台硬件**没有可用的力矩环**（协议里不存在目标力矩/电流闭环，`0x0D` 写入也无法回读），
唯一可行的限力方式是：运动中轮询 `0x18`（**实测负荷**），一旦超过阈值立刻停止继续下发。

```bash
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 60 --max-load 100 --load-every 3
```

```json
{"aborted": true, "reason": "load_limit", "id": 8, "load": 129,
 "limit": 100, "step": 4, "position": 973}          // 退出码 4
```

## 实现要点

- 实现：步进逼近的每个节拍**轮转抽检**一个关节（`load_check_every`），不破坏角速度节拍
- 触发后各关节**停在最后一个目标位置**保持不动
- API：`Servo.move_at_speed / rotate / set_angle / sweep`、`Robot.move / play` 均支持 `max_load`
- `max_load` 需要与 `speed_dps` 同用；不带 `speed_dps` 时传 `max_load` 会抛 `ValueError`
- 触发时抛出 `LoadLimitExceeded`（CLI 退出码 `4`），`.as_dict()` 便于上报

## 阈值与检查间隔怎么选

| 用法 | 阈值 | 检查间隔 |
|---|---|---|
| **推荐**：跳过起步阶段，既能抓住真正卡死、又不误报 | `100` 左右 | `--load-every 3`（或更大） |
| 只做粗保护：不关心起步误报，只要能抓住严重卡死 | `150` 以上 | 默认（每拍）即可 |

```bash
# 推荐配置
python -m romanbo --port COM3 jog --id 8 --degrees -45 --speed 60 \
    --max-load 100 --load-every 3
```

**为什么这样定**：起步瞬间有一次**加速涌流**，它不是卡死——真正的卡死是**持续**高负荷。
而两条步进路径的采样时刻不同，导致同一个阈值表现不一样：

| 入口 | 采样点 | 量到的是 |
|---|---|---|
| `jog` / `angle` / `sweep`（`Servo.move_at_speed`） | 发出该步**之后立刻** | **含**起步涌流 |
| `move` / `play`（`RomanboRobot` 的步进循环） | `sleep(节拍)` **之后** | **持续负荷**（涌流已衰减，天然跳过） |

涌流幅度随**单步跨过的 ADC 数（≈ 步长）**增大、只持续几毫秒，所以「把首次采样推后」
（`--load-every 3`）比单纯抬高阈值更准；`--speed` 越小则步长越小、涌流也越小。

- `--load-every` 在 `move` / `jog` / `angle` / `sweep` / `play` 上都有；不给时用库默认
  `1`。对应的库参数是 `load_check_every`，见
  `Servo.move_at_speed / set_angle / rotate / sweep` 与 `RomanboRobot.move / play`。
- [可视化控制台](WEBUI.md)界面里同样有「负荷检查间隔」输入框（默认 3）。
- 各场景的具体实测负荷与验证过程见[实测结论 §3](FINDINGS.md)。

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
