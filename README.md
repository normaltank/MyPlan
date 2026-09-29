# 高校二级学院师资队伍智能全流程采集与结构化平台

> **前台同事下单，后台智能体接单履约**。  
> 面向全国高校二级学院师资队伍的浏览器采集与标准化交付平台。依托真实浏览器侦察、确定性提取与覆盖审计，目标是减少漏采和误收；只有审计通过的学院记录才进入大学交付表。

---

## 一、 系统架构与业务闭环

2026-09-27 新流程已增加覆盖账本、工单页面断点、经本轮浏览器核验的结构方案复用、局部分支补缺及大学交付版本。方案现永久保存于 `server/outputs/plan-cache/plans.sqlite3`，保留历史版本，每次复用仍须本轮浏览器验证。运行条件与文件含义见 [可验证运行流程](docs/architecture/verifiable-harvest-runtime.md)，实测范围见 [实现审计](docs/audits/2026-09-27-verifiable-harvest.md)。

普通 URL 分页中断后可重新验证最后成功页，再续采后续页；同 URL AJAX、变化或损坏的前沿会从分支入口重走。普通单页名录只有明确可见的空态提示、独占容器及本轮独立浏览器证据，才允许确认为空；空白页面、隐藏内容、未加载图片或仍在请求的页面继续阻断。全学院零人结果进入复核，不自动导出，也不重复调用模型补结构。侦察协议更新后，旧结构方案永久保留，但必须符合当前契约才能复用。

平台业务原则为：**采集单位是学院，交付单位是大学**。

```
┌─────────────────────────────────────────────────────────────┐
│                   本地 Web 前台交互界面                      │
│                  http://localhost:8000                      │
│        (同事录入 1 所或批量学院工单，一键下载交付 Excel)       │
└──────────────────────────────┬──────────────────────────────┘
                               │ POST /api/collect_batch
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI 异步任务中枢队列                     │
│               (server/agent_queue.py :8000)                 │
└──────────────────────────────┬──────────────────────────────┘
                               │ 轮询取单 / CLAIM（带三要素租约围栏）
                               ▼
┌─────────────────────────────────────────────────────────────┐
│         常驻队列消费者 server/agent_worker.py（轨 C）         │
│         （独立进程轮询本地队列，无需任何反向 Webhook；        │
│           亦兼容 IDE Subagent 派发路径 · 轨 A）              │
└──────────────────────────────┬──────────────────────────────┘
                               │  ┌─ 租约心跳全程覆盖（300s 租约，60s 续租）
                               ▼  ▼
┌─────────────────────────────────────────────────────────────┐
│              侦察段 server/agent_recon.py（唯一闸门）         │
│  - dsh (DeepSeek Harness) + Playwright MCP 真实打开官网       │
│  - 只产出「URL + CSS 选择器」组成的采集计划 JSON              │
│  - 双门禁：_validate_plan（schema）+ check_plan_hygiene（红线1）│
└──────────────────────────────┬──────────────────────────────┘
                               │ plan JSON（绝不含姓名/职称/邮箱/简介）
                               ▼
┌─────────────────────────────────────────────────────────────┐
│             确定性采集内核（零模型调用，红线 1）              │
│  - 真实操控浏览器遍历系所目录、A-Z 索引与动态 AJAX 翻页      │
│  - 逐人穿透学者主页，直接提取正文全文 (零 AI 总结，无信息丢失)│
│  - 审计对账：可见卡片数 vs 提取候选数，不相等即拒绝交付       │
└──────────────────────────────┬──────────────────────────────┘
                               │ 提取页面 DOM / HTML
                               ▼
┌─────────────────────────────────────────────────────────────┐
│            计算机科学底层提炼深模块 (faculty_extractor.py)   │
│  - DOM 同构子树聚类 (结构指纹自然滤除非教师导航与分页离群点)   │
│  - 盒模型语义分词 (遵循 CSS Block 换行边界，杜绝电话邮箱粘连)  │
│  - 最小闭包正文容器 (MECC 空间几何隔离，杜绝页脚公用邮箱泄漏) │
│  - 双 Sheet 守恒公理 (N_1 + N_2 = N_total 强制互斥校验)      │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼ POST /api/task/{id}/sync 同步
┌─────────────────────────────────────────────────────────────┐
│                以大学为单位汇总交付资产                     │
│  - 大学专属标准双 Sheet 交付表: server/outputs/<大学名称>.xlsx│
│  - 网页端一键下载 & 全校总数据库合并: 教师列表.xlsx         │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、 绝对不可触碰的三大红线

1. ⚖️ **模型调用只用于侦察，产出物严禁含数据字段**：
   - 大模型调用的唯一入口是服务器端 Agent 运行时（DeepSeek Harness + DeepSeek API），确定性内核（`faculty_extractor.py`、`harvest_workflow.py`）严禁内嵌模型调用；
   - 模型只做页面结构判断与导航决策；其产出物（采集计划 JSON）**只允许含 URL 与 CSS 选择器**，严禁含姓名、职称、邮箱或简介文本。
2. ❌ **严禁硬编码关键词黑名单与禁止 AI 二次总结**：
   - **学术简介全文复制**：直接提取学者个人详情页主体正文全文（去除页眉页脚与通用导航噪点后，原汁原味保留完整履历、科研项目与获奖成果），**严禁使用 AI 大模型二次总结压缩**，杜绝成果遗漏并使文本生成 Token 消耗归零；
   - **废除死板黑名单**：全面采用 DOM 同构聚类与容器空间几何去噪，严禁编写脆弱的 `if "学院概况" in text` 等规则黑名单。
3. ⚖️ **双 Sheet 守恒公理 100% 成立**：
   - 产出表格严格分为两个 Sheet：
     - **Sheet 1**：`有邮箱学者 (共 X 位)`
     - **Sheet 2**：`无邮箱学者 (共 Y 位)`
   - 守恒公理：$$\text{Sheet 1 (X)} + \text{Sheet 2 (Y)} = \text{全院专任教师总人数}$$
   - 两表标准 10 列字段完全一致、行互斥、一人不漏。

---

## 三、 核心计算机科学算法基础 (CS Foundations)

针对高校网站千奇百怪的异构排版，系统在 [`server/faculty_extractor.py`](file:///d:/Agent%20Workspace/Intern/MyPlan/server/faculty_extractor.py) 中构建了坚固的底层算法：

- **DOM 同构子树聚类 (Isomorphic Subtree Clustering)**：基于结构标签序列（STS）指纹计算同胞节点最大簇，数学层面自然剔除非教师导航链接、分页按钮与页眉页脚离群点。
- **盒模型语义分词 (Layout-Aware Tokenization)**：遵循 W3C CSS Block 格式化上下文在块级元素边界物理发射断句，排版物理根源杜绝电话号码粘连，并 100% 保护高校纯数字工号邮箱（如 `8843120002@jiangnan.edu.cn`）。
- **最小闭包正文容器 (MECC Scoping)**：基于正文密度几何定位正文主容器，隔离 Footer 公用邮箱，杜绝 `sdy@hhu.edu.cn` 等全院公共邮箱泄露。
- **自动化测试防护网**：[`tests/test_faculty_extractor.py`](file:///d:/Agent%20Workspace/Intern/MyPlan/tests/test_faculty_extractor.py) 覆盖斑马纹抗性、闭合标签断句、Schema 守恒校验等测试，毫秒级保障。

---

## 四、 核心数据资产与规模

| 资产类型 | 文件路径 | 现存规模 / 覆盖范围 | 规范特性 |
| :--- | :--- | :--- | :--- |
| **全量核心师资数据库** | [`教师列表.xlsx`](file:///d:/Agent%20Workspace/Intern/MyPlan/教师列表.xlsx) | **4,248 位** 专任在职学者 | 22+ 所全国名校，去重去噪，标准 10 列 |
| **批次 4f0dee1a 合并表** | `server/outputs/批量采集教师列表_合集_南京林业大学_江苏大学_18学院_20260922.xlsx` | **1,355 位** 学者 (831 有邮箱) | 南京林业大学 (8 院) + 江苏大学 (10 院) |
| **批次 a01cf3d1 合并表** | `server/outputs/批量采集教师列表_合集_河海大学_江南大学_14学院_20260922.xlsx` | **630 位** 学者 (578 有邮箱) | 河海大学 (6 院) + 江南大学 (8 院) |
| **批次 04d85c4d 合并表** | `server/outputs/批量采集教师列表_合集_20260921_101228.xlsx` | **204 位** 学者 (46 有邮箱) | 上海外国语大学 (6 院) |
| **标杆名校群交付表** | `server/outputs/` | **650+ 位** 学者 (>95% 邮箱率) | 清华、北大、复旦、浙大、武大、人大、中山、南大等 |

---

## 五、 快速启动与运维指南

### 1. 启动 Web 后台服务
```powershell
.runtime/worker-venv/Scripts/python.exe -m server.start_server
```
Windows 本机也可双击 `双击启动局域网服务.bat`。它会检查后台接单心跳，缺少在线消费者时启动常驻 `server.agent_worker`，然后启动网页；已在线的消费者或已监听的网页不会重复启动。两者都已在运行时，窗口会打开网页并提示后等待回车，不会一闪而过。**更新代码后请双击 `重启本地服务.bat`**：它只结束本项目虚拟环境里的接单进程与网页进程（含子进程），再按同样流程重新启动；旧的网页窗口会显示已退出，直接关闭即可。每次启动的过程记录在 `.runtime/launcher.log`。首次启动接单服务时，窗口会要求输入 DeepSeek API Key（输入不回显），并用当前 Windows 账号加密保存在本机 `.runtime/deepseek-api-key.dpapi`；此文件不能复制到其他账号或服务器当作凭据。当前账号后续双击无需重输。worker 日志在 `.runtime/local-worker.out.log` 和 `.runtime/local-worker.err.log`。网页默认监听 `http://127.0.0.1:8000`。

### 员工登录与工单归属

首次从服务器本机打开网页时，按页面提示创建管理员账号；也可以在本机终端执行 `python -m server.manage_users create-admin`（密码不回显，也不作为命令行参数）。管理员登录后在页面创建员工账号，员工可自行修改初始密码。每个员工只看自己的工单、批次、交付版本与每日统计；管理员可查看全部并按员工查看每日提交学院数、完成学院数和通过审计的教师记录行数。旧工单没有可靠的提交人信息，只有管理员可见。

网页的员工账号登录仅允许本机 HTTP 或 HTTPS。默认服务只监听 `127.0.0.1:8000`；多台员工电脑访问时，应在服务器配置 HTTPS 反向代理，并将它转发到本机端口。不要把 8000 端口的 HTTP 服务直接开放到局域网或互联网。教师记录数按完成工单统计，复采会再次计入，因此它表示工作量，不表示跨工单去重的人数。

### 图片文字与邮箱复核

工单因图片简介的低置信 OCR 行或邮箱冲突被挡住时，工单进度会显示“核对图片文字与邮箱”。提交员工可对照原图保存决定；管理员可查看并复核全部工单。完成该工单的全部图片复核后，点击“重新采集此工单”会使原工单重新入队，保留提交人和原始证据，重新执行确定性审计。原图或 OCR 已变化时，旧决定不会直接套用。旧的 `scratch` 验收材料没有员工归属，管理员可从网页中的“查看历史图片复核材料”进入；`8765` 是先前的本机独立页面，不再作为新员工工单入口。图片复核只解决其对应的证据问题，其他分支、身份或覆盖审计未通过时仍会阻断交付。

网页右侧默认显示本浏览器的当前请求；没有活动请求时显示“待提交”。已结束的旧批次可从“历史工单”主动查看，不会自动占据新任务进度。顶部“后台自动接单在线”取自常驻 worker 的实时心跳；显示离线时提交会入队等待，可重新双击上述 BAT 启动消费者。提交单院或批量任务都必须填写有效的学院官网入口 URL，缺少入口的批量请求整批不会入队。

### 2. 单独启动常驻 Agent 消费者（服务部署或排障）
```powershell
$env:DEEPSEEK_API_KEY = "<你的 Key>"
python -m server.agent_worker
```
worker 常驻轮询 `server/tasks.db`；使用锁定依赖中的 Harness 可执行程序与项目独立 `.runtime/dsh-home`，自动插入项目的 Python Playwright MCP 插件。先安装本环境的 Chromium。`DSH_BIN`、`DSH_HOME` 仅用于明确的自定义部署，不再自动拾取 PATH 上其它版本的 dsh 或用户全局 profile。
它不需要任何反向 Webhook，因此不受 IDE 宿主平台的唤醒限制。
按最新调度要求，worker 对全部待采集学院并行启动独立 Harness 会话，不设置项目级并发上限。每个工单的中间文件隔离在 `server/outputs/tasks/<task_id>/`，同一大学的最终 Excel 写入按序执行。服务器会话只负责结构侦察，确定性采集与交付由 worker 执行；这与 IDE 原生 Subagent 的完整履约职责不同。

侦察由代码分阶段执行：先读取 `server/recon_discovery.md` 核实全部名录 URL，再为每个分支用新的短上下文执行 `server/recon_instructions.md`。阶段只提供对应的真实浏览器工具；每个 URL、卡片选择器与实际下一页都须有本轮浏览器证据。确定性代码合并通过门禁的分支计划，再执行采集。无效计划或未解决审计最多返回侦察补缺两轮（`--max-recon-repairs`），全程保留原工单租约。

侦察默认不按固定请求数或 Token 数截断正常学院。入口发现只提交实际承载名录的页面，避免把菜单页和个人详情页重复当成名录分支；分支验证失败后保留同一工单内已核实的其他分支，只补有问题的分支。补缺反馈只传给对应阶段；若连续两轮仍是同一问题且没有新增已核实分支，停止无进展的重试。运维如需额外费用保险，可同时设置 worker 参数 `--max-recon-calls` 与 `--max-recon-tokens`；两者默认均为 `0`（关闭）。启用后按学院累计计数，缓存输入也计入 Token，达到阈值时失败并保留用量审计。Token 在每次请求完成后检查，因此不是严格费用上限。

**资源与用量保护（2026-09-29 起默认开启，均可关闭）**：全部学院仍同时开工，以下几项只限制共享资源：
- 按站点限流：同一大学站点（如 `cs.x.edu.cn` 与 `law.x.edu.cn` 同属 `x.edu.cn`）上，本 worker 所有学院同时打开的详情页合计不超过 8 个。环境变量 `HARVEST_PER_SITE_CONCURRENCY` 可调，设为 `0` 关闭。单院 `profile_concurrency` 仍然有效。
- 内存保护：已有学院在跑、且可用物理内存低于 1024MB 时，worker 暂缓领取新工单，内存恢复后继续；第一个学院不受影响。参数 `--min-free-memory-mb`，`0` 关闭。
- 侦察用量告警：单学院侦察累计超过 100 万 Token 或 150 次模型请求时，只告警、不中断。告警写入 worker 日志和任务目录的 `recon-usage-alert.json`，并随工单统计保存为 `recon_usage_alerts`。参数 `--recon-alert-tokens`、`--recon-alert-calls`，`0` 关闭。需要硬性熔断时，仍使用上面的 `--max-recon-calls` 与 `--max-recon-tokens`。
- 批量提交上限：单次最多 30 个学院（`server/config.json` 的 `max_batch_colleges`），同一学院重复出现的批次会整批拒绝。
- 单会话请求上限：入口发现会话最多 40 次模型请求，单个分支验证会话最多 25 次。超过时判定页面结构超出当前计划格式，该学院直接失败、不再补缺，审计状态为 `recon_phase_limit`。参数 `--max-discovery-calls`、`--max-branch-calls`，`0` 关闭。
- 侦察超时会立即结束对应的 Harness 会话，已发生的用量照常记入 `recon-attempts.json`；以前超时后会话仍在后台继续消耗，且用量记为未知。
- 侦察截图只发送当前视口或指定元素的 JPEG（高不超过 2000px），不再发送整页 PNG；同一页面的重复 HTML 快照只保存一份。

**启动自检**：常驻 worker 领取工单前，会检查算法层、pypinyin、OCR、PyMuPDF、Harness 运行时、`DEEPSEEK_API_KEY` 和 Playwright Chromium 是否齐全。任一缺失时，worker 在 `.runtime/local-worker.err.log` 写明原因并退出（退出码 2），不会带着降级的依赖继续采集；双击启动脚本会等待自检结果并提示。排障时可加 `--skip-self-check` 跳过。Excel 导出时，抓取到的文本一律按文本写入（以 `=` 开头的内容不会变成公式），xlsx 无法保存的控制字符会被去掉，不再导致整份大学表导出失败。

`server/recon_browser_mcp.py` 提供真实 Playwright 的精简 MCP 工具，文本按范围返回且单次最多 8000 UTF-8 字节；导航单次最多读取 64 个链接，超过返回预算仍明确给出未读范围。完整 HTML/JSON/截图保存到 `server/outputs/recon-evidence/<phase_session_id>/`。此预算只作用于侦察上下文，教师完整简介与 Excel 不受影响。运行时叠加 `server/recon_runtime.cordis.yml`，关闭无关技能目录、工程文件工具与通用指令注入，并保存每阶段及补缺轮次的真实 Token 元数据。实测结果见 `docs/audits/2026-09-26-harness-context-audit.md`；本次异常用量与修订见 `docs/audits/2026-09-29-nxist-recon-cost.md`。

运行补丁用 `insert` 安装 `dsh-mcp-client`，默认独立 ID 为 `faculty-recon-browser`，无需事先手工配置浏览器插件。阶段与证据会话通过启动参数显式传递。每次模型运行前先检查实际合成配置：浏览器缺失/重复、无关工具或通用指令注入仍启用、补丁未生效时直接失败，不花 Token 试错。自定义 profile 也须通过此检查。先在该环境运行 `python -m playwright install chromium`。编辑侦察 MD 或 Python 后需重启常驻 worker。

常驻进程使用固定环境，不能从会随启动器退出而清理的临时环境启动后台子进程。本机已使用 `.runtime/worker-venv`，依赖来自 `requirements.lock`：
```powershell
uv venv .runtime/worker-venv --python 3.12
uv pip sync --python .runtime/worker-venv/Scripts/python.exe requirements.lock
.runtime/worker-venv/Scripts/python.exe -m playwright install chromium
.runtime/worker-venv/Scripts/python.exe -m server.agent_worker
```
启动前在进程环境设置 `DEEPSEEK_API_KEY`；不要写入项目文件。

| 命令 | 用途 |
| :--- | :--- |
| `python -m server.agent_worker` | **常驻**（生产形态） |
| `python -m server.agent_worker --once` | 只处理一个工单后退出（**排障用，勿用于生产**） |

> ⚠️ **用 `--once` 跑生产会导致"提单后一直没人接单"**——它处理一个工单就退出。
> 生产环境应由 systemd 守护（Phase 4，尚未交付）。

### 3. 运行核心算法与卫生门禁测试
```powershell
uv run --no-project --with-requirements requirements.lock --python 3.12 python -m pytest tests/ -q
```
`requirements.lock` 固定 Python 3.12 下解析出的依赖版本；更新 `requirements.txt` 后用 `uv pip compile requirements.txt --output-file requirements.lock --python-version 3.12` 重建。2026-09-24 的隔离环境回归结果为 **137 passed、1 skipped**。开发机原有 NumPy 2.5.3 超出 SciPy 1.15.2 声明的 `<2.5` 范围，因此以锁定环境结果为准。

最新 2026-09-27 固定环境全量回归为 **365 passed、1 skipped**，生产队列与测试数据库隔离；包含分页续采、严格空态、永久方案库、逐详情正文范围证明、浏览器脚本隔离和交付门禁。唯一跳过来自缺少历史源码归档。六所预先冻结的新学校随机学院真实测试为 1 校独立通过、5 校保留阻断；本轮没有 DeepSeek API 请求，不能等同 Harness 自主侦察或全国适配率。见 [随机学院验证](docs/audits/2026-09-27-random-college-validation.md) 和 [先前实现审计](docs/audits/2026-09-27-verifiable-harvest.md)。

2026-09-28 前端更新后的固定环境全量回归为 **376 passed、1 skipped**；本地浏览器验证默认页、历史待复核和重置行为，旧问题版本下载仍返回 409。此轮没有提交生产工单或调用 DeepSeek；详情见 [前端与接单状态审计](docs/audits/2026-09-28-web-current-request.md)。

### 4. 手工/排障：学院工单采集
> 常规履约走上面的常驻 worker。手工路径用于排障，或由 IDE 主控按 [`faculty-browser-harvest/SKILL.md`](.agents/skills/faculty-browser-harvest/SKILL.md) 派发一学院一 Subagent。旧 `auto_dispatcher.py` 现仅巡检，不会认领或采集；旧 HTTP 批量脚本不用于工单履约。
```powershell
python -m server.browser_batch_tool --plan <已核实计划.json> --output-dir server/outputs --task-id <task_id> --worker-id <worker_id>
```
已认领工单须先设置 `HARVEST_LEASE_TOKEN` 环境变量；详细步骤见工具目录。

> **当前验收边界**：已知生僻姓案例和同构卡片中的未列姓氏已有离线回归；可疑分页与缺少卡片选择器会阻断交付。真实网站的完整分支覆盖、WAF 站点和 Linux 常驻运行仍需单独端到端验证。历史 `roster_card_mismatch` 记录见 [五方审计](docs/audits/2026-09-24-five-perspective-review.md) §A3。

---

## 六、 核心文档导航

- 🏛️ **最高行动宪章**：[`AGENTS.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/AGENTS.md)（红线规范、Subagent 派发铁律、阶段边界卫生）
- 📋 **交接文档与系统全景**：[`AGENT_HANDOVER.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/AGENT_HANDOVER.md)（业务规则库、系统状态、历史交付资产总表）
- 📖 **领域术语与概念字典**：[`CONTEXT.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/CONTEXT.md)（Single Source of Truth）
- 🛠️ **标准采录作业 SOP**：[`.agents/skills/faculty-browser-harvest/SKILL.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/.agents/skills/faculty-browser-harvest/SKILL.md)
- 📐 **架构决策记录**：
  - [`学院采集通用编排接口`](file:///d:/Agent%20Workspace/Intern/MyPlan/docs/architecture/college-harvest-workflow.md)（`capture_sources → reconcile_rosters → eligibility → commit`；具体院校只作为验收夹具）
  - [`docs/adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/docs/adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md)
  - [`docs/adr/0003-cs-principled-deep-module-extraction.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/docs/adr/0003-cs-principled-deep-module-extraction.md)
- 🚀 **服务器端 Agent 运行时接入计划**：[`docs/superpowers/plans/2026-09-24-server-agent-runtime.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/docs/superpowers/plans/2026-09-24-server-agent-runtime.md)（dsh + DeepSeek API 的选型、实测结论、Phase 0–4；§二·补二 为端到端闭环交付记录）
  - 配套配置：[`.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml`](file:///d:/Agent%20Workspace/Intern/MyPlan/.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml)（浏览器工具挂载，含三个必踩坑的说明）
  - 运行时模块：[`server/agent_worker.py`](file:///d:/Agent%20Workspace/Intern/MyPlan/server/agent_worker.py)（常驻队列消费者）、[`server/agent_recon.py`](file:///d:/Agent%20Workspace/Intern/MyPlan/server/agent_recon.py)（模型↔数据唯一闸门）
- 🔍 **五方对抗性审查发现**：[`docs/audits/2026-09-24-five-perspective-review.md`](file:///d:/Agent%20Workspace/Intern/MyPlan/docs/audits/2026-09-24-five-perspective-review.md)（守恒级缺陷、红线 2 活体违规、无人值守运维缺口）
- **Harness 指令与 Token 审计**：[2026-09-26 审计](docs/audits/2026-09-26-harness-context-audit.md)（实际 Markdown 加载、跨任务文件污染、技能目录开销与全量并行调度的实现边界）
