# API 参考

> 本页由 **docstring 自动生成**（mkdocstrings），因此不会与代码脱节。
> 协议层面的语义、实测结论与限制见 [协议规格](SERVO_SPEC.md)；
> 快速上手与命令行用法见
> [项目 README](https://github.com/LQX-Code-SH/Romanbo-Python-SDK#readme)。

## 快速示例

```python
from romanbo import RomanboRobot, LoadLimitExceeded

with RomanboRobot("COM3") as robot:          # 或 connect("COM3", mock=True)
    print(robot.scan(1, 32))                 # 在线 ID
    s = robot.servo(8)
    s.torque(True)
    s.rotate(15, speed_dps=60)               # 以 60 °/s 相对转 15°

    try:
        s.move_at_speed(1023, 120, max_load=60)      # 120 °/s + 软件限力
    except LoadLimitExceeded as exc:
        print(exc.as_dict())

    robot.move({8: 600, 10: 480}, speed_dps=60, start=robot.capture([8, 10]))
    robot.play_rsc("examples/data/demo.rsc", speed_dps=60, max_load=60)
```

## 整机

::: romanbo.robot

## 单舵机

::: romanbo.servo

## 协议层（字节级）

::: romanbo.protocol

## 传输层

::: romanbo.transport

## 工程文件（`.rsc`）

::: romanbo.rsc

## 关节与换算

::: romanbo.joints

## 基准报文自检

::: romanbo.golden

## 命令行

::: romanbo.cli
