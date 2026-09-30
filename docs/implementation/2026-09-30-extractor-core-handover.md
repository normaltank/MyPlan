# 采集内核修复与续接说明

更新时间：2026-09-30 18:48（UTC+8）。用户因额度接近上限要求保存和交接；本轮停在验证与备份阶段。

## 当前主要任务

稳定已有的“师资名录 → 教师个人 URL → 原始教师信息 → 审计 → 大学 Excel”链路。优先修提取内核，暂缓增加外围审核功能、网站类型库、依赖升级和大规模重构。入口发现、队列、结构侦察、缓存恢复、复核和交付已有成果，不能重新从零搭建。人工核准的师资入口可以接收，但还没有完整落地“独立审核 B 后再启动 C”的新工作流，也没有完整官网自报总条数对账。

用户要求客观说明问题与验证边界；不能承诺 100% 准确率。此前 0.7 元/千人的口径不是已验证的端到端成本。

## 工作区与部署状态

- 路径 `D:\Agent Workspace\Intern\MyPlan`，分支 `codex/harvest-runtime-improvements`，HEAD `b0aedb84a99424e9a361e059a67f5f7793856227`。
- 大量未提交文件包含前几轮运行改进、两工单排障和本轮内核修复；不要只保存本轮差异，也不要盲目 `git add -A` 后混成已验证完成提交。本轮没有提交、推送或创建 PR。
- 本地常驻服务仍是 2026-09-30 12:30 启动的进程：worker 3612/99956，Web 107620/133784。**最近排障及本轮内核修复尚未重启部署**。进程存在不能证明它加载了磁盘上的新代码。
- 开发 Python：`.runtime/dev-venv/Scripts/python.exe`；生产 Python：`.runtime/worker-venv/Scripts/python.exe`。
- 原始 HTML、图片、PDF、教师数据留在被忽略的 `server/outputs/`、`.runtime/`，严禁提交。备份也留在 `.runtime/backups/`。
- 本次保存目录：`.runtime/backups/extractor-core-wip-20260930-184825/`。其中 `worktree.zip` 保存全部未提交的已跟踪/未跟踪非忽略文件、原始回归探针和交接元数据；`worktree.patch` 保存已跟踪差异，`manifest.json` 记录分支/HEAD/文件摘要。它是本地备份，不是 Git 提交或远端推送。官网原始证据仍在本机忽略目录，不打入源码备份。
- Opus 最新任务在云电脑，尚未提交，因额度不足暂停；不能从本机 Git 获取这些成果，不要继续找本地 Claude 会话。后来拿到补丁/分支后再比较。

## 本轮已经修改

1. **正文完整性**：新增 `server/dom_regions.py`，用完整结构名称识别噪声，取消 class/id 任意子串判断。`detail_mecc` 和 `faculty_extractor` 共用规则。更重要的是浏览器已核准正文通过 `preserve_content` / `verified_profile_urls` 明确传给解析器，完整保留选中范围；不再重新猜 MECC 或删除其中 header/footer 类元素。原始快照不修改。
2. **邮箱归属**：`ProfileDetail` / `FacultyProfile` 新增 `contact_emails`，从正文内明确的邮箱字段、表格标签或 mailto 可见文字取正证据。独立个人页的唯一明确邮箱不再要求符合姓名拼音。多个地址、重复归属、存在地址但缺少归属证据以既有 `ambiguous_email_assignment` / `ambiguous_shared_email` 阻断，不能静默归入无邮箱表。拼音匹配接口仍保留，既有公共服务邮箱反例仍通过。
3. **卡片边界**：`extract_with_review` 新增可选 `card_selector`，`reconcile_rosters` 传入已核实选择器，普通名单逐卡处理，全页矿掘不能覆盖已核实范围。姓名对应的链接优先于前面的照片资源；一张卡片有两个明确教师链接时保留两条候选并报数量不符，不能当一人通过。
4. **未知姓名复核**：已核实卡片中的外籍译名等低先验标签进入既有 `name_review`，决定仍绑定原卡片和 URL。机器状态保留 `rare_surname_needs_review` 兼容接口，额外 `reason=unrecognized_name_slot` 在单项复核卡片显示“姓名待确认”；仅该问题不再走模型结构补缺。
5. **名单串边界**：最近空格名单解析误把“教授”当第二个姓名，已用现有职称词表识别字段注释。拥有个人姓名链接的卡片不再被名单串处理误转为无个人主页。保留空格/NBSP 多姓名处理。

核心修改文件：`server/dom_regions.py`、`detail_mecc.py`、`faculty_extractor.py`、`harvest_workflow.py`、`browser_batch_tool.py`、`name_list.py`、`name_review.py`、`image_bio_review.py`。新增正式回归 `tests/test_extractor_core_contract.py`。

## 已跑的验证

- 审查时原始探针当前 **6 passed**；修复前当前文件和 b0aedb8 独立工作树都是 **6 failed**。包括两个真实 Chromium、全网络本地路由的 `run_plan` 验证，证明邮箱/正文原来的静默遗漏已修复；没有访问学校或调用 DS。
- 最新相关组合 **52 passed**：内核契约、罕见姓名复核、空格名单、固定采集验收。正式内核契约 11 个用例在此组合通过。
- 另有正文范围/MECC 组合 50 passed、邮箱/提取组合 25 passed、姓名/失败策略组合 35 passed。这些有重叠，不可相加当成全量通过数。
- 本轮触及的 Python 文件 ruff 全通过；原始探针保存在 `.runtime/test_extractor_review_20260930.py`。
- **本轮还没跑最终全量回归**。历史 700 passed/1 skipped 与更早 667 passed/1 skipped 都不代表现在的文件已全量通过。

复现命令：

```powershell
.\.runtime\dev-venv\Scripts\python.exe -m pytest tests/test_extractor_core_contract.py tests/test_name_list_spacing.py tests/test_rare_surname_review.py tests/test_harvest_acceptance.py -q --tb=short
.\.runtime\dev-venv\Scripts\python.exe -m pytest .runtime/test_extractor_review_20260930.py -q --tb=short
```

## 接下来按顺序做

1. 跑最终全量 `python -m pytest -q`、`python -m ruff check .`、`git diff --check`；修复真实回归，不降低既有对账/交付断言。
2. 把私有探针中的两项 Chromium 整流程回归移入正式测试并标记 `@pytest.mark.browser`；增加已核准正文中的 header/footer 类内容完整保留的整流程反例。当前新增正式文件只有单元/公开接口回归。
3. 检查邮箱行为剩余边界：共享多人主页 `_resolve_shared_page_candidates` 仍以拼音匹配为主，非拼音邮箱可能遗漏，须先建立失败用例；本轮没有宣称它已修复。普通 HTML 的邮箱歧义已阻断，但不能宣称已有完整人工裁决入口（现有专用邮箱复核主要针对图片）。核实 mailto 标签、拆开的表格标签及多联系字段后再放行。
4. 回放已保存的法学院、美术与设计学院页面，并用有限实网验证修复；此前合成页通过不能当成新一轮学院成功。要付费采集时遵守学院级技能和独立派发要求。
5. 全量与边界检查通过后，先确认没有 queued/running/compiling 工单，保存正式库与日志，再使用项目现有启动脚本切换服务。`.runtime/restart_after_roster_fix.ps1` 是未执行的旧草稿，含 PowerShell/Python 引号问题，不可直接运行。切换后核验 Web、队列状态接口、worker 心跳和实际加载时间。
6. 再处理两张真实失败工单的来源范围和恢复交付，随后同步 Opus 云端补丁。官网总条数对账和分阶段审核排在内核稳定之后；Playwright 升级/包结构最后。

## 两张工单仍未交付

- `94c37a15de674b2c956b1469c802678a`：湖南理工学院法学院。主名录实网三页 9/9/2，共 20 条通过；补充 PDF 待核准。PDF 19 页，16 个个人页标签，与主名录规范姓名重叠 13，标签并集 23；其中一页标题/正文姓名冲突，不能称新增三人或全学院 23 人。简介中的 37 人无明确日期，仅为差额线索。正式任务仍 failed，诊断已附 attempt-4，无 Excel、无教师入库。
- `db9a7d3401c34f5b8ef20464360ef5f6`：用户确认按链接采集湖南文理学院美术与设计学院，正式库已更正目标并保留原值。此前预核验为 26 可见卡片 + 46 机构名单 = 72 来源观察、53 候选姓名；不是 53 个已交付教师。本轮新内核尚未重放这些页面，未执行最终详情批采/身份对账，任务仍 failed。证据在 `server/outputs/debug-recent-20260930/commerce/recovery/`。

不要把两工单重新排队后任由旧进程重复收费。完整历史与本机证据索引见 [两工单排障](../audits/2026-09-30-two-live-college-failures.md)、[内核审查](../audits/2026-09-30-extractor-core-review.md)、[前期运行改进](harvest-runtime-improvements.md)。
