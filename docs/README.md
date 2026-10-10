# MyPlan 文档导航

更新：2026-10-10。项目旧 Markdown 未经用户审核；本次按用户重述的目标统一指导。文档中的“目标”“实现”“测试通过”“实网验收”必须分别理解。

## 当前工作入口

| 文件 | 作用 |
| --- | --- |
| [根目录 AGENTS.md](../AGENTS.md) | 所有 agent 的阅读顺序与工作方式 |
| [CONSTITUTION.md](../CONSTITUTION.md) | 用户已确认的目标、当前阶段及可改进的工程路线 |
| [改进计划](improvement-plan.md) | 整体代码审查、历史回归、新学院探索与未见验证 |
| [当前交接](../AGENT_HANDOVER.md) | 已有模块定位、需求差距及证据边界 |
| [当前待办](review-backlog.md) | 可执行事项，完成后附证据 |
| [领域术语](../CONTEXT.md) | 来源、身份、完整性和交付语义 |
| [运维说明](operations.md) | 命令与运行诊断；历史部署记录不证明当前环境状态 |
| [项目访问保护](project-access-protection.md) | 所有者写入权限、分支／标签保护及实际配置边界 |

## 会影响实际运行的 Markdown

- [入口发现](../server/recon_discovery.md)
- [提供师资链接时的补充指令](../server/recon_provided_links.md)
- [分支结构验证](../server/recon_instructions.md)
- [项目采集 skill](../.agents/skills/faculty-browser-harvest/SKILL.md) 及 [工具说明](../.agents/skills/faculty-browser-harvest/TOOLS.md)

服务器侦察会直接加载前三份文件；修改时须验证实际加载、结构输出、证据和工具契约。根目录文档更新不会自动进入未更新或未重启的运行进程。

## 技术设计与历史材料

architecture 与 adr 中的文件分别标记当前实现说明、设计待核或历史决策。所有调用路径是否遵守其中接口，必须通过代码审查证明。

audits、implementation、research、history 及 superpowers 中的带日期文档记录当时观察、计划或实验。保留原始样本与数字用于追溯，不作为当前优先级、用户授权或采集成功率证明；重新使用之前核对代码版本、运行环境、样本与证据。

[旧交接快照](history/2026-10-10-handover-before-guidance-refresh.md) 与 [旧待办快照](history/2026-10-10-backlog-before-guidance-refresh.md) 保存本次更新前的信息。通用工具技能保留原有用途，不另立本项目的业务要求。
