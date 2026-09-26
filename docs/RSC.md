# `.rsc` 工程文件

`.rsc` 是 JSON 文本，三层结构：

```
Header : FileName / Type / ProjectName / MotorCount / MotorState[] / DialPosition[] / Image(base64 PNG)
Body   : "0".."n" 场景信息（SceneColor/SceneName/MotionCount）+ Motion 数组（扁平化动作帧）
扩展    : Setup[]（每场景一组 PID）、Program（程序块图）、Remocon[]（遥控映射）、KeyIndex[]（程序槽）
```

## 生成 `.rsc`（示教 → 导出）

`Body` 里的场景键（`"0"`/`"1"`…）带 `SceneName`/`MotionCount`/`SceneColor`，
`Body.Motion` 是帧记录数组；一帧为
`{SceneNumber, MotionNumber, Period, MotorValue:{ADC×17}, LEDValue:{LED×17}}`。

注意 **`MotorValue`/`LEDValue` 是「重复同名键的对象」而不是数组**，
标准 `json.dumps` 生成不了，所以由 `rsc.dumps_project()` / `rsc.write_project()` 手工拼文本；
`Version` 与 `Setup`(PID) 都是可选的（仓库样例 `examples/data/demo.rsc` 里就没有这两个键）。

```bash
# 手扳到位 → 记一帧；重复几次即累积成动作序列
python -m romanbo --port COM3 teach --ids 8,10 --file taught.json --period 500
python -m romanbo export --file taught.json --out taught.rsc            # 离线生成工程文件
python -m romanbo --port COM3 play taught.rsc --ids 8,10 --speed 15     # 回放验证
```

单个动作帧：

```json
{ "SceneNumber": 0, "MotionNumber": 0, "Period": 500,
  "MotorValue": { "ADC": 512, "ADC": 563, "ADC": 205 },
  "LEDValue":   { "LED": 0, "LED": 1, "LED": 2 } }
```

## 播放

`examples/data/demo.rsc` 是随仓库分发的样例工程（17 通道 / 2 场景 / 3 帧）：

```bash
python -m romanbo --port COM3 play examples/data/demo.rsc --speed 60
python -m romanbo --port COM3 play examples/data/demo.rsc --scene 1 --loop
python -m romanbo info examples/data/demo.rsc               # 只看摘要（离线）
```

```python
from romanbo import RscProject, RomanboRobot

project = RscProject.load("examples/data/demo.rsc")
print(project.summary())
with RomanboRobot("COM3") as robot:
    frames = list(project.frames())
    start = robot.capture([i for i in frames[0].targets()])     # 起始位置
    robot.play(frames, speed_dps=60, start_positions=start, max_load=60)
```

## 注意事项

1. 帧里的 `Period` 是**帧间间隔**，不是舵机走完时间（见[实测结论 §1](FINDINGS.md)）；
   给 `--speed` 时会被忽略，改为按角速度对关键帧做插值
   （`--speed-scale` 只在**没给 `--speed`** 时按倍率缩放帧间间隔）。
2. 数组下标 `i` 对应舵机 ID `i + id_offset`（默认 1），即**通道 1..17 → 舵机 1..17**；
   只接了部分舵机时，其余 ID 无设备、无应答（不影响）。
3. 帧里是**绝对 ADC**，首帧会直接跳到该姿态；不确定时先 `--speed` 放慢并观察。
4. 格式缺陷（读取已兼容）：`Program`/`Temp` 节点存在**重复 JSON 键**；`KeyIndex[]` 是
   「字符串里再塞 JSON」的双重序列化；`v1.1.1` 与 `v1.2.0` 的帧字段不同且无版本迁移。
