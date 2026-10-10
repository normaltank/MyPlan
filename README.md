# MyPlan · 高校学院师资采集

输入大学、学院、学院链接及可选师资链接，采集可核验的官方师资信息，按大学汇总。

> 工作指导已按用户 2026-10-10 的重新说明更新。所有 agent 从 [AGENTS.md](AGENTS.md) 开始；需求见 [CONSTITUTION.md](CONSTITUTION.md)。旧 Markdown 未经用户审核，历史记录不能覆盖最新要求。

## 当前目标与状态

采集学院官网正式师资，包括兼职、外聘等；输出姓名、职称、邮箱、大学、学院、个人主页与完整简介，官网缺失字段如实留空。给定师资链接是优先入口，仍检查其他师资分支；只有前三列时需另交四列入口映射表。

同大学经证据确认是同一人时合并学院关联。已确认记录可部分交付并标明缺失范围；身份或字段归属不清的记录单列待核，不能把部分结果称为全院采齐。人工可以确认无师资，自动零采集和技术失败不能代替人工判定。

用户反馈多个学院仍失败，尚不能开始常规业务运行。当前先统一文档，再并行进行完整代码质量审查与采集泛化验证；服务器部署、来源表治理和邮件／飞书通知后续处理。具体见 [改进计划](docs/improvement-plan.md)。

上述是业务目标，当前实现仍有差距。三列入口表、可信部分交付、完整复核恢复等能力须分别查证；已存在模块及待核点见 [当前交接](AGENT_HANDOVER.md)。确定性解析、人数守恒或自动测试通过都不能单独证明采集准确且完整。

## 当前工程路线

服务器 worker 组织结构侦察、浏览器访问、确定性解析／本地 OCR、证据审计、人工复核与大学汇总。现有计划仍只传递允许的结构字段，完整原文和来源由采集链路保存。技术路线可以基于代码与样本证据改进，不以旧文档中的架构“红线”阻止审查。

现有 Excel 使用“教师列表”和“无邮箱教师列表”两个工作表及十列兼容格式。分表互斥和人数相加只证明输出集合内部一致，师资来源、人员身份和全院完整性仍需独立核验。

## 现有本机运行方式（参考）

当前阶段不以部署为前置工作。实际运行、更新或重启时核对本机配置和任务状态；以下保留已有操作入口。

1. 双击 `双击启动局域网服务.bat`：启动后台接单 worker 和网页，然后打开 http://127.0.0.1:8000 。
2. 第一次启动会要求输入 DeepSeek API Key。它用当前 Windows 账号加密保存在 `.runtime/`，以后不用再输。
3. 启动窗口会打印“同事访问地址”，同事在局域网里打开它、填上自己的姓名就能用，没有密码和管理员。
4. 更新代码后双击 `重启本地服务.bat`。

启动记录和 worker 日志在 `.runtime/`（`launcher.log`、`local-worker.out.log`、`local-worker.err.log`）。部署参数、资源保护、排障方法见 [运维手册](docs/operations.md)。

## 日常使用

- **提交**：单个或批量学院，每个学院都要填学院主页；批量可加第四列师资链接（同一学院多行合并）；一次最多 30 个学院。格式见[运维手册](docs/operations.md#2026-09-30-批量输入token-与准确性核对)。
- **进度**：网页右侧显示本浏览器当前的请求；旧批次在“历史工单”里查看。
- **被挡住的工单**：提示“人工复核”时，对照原图或名录页确认或改正（图片文字、邮箱归属、生僻姓氏的人名），再点“重新采集此工单”。
- **下载**：当前实现主要在目标学院通过审计后生成大学交付表。新目标允许已确认记录部分交付并标明缺失范围，这部分状态、导出和恢复行为仍须逐项核对，不能把文档更新当作已实现。

## 页面类型

侦察时由 Agent 判断名录属于哪种形态，再交给对应的确定性处理。定义见 [CONTEXT.md](CONTEXT.md)，侦察规则见 [server/recon_instructions.md](server/recon_instructions.md)。

| 类型 | 计划写法 | 适用页面 |
| :--- | :--- | :--- |
| 普通卡片名录 | `card_selector` + 单页或下一页控件 | 每人一张卡片，可链接个人主页 |
| 目录型名录 | `kind: "navigation"` | 入口页链接到多张同构名录（系所、科室） |
| 严格空名录 | `empty_state_selector` + `roster_container_selector` | 官网明确显示暂无人员 |
| 逐人图片名录 | `card_selector` 只命中原图 | 名单是文字，每人简介是一张图，本地 OCR |
| 整图表格名录 | `kind: "image_table"` | 格线表格由本地 OCR 与逐格复核处理 |
| 其他多人海报 | 保留原图和未解决项 | 需有可核实的分人／字段机制，不使用模型在计划中录入姓名 |
| 分段平铺名录 | `kind: "segmented"` | 一篇正文里平铺多人，或姓名只写在简历图里 |

## 开发与测试

```powershell
# 运行环境（worker 与网页）
uv venv .runtime/worker-venv --python 3.12
uv pip sync --python .runtime/worker-venv/Scripts/python.exe requirements.lock
.runtime/worker-venv/Scripts/python.exe -m playwright install chromium

# 测试与检查（开发依赖另装，不进运行环境）
uv run --no-project --with-requirements requirements-dev.lock --python 3.12 python -m pytest -q
uv run --no-project --with-requirements requirements-dev.lock --python 3.12 python -m pytest -q -m "not browser"   # 跳过启动浏览器的测试，约 1 分钟
uv run --no-project --with-requirements requirements-dev.lock --python 3.12 ruff check .
```

- 运行依赖在 `requirements.txt`，测试与检查工具在 `requirements-dev.txt`。两者分别锁定为 `requirements.lock`、`requirements-dev.lock`（Python 3.12，Windows 与 Linux 通用）。改了依赖后重建：
  `uv pip compile requirements.txt --universal --python-version 3.12 -o requirements.lock`
  `uv pip compile requirements-dev.txt --universal --python-version 3.12 -o requirements-dev.lock`
- 启动真实 Chromium 的测试标记为 `@pytest.mark.browser`；新写的浏览器测试也要加这个标记，否则 CI 的单元测试任务会失败。
- 推送到 `main` 或提 PR 时，GitHub Actions（`.github/workflows/tests.yml`）运行 ruff、单元测试和浏览器测试。ruff 只查未定义名、未用导入等会出错的问题，不强制代码风格。

## 目录

| 路径 | 内容 |
| :--- | :--- |
| `server/` | 网页、队列、worker、侦察、采集、审计与 Excel 导出 |
| `server/recon_discovery.md`、`server/recon_provided_links.md`、`server/recon_instructions.md` | 入口发现、给定链接补充及分支核实指令，由代码直接读取 |
| `server/templates/` | 网页的主页、填姓名页与共用样式 `base.css`（静态文件，改后刷新即生效；不依赖外部 CDN） |
| `Opus/` | 名录挖掘、邮箱匹配等算法层 |
| `tests/` | 自动化测试 |
| `scripts/` | 本机启动与重启脚本 |
| `docs/` | 运维手册、架构、审计与历史记录 |
| `.agents/skills/faculty-browser-harvest/` | IDE 路径的采集作业规范 |

## 文档导航

- [运维手册](docs/operations.md)：启动与重启、同事访问、复核、worker 参数、资源与用量保护、测试记录、手工采集
- [AGENTS.md](AGENTS.md)：所有 agent 的当前工作入口
- [项目目标](CONSTITUTION.md)：用户重新确认的采集、核验和交付要求
- [改进计划](docs/improvement-plan.md)：代码审查、历史回归和新学院验证
- [文档导航](docs/README.md)：活跃文档与历史材料的边界
- [AGENT_HANDOVER.md](AGENT_HANDOVER.md)：已有模块、需求差距和本轮核对范围
- [CONTEXT.md](CONTEXT.md)：领域术语与页面类型定义
- [可验证运行流程](docs/architecture/verifiable-harvest-runtime.md)：恢复、缓存、补缺与交付条件
- [学院采集通用编排接口](docs/architecture/college-harvest-workflow.md)
- 架构决策：[ADR 0002](docs/adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md)、[ADR 0003](docs/adr/0003-cs-principled-deep-module-extraction.md)
- 历史设计：[服务器端 Agent 运行时接入计划](docs/superpowers/plans/2026-09-24-server-agent-runtime.md)
- 审计记录：[docs/audits/](docs/audits/)
- [CHANGELOG.md](CHANGELOG.md)：按日期的变更记录
- [审阅待办](docs/review-backlog.md)：尚未完成的改进项及顺序
- 历史：[2026-09-29 清理清单](docs/cleanup-2026-09-29.md)、[2026-09-22 数据资产快照](docs/history/2026-09-22-data-assets.md)
