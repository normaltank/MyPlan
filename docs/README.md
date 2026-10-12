# MyPlan 文档导航与维护

## 按问题阅读

| 要回答的问题 | 唯一负责的文件或来源 |
| --- | --- |
| 项目做什么，关键术语是什么意思 | [CONTEXT.md](../CONTEXT.md)：目标、术语和关键契约 |
| Agent 如何开始和结束工作 | [AGENTS.md](../AGENTS.md)：简短入口、执行边界与验证要求；[CLAUDE.md](../CLAUDE.md) 仅转向它 |
| 当前先做什么、改动如何清理、根因修复在哪里 | [维护清理与验证计划](improvement-plan.md)：当前优先级、范围、停止条件与后续验证；由用户实际任务确定执行 |
| 类型库当前是什么、离目标还有什么差距 | [类型库架构与现状](architecture/type-library.md)：代码依据、复用边界与待实施能力；定义引用 CONTEXT |
| 现有采集如何执行、哪些仍是目标 | [学院采集流程](architecture/college-harvest-workflow.md)：当前生产调用链及实现边界 |
| 代码在哪里 | [模块索引](../AGENT_HANDOVER.md)：稳定导航；细节查调用者、测试和 CLI |
| 怎样运行、核验环境或排障 | [运维手册](operations.md)：当前操作入口 |
| 具体提交、开放 PR、CI 到什么状态 | Git 与 GitHub；不在多份 Markdown 里复制动态状态 |
| 为什么作出某项长期取舍 | 对应 ADR；实现说明见 [architecture](architecture/) |
| 某个反例、实验或审查如何复现 | 对应 [audits](audits/)、[implementation](implementation/) 或 [site-types](site-types/) 记录 |

[CONSTITUTION.md](../CONSTITUTION.md) 与 [review-backlog.md](review-backlog.md) 分别保留项目目标和审查待办的兼容入口。[技能工作项路由](agents/issue-tracker.md) 按用户任务定位材料。[本轮文档核对记录](audits/2026-10-12-documentation-alignment.md) 列全部项目相关 Markdown 的修改／保留理由与检查范围，不是维护 PR 清理审计。

## 修改时只更新负责该信息的地方

业务目标或术语改变，更新 CONTEXT；类型库实现事实更新其架构说明，采集编排事实更新采集流程；清理范围、根因分类与后续验证约束更新维护计划；具体进展写入对应工单、PR 或已有证据记录；操作改变更新运维手册。其他入口保持正确引用，不复制另一套规则。

现行说明分别标识“当前已实现”“用户确认的目标”“待验证或待实施”。每项实现描述附代码／配置入口和核对基线；类型案例、旧实验、测试存在或文档变更不等于功能已上线。CONTEXT 中的完整性与字段契约由所有入口引用，不能以旧“专任”或零遗漏表述替代。

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
| [0005](adr/0005-plan-driven-browser-harvest.md) | 保留此前计划与通用机制路线；新增替代关系指向用户确认的类型库目标，不批准新存储或重构 |

## 运行时 Markdown 单独对待

[recon_discovery.md](../server/recon_discovery.md)、[recon_provided_links.md](../server/recon_provided_links.md)、[recon_instructions.md](../server/recon_instructions.md) 由程序直接加载，属于运行逻辑。它们必须自包含，不能依赖模型另读根目录规则。修改时验证实际加载、结构 Schema、浏览器工具与证据契约。

DS 使用上述三份运行提示词；旧 `faculty-browser-harvest/SKILL.md` 未被当前运行时加载，已移除以免被开发 agent 自动发现并误用。原文从 Git 历史追溯。[TOOLS.md](../.agents/skills/faculty-browser-harvest/TOOLS.md) 保留执行层接口参考，原目录中的退役脚本保留明确拒绝旧调用的兼容行为。同目录体系下旧 `.agents/plugins/faculty_harvest_tools/` 的 `plugin.json`、`mcp_config.json` 与 `dsh-agent-runtime.cordis.yml` 是退役 IDE／浏览器实验配置；当前 worker 从 `server/agent_recon.py` 加载 `server/recon_runtime.cordis.yml` 并挂载项目 scout，旧配置不能作为现行安装步骤。开发维护与全链路审查 agent 将运行指令作为源码资料维护，通用开发技能按当前任务使用。

## 历史记录与证据

带日期的审计、实施、研究、历史及旧计划记录当时的观察。其原始样本、数字和复现方法有追溯价值；不自动成为当前需求、优先级、合并状态或成功率证明。重用前核对代码版本、环境、样本与原件位置。`site-types/` 是结构案例与失败边界资料；`observed` 不等于可执行类型批准，也不保证实际已登记数据库，统一边界见类型库架构。

[CHANGELOG.md](../CHANGELOG.md) 保留历史变更；普通提交经过直接查 Git。旧交接与待办保留在 Git 及 [history](history/)。不要求每轮复制一份旧文件快照，也不批量改写历史实验的原始结论。
