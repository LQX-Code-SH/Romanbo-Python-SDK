# 更新日志

本文件记录本项目的所有重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增
- **文档站**：`mkdocs.yml`（Material 主题）+ `docs/`，其中 **API 参考由 docstring 自动生成**
  （mkdocstrings），`mkdocs build --strict` 已在 CI 中作为门禁
- `docs/evidence/`：真机探针日志归档为可独立复核的原始证据，并附证据说明
- `.github/workflows/docs.yml`：PR 校验 + 主分支自动部署到 GitHub Pages
- `.github/workflows/release.yml`：推送 `v*` 标签即构建、校验、从 sdist 冒烟后
  经 Trusted Publishing 发布到 PyPI 并创建 GitHub Release（配置见 `CONTRIBUTING.md`）
- `README.en.md`：英文版项目说明，与中文版互相链接

### 变更
- **docstring 规范化**：RST 指令（`.. note::` / `.. warning::` / `.. math::`）改为 Markdown 等价写法，
  Sphinx 角色（`:class:` / `:meth:` 等 61 处）改为行内代码，使自动生成的 API 参考可读
- 文档站锚点改用 Unicode slugify，与 GitHub 的锚点规则保持一致（中文标题可正常跳转）
- `docs/SERVO_SPEC.md` §7 明确与自动生成 API 参考的主从关系（以 docstring 为准）
- `Servo.__all__` 补上 `LoadLimitExceeded`
- **主页 README 精简为入口页**（特性 / 安装 / 30 秒上手 / API 速览 / 实测摘要 / 安全提示 / 文档导航），
  细节全部下沉到 `docs/`：新增 [安装与环境](docs/INSTALL.md)、[命令行参考](docs/CLI.md)、
  [协议速览](docs/PROTOCOL.md)、[真机实测结论](docs/FINDINGS.md)、[工程文件](docs/RSC.md)、
  [软件限力](docs/LOAD_LIMITING.md)、[测试与自检](docs/TESTING.md)、[安全与已知限制](docs/SAFETY.md)
  八个页面；`docs/README.md` 与文档站导航按「入门 / 协议与硬件 / 接口与数据 / 测试 / 安全」重组
- **CI action 升到 Node 24 系列**（`checkout` v7、`setup-python` v7、`upload-artifact` v7、
  `download-artifact` v8、`configure-pages` v6、`upload-pages-artifact` v5、`deploy-pages` v5、
  `action-gh-release` v3），消除 "target Node.js 20 but are being forced to run on Node.js 24"
  弃用告警；已逐个核对跨主版本发布说明（`download-artifact` v5 的破坏性变更只影响"按 ID 下载
  单个产物"，本仓库按 `name` 下载；`upload-pages-artifact` v4+ 默认排除点文件，而本站产物
  `site/` 无点文件）
- `.github/workflows/docs.yml`：`configure-pages` 仅在非 PR 事件执行（PR 令牌无 Pages 写权限），
  并注明前置条件——仓库需先启用 Pages，且 `enablement` 不接受 `GITHUB_TOKEN`

### 修复
- `SerialTransport.write()` 的帧间隔守卫改用 `time.perf_counter()`：Windows 上
  `time.monotonic()` 在 CPython <= 3.12 走 `GetTickCount64()`（粒度约 15.6 ms），
  跨刻度时会误判"已过 15.6 ms"而**跳过节流**，真机上仍可能丢帧——即
  `MIN_FRAME_GAP` 本要避免的"多关节只有第一个生效"（本地反事实复现：真实间隔 0.00 ms）
- `tests/test_standalone.py::TestFrameGap` 改为**注入时钟**判定"节流决策"，
  不再读真实秒表：原实现用 `time.monotonic()` 打点，在 windows-latest /
  Python 3.11 上恒读到 0.0 ms，是 CI 长期红灯的原因之一
- `tests/test_cli.py` 的串口提示用例断言了写死的 `"USB"`，而 macOS 分支文案是
  `/dev/tty.usbserial-XXXX` → 只在 macOS 上失败；现改为逐平台校验各自分支
- `pyproject.toml`：`authors` 里不允许出现 `url` 字段（PEP 621），此前会导致
  **`python -m build` 直接失败**——即发布流程不可用；现改为只保留 `name`，
  仓库地址放到 `[project.urls]`。同时移除已弃用的 License 分类器（改由 `license` 声明）

### 变更（行为）
- `RomanboRobot.capture()` 默认 **`retries=0`**（探测式）：对不存在的 ID 只等一次超时。
  此前默认重试 2 次，17 通道只接 2 个舵机时整批回读会多花约 13 s。
  需要更强容错时显式传 `retries=2`。`Servo.get_position()` 相应新增 `retries=` 参数。
- `romanbo move` 新增 `--settle 秒` 与 `--readback`：`--speed` 是步进逼近，函数返回时
  最后一拍刚下发完、舵机仍在运动，此前直接回读会读到中间值；`--readback` 现会等待
  到位（默认 0.3 s）后回读实际位置并输出误差。`read` 改为单次超时（`retries=0`）。

## [1.0.0] - 2026-09-26

首个公开发布版本。

### 新增
- **协议层**（`romanbo.protocol`）：帧编解码、命令码、校验、增量帧解析
- **传输层**（`romanbo.transport`）：`SerialTransport`（pyserial）与 `MockTransport`（离线模拟）
- **单舵机 API**（`romanbo.servo`）：位置/角度/角速度、轮子、扭矩、周期、PID、限位、
  实测负荷、LED、零点校准、参数回读
- **整机 API**（`romanbo.robot`）：连接自检、ID 扫描、多关节同步动作、示教回读、`.rsc` 播放
- **工程文件**（`romanbo.rsc`）：`.rsc` 解析与生成（兼容「重复键」文本格式）
- **关节换算**（`romanbo.joints`）：机型通道表、ADC↔角度、步进逼近规划
- **命令行**（`romanbo.cli`）：`selftest` / `ports` / `scan` / `read` / `teach` / `export` /
  `move` / `jog` / `angle` / `sweep` / `load` / `pid` / `limit` / `play` 等 20 余个子命令
- **基准报文自检**（`romanbo.golden`）：60 条逐字节基准向量
- 软件限力（`--max-load`）：通过轮询实测负荷实现，超限即停止并返回退出码 4

### 修复
- **帧间隔**：两帧之间无间隔时，后发的那一帧会被舵机静默丢弃，导致多关节动作
  只有第一个关节生效。现由 `SerialTransport.write()` 统一强制 `MIN_FRAME_GAP = 2 ms`
  （`protocol.MIN_FRAME_GAP`），`move` / `play` 的多关节路径一并修复

### 文档
- `README.md`：安装、上手、命令行参考、Python API、真机实测结论、安全与已知限制
- `docs/SERVO_SPEC.md`：字节级逐命令协议规格（含证据强度标记与未验证项清单）
- `docs/SERVO_TEST_PLAN.md`：分级测试方案（L0~L7 用例、判定门限、缺陷回归）
- `docs/evidence/`：真机联调探针日志（部分实测结论的原始证据）
- `docs/README.md`：文档索引

[Unreleased]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/releases/tag/v1.0.0
