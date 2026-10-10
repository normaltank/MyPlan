# MyPlan 模块索引

本文件帮助定位代码，不保存逐次提交日志。先读 [CONTEXT.md](CONTEXT.md) 理解契约；任务按 [AGENTS.md](AGENTS.md) 分工，采集维护状态与证据见对应工单和 PR，全链路审查 agent 的专项状态见 [五项计划](docs/improvement-plan.md)。当前分支、开放 PR、合并状态及 CI 直接查 GitHub，不从旧交接文字推断。

## 按调用链定位

| 边界 | 主要入口 | 阅读目的 |
| --- | --- | --- |
| 输入与网页 | `server/app.py`、`server/intake.py`、`server/templates/` | 三／四列输入、批次、结果和复核交互 |
| 队列与 worker | `server/agent_queue.py`、`server/agent_worker.py` | 租约、状态、失败分类、补缺、完成与恢复 |
| 入口发现与计划 | `server/agent_recon.py`、`server/recon_browser_mcp.py` | 浏览器观察、结构计划、Schema、缓存及阶段反馈 |
| 浏览器采集与覆盖 | `server/browser_batch_tool.py`、`server/harvest_workflow.py`、`server/harvest_coverage.py` | 名录分支、分页、断点、原件与审计 |
| 人员、字段与身份 | `server/faculty_extractor.py`、`server/faculty_identity.py`、`server/titles.py`、`Opus/` | 人员范围、职称、邮箱、简介及合并证据 |
| 来源资格 | `server/harvest_eligibility.py`、`server/recon_contract.py` | 页面用途、学校与学院归属、师资范围 |
| 图片、PDF 与长简介 | `server/image_biography.py`、`server/pdf_roster.py`、`server/pdf_biography_owner.py`、`server/biography_workbook.py` | 原件读取、本人范围、复核裁图及长正文导出 |
| 复核与恢复 | `server/review_registry.py`、`server/review_evidence.py`、`server/recovery_policy.py` 及相关 `*_review.py` | 决定绑定、失效、原件携带和重采闭环 |
| 大学汇总与下载 | `server/university_delivery.py`、`server/university_identity_review.py` | 去重、学院关联、交付门禁、版本和下载一致性 |
| 人工无师资 | `agent_queue.mark_no_faculty` 及 `app.py` 对应路由／模板 | 人工结论、记录变化、统计和旧版本影响 |
| 回放与核验辅助 | `server/case_replay.py`、`server/spot_check.py`、`tests/test_harvest_acceptance.py` | 保存页比较、输出抽检、原件缺口和验收边界 |

定位后追踪真实调用者及测试；文件存在不证明所有入口已遵守同一契约。

## 运行时 Markdown

`agent_recon.py` 在导入时读取以下文件；其内容进入模型侦察，修改后须核对计划卫生、工具和提示词相关测试：

- [发现入口](server/recon_discovery.md)：三列输入后的师资入口发现。
- [给定链接补充发现](server/recon_provided_links.md)：优先核实给定链接并检查遗漏分支。
- [分支结构验证](server/recon_instructions.md)：名录、正文、分页等结构核验。

既有 worker 不会因根目录 Markdown 更新而自动重新加载；实际运行须记录加载的代码、配置、模型和提示词版本。采集作业路由见 [AGENTS.md](AGENTS.md)，运行和排障见 [运维手册](docs/operations.md)。

## 按问题找既有证据

先查相关文件的 Git 历史和 PR；需要原始反例时，再读 `docs/implementation/`、`docs/audits/` 和 `docs/site-types/` 的对应材料。例如：

- 复核原件与恢复：[review-evidence-metadata](docs/implementation/2026-10-10-review-evidence-metadata.md)。
- 明确职称字段：[declared-title-fields](docs/implementation/2026-10-10-declared-title-fields.md)。
- 完整简介与工作簿：[full-biography-workbook](docs/implementation/2026-10-10-full-biography-workbook.md)。
- PDF 本人范围、履历与重试：[pdf-owner-resume-sections](docs/implementation/2026-10-10-pdf-owner-resume-sections.md)、[native-pdf-owner-retry](docs/implementation/2026-10-11-native-pdf-owner-retry.md)。
- 个人链接卡片边界：[roster-link-boundary](docs/implementation/2026-10-10-roster-link-boundary.md)。

这些是对应版本的证据记录。记录中的“通过”“待 CI”“未部署”不自动延伸到当前主线或用户运行环境。更早交接保留在 Git 及 [旧快照](docs/history/2026-10-10-handover-before-guidance-refresh.md)。
