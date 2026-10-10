# MyPlan 当前交接与模块索引

更新：2026-10-10。先读 [AGENTS.md](AGENTS.md) 和 [项目目标](CONSTITUTION.md)。本文件用于定位代码与说明差距，避免新 agent 重复实现已有模块。

## 当前阶段与核对基线

用户反馈多个学院仍采集失败，尚不能开始常规业务运行。当前先统一指导文档，再并行进行整体代码质量审查和历史回归／新学院泛化验证。执行计划见 [docs/improvement-plan.md](docs/improvement-plan.md)。

指导文档更新提交为 9783b8858bb18bf87656847bc5014f77ab402ab6，其 [CI 运行 38029173964](https://github.com/normaltank/MyPlan/actions/runs/38029173964) 有三项测试失败。两处缺图测试对齐已由 [PR #51](https://github.com/normaltank/MyPlan/pull/51) 合入 80e6359，内容与本次本地复现、审查及回归使用的补丁一致。在此基线上补充修正 site_limits 中陈旧准入与等待期间未重查冻结的问题；缺图仍须人工复核，原浏览器限速断言保留。

修复包含锁定依赖下的本地回归与独立审查，具体记录见 [变更记录](CHANGELOG.md)；远端验收以修复提交对应的 Actions 最终结果为准。修改前仍须刷新分支与开放 PR，不能拿旧分支的绿灯代替当前提交。未验证用户实际运行版本，也未执行真实学院采集，不能沿用历史“已部署”描述。

## 已有代码入口

分页位移开发增量：`pagination_identity` 将实际前进控件绑定名录结构；多页缓存恢复重新访问前沿。机制与限制见 [说明](docs/implementation/2026-10-10-moving-pagination.md)，当前未部署、未完成真实学院验收。

本次链接边界增量：`FacultyDirectoryExtractor.links_outside_cards` 检查文字叶子遗漏祖先个人链接；侦察与确定性对账共用，失败在恢复表归为结构问题。完整链接及无链接路径保持；土木的全分支、无链接成员和归属仍待重验。机制、57项相关回归及独立复核边界见 [说明](docs/implementation/2026-10-10-roster-link-boundary.md)。

以下模块在该版本仓库中存在；列出入口不等于所有路径已经对齐或真实学院已验收。

| 环节 | 主要入口 | 审查重点 |
| --- | --- | --- |
| 输入与队列 | server/intake.py、server/app.py、server/agent_queue.py | 三／四列是否一致、状态与批次范围、任务隔离 |
| 编排与侦察 | server/agent_worker.py、server/agent_recon.py | 发现、验证、采集、恢复的契约；缓存与运行版本 |
| 运行指令 | server/recon_discovery.md、server/recon_provided_links.md、server/recon_instructions.md | 全师资口径、给定链接补分支、结构 JSON 和浏览器证据 |
| 浏览器与覆盖 | server/browser_batch_tool.py、server/harvest_workflow.py、server/harvest_coverage.py、server/pager_evidence.py | 名录／个人页角色、分支分页、来源与人数证据 |
| 字段与身份 | server/faculty_extractor.py、server/detail_mecc.py、server/faculty_identity.py、server/titles.py、server/name_list.py | 重复规则、字段归属、同名合并、原文保留 |
| 来源资格 | server/harvest_eligibility.py | 来源归属；旧专任口径是否在其他入口残留 |
| 图片与 PDF | server/image_biography.py、server/pdf_biography_owner.py、server/pdf_roster.py、server/image_table_roster.py、server/segmented_roster.py | 单元格／人员章节边界、OCR 不确定性、原件及字段证据 |
| 失败分流 | server/recovery_policy.py | 状态码、重试／结构修复／人工复核是否一致 |
| 学院复核 | server/review_registry.py、server/review_store.py、server/task_image_review.py | 登记、决定保存、证据失效、恢复传递及闭环 |
| 大学身份与交付 | server/university_identity_review.py、server/university_delivery.py | 跨学院身份、版本与原始记录、部分输出与完整状态 |
| 人工无师资 | server/no_faculty_review.py、agent_queue.mark_no_faculty | 人工判定、原记录处理、统计及重新采集边界 |
| 回放与验收 | server/case_replay.py、server/harvest_benchmark.py、server/spot_check.py、tests/test_harvest_acceptance.py | 可重放证据与独立参考答案的差别，新样本有效性 |

复核入口已有学院页 /review/task/{task_id} 和大学页 /review/university/{batch_id}。学院复核类别由 review_registry.KINDS 登记，决定文件通过 review_store 管理，跨 attempt 传递查看 task_image_review.carry_review_decisions。大学身份复核独立存在，不要照旧待办重新建设。

正文修复增量：计划拒绝保留采集的全部 URL 反例和完整计划；访问失败的伴随正文问题不再强迫扩大正常页面 CSS。见[恢复说明](docs/implementation/2026-10-10-profile-repair-counterexamples.md)。单人 PDF 简介按原件全部页的人员章节证据核对归属，保留作者／合作者全文，见[PDF 说明](docs/implementation/2026-10-10-pdf-biography-owner-scope.md)及[观察类型](docs/site-types/pdf-single-owner-biography-sections.md)。本地机制回归已通过，提交 CI、运行加载与真实学院验收仍分别核验；邮箱争议和来源资格不据此宣称解决。

## 新目标与现有实现的已知差距

1. 运行指令原先限定专任且限制给定链接的后续发现；本轮 Markdown 调整该口径。实际全院入口召回仍须真实任务验收，已有 coverage_scope 标记不能被文档更新自动升级。
2. 当前代码多处以未解决项阻断学院／大学发布。用户已确认可信部分可交付、争议记录单列；需要检查状态、导出、版本、恢复和身份合并后逐步落实，不能直接删除门禁。
3. 三列输入后另交四列师资入口表属于已确认目标；尚未完成全链路实现核查，不宣称已经提供。
4. 旧文档曾允许某些“唯一同名即合并”、以名录 URL 填个人主页、未知职称填默认“教师”等规则。需核对实际代码并对齐身份有证据、字段缺失如实记录的新口径。
5. 原始工单证据包尚未在本次工作环境取得。仓库已有案例计划和测试夹具；历史记录中的真实 PDF、日志、完整页面包所在外部路径须另行核实。
6. 复核补入口／补人／改字段后自动继续，及异常复核加正常抽检，是用户目标；各种复核入口的覆盖与恢复方式需逐项查证，不认定全部已完成。

## 人工无师资：当前真实行为

保存接口为 POST /api/task/{task_id}/no-faculty，调用 agent_queue.mark_no_faculty。只处理已结束的 harvest 工单；状态改为 completed、记录数清零并移除该工单数据库记录。stats.no_faculty 保存判定人、时间、原状态、原错误和原人数；coverage_scope 为 no_faculty_reviewed。

该标记目前属于本次工单，不是永久学院排除表。汇总完成数包含此类任务，评估时需单列；所读入口未见撤销判定接口，不应宣称可以直接撤销。旧复核文案仍有“没有专任教师”，界面代码需要另行对齐。历史材料称运行文件另存磁盘，其保留和恢复完整性仍需验证。

## 运行指令的生效边界

agent_recon 在模块导入时读取三份运行 Markdown；现有常驻 worker 不会自动热加载。后续实际采集须确认运行进程加载的版本。recon_plan_cache 的契约摘要包含这三份文件，旧计划缓存不能作为新口径已经验收的证据。本轮文档更新不触发部署、重启或生产工单。

## 本轮后续与历史

按 [审阅待办](docs/review-backlog.md) 和 [改进计划](docs/improvement-plan.md) 推进。每项改动报告代码版本、验证环境、样本范围和未完成项。

原长交接保存在 [更新前的交接快照](docs/history/2026-10-10-handover-before-guidance-refresh.md)。按具体问题查阅历史模块说明，不把其中“用户已确认”“当前已部署”“100% 采齐”继续作为现行事实。
