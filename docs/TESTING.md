# 测试与自检

## 日常自检

```bash
python -m unittest discover -s tests -t .      # 236 项单元测试（全部通过，无跳过）
python -m romanbo selftest                     # 60 条基准报文（逐字节比对）
python -m romanbo --mock scan                  # 离线模拟器冒烟
python examples/04_offline_frames.py           # 打印每条指令的真实报文
```

**全部单元测试都不需要硬件**：串口相关路径由 `MockTransport`（`romanbo/transport.py`）
承担，可注入丢包等故障。

## 测试文件分工

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_protocol.py` | 帧编解码、校验、命令打包（含协议位域）、基准向量 |
| `tests/test_rsc.py` | `.rsc` 解析与生成（重复键、场景过滤、字段缺失、往返一致） |
| `tests/test_mock.py` | 整机流程、握手、扫描、示教、播放（离线模拟器）、`capture` 重试语义 |
| `tests/test_speed.py` | 角速度步进规划、多关节同节拍、`play` 插值 |
| `tests/test_load.py` | 实测负荷语义、软件限力、**抽检间隔在每个入口都生效**、别名兼容 |
| `tests/test_torque_level.py` | 出力档位 H/M/L/W 的位域与 CLI 取值 |
| `tests/test_cli.py` | 命令行参数解析与 `move --settle/--readback` 行为 |
| `tests/test_doc_led.py` | 协议文档 LED 示例与本实现的逐字节交叉校验 |
| `tests/test_standalone.py` | 脱离仓库可用性（子进程屏蔽 `serial`）、收发锁、帧间隔 |
| `tests/test_servo.py` | 舵机层健壮性：畸形/截断回包不得**静默**变成错误数值 |
| `tests/test_webui.py` | 控制台后端：访问控制（令牌 / Host）、前后端接口契约、页面静态一致性、**运动期间的并发响应**、请求体上限 |

## 文档站

```bash
python -m pip install -e ".[docs]"             # 文档站依赖
python -m mkdocs build --strict                # 构建（任何告警即失败）
python -m mkdocs serve                         # 本地预览 http://127.0.0.1:8000
```

CI 会在 PR 上以 `--strict` 构建文档站，并校验 API 参考确实已生成。

## 本机模拟 CI 条件

CI 上跑红的、本机却全绿的原因，多半是**环境差异**（本机插着适配器、有某个设备节点）。
要提前发现，可以在本机把那个条件"关掉"再跑一遍：

```bash
# 例：把 /dev/ttyUSB* 视作不存在（模拟 CI 上没有适配器）
python - <<'PY'
import os, sys, unittest
real = os.path.exists
os.path.exists = lambda p: False if str(p).startswith("/dev/ttyUSB") else real(p)
sys.argv = ["x", "discover", "-s", "tests", "-t", "."]
unittest.main(module=None, argv=sys.argv, exit=False)
PY
```

同理可用 `mock.patch.object(sys, "platform", "win32")` 跑到 Windows 分支——别把"只在某个
平台上炸"的断言留给 CI 去发现（2026-09-27 一天内因此红了两次）。

## 真机验证

| 想知道什么 | 去哪儿看 |
|---|---|
| 实测数据与结论、**验证环境** | [真机实测结论](FINDINGS.md) |
| 分级用例与判定门限（L0~L9） | [测试方案](SERVO_TEST_PLAN.md) |
| 某轮执行的逐条结果 | [真机测试记录](TEST_REPORTS.md) |
| 原始收发字节 | [证据日志](evidence/README.md) |

手上有硬件时，最小验证流程：

```bash
python -m romanbo --port /dev/ttyUSB0 selftest            # 1. 离线链路
python -m romanbo --port /dev/ttyUSB0 scan                # 2. 在线发现
python -m romanbo --port /dev/ttyUSB0 read --ids 8,10     # 3. 只读参数
python -m romanbo --port /dev/ttyUSB0 move --targets 8:540,10:480 --speed 30 --readback
```

> 第 4 步是**多关节回归的关键用例**（L4-01）：若只有第一个关节到位，
> 说明帧间隔保护失效，见[实测结论 §8](FINDINGS.md)。
