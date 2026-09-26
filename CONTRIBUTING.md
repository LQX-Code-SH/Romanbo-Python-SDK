# 贡献指南

感谢你参与改进本项目。以下是最小必要约定。

## 环境准备

```bash
git clone https://github.com/LQX-Code-SH/Romanbo-Python-SDK.git
cd romanbo
python -m pip install -e ".[dev]"     # 或：pip install -e . && pip install pyserial
```

## 开发约定

- **Python 3.8+**：包内模块统一使用 `from __future__ import annotations`，
  新增代码请保持该约定，避免在运行期求值 `list[int]` 这类写法。
- **依赖最小化**：运行期只允许 `pyserial`，且只在打开真实串口时惰性导入。
  离线功能（`.rsc` 解析、报文编码、模拟器）必须**纯标准库**。
- **类型标注**：公开函数与方法的参数、返回值都要标注（本包带 `py.typed`）。
- 行宽 100，编码 UTF-8，换行 LF（见 `.editorconfig`）。

## 测试

```bash
python -m unittest discover -s tests -t .    # 全部单元测试
python -m romanbo selftest                   # 60 条基准报文逐字节比对
python -m romanbo --mock scan                # 离线冒烟
```

- 新增协议行为 → 在 `tests/test_protocol.py` 补基准向量；
- 新增整机逻辑 → 用 `MockTransport` 覆盖，**不要在单元测试里访问真实串口**；
- 涉及真机的结论 → 写清「协议文档定义 / 真机实测 / 未验证」，并附实测环境与日期。

## 文档

文档站用 **MkDocs + Material + mkdocstrings** 构建，源文件在 `docs/`：

```bash
python -m pip install -e ".[docs]" ruff
python -m mkdocs serve            # 本地预览 http://127.0.0.1:8000
python -m mkdocs build --strict   # CI 用；任何告警都会导致失败
```

约定：

- **`docs/api.md` 的 API 参考由 docstring 自动生成**，不要在文档里重复维护函数签名；
- 改公开接口时，把「语义、限制、实测结论」写进 docstring；
- docstring 用 **Sphinx 风格**（`:param x:`、`:returns:`），正文用 Markdown；
  需要强调风险时用 `!!! warning` 这类 Markdown admonition，**不要写 `.. warning::`**（RST 写法不会渲染）；
- 交叉引用写成行内代码（`` `romanbo.servo.Servo` ``），不要用 `:class:` 之类的 RST 角色；
- 站内锚点由 pymdownx 的 Unicode slugify 生成，与 GitHub 保持一致，中文标题可正常跳转。

## 危险的改动

以下内容会改动真机状态或协议语义，请单独开 issue 讨论后再提 PR：

- `0x0F`（`GetPositionLimit` / `GetMotionPeriod` 同码）相关行为——无数据帧会改写位置限值
- 位置限值、PID、零点校准、改 ID、复位等**掉电保存**的写入命令
- 帧间隔、扫描静默期等时序常量（会直接影响多关节动作正确性）

## 提交与 PR

1. 从 `main` 切分支：`git checkout -b fix/xxx` 或 `feat/xxx`；
2. 提交信息用「类型: 摘要」形式，例如 `fix: 多关节连发丢帧`、`docs: 补充 .rsc 迁移说明`；
3. PR 中说明**动机、改动点、验证方式**；涉及真机验证的附上命令与输出；
4. 确保 `unittest` 与 `selftest` 全绿。

## 安全

本项目会驱动真实硬件。提交涉及运动的示例或测试时，务必：

- 默认使用小幅度、低速度并带 `--max-load`；
- 明确标注「会驱动真机」；
- 不要提交会让关节失控（如关闭限位后满速运行）的默认参数。
