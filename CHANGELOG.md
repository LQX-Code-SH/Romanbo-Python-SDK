# 更新日志

本文件记录本项目的所有重要变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 修复
- **节拍改按绝对时刻排拍**（`Servo.move_at_speed` 与 `RomanboRobot._move_stepwise`）：
  原来是"每拍睡 dt"，`sleep` 自身的过冲会**逐拍累积**——Windows 的 `Sleep` 粒度约 15.6 ms，
  而一拍才 100 ms，长动作最坏慢约 13%，正好吃掉文档承诺的 ±4% 角速度。现在 `deadline`
  每拍累加 `dt`，某拍迟到（sleep 过冲、负荷回读往返、帧写慢）都会在下一拍补回来：
  总时长 ≈ N×dt **加一次过冲**，不再累积；补发不会挤爆总线（帧间隔由 `MIN_FRAME_GAP` 兜底）。
  同时统一了原先"无限力走 `sleep(dt)`、有限力才扣耗时"的双路径（现在两者同一套）
- **节拍漂移加回归测试**：受控时钟下让每次 `sleep` 过冲 30 ms，断言总时长 ≈ N×dt（旧的
  逐拍累积实现会是 N×0.13，直接被抓住）；单机关节与多关节两条路径各一条
- `docs/RELEASING.md` §1.2 把 **Required reviewers 明确写成可选项**，并给出两种模式的对照
  （不加 = 全自动发布，适合单人仓库；加了 = 每次需人工 Approve）。原文写"建议勾选"容易让人
  以为不做就差点什么；同时说明 `pypi` 环境一般**不用手动建**（工作流引用时会自动创建，
  手动建会报 `Name has already been taken`）
- **文档里的用例总数不再靠记性**（`tests/test_doc_counts.py`）：一天之内这个数字在 README 与
  `docs/TESTING.md` 里过期了三回（188 → 201 → 236 → 237），现在直接数出来对比——不一致就红，
  而历史记录（`TEST_REPORTS.md` 等）写的是当时的数字，**不**参与同步
- 文档补上"已发布到 PyPI"：`README.md` 的安装段与 `docs/INSTALL.md` 的"四种用法"加入
  `pip install romanbo`（含国内镜像同步延迟的提示）
- **`MANIFEST.in` 改用通配符 `deploy/*.rules`**：写死文件名时，规则从 `99-` 改名 `60-` 后
  sdist 会**静默漏掉** `deploy/`（1.1.1 的 sdist 就是这样——发布后在 PyPI 上核对才发现），
  并加了测试守住；`docs/RELEASING.md` 补上"发布后验证的两个坑"（国内镜像有同步延迟、
  sdist 内容要核对）

## [1.1.1] - 2026-09-27

### 新增
- **`ports --fix`：串口权限一键放权**（Linux）：整条权限流程里唯一需要 root 的一步是"装
  udev 规则"，其余（判定成因、生成规则、复验）都能自动。现在按设备**真实的 VID:PID**
  生成规则（不依赖你去仓库里找文件）、把要写入的内容与要执行的操作先打出来、问一次、
  走 `sudo`、最后复验。**已经能用时什么都不做**（不写系统文件、不触发 udev）；
  「已被占用」不算权限问题、非 USB 口（没有 VID:PID）不硬塞规则；非交互环境配 `--yes`
- 规则模板与仓库文件**同源**（`transport.UDEV_RULE_TEMPLATE` / `udev_rule_for` /
  `parse_usb_ids`），并有测试守住一致性，避免两边漂移

### 修复
- **设备消失的判定补上「设备节点是否还在」**（`SerialTransport._ensure_alive`）：真机实测
  拔掉适配器后读写**只会静默超时**（`in_waiting` 返回 0、`read` 返回空、`write` 甚至不报错），
  按 errno 根本抓不住——上层只看到"舵机未应答"，`is_open` 一直为真、控制台以为还连着
  （2026-09-27 真机日志：`TimeoutError: 等待回包超时` + `is_open=True` +`lost_reason=None`）。
  现在 POSIX 下每次读写顺带 `stat` 一次设备节点（微秒级，相对 2 ms 轮询可忽略），
  节点消失即判定断开并把 `OSError` 抛给上层；Windows 的端口名不是文件系统路径，不做该检查
- **规则文件名改为 `60-` 前缀**（原 `99-`）——**uaccess 真正生效的关键**：udev 按文件名顺序
  执行规则，而执行 `uaccess` 内建的是系统规则 `73-seat-late.rules`，`99-` 排在其后，于是
  tag 加上了（`udevadm info` 里 `CURRENT_TAGS=:uaccess:` 清清楚楚）**ACL 却永远不生成**，
  症状是"规则装了、tag 有了，还是 `Permission denied`"（2026-09-27 实测）。仓库文件随之
  改名 `deploy/60-romanbo-usb-serial.rules`，并有测试守住"名字必须排在 73 之前"
- `ports --fix` 三处配套：写入新路径、清理早期遗留的 `99-*` 规则（避免两份并存）、
  trigger 后加 `udevadm settle` + 复验重试（trigger 只是排队事件，立刻探测会读到旧权限
  ——第一次就是这么误报"仍然打不开"的）
- **`deploy/99-usb-serial.rules` 加 `TAG+="uaccess"`**：原规则只给 `0660 + dialout`，而
  `dialout` 组要**注销重新登录**才进会话——"装了规则却还是 Permission denied"多半是这个
  （2026-09-27 就卡在这）。`uaccess` 由 systemd-logind 给当前登录的桌面用户补一条 ACL，
  reload/trigger 后**立刻生效**。`docs/INSTALL.md` 同时写明两个顺序坑：`chmod` 之后再跑
  `udevadm trigger` 会被按规则覆盖回 `0660`；只 `usermod -aG dialout` 而没重新登录同样
  不生效（`id -nG | grep dialout` 一眼可查）
- **控制台端口下拉改为过滤占位口**（前端）：`/api/ports` 仍返回全部（数据不丢），但下拉框
  只列已识别的设备，状态行报「已隐藏 N 个没有接硬件的占位口」——原先 33 项里得在 32 个
  `ttyS*` 中找 FT230X
- **控制台的端口下拉沿用同一口径**（`/api/ports` 已识别设备排前；页面不再一律写"被占用"）：
  原先 32 个占位口会把 FT230X 挤到列表最后，且**权限不足被写成"被占用"**（与 `ports` 当初
  那个误导同源）。现在按 `reason` 显示"权限不足 / 已被占用 / 打不开"，没有 `description`
  的标为"没有接硬件的占位口"。GUI 里**不隐藏**（下拉框得能选到任何端口），只排序
- **`ports` 默认不再列出没有接硬件的占位口**（Linux 上 `ttyS0..ttyS31` 这类主板遗留串口，
  真机上 32 个）：它们会以 100 多行噪音把真正的适配器埋掉（FT230X 排在第 35 位），而且对
  "没有硬件"的口给 `dialout` / `chmod` 建议属于误导。现在默认只列**已识别**的设备，末尾
  报「已隐藏 N 个…加 `--all` 显示」（**不静默丢弃**），`--all` 看全；排查建议也从"每个端口
  各打一份"改为**按原因汇总一次**（`--json` 与文本同一口径）
- **管道被下游提前关闭时安静退出**（`... | head` / `| grep -q`）：原先写入撞上 `EPIPE`
  会打印整段 `BrokenPipeError` 栈回溯——正常用法下的噪音
- **设备消失（拔插/重枚举）后不再继续自报「已连接」**（`SerialTransport._mark_lost`，
  `webui.WebConsole.state` 新增 `lost_reason`）：设备被拔掉后**句柄仍是"打开"状态**
  （`is_open` 为真），控制台于是继续显示 `connected: true` 并保留陈旧的在线列表，直到
  发命令才报 `Input/output error`——这期间用户会以为机器人还能控。现在读写遇到"设备消失"
  类 errno（`EIO` / `ENXIO` / `ENOENT` / `ENODEV` / `ESTALE` / `EBADF`）会**主动关闭**
  并记下原因，`connected` 立刻变假，HTTP 错误里补一句「串口已断开…请重新连接」。
  **普通读超时不算断线**（舵机偶尔漏答是常态）
- **控制台连不上串口时给出可照做的处置**（`webui.WebConsole.connect`，新增
  `transport.port_error_hint`）：原先页面上只有 `SerialException: [Errno 13] Permission
  denied`，看不出是"该去加 `dialout` 组"还是"该去关占用程序"。现在按 `errno` 分类后把处置
  拼进**消息本身**（启动时的自动连接与页面上的「连接」都只打印 `str(exc)`），并且这类失败
  由 500 改判为 **409**——它是用户可修的问题，不是服务端故障
- **`ports` 不再把「权限不足」误报成「已被占用」**（`cli.cmd_ports` +
  `transport.list_serial_ports` 新增 `reason` 字段）：原先两者共用一句提示（"多为权限
  问题（dialout 组）或已被占用"），遇到**适配器重枚举后新节点没放权**的情况
  （`ttyUSB0` → `ttyUSB1`，权限退回默认的 `0660 root:dialout`）很容易被当成有进程在
  占用它——实际没有任何进程持有。现在按 `errno` 分流：`EACCES`/`EPERM` → 给出
  `usermod -aG dialout` 与临时 `chmod` 的处置；`EBUSY` → 给出 `lsof` / `fuser` 查持有者。
  **不能按异常类型判断**：pyserial 把权限不足也包成 `SerialException`（`[Errno 13]`），
  与「已被占用」的异常类型完全相同（真机实测）

### CI
- **Release 工作流加标签/版本一致性守卫**：标签必须等于 `v` + `romanbo.__version__`，
  否则构建阶段直接失败——避免把 1.1.0 的包打上 `v1.2.0` 的标签发出去

### 文档
- **随仓库提供 Linux 串口权限的 udev 规则**（`deploy/99-usb-serial.rules`）：`docs/INSTALL.md`
  的「临时放开权限」一段补上**永久解决**的三条命令（加入 `dialout` 组 + 装规则 + reload），
  并写明两个让规则静默失效的易错点（`idVendor` 留字面量 `xxxx`、把 `ACTION` 拼成
  `KERNELACTION`——2026-09-27 排查的那台机器上就是这两种写法）；同时给出
  `/dev/serial/by-id/` 稳定路径的用法，避免重插后 `ttyUSB0`/`ttyUSB1` 变号
- **新增[发版与发布](docs/RELEASING.md)**：PyPI Trusted Publishing 首次配置的五处字段、
  GitHub environment 与仓库变量的两个坑（必须建在 Variables、值必须是**小写** `true`）、
  每次发版的固定流程、发布后验证清单、出错处置（`invalid-publisher`、版本号不可重用、
  yank 的语义）、版本号约定
- `CONTRIBUTING.md` 的「发布」收敛为速记版 + 指向上面的新页，发布细节不再存两份
- **「测试记录」独立成页**（`docs/TEST_REPORTS.md`）：测试方案只保留用例定义、判定门限与
  记录模板（该记什么），每轮执行的环境、基线快照、逐条结果、准出结论与当轮新增缺陷
  移到记录页（实际记了什么）。文档站导航同步按「使用说明 / 规格与验证记录」两组对齐
- `docs/TESTING.md` 的「真机验证」改为一张指路表（实测数据 / 用例 / 记录 / 证据各去哪看），
  环境描述统一由 FINDINGS 承载，不再重复
- **按「使用说明 / 规格与验证记录」重划文档边界**：使用说明（README、INSTALL、CLI、
  WEBUI、RSC、LOAD_LIMITING、SAFETY、PROTOCOL）不再记录开发与测试过程及结果——
  删掉「验证环境：两个 MOS 舵机串联，ID 8/10，COM3，2026-09-25」这类句子、
  README 的「关键实测结论」表（数据保留在 FINDINGS）、WEBUI 的整节「真机验证记录」、
  LOAD_LIMITING 的实测阈值表与复测叙述、各处的内联实测数值与日期
- 被移出的内容全部归入[真机实测结论](docs/FINDINGS.md)（新增 §9 控制台验证：
  扫描/读数/运动/LED 的逐帧报文与到位误差；补扫描耗时与真实 `.rsc` 播放记录）
- 每篇文档开头标明**本文讲什么 / 不讲什么**，`docs/README.md` 索引改为两张表并写明
  两类文档的边界；英文 README 同步
- 顺带修正 `README` / `CLI.md` / 测试计划里会误触发起步涌流的限力示例
  （`--max-load 60` → `100` + `--load-every 3`）

## [1.1.0] - 2026-09-27

### 新增
- **可视化控制台**：`romanbo/webui.py`（后端）+ `romanbo/webui_page.py`（前端）+
  `romanbo webui` 子命令 —— 只监听本机的 Web 界面：扫描、实时读数、单/多关节运动、
  参数读写，以及**原始收发报文日志**。只用标准库（`http.server`），页面是单个内联
  HTML（无 CDN、无框架），运行期依赖仍只有 pyserial；带访问令牌、Host 校验，默认只绑
  回环；报文日志抓在传输层，与串口助手看到的一致。见 `docs/WEBUI.md`
- `romanbo.transport.list_serial_ports()`：串口枚举与占用探测提成共用函数，`ports`
  子命令改为调用它（行为不变）
- `tests/test_webui.py`：30 项后端回归，含访问控制（令牌 / Host）、**前后端接口契约**
  （页面调用的每个接口都必须真实存在）与页面静态一致性（JS 引用的 id 必须存在）
- **文档站**：`mkdocs.yml`（Material 主题）+ `docs/`，其中 **API 参考由 docstring 自动生成**
  （mkdocstrings），`mkdocs build --strict` 已在 CI 中作为门禁
- `docs/evidence/`：真机探针日志归档为可独立复核的原始证据，并附证据说明
- `.github/workflows/docs.yml`：PR 校验 + 主分支自动部署到 GitHub Pages
- `.github/workflows/release.yml`：推送 `v*` 标签即构建、校验、从 sdist 冒烟后
  经 Trusted Publishing 发布到 PyPI 并创建 GitHub Release（配置见 `CONTRIBUTING.md`）
- **命令行限力间隔 `--load-every N`**（`move` / `jog` / `angle` / `sweep` / `play`）：
  起步涌流只是**几毫秒的瞬态**，而 `move_at_speed` 的采样点就在它峰上，取 `3` 可跳过
  它（阈值 `100` 左右即可，沿用默认 `1` 时阈值需 >150）。同时给
  `Servo.set_angle / rotate / sweep` 补上 `load_check_every` 参数——此前这三处根本没有
  该参数，传进去会被直接忽略（只有 `move_at_speed` 生效）
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
- **限力机制补上实测机制解释**：`docs/FINDINGS.md` §3 新增单步实验（步长 5/10/20/30 ADC
  的涌流峰值、以及它只持续十几毫秒的样本序列），`docs/LOAD_LIMITING.md` 据此说明
  「涌流由**步长**决定」与「两条路径采样点不同（`move`/`play` 在节拍 sleep 之后采样，
  量的是持续负荷；`jog`/`angle`/`sweep` 发出该步后立刻采样，含涌流）」
- `docs/CLI.md` 的限力示例改为 `--max-load 100 --load-every 3`（原 `--max-load 60`
  按实测定会在起步涌流上误触发）
- **CI action 升到 Node 24 系列**（`checkout` v7、`setup-python` v7、`upload-artifact` v7、
  `download-artifact` v8、`configure-pages` v6、`upload-pages-artifact` v5、`deploy-pages` v5、
  `action-gh-release` v3），消除 "target Node.js 20 but are being forced to run on Node.js 24"
  弃用告警；已逐个核对跨主版本发布说明（`download-artifact` v5 的破坏性变更只影响"按 ID 下载
  单个产物"，本仓库按 `name` 下载；`upload-pages-artifact` v4+ 默认排除点文件，而本站产物
  `site/` 无点文件）
- `.github/workflows/docs.yml`：`configure-pages` 仅在非 PR 事件执行（PR 令牌无 Pages 写权限），
  并注明前置条件——仓库需先启用 Pages，且 `enablement` 不接受 `GITHUB_TOKEN`

### 修复
- **`TimeoutError` 不再被报成「串口打不开」**：它是 `OSError` 的子类，原先被
  `except OSError` 一并捕获，于是**设备没应答**（ID 不在线上、总线偶发丢帧）时会打印
  「串口打不开 → 常见原因：权限不足（需要 dialout 组）/ 端口名不对」——把排查方向
  带偏（本轮自己就被这条提示误导过一次）。现在先拦 `TimeoutError`，给「设备未应答」
  与正确的排查步骤；设备返回错误帧（如过载）也从栈回溯改为一行「协议错误：…」。
  退出码仍是 `5`（= 串口/通信错误）
- **`--json` 的 stdout 现在只有结果**：进度/日志行（「读取起始位置…」、`sweep` 的每步
  报告、`play` 的逐帧行、`--frames` 的原始帧）一律改走 **stderr**，
  `python -m romanbo --json … | jq` 可以直接用；顺带给 `play --json` 补上结果
  （原先只打印进度行，`--json` 下 stdout 是空的）
- **确认类命令补上 `--json` 结果**：`torque` / `led` / `pid` / `limit` / `param` /
  `wheel` / `sync` / `calib` / `set-id` / `reset` / `export` 原先在 `--json` 下仍只打
  一行中文，机器无法消费。现在统一经 `_result()` 输出，且**多 ID 命令汇总成一份**
  （`pid` / `limit` 的键是 ID 字符串，值为回读确认后的实测值），
  因此「stdout 只有一份 JSON 文档」的契约对所有命令成立
- **节拍与超时改用 `time.perf_counter()`**：`Servo.move_at_speed` 的节拍预算、
  `sweep` 的 `elapsed_s`、`Robot._wait_for` 与两个 `read_available` 的超时原先都用
  `time.monotonic()`，而 Windows + CPython <= 3.12 下它粒度约 15.6 ms——100 ms 的节拍
  会有 ±15% 误差，直接吃掉文档承诺的 ±4% 角速度（与 `SerialTransport.write` 同一类问题）
- **紧急停止不再排队等设备锁**（`webui.WebConsole.stop_all`）：它原先与 `goto` /
  `multi_move` 共用一把设备锁，而运动会把锁持有整个动作过程（慢速长距离可达数十秒），
  于是"紧急停止"要等运动自己走完才生效——安全按钮失效。现在直接下发 `SET_TORQUE`，
  每帧只短暂占用收发锁，能**插进运动节拍之间**生效：实测运动进行中调用返回 **0.1 ms**，
  反事实（等锁）是 **1654 ms**，那段时间舵机一直在动
- **运动期间实时读数不再冻结**（`webui.WebConsole.servo_live`）：读操作也不该取设备锁
  （单次请求的原子性已由 `RomanboRobot` 内部收发锁保证），否则界面读数会在最需要看
  读数的运动过程中整体停住
- **位置越界不再静默变成相对运动**（`protocol.build_set_position`）：`position` 只占
  10 位，`d[5]` 的 bit2 是 `relative`、bit3/bit4 是出力档位，而原守卫放到 2047
  （注释还写着"3 位给位置高位"）。于是 1024..2047 会置上 relative 位：`1500` 被固件
  当成相对运动、`2047` 变成"相对 +1023"，都是静默的非预期运动。现按真实位宽收紧到
  `0..1023`；`tests/test_torque_level.py` 里固化该错误边界的断言一并改正
- `Servo._decode_position()` 在回包被截断（数值段不足 2 字节）时不再返回 `body[0]`
  ——那会给出"看着合理"的错值（3 而不是 1012），调用方据此算出的位移、角度、
  `rotate()` 基准全错却没有提示；现在抛 `ProtocolError`
- `FrameParser` 的 `LEN` 候选顺序改为**整帧长度优先**（舵机请求与回包都是这个语义）：
  原先先试 `declared + 6`，缓冲恰好够长时会把一条完整帧错切成长窗口，凑出校验和
  （约 1/256）就吞掉后续字节，表现为偶发丢帧/串位
- `Servo.sweep()` 的 `period_ms` 分支改用注入的 `sleep`（原先写死 `time.sleep`，
  传了 `sleep=` 也照样真阻塞，与步进分支不一致，测试无法压缩时间）
- HTTP 请求体加上限（1 MiB）：声明超大 `Content-Length` 时立即报错，不再让线程挂在
  `read()` 上（此前是唯一没有上限的入口）
- **CLI 输出编码**：Windows 上输出被重定向时 stdout 用区域编码（en-US 为 cp1252），
  编码不了中文的 `print` 抛 `UnicodeEncodeError` 直接中断命令——`python -m romanbo
  selftest` 因此在 windows-latest 上稳定失败（基准向量名含"[协议推算]"）。
  现在 `cli.main()` 只在当前编码真的表示不了中文时才把 stdout/stderr 切到 UTF-8
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
- **`build_set_position` 接受的位置范围由 `0..2047` 收紧为 `0..1023`**（原因见「修复」）：
  越界值以前会静默置上 `relative` 位，现在抛 `ValueError`。仓库内没有依赖旧范围的调用方
  （样例 `.rsc` 的 ADC 实测落在 205..600）
- `--targets` 改为 argparse 的 `type=` 解析：位置越界（>1023）或 ID 越界现在是一行干净的
  参数错误（退出码 2），而不是走到协议层才抛栈回溯
- 控制台的 LED 控制由「原始数值输入」改为 **8 色选择**（灭/红/绿/蓝/黄/紫/青/白）：
  协议文档只定义了这 8 种组合，原来的 0..255 输入极易误解——`set_led(8)` 实际是
  **全灭**（内部左移 5 位后低 3 位被挤出）。`/api/servo/{id}/led` 同时保留 `{value}`
  写法，响应里返回**实际下发的 `d[5]`** 便于对着报文日志核对
- **软件限力的阈值指导被真机复测修正**：2026-09-27 测得**起步第 1 步**的负荷冲击达
  **128~147**（加速涌流，不是卡死），运动中只有约 85——原文档建议的「阈值取 60 左右」
  会把正常起步判成故障。现改为推荐「阈值 100 + `load_check_every=3`」，
  详见 `docs/LOAD_LIMITING.md` 与 `docs/FINDINGS.md` §3。控制台里限力默认**关闭**，
  并新增「负荷检查间隔」输入（默认 3）；命令行暂未暴露 `--load-every`
- `RomanboRobot.capture()` 默认 **`retries=0`**（探测式）：对不存在的 ID 只等一次超时。
  此前默认重试 2 次，17 通道只接 2 个舵机时整批回读会多花约 13 s。
  需要更强容错时显式传 `retries=2`。`Servo.get_position()` 相应新增 `retries=` 参数。
- `romanbo move` 新增 `--settle 秒` 与 `--readback`：`--speed` 是步进逼近，函数返回时
  最后一拍刚下发完、舵机仍在运动，此前直接回读会读到中间值；`--readback` 现会等待
  到位（默认 0.3 s）后回读实际位置并输出误差。`read` 改为单次超时（`retries=0`）。

### 文档
- **按 `docs/SERVO_TEST_PLAN.md` 完成一轮真机验收**（2026-09-27，覆盖 L0~L8-01）：
  L0 全过（黄金向量 60/60）、L1~L4 通过率 100%、L5 已备份且参数全部成功回滚、
  L7 全过（连发丢帧回归等）、`L8-01` 连续 50 次 `get_position` → `ok=50 fail=0`，
  满足 §5 的 Release Gate；完整记录见 `docs/SERVO_TEST_PLAN.md` §10
- **修正测试计划自身的两处缺陷**：L3-03 / L3-06 的限力阈值（60 / 100，默认每步采样）
  会撞上起步涌流而中止、容易被误判为缺陷 → 改为 `100 + --load-every 3`；
  L3-04 的「角速度测量方法」描述不足，用进程墙钟会得到 −35%/−99% 的假失败
  → 补上 `on_step` 逐步时间戳法（复测 −2.4%/−2.4%/+7.0%）
- `docs/SERVO_TEST_PLAN.md` / `docs/INSTALL.md` 的版本号引用同步到 v1.1.0

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

[Unreleased]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/releases/tag/v1.1.0
[1.0.0]: https://github.com/LQX-Code-SH/Romanbo-Python-SDK/releases/tag/v1.0.0
