# MyPlan 文档导航与维护

## 按问题阅读

| 要回答的问题 | 唯一负责的文件或来源 |
| --- | --- |
| 项目做什么，关键术语是什么意思 | [CONTEXT.md](../CONTEXT.md)：目标、术语和关键契约 |
| Agent 如何开始和结束工作 | [AGENTS.md](../AGENTS.md)：简短入口、执行边界与验证要求；[CLAUDE.md](../CLAUDE.md) 仅转向它 |
| 采集维护 agent 现在处理什么 | 用户当前任务及对应采集报错、工单、PR；路由见 [AGENTS.md](../AGENTS.md) |
| 全链路审查 agent 的任务、证据和阻塞是什么 | [审查专用五项计划](improvement-plan.md)：仅该专项的任务与状态清单 |
| 代码在哪里 | [模块索引](../AGENT_HANDOVER.md)：稳定导航；细节查调用者、测试和 CLI |
| 怎样运行、核验环境或排障 | [运维手册](operations.md)：当前操作入口 |
| 具体提交、开放 PR、CI 到什么状态 | Git 与 GitHub；不在多份 Markdown 里复制动态状态 |
| 为什么作出某项长期取舍 | 对应 ADR；实现说明见 [architecture](architecture/) |
| 某个反例、实验或审查如何复现 | 对应 [audits](audits/)、[implementation](implementation/) 或 [site-types](site-types/) 记录 |

[CONSTITUTION.md](../CONSTITUTION.md) 与 [review-backlog.md](review-backlog.md) 分别保留项目目标和专项审查待办的兼容入口。[技能工作项路由](agents/issue-tracker.md) 按当前任务定位材料，不将专项计划分配给所有 agent。

## 修改时只更新负责该信息的地方

业务目标或术语改变，更新 CONTEXT；采集维护进展写入对应工单、PR 或已有问题记录，专项审查进展由审查 agent 更新五项计划；接口或操作改变，更新实际受影响的技术／运维说明。其他入口只需保持正确指向。

PR 描述写问题、变化与验证；CI 保存检查结果。只有需要复用的反例、复现过程或重要取舍才新增独立文档，并从相关任务或模块链接。普通维护不要求同时追加 handover、backlog、operations、changelog 四份流水账。

跨 agent 的结论必须可持久读取。临时文件在交接前提炼到现有工作项或证据记录；不要让主清单只链接某台机器的临时目录。完成的事项更新原条目，不靠不断追加“本次增量”使新旧状态并存。

## ADR 的用途

ADR 记录已选定、需要保留理由的重要工程取舍：背景、决定、被放弃的选项、后果与适用边界。提案先放在设计讨论或工作项里，不能仅写上 Accepted 就变成用户决定。已定不等于永远不能修改，也不证明已经实现或验收。

改变产品目标、交付口径或尚未授权的重大取舍时请用户决定；已有授权范围内的修复和重构，给出证据、替代方案及验证即可继续。改变已定工程决策时记录替代关系，不把每次改 ADR 自动变成额外审批。

现有 ADR 的阅读边界：

| 文件 | 状态与用途 |
| --- | --- |
| [0001](adr/0001-page-qualification-is-a-mandatory-gate.md) | 旧来源资格设计提案入口，未确认全部路径实现，不作为新审批规则 |
| [0002](adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md) | 已被替代的历史两层采集方案 |
| [0003](adr/0003-cs-principled-deep-module-extraction.md) | 历史抽取方案；任意 CMS 适配、零遗漏等旧表述不是当前保证 |
| [0004](adr/0004-clinical-faculty-evidence-boundary.md) | 旧临床范围说明入口，当前业务规则统一到 CONTEXT |
| [0005](adr/0005-plan-driven-browser-harvest.md) | 当前计划与可复用机制路线的取舍说明，允许按证据改进 |

## 运行时 Markdown 单独对待

[recon_discovery.md](../server/recon_discovery.md)、[recon_provided_links.md](../server/recon_provided_links.md)、[recon_instructions.md](../server/recon_instructions.md) 由程序直接加载，属于运行逻辑。它们必须自包含，不能依赖模型另读根目录规则。修改时验证实际加载、结构 Schema、浏览器工具与证据契约。

DS 使用上述三份运行提示词；旧 `faculty-browser-harvest/SKILL.md` 未被当前运行时加载，已移除以免被开发 agent 自动发现并误用。原文从 Git 历史追溯。[TOOLS.md](../.agents/skills/faculty-browser-harvest/TOOLS.md) 保留执行层接口参考，原目录中的退役脚本保留明确拒绝旧调用的兼容行为。开发维护与全链路审查 agent 将运行指令作为源码资料维护，通用开发技能按当前任务使用。

## 历史记录与证据

带日期的审计、实施、研究、历史及旧计划记录当时的观察。其原始样本、数字和复现方法有追溯价值；不自动成为当前需求、优先级、合并状态或成功率证明。重用前核对代码版本、环境、样本与原件位置。

[CHANGELOG.md](../CHANGELOG.md) 保留历史变更；普通提交经过直接查 Git。旧交接与待办保留在 Git 及 [history](history/)。不要求每轮复制一份旧文件快照，也不批量改写历史实验的原始结论。
