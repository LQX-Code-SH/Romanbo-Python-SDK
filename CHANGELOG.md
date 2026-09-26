# 更新日志

本文件记录本项目的所有重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 计划中
- `docs/` 文档站（README 瘦身、协议规格与 API 参考自动生成）
- 只读探测类调用的默认重试策略优化（`capture()`）

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
- `SERVO_SPEC.md`：字节级逐命令协议规格（含证据强度标记与未验证项清单）
- `SERVO_TEST_PLAN.md`：分级测试方案（L0~L7 用例、判定门限、缺陷回归）

[Unreleased]: https://github.com/LQX-Code-SH/romanbo/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/LQX-Code-SH/romanbo/releases/tag/v1.0.0
