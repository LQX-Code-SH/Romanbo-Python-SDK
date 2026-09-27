# 发版与发布

> **本文讲什么**：把新版本发到 PyPI 与 GitHub Release 的**逐步操作清单**——首次配置
> （PyPI Trusted Publishing）、每次发版的固定流程、发布后验证、发错了怎么补救。
>
> **不讲什么**：代码贡献流程见仓库根目录的 `CONTRIBUTING.md`（不在文档站内）；测试怎么做
> 见[测试与自检](TESTING.md) 与[测试方案](SERVO_TEST_PLAN.md)。

## 0. 全景

推送 `v*` 标签即触发 `.github/workflows/release.yml`，三个作业：

| 作业 | 做什么 | 人工介入 |
|---|---|---|
| 构建并校验发行包 | `python -m build` → `twine check` → 校验 wheel / sdist 内容（`py.typed`、控制台入口、LICENSE、CHANGELOG.md、mkdocs.yml）→ **从 sdist 装一遍跑 `selftest`** → 上传 artifact | 无 |
| 发布到 PyPI | OIDC 直连上传（无需 API token） | **首次配置前默认跳过**；配了 Required reviewers 时需在 Actions 页面 Approve |
| 创建 GitHub Release | 挂上 `dist/*` 附件并自动生成 release notes | 无 |

构建阶段还有一道守卫：**标签必须等于 `v` + `romanbo.__version__`**，不一致直接失败——
避免把 1.1.0 的包打成 `v1.2.0` 的标签发出去。

## 1. 首次配置（一次性，约 5 分钟）

顺序建议：**先 PyPI 再 GitHub**。

### 1.1 PyPI 侧：添加 Trusted Publisher

1. 登录 <https://pypi.org/>（没有就注册；PyPI 强制要求开启 2FA）
2. 打开 <https://pypi.org/manage/account/publishing/>
3. 在「Add a new pending publisher」表单里**逐字**填写：

| 字段 | 填什么 |
|---|---|
| PyPI Project Name | `romanbo` |
| Owner | `LQX-Code-SH` |
| Repository name | `Romanbo-Python-SDK` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

4. 点 **Add**
5. **预期**：页面下方 Pending publishers 列表出现一行 `romanbo · release.yml · pypi`

!!! warning "五个字段一个都不能错"
    PyPI 添加时不校验仓库是否存在，填错要等到**发布那一刻**才报
    `invalid-publisher`。表里的值直接对照仓库地址与工作流的 `environment.name`。

!!! note "项目已经发布过怎么办"
    PyPI 上已有 `romanbo` 时不能用 pending publisher，改为：项目页 →
    **Manage** → **Publishing** → **Add a new publisher**，字段同上。

### 1.2 GitHub 侧：建 environment

1. 仓库 → **Settings** → 左栏 **Environments** → **New environment**
2. 名字填 `pypi`（必须与 §1.1 的 Environment name 完全一致）
3. 建议勾选 **Required reviewers** 并选上自己：发布前会停下来等你在 Actions 页面点
   Approve，防误发
4. （可选）**Deployment branches and tags** 选 `Selected branches and tags`，只放行 `v*`

### 1.3 GitHub 侧：打开开关

1. 仓库 → **Settings** → **Secrets and variables** → **Actions** → 切到 **Variables** 标签页
2. **New repository variable**：Name `PUBLISH_PYPI`，Value `true`
3. **预期**：变量列表出现 `PUBLISH_PYPI = true`

!!! danger "两个常见坑"
    - 必须建在 **Variables** 里，不是 Secrets（工作流读的是 `vars.PUBLISH_PYPI`）
    - 值要是小写字符串 `true`。工作流里比较的是 `vars.PUBLISH_PYPI == 'true'`，
      填 `True` / `1` / `yes` 都会判为不相等 → 作业被静默跳过

不设这个变量时 PyPI 作业**默认跳过**：未配置 Trusted Publishing 时它必然认证失败、
把整个工作流染红，而 GitHub Release 其实已经成功。

## 2. 每次发版

### 2.1 准备（在 `main` 上）

```bash
# ① 改版本号（pyproject 用 dynamic attr，版本号只此一处）
$EDITOR romanbo/__init__.py          # __version__ = "x.y.z"

# ② CHANGELOG：把 [Unreleased] 改成 [x.y.z] - YYYY-MM-DD，并补版本对比链接
$EDITOR CHANGELOG.md

# ③ 本地三件套
python -m unittest discover -s tests -t .
python -m ruff check .
python -m mkdocs build --strict

# ④ 试打包（与 CI 同一套命令）
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
python -m pip install dist/*.tar.gz --force-reinstall --no-deps && python -m romanbo selftest
```

### 2.2 提交并等 CI 绿

```bash
git add -A && git commit -m "release: x.y.z"
git push origin main
```

等 **CI**（Windows / macOS / 3×Ubuntu）与 **Docs** 两个工作流变绿。

### 2.3 打标签并推送（这一步才真正发布）

```bash
git tag -a vx.y.z -m "vx.y.z"
git push origin vx.y.z
```

### 2.4 盯 Release 工作流

仓库 → **Actions** → **Release**：

1. `构建并校验发行包` 先跑（含标签/版本一致性守卫）
2. 配了 Required reviewers 时，`发布到 PyPI` 停在 **Waiting** → 点
   **Review deployments** → **Approve and deploy**
3. `创建 GitHub Release` 自动跑完

## 3. 发布后验证（必做）

| 检查 | 怎么查 | 预期 |
|---|---|---|
| PyPI 上有该版本 | <https://pypi.org/project/romanbo/#history> | 出现 `x.y.z`（上传后可能延迟几十秒） |
| 能装上并可用 | 干净环境：`python -m venv /tmp/v && /tmp/v/bin/pip install romanbo==x.y.z && /tmp/v/bin/python -m romanbo selftest` | `通过 60 / 失败 0` |
| GitHub Release | 仓库 → Releases | 有 `vx.y.z`，附件含 `.whl` 与 `.tar.gz` |

## 4. 出错了怎么办

| 现象 | 原因 / 处置 |
|---|---|
| `发布到 PyPI` 作业 skipped | `PUBLISH_PYPI` 没建、建到了 Secrets 里、或值不是小写 `true` |
| `invalid-publisher` / OIDC 认证失败 | §1.1 的五个字段与 §1.2 的 environment 名逐字核对；工作流文件名改了也要同步改 PyPI 侧 |
| `标签 vX.Y.Z 与 __version__ ... 不一致` | 守卫拦下了：把 `romanbo/__init__.py` 改对，删标签重打（`git tag -d vX.Y.Z` + `git push --delete origin vX.Y.Z`） |
| `File already exists`（重跑时） | 该版本已上传成功，PyPI 不允许覆盖。只是后续作业失败时，在 Actions 页面**只重跑失败作业**即可避开上传；确实需要重传就给 `pypa/gh-action-pypi-publish` 加 `with: skip-existing: true` |
| 版本号已被占用 | PyPI 的版本号**永久不可重用**（删掉的也不行），只能换更高的号重发 |
| 发错了版本 | PyPI 只能 **yank**（项目页 → Manage → Releases → Yank），不能删除。yank 后 `pip install romanbo==x.y.z` 仍能装，但不会被 `pip install romanbo` 解析到 |

## 5. 版本号怎么定

遵循 SemVer，本项目当前的约定：

- **patch**（1.1.0 → 1.1.1）：只修 bug、无 API 变化
- **minor**（1.1.0 → 1.2.0）：加功能、加可选参数；**行为修正也算**，包括收紧参数校验
  （例如位置范围从 `0..2047` 收到 `0..1023`）
- **major**（1.x → 2.0）：删/改 API、改协议字节、降低 Python 版本支持

`requires-python` 当前是 `>=3.8`，CI 覆盖 3.8~3.13；用到新语法时要同步收窄这个范围。

## 附：首次发布的实际经过（1.1.0 / 1.1.1）

- **v1.1.0 只发布了 GitHub Release，PyPI 上没有它**：首次发版时 PyPI 作业因未配
  Trusted Publishing 而**认证失败**，把工作流染红；之后改成"未配置就默认跳过"，
  于是那次运行里该作业是 `skipped`——注意 GitHub **不能重跑 `skipped` 的作业**，
  所以想补发只能重新打标签
- **PyPI 从 1.1.1 开始**：`v1.1.0` 之后 `main` 上又积累了一批修复（断线判定、端口诊断、
  `ports --fix`、udev 规则前缀……），直接拿它们发 "1.1.0" 会与 tag `v1.1.0` 的内容不一致，
  所以按正常流程发了 1.1.1（`1.1.0` 在 PyPI 上就空着，无害）
- **`workflow_dispatch` 是安全的重发路径**：`github-release` 作业加了
  `if: startsWith(github.ref, 'refs/tags/')` 守卫，手动触发只会重跑"构建 + 发布到 PyPI"，
  不会再造一个 release；再加 `skip-existing: true` 就能在 PyPI 已上传过的情况下重跑
- **v1.1.1 已发布**（2026-09-27）：Release 工作流三个作业全绿，PyPI 上有 `1.1.1`，
  干净环境 `pip install romanbo==1.1.1` + `selftest` 通过

!!! tip "发布后验证的两个坑"
    - **国内镜像有同步延迟**：`pip config` 指向清华源时，刚发布的版本可能几分钟内仍报
      "找不到版本"。核对用官方源：`pip install --index-url https://pypi.org/simple …`，
      或直接看 <https://pypi.org/pypi/romanbo/json>
    - **sdist 内容要核对**：`MANIFEST.in` 写死文件名的话，文件改名后会**静默漏掉**
      （1.1.1 的 `deploy/` 就是这么漏的）——所以那里用通配符，并有测试守着
