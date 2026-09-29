# 高校二级学院师资采集平台

> 前台同事提交学院工单，后台智能体接单采集，按大学交付标准双 Sheet Excel。

同事在本地网页提交一个或一批学院，每个学院附官网入口 URL。后台常驻 worker 逐学院认领工单：先由大模型在真实浏览器里侦察官网结构，只产出由 URL 和 CSS 选择器组成的采集计划；再由确定性代码用浏览器访问名录和个人主页，提取十列数据并审计覆盖。审计通过的学院按大学汇总为 `server/outputs/<大学名称>.xlsx`，在网页上下载。

## 工作流程

```
网页前台  server/app.py（http://127.0.0.1:8000）
   │ 提交学院工单
   ▼
任务队列  server/tasks.db（SQLite，带租约）
   │ 常驻 worker 认领，所有学院同时开工（server/agent_worker.py）
   ▼
侦察      server/agent_recon.py —— 全项目唯一调用大模型的地方
   │ DeepSeek Harness + 真实浏览器；只输出 URL 与 CSS 选择器
   ▼
采集与审计  server/browser_batch_tool.py · harvest_workflow.py · faculty_extractor.py
   │ 浏览器翻页、访问个人页、提取正文全文、卡片对账、覆盖账本
   ▼
交付      按大学汇总双 Sheet Excel，网页下载
```

## 三条红线

1. **模型只做结构侦察**：计划里只允许 URL 与 CSS 选择器；姓名、职称、邮箱、简介一律由确定性代码从页面提取。
2. **不写关键词黑名单，不用 AI 总结简介**：靠 DOM 结构聚类和正文容器定位去噪，简介保留原文全文。职称词表、姓氏先验只用来识别字段，不用来删改页面内容。
3. **双 Sheet 守恒**：工作表 `教师列表`（有邮箱）+ `无邮箱教师列表` = 该大学已采集的专任教师总数；两表 10 列相同、行互斥。这只证明分表正确；有没有漏人，要看覆盖账本对名录分支、翻页终点和卡片数的核对。

完整条文见 [AGENTS.md](AGENTS.md)，算法术语见 [CONTEXT.md](CONTEXT.md)。

## 快速开始（Windows 本机）

1. 双击 `双击启动局域网服务.bat`：启动后台接单 worker 和网页，然后打开 http://127.0.0.1:8000 。
2. 第一次启动会要求输入 DeepSeek API Key。它用当前 Windows 账号加密保存在 `.runtime/`，以后不用再输。
3. 第一次在本机打开网页时，按提示创建管理员账号，再在网页上给同事创建员工账号。
4. 更新代码后双击 `重启本地服务.bat`。

启动记录和 worker 日志在 `.runtime/`（`launcher.log`、`local-worker.out.log`、`local-worker.err.log`）。部署参数、资源保护、排障方法见 [运维手册](docs/operations.md)。

## 日常使用

- **提交**：单个或批量学院，每个学院都要填官网入口 URL；一次最多 30 个学院。
- **进度**：网页右侧显示本浏览器当前的请求；旧批次在“历史工单”里查看。
- **被挡住的工单**：提示“核对图片文字与邮箱”时，对照原图确认或改正，再点“重新采集此工单”。
- **下载**：一所大学的目标学院全部通过审计后，生成该大学的交付表。

## 页面类型

侦察时由 Agent 判断名录属于哪种形态，再交给对应的确定性处理。定义见 [CONTEXT.md](CONTEXT.md)，侦察规则见 [server/recon_instructions.md](server/recon_instructions.md)。

| 类型 | 计划写法 | 适用页面 |
| :--- | :--- | :--- |
| 普通卡片名录 | `card_selector` + 单页或下一页控件 | 每人一张卡片，可链接个人主页 |
| 目录型名录 | `kind: "navigation"` | 入口页链接到多张同构名录（系所、科室） |
| 严格空名录 | `empty_state_selector` + `roster_container_selector` | 官网明确显示暂无人员 |
| 逐人图片名录 | `card_selector` 只命中原图 | 名单是文字，每人简介是一张图，本地 OCR |
| 海报名录 | `poster_reviews` + 人工核对人数 | 一张图里有多人（IDE 路径） |
| 分段平铺名录 | `kind: "segmented"` | 一篇正文里平铺多人，或姓名只写在简历图里 |

## 开发与测试

```powershell
uv venv .runtime/worker-venv --python 3.12
uv pip sync --python .runtime/worker-venv/Scripts/python.exe requirements.lock
.runtime/worker-venv/Scripts/python.exe -m playwright install chromium
uv run --no-project --with-requirements requirements.lock --python 3.12 python -m pytest tests/ -q
```

依赖版本锁定在 `requirements.lock`（Python 3.12）。改了 `requirements.txt` 后，用 `uv pip compile requirements.txt --output-file requirements.lock --python-version 3.12` 重建。

## 目录

| 路径 | 内容 |
| :--- | :--- |
| `server/` | 网页、队列、worker、侦察、采集、审计与 Excel 导出 |
| `server/recon_discovery.md`、`server/recon_instructions.md` | 两个侦察阶段的指令，由代码直接读取 |
| `Opus/` | 名录挖掘、邮箱匹配等算法层 |
| `tests/` | 自动化测试 |
| `scripts/` | 本机启动与重启脚本 |
| `docs/` | 运维手册、架构、审计与历史记录 |
| `.agents/skills/faculty-browser-harvest/` | IDE 路径的采集作业规范 |

## 文档导航

- [运维手册](docs/operations.md)：启动与重启、员工账号、复核、worker 参数、资源与用量保护、测试记录、手工采集
- [AGENTS.md](AGENTS.md)：红线与智能体派发规则
- [AGENT_HANDOVER.md](AGENT_HANDOVER.md)：交接文档、业务规则与系统状态
- [CONTEXT.md](CONTEXT.md)：领域术语与页面类型定义
- [可验证运行流程](docs/architecture/verifiable-harvest-runtime.md)：恢复、缓存、补缺与交付条件
- [学院采集通用编排接口](docs/architecture/college-harvest-workflow.md)
- 架构决策：[ADR 0002](docs/adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md)、[ADR 0003](docs/adr/0003-cs-principled-deep-module-extraction.md)
- [服务器端 Agent 运行时接入计划](docs/superpowers/plans/2026-09-24-server-agent-runtime.md)
- 审计记录：[docs/audits/](docs/audits/)
- [CHANGELOG.md](CHANGELOG.md)：按日期的变更记录
- [审阅待办](docs/review-backlog.md)：尚未完成的改进项及顺序
- 历史：[2026-09-29 清理清单](docs/cleanup-2026-09-29.md)、[2026-09-22 数据资产快照](docs/history/2026-09-22-data-assets.md)
