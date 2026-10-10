> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行要求见 [项目目标](../../CONSTITUTION.md)，当前执行见 [改进计划](../improvement-plan.md)；使用原结论前核对代码版本、样本与原始证据。

# 采集内核审查续接计划（交给下一个 agent）

> **续接状态（2026-10-01）**：内核修复完成，Windows 全量 857 项通过、1 项跳过，验证阶段正式库隔离成立。用户已授权合入 main、推送与部署，现已完成；用户明确不处理旧失败学院，将另选学院测试。修复与离线统计见[续接验证报告](../audits/2026-09-30-core-chain-continuation.md)，服务端口与四字段输入见[部署记录](../audits/2026-10-01-local-deployment.md)。下文保留原计划作为依据；职称规则仍待选择。临时文件删除被自动审批拒绝，尚未清理。

写于 2026-09-30 23:20（UTC+8）。上一位 agent 因额度不足停止。审查结论、证据和调用链见 [内核调用链审查](../audits/2026-09-30-core-chain-review.md)，本文件只写接下来怎么做。

## 0. 先读，再动手

1. 必读：`AGENTS.md`、`CONTEXT.md`、[内核调用链审查](../audits/2026-09-30-core-chain-review.md)、[采集内核交接](2026-09-30-extractor-core-handover.md)（两张失败工单的来龙去脉）。
2. 用户规则，每条都要遵守：
   - 只修稳现有采集链路，不加外围功能，不做大重构；Playwright 升级和包结构整理放到最后。
   - 模型只做结构侦察；姓名、职称、邮箱、简介一律由确定性代码提取，简介保留全文。
   - 覆盖门禁和交付门禁只能收紧，不能放宽；无法证明的项目进入复核，不能静默交付。
   - 教师原始页面、PDF、数据库和产出只留在本机被 Git 忽略的目录，不进 Git，不上传。
   - 以下操作必须先得到用户确认：付费复跑学院、访问学校网站、部署或重启服务、处理生产工单、合并到 `main`、推送到 GitHub。
   - 测试通过不等于实网采集完成，汇报时要说清验证边界。
3. 当前代码状态：
   - 本机仓库 `D:\Agent Workspace\Intern\MyPlan`，当前检出 `main`（`f5fa1a3`，工作区干净）。
   - 审查分支 `claude/core-chain-review` 基于 `f5fa1a3`，提交 `d297605` 加本续接文件。只在本机，未推送，未合入 `main`，未部署。
   - `d297605` 做了四件事：
     - 测试隔离：`agent_queue.default_db_path()` 和 `agent_recon.recon_evidence_root()`，配合 `tests/conftest.py`。
     - 邮箱归属收紧：`Opus/email_matching._pinyin_match`，以及 `detail_mecc.is_personal_email_for_scholar`，这是唯一实现。
     - 邮箱粘连切分：`Opus/text_lists.email_spans`。
     - 翻页按已核实卡片判断：`browser_batch_tool._unit_signature` 和 `_wait_for_change(unit_selector=...)`。
4. 环境注意：
   - **Windows**：开发 Python 是 `.runtime\dev-venv\Scripts\python.exe`，生产 Python 是 `.runtime\worker-venv\Scripts\python.exe`。
   - **Cowork 的 `device_bash`**：它运行在 Linux 虚拟机里，跑不了 Windows 的 exe，而且不能删文件。在那里执行 `git status` 会留下空的 `.git/index.lock`。所以只读 git 命令一律加 `--no-optional-locks`；提交和检出放到 Windows 上做，或者用 bundle 加 `git fetch`（上一轮验证过可行）。
   - **Cowork 桌面控制**：终端和资源管理器只能点击，不能输入。需要在 Windows 上执行的命令，请用户自己双击或运行。
   - **云端 Linux 跑测试**：Playwright 1.47 需要 Chromium 1134。做法是新建一个目录 `<dir>`，建立 `<dir>/chromium-1134/chrome-linux/chrome` 指向 `/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell` 的软链接，再在 `<dir>/chromium-1134/` 下放两个空文件 `INSTALLATION_COMPLETE` 和 `DEPENDENCIES_VALIDATED`，然后设 `PLAYWRIGHT_BROWSERS_PATH=<dir>`。不要运行 `playwright install`。
   - **只读正式库**：`sqlite3.connect('file:server/tasks.db?mode=ro&immutable=1', uri=True)`，只输出计数，不输出姓名或邮箱。
5. 需要用户手动清理的本机遗留：
   - `.runtime\claude-review-20260930\_to_delete\`：虚拟机里 git 留下的锁文件和临时包，已挪到这里。
   - `.runtime\claude-review-20260930\rt_b`：测试文件，可删。
   - 正式库里那条假的自检失败记录，下次正常启动 worker 时会被覆盖，无需处理。

## 1. 验证已提交的改动（只读，不用确认）

1. 在 Windows 上检出 `claude/core-chain-review`，逐项审阅 `git diff f5fa1a3..claude/core-chain-review`。
2. 用户在 Windows 上跑全量：`.\.runtime\dev-venv\Scripts\python.exe -m pytest -q`，加上 `ruff check .` 和 `git diff --check`。对照结果：云端全量 843 passed、3 skipped；Windows 上 3 个 skip 里有 2 个可能不 skip。
3. 核对 `server/tasks.db` 的修改时间在测试前后没有变化，`server/outputs/recon-evidence/standalone/` 没有新增目录。这一步证明测试隔离生效。

## 2. P1-3：字段提取阻塞 worker 事件循环（最优先）

问题：`browser_batch_tool.run_plan` 在 worker 的 asyncio 循环里同步调用 `FacultyHarvestPipeline.harvest_candidates` 和 `_enrich_extracted_records`。没有正文选择器的个人页要做跨页模板推断，耗时随页数平方增长。阻塞期间心跳和租约续期（300 秒）都停住，同一进程里的其他学院也被卡住。

修两处，先写失败测试：

1. **按页面缓存兄弟页结构**，结果必须与原来完全一致。
   - 做法：给 `Opus/template_infer.infer_template_nodes_robust` 加一个可选参数 `pages: dict[int, _Page]`，按 `id(soup)` 复用 `_Page`。`detail_mecc.DetailMECCExtractor.extract_cluster` 在循环外建一个字典，传给每次调用。
   - 测试：生成 20–60 个合成个人页（带导航和页脚、正文长度不同、部分页含邮箱），比较改前改后每页的 `full_bio`、`emails`、`contact_emails`、`email`，要逐字相同。改前的基准可以先存成 fixture。
   - 基准数据：合成 6KB 页面，50 页 5.9 秒、100 页 25.7 秒。剖析显示 `_Page.__init__` 占 69%，60 页时构建了 3600 次，缓存后应是 60 次。
2. **把 CPU 密集段放进线程**。
   - 做法：`run_plan` 中改为 `extraction = await asyncio.to_thread(FacultyHarvestPipeline.harvest_candidates, ...)`，`_enrich_extracted_records` 同样处理。
   - 线程安全：`_readings` 用 `lru_cache`，`DomainConventionModel` 每次新建，BeautifulSoup 对象不跨线程共享。检查这几处没有模块级可变状态被并发改写。
   - 测试：用 monkeypatch 把提取函数替换成 `time.sleep(2)`，同时起一个每 0.1 秒计数的协程，断言提取期间计数照常增长，证明事件循环没有被阻塞。再补一个整流程断言，确认 `run_plan` 的结果不变。
3. 可选：`reconcile_rosters` 和 `identity_review` 的开销较小，这次先不动，在报告里注明。

## 3. P2-6：职称优先级（先统计，再请用户决定）

问题：`harvest_candidates` 里 `title = prof.title if prof.title != "教师" else cand.title`，个人页正文中最早出现的职称词会覆盖名录卡片上写明的职称。例如 `resolve_title("曾任副教授，现任教授")` 返回“副教授”。

1. 先在本机虚拟机做只读统计。虚拟机需要装 pypinyin、scipy、numpy，可以用 `pip install --target $HOME/pylib ...`，上一轮已装过，会话重开后可能要重装。
   - 用 `server/outputs/tasks/*/attempt-*/*_snapshots/` 里的名录页和个人页快照，统计卡片职称和个人页职称不一致的条数。
   - 在不一致的条目中，再统计个人页后文是否同时出现了卡片职称（这说明个人页取到的是履历中的旧职称）。
   - 只输出计数。
2. 拿统计结果请用户决定规则。建议的优先级：个人页中带标签的“职称：”字段 > 卡片职称（非“教师”）> 正文中最早出现的职称词。
3. 用户同意后，先写失败测试再改。

## 4. 评估邮箱收紧的影响（只读回放，不用确认）

邮箱归属收紧后，没有标签、也不能拼出本人姓名的地址会从“直接交付”变为 `ambiguous_email_assignment` 复核。

- 用本地保存的法学院 attempt-4 个人页（和美术与设计学院已保存的页面，如有）回放 `harvest_candidates`，统计新增的复核项数量，报告给用户。
- 已知数据：历史 385 个已交付邮箱中，有 11 个只在旧规则下成立，但缺少本地快照，无法判断对错。

## 5. P3 小项（有余力再做，每项先写失败测试）

- `harvest_eligibility.classify_candidates`：医院来源且没有个人页时，决定是 `profile_unavailable`，但没有写入 `unresolved`。学院会显示完成，大学级交付却被挡住。
  - 做法：补一个 unresolved 项，并把这个代码加进 `recovery_policy` 的 review 组。
- `faculty_extractor` 里定义了一份重复的 `LayoutAwareTokenizer`，覆盖了从 `detail_mecc` 导入的版本，导致共享页卡片不识别行内样式 `display:block`。
  - 做法：删掉重复定义，确认相关测试不变。
- Excel 单元格上限是 32,767 字符。本机最长简介 20,186 字，暂未超限。是否在导出时检测超限并阻断，请用户决定；不要擅自截断。
- L1 缓存（`runtime_l1`，`wrappers.db`）实际处于闲置状态。本轮不动，只记录。

## 6. 收尾（每一步都要用户确认）

1. 合入 `main`，是否推送到 GitHub 由用户决定。
2. 部署：
   - 确认没有 queued、running、compiling 状态的工单。
   - 备份 `server/tasks.db` 和 `.runtime/*.log`。
   - 由用户双击 `重启本地服务.bat`。不要运行 `.runtime/restart_after_roster_fix.ps1`。
   - 核验网页、`/api/worker/status` 心跳和 `/api/queue/stats`。
3. 按[采集内核交接](2026-09-30-extractor-core-handover.md)处理两张失败工单，每一步先告诉用户要做什么：
   - 94c37a15：只差补充 PDF 的人工核准。
   - db9a7d34：需要访问学校网站做详情批采和身份对账，要先征得同意。
4. 更新 `CHANGELOG.md` 和审查文档的验证结果，写清测试结果与实网验证的边界。
