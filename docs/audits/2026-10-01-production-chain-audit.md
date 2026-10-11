> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行业务见 [CONTEXT.md](../../CONTEXT.md)，开发任务按 [AGENTS.md](../../AGENTS.md) 分工；使用原结论前核对代码版本、样本与原始证据。

# 生产采集调用链审查（2026-10-01）

- 审查提交：`main` `dbfdfb9e7b030d1ebd5493eec19e0b30c4b572a2`（Record synchronized baseline and Claude project assignments）。任务 A 合并后（`a25084c`）在新 `main` 上重跑了全部复现，结论不变；下文行号以 `a25084c` 为准。
- 性质：只读审查。未改生产代码、未操作生产任务库、未访问真实高校站点、未调用 DeepSeek 或其他付费侦察。
- 验证环境：隔离 Python 3.11 虚拟环境，`requirements-dev.txt`；Playwright 1.47 + 本机 Chromium headless shell；临时 `HARVEST_TASK_DB` 与证据目录。页面全部为 `context.route` 返回的合成 HTML，人名为虚构。
- 基线：在 `dbfdfb9` 上全量 `pytest` 979 通过、1 失败、3 跳过（失败见 T1，已由任务 A 的 `850d8c9` 修好，`a25084c` 上该文件全部通过）。
- 复现脚本：[`2026-10-01-production-chain-repro/`](2026-10-01-production-chain-repro/)。A1、A2、A4 已分别由 PR #10、PR #8 修复，各自的回归测试已在 `tests/` 中，本目录对应复现随之删除，只保留 L1（已知限制，按现状断言）。运行：`python -m pytest -q docs/audits/2026-10-01-production-chain-repro`（不在 `testpaths` 内，CI 不收集，ruff 已排除 `docs/`）。

## 结论速览（按实际影响排序）

| 编号 | 问题 | 类别 | 建议 |
|---|---|---|---|
| A2 | 加载后才渲染的分页按钮或导航子链接被当成“已到末页”，学院少人仍判完整并交付 | 已复现缺陷，已由 PR #10 修复 | 修复后按疑点部分回查已交付学院 |
| A1 | 未写 `max_pages` 的名录超过 20 页时，worker 检查点在第 21 页抛错，整学院以“访问失败”告终 | 已复现缺陷，已由 PR #10 修复 | 无需处理 |
| A4 | 批量子任务的“表格”和单学院“下载学院中间成果”链接全部 404 | 已复现缺陷，已由 PR #8 修复 | 无需处理 |
| T1 | `dbfdfb9` 上 `tests/test_empty_roster.py` 一项测试失败 | 已在 `main` 修复（`850d8c9`） | 无需处理 |
| S1 | 采集完成后若回传 Web 失败（如 Web 重启），工单直接终态失败，只能重新提单 | 尚未证实的疑点 | 可以暂缓 |
| C1 | IDE 海报流程的 `verified_candidates` 由 Subagent 录入姓名、职称，与宪法“数据由确定性代码提取”不一致 | 宪法一致性（非生产 worker 路径） | 已处理（2026-10-08，按现行流程拒收） |
| L1 | 同一批次两个学院出现同名不同人，大学版本被阻断，且没有跨学院复核入口 | 已知功能限制，2026-10-08 补上大学级复核入口 | 负责人批准后实施（不同教师／证据不足） |
| L2–L4 | 末页“下一页”仍可点、短彩色图片按照片跳过、`split_glued_numeric` 空操作 | 已知功能限制 | 保持现状 |

## 已从代码核实的调用链

1. 提单：`server/app.py` `/api/collect`、`/api/collect_batch` → `AgentTaskQueue.submit_task/submit_batch`（SQLite，`owner_user_id`、`batch_id`）。
2. 认领：`server/agent_worker.py` 常驻进程 → `claim_task`（`worker_id`＋`lease_token`＋`lease_until` 三要素围栏，后台续租）。
3. 侦察：`server/agent_recon.py` DeepSeek Harness＋Playwright MCP；`check_plan_hygiene`、`validate_model_plan` 只放行 URL、选择器、控制值；`selector_evidence_issues` 要求本轮浏览器观察证明（卡片数、`nextCount == 1` 等）；计划带摘要，`_resume_plan` 24 小时内同工单复用。
4. 确定性采集：`browser_batch_tool.run_plan` → `_capture_sources_with_state`（`_capture_branch` 分页、`_discover_children` 导航）→ `reconcile_rosters` → 个人页 `capture_profiles`；字段由 `faculty_extractor`、`detail_mecc`、`image_biography`（本地 OCR）、`segmented_roster` 提取。
5. 覆盖与审计：`harvest_coverage`（`TERMINAL` 终止证明）、身份复核、`field_checks`；`recovery_policy` 分 access／structure／review／stop，最多 2 次本地访问重试、2 次付费结构修复。
6. 回传与交付：`agent_worker._sync` → `browser_batch_sync.sync_report` → `POST /api/task/{id}/sync` → `complete_task` → `university_delivery.publish_delivery`（版本化批次工作簿＋当前快照，`excel_export` 双 Sheet 十列）。
7. 下载：`server/templates/index.html` 渲染 `download_url`、`university_files` → `app.download_file`（非管理员只能下载 `versions/` 下且范围 ready 的文件）。

## 已复现缺陷

### A2 加载后渲染的分页或导航被判为完整（fail-open，静默少人；已由 PR #10 修复）

PR #10（`ebd06fa`）按下文最小修复落地：读取页面和子链接前有界等待 `networkidle`，首页读不到已核实的下一页控件时记 `pagination_not_verified`。回归测试在 `tests/test_deferred_pagination_and_long_roster.py`。以下为修复前的记录。

- **位置**：`server/browser_batch_tool.py:500`（`goto(..., wait_until="domcontentloaded")`）→ `:523` 立即 `page.content()` → `:551-556` 下一页控件计数为 0 即记 `next_control_absent` 并结束；`:521-522` 只有 `empty_state_selector` 分支才等 `networkidle`。导航同理：`:608-612` `_discover_children` 在 DOMContentLoaded 后立即读子链接。对照：断点续采路径 `:478-479` 已经等 `networkidle`，新采路径没有。
- **为何被判完整**：`server/harvest_coverage.py:5,20-21` 把 `next_control_absent` 视为终止证明；导航分支只要求每个**已发现**子分支有终止证明，少发现的子链接不会出现在任何账上。
- **触发条件**：分页按钮或院系导航由页面脚本在 DOMContentLoaded 之后插入（常见于 Vue/jQuery 渲染的列表、AJAX 返回总页数后再画分页条）。侦察阶段在已稳定的浏览器里看到了控件（`server/agent_recon.py:595` 要求 `nextCount == 1`），采集阶段读得太早。
- **复现**（`test_a2_deferred_pager.py`、`test_a2_navigation_children.py`）：
  - 分页：第 1 页 2 人，`a.next` 在 DOMContentLoaded 后延迟插入，第 2 页 2 人。服务端直接渲染时采到 2 页 4 人；延迟 400 ms 时 `directory_pages=1`、`total=2`、`end=next_control_absent`、`coverage.complete=True`、`delivery_ready=True`，`unresolved` 为空。延迟扫描：0 ms 3/3 正常，50 ms 3 次失败 1 次，150 ms 与 400 ms 3/3 失败。
  - 导航：3 个院系链接中 1 个延迟 300 ms 插入，`discovered_children=2`、`total=2`、`delivery_ready=True`，第三个院系整体缺失。
- **预期**：控件晚到时继续翻页；确实读不到时至少不应给出终止证明。**实际**：少采的教师不进任何未解决项，学院按完整交付。
- **影响**：这是唯一能让“少人”静默进入交付的路径，违反“不能把采集不完整判为完成”。真实站点的发生率未测（见疑点部分）。默认开启的交叉核对 `agent_worker.py:583-590` 只记录差异、不改变交付，挡不住。
- **最小修复**（只动 `_capture_branch`、`_discover_children`，不改计划契约）：
  1. 在读取页面和判定 `next_control_absent` 前，做一次有界的 `wait_for_load_state("networkidle", timeout≈3000)` 并吞掉超时（与 `:479` 一致），然后重读控件计数；`_discover_children` 读子链接前同样等待。
  2. 首页保护：`next_selector` 分支在第 1 页（非断点恢复）仍读不到控件，与侦察证据矛盾，记为现有的 `pagination_not_verified`（structure）而不是终止证明，让它走结构修复而不是静默交付。
  导航分支没有侦察时的子链接数可对照，硬性保护需要新增计划字段（契约变化，本次不建议）；第 1 步已覆盖常见情况。
- **为何测试没覆盖**：现有浏览器测试的分页与导航夹具全部是服务端直出 HTML，控件在 DOMContentLoaded 时已经存在。

### A1 超过 20 页的名录在 worker 检查点下必然失败（fail-closed，错误归因；已由 PR #10 修复）

PR #10 去掉了没有 `max_pages` 时的 20 页检查点上限，回归测试 `test_roster_longer_than_twenty_pages_completes_under_worker_checkpoint`。以下为修复前的记录。

- **位置**：`server/harvest_state.py:121-122` `save_roster_progress` 要求 `0 <= sequence < branch.get("max_pages", 20)`，否则 `ValueError("invalid roster sequence")`；`:141` `load_roster_progress` 与 `browser_batch_tool.py:664` 完成分支校验也用同样的 `20` 上限。`browser_batch_tool.py:690-692` 每页回调检查点，异常被 `_capture_branch` 的 `:576` 捕获，变成 `:592` `browser_navigation_failed`。
- **触发条件**：分页名录超过 20 页，且计划未写 `max_pages`。`server/recon_instructions.md:27,44` 明确要求侦察在总页数未核实时省略 `max_pages`，所以这是默认情况。不带 `state` 调用时无此上限（`tests/test_quantity_limits.py` 的 22 页夹具能采完），只有生产 worker 的带检查点路径受影响。
- **复现**（`test_a1_long_roster_checkpoint.py`，22 页合成名录）：无状态采集 22 页、`next_control_absent`；按 worker 方式 `run_plan(..., state=HarvestState(...))` 时 `directory_pages=21`，`unresolved` 为 `browser_navigation_failed`（`ValueError: invalid roster sequence`）与 `branch_termination_unverified`，`delivery_ready=False`。
- **预期**：采完 22 页并交付。**实际**：第 21 页失败，`recovery_policy.py:11,67-83` 把它与同分支的终止未证实都归为 access。按代码推断，两次本地重试都从已存的 20 页恢复，再次在第 21 页抛错，工单最终在 `agent_worker.py:334-353` 以“审计未通过交付门禁”失败，失败码为 `browser_navigation_failed`（worker 全流程未实跑）。
- **影响**：大学院系的长名录（每页 10–15 人、200 人以上）必定失败，不会少人，但会让员工和运维误判为网站访问问题。
- **最小修复**：检查点上限与采集上限保持一致。没有 `max_pages` 时不设检查点页数上限（或使用与 `_capture_branch` 一致的显式安全上限），三处 `branch.get("max_pages", 20)` 一起改。
- **为何测试没覆盖**：`tests/test_quantity_limits.py` 的 22 页用例不带 `state`；`tests/test_pagination_resume.py` 带 `state` 但只有几页。

### A4 学院级下载链接全部 404（已由 PR #8 修复）

PR #8（`4dc1c13`）按下文第一种方案修复：`build_payload` 不再发送学院级 `download_url`，前端不显示该链接，学院结果只经版本化大学交付。回归测试为 `tests/test_browser_batch_sync.py::test_worker_layout_sends_no_unservable_college_link`，本审查的 A4 复现因此删除。以下为修复前的记录。

- **位置**：`server/browser_batch_sync.py:69` `"download_url": f"/download/{excel.name}"`，只取文件名；worker 实际把学院表写在 `outputs/tasks/<task_id>/attempt-N/<学校>_<学院>.xlsx`（`agent_worker.py:429`、`browser_batch_tool.py:1208`）。`server/app.py:744-749` `download_file` 按 `OUTPUT_DIR / filename` 查找，找不到即 404；`:754-755` 非管理员本来也只能下载 `versions/` 下的文件。前端 `server/templates/index.html:880`（批量子任务“表格”）与 `:829`（单学院“下载学院中间成果”）直接渲染该链接。生产队列库有 `college`、`batch_id` 列，`sync_report` 走 `:90-110` 分支，不会改写这个 URL（`:137` 的改写只在旧库分支）。
- **复现**（原 `test_a4_college_download_link.py`，修复后已删除）：`build_payload` 给出 `/download/样本大学_样本学院.xlsx`，以内部管理员身份调用 `download_file` 得到 404，文件实际存在于 `tasks/abc123/attempt-1/`。
- **预期**：链接能下载，或者不显示。**实际**：所有用户点击都 404。
- **影响**：大学级下载不受影响（`publish_delivery` 生成 `versions/` 链接）。但大学版本未 ready 时（例如 L1 阻断、批次未齐），员工唯一可见的学院级入口是坏链接。
- **最小修复**：二选一，建议前者，保持简单：
  - `build_payload` 不再给学院级 `download_url`（置空），前端自然不显示；学院中间成果只通过大学版本交付。
  - 或给出相对 `OUTPUT_DIR` 的完整路径，并在 `download_file` 中明确管理员可下载 `tasks/` 路径。
- **为何测试没覆盖**：同步测试只断言 payload 字段存在，没有用 worker 的真实目录布局把链接送进 `download_file`。

### T1 测试替身过时（已修复）

`dbfdfb9` 上 `tests/test_empty_roster.py:70` 用 `SimpleNamespace` 冒充候选人，缺少 `harvest_workflow.py` 已读取的 `mailto_conflicts`，非浏览器 CI（`.github/workflows/tests.yml:34`）会失败。任务 A 的提交 `850d8c9` 已改用真实 `FacultyCandidate`，`a25084c` 上通过，不再列为待修。

## 尚未证实的疑点

- **A2 在真实站点的发生率**：只用合成页面证明了机制。哪些已采学院是“分页条晚到”的站点，需要用已有的侦察证据（`nextCount` 观察）与采集审计（`directory_pages=1` 且 `next_control_absent`）对照筛查。本次不碰生产库，未做这项统计。A2 已修复，建议用这个条件回查修复前已交付的学院，必要时重采。
  - 2026-10-08 回查：只读脚本 `python -m server.a2_lookback`（加 `--json` 输出完整结果）对照每个已完成工单的覆盖账本与侦察浏览器证据，查三类：分页在侦察看到有效“下一页”的页面停下、某页卡片数少于侦察所见、导航子链接少于侦察所见。在本机生产库副本（124 个工单，91 个已完成，全部早于 PR #10）上运行：留有 worker 覆盖证据的 5 个学院未见异常；另 12 个只有 IDE 旧版审计的学院没有首页即停的分页（无侦察证据，后续页无法对照）；其余 74 个是 9 月 21–24 日旧流程产出，没有分页证据，此法无法判断。
- **S1 回传失败即终态**：`server/agent_worker.py:811-823` 任何异常（含 Web 重启导致的 `ConnectError`）都调用 `_mark_failed`。worker 是独立进程（`--base-url`），`retry_failed_review_task`（`agent_queue.py:272`）只服务人工复核后的重试，所以普通失败只能重新提单，新 `task_id` 用不上 `_resume_plan` 的计划复用，可能多一次付费侦察。触发需要 Web 恰好在回传时不可用，概率低，**可以暂缓**；如果要修，最小做法是对连接类错误在租约有效期内重试回传。

## 已知功能限制（保持现状）

- **L1 跨学院同名阻断大学版本**（`test_l1_cross_college_homonym.py`）：`server/university_delivery.py:42-65` 的 `approved_pairs` 只来自各工单自己的身份复核，同一批次化学学院与物理学院各有一位“王伟”（主页不同）时，冲突 `homonym_or_distinct_profile` 无法被任何现有复核放行，`publish_delivery` 返回 `blocked`，两学院都已 completed。这是宪法“同名不同人不得合并”的保守实现，方向正确；缺的是大学级复核入口。是否加、放在哪里，属于业务决策，不建议本轮顺手改。另外 `app.py:405-410` 在身份待复核时会清空下载链接，而 A4 修复后不再提供学院级链接，员工此时看不到任何下载入口。
  - 2026-10-08 负责人批准实施：大学交付范围新增复核页 `/review/university/{batch_id}?school=…`，逐组并排展示同名的全部原始观察（学院、主页、职称、邮箱、完整简介、学院审计哈希）。仅管理员可判定“不同教师”或“证据不足”，须写理由；决定追加写入 `tasks.db` 新表 `university_identity_reviews`，绑定完整观察集合，来源或记录变化即失效。生效的决定计入版本摘要和 manifest，撤销后依赖它的旧版本下载返回 409。跨学院“同一人合并”是新口径，未开放。复现脚本保持原样：未复核时仍为 blocked；放行路径见 `tests/test_university_identity_review.py`。
- **L2 末页“下一页”仍可点击**：最后一页的下一页链接仍可用、点击后内容不变，会记为 `pagination_unchanged`（structure），触发付费修复。属于 fail-closed，不丢人，保持。
- **L3 短彩色图片按照片跳过**：没有姓名的短彩色图片不做 OCR，是文档记录的取舍。
- **L4 `split_glued_numeric` 为空操作**：死代码，不影响结果。按“不因代码风格提改造”的要求，不处理。

## 宪法一致性（重点 6）

- **生产 worker 路径符合宪法**：模型输出只经 `check_plan_hygiene`（`agent_recon.py:281` 拒绝非结构字段）与 `validate_model_plan` 进入计划；`submit_plan` 绑定本轮观察与摘要；`build_repair_feedback` 回传给侦察模型的只有状态码、URL、选择器和整数计数，不含教师数据或页面叙述。字段全部由确定性代码从页面、图片 OCR 提取。
- **C1 IDE 海报流程**：`browser_batch_tool.py:101-117,893-905` 接受计划里的 `verified_candidates`（姓名、职称），`.agents/skills/faculty-browser-harvest/TOOLS.md:80-100` 让 Subagent 对照截图逐人录入。有截图证据和 `expected_count` 门禁，生产 worker 也会拒绝这个字段（`tests/test_agent_recon_hygiene.py:92`），但它仍是“模型录入数据”，与宪法第二条不一致。建议负责人决定：保留为人工复核通道，并在文档中写明“只能由人确认”；或者停用。**可以暂缓**，不影响生产交付。
  - 2026-10-08 处理：负责人决定按现行流程统一口径。`browser_batch_tool._validate_plan` 遇到 `verified_candidates` 或 `poster_reviews` 直接报错（与服务器侦察 `validate_model_plan` 一致），并删除录入人名入表、海报覆盖放行和 `agent_verified` 资格分支。每人一段的图文记录走 `segmented` 本地 OCR 与图片审核页；整张海报名单在确定性读取模块完成前保持未解决。`verified_appointments`（医院任职引文）不在本条范围，未改。

## 与并行任务的重叠

- **任务 A（多人 PDF 名录，已合并为 `a25084c`）**：同在 `server/browser_batch_tool.py`。任务 A 只改了 `run_plan` 中的整合部分，未动 A1、A2 涉及的 `_capture_branch`、`_discover_children` 和 `harvest_state` 检查点，修复不会与它冲突。`embedded_pdf_roster_requires_review` 与 PDF 分段问题归任务 A，本审查不重复报告。
- **任务 B（HTML 邮箱歧义复核恢复，已合并为 `b782b4b`）**：任务 B 读写 `harvest_state.py` 和单工单恢复；PR #10 修改检查点上限时已在其合并后的 `main` 上通过。L1 的跨学院身份复核入口与任务 B 的复核、恢复模式相近，但不在任务 B 范围内。

## 已核对、未发现问题的部分

- 租约围栏：`complete_task`、`sync_task_result` 都核对 `worker_id`＋`lease_token`，过期租约写入返回 409；`_mark_failed` 不改内存状态，避免二次写入。
- 恢复与缓存：`HarvestState` 按计划摘要隔离、24 小时过期；分支缓存要求终止证明、页数、序号与 HTML 摘要一致才复用；`_resume_plan` 绑定 `task_id`、入口 URL 和契约版本，未见跨工单混用。
- 交付门禁：审计未通过（`delivery_ready=False` 或有未解决项）时不同步（`agent_worker.py:334-353`）；大学工作簿只由 `publish_delivery` 从已完成学院快照生成，双 Sheet 守恒由 `excel_export` 保证；非管理员只能下载 ready 的版本文件。
