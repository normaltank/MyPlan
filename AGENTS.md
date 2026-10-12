# MyPlan agent 工作入口

## 阅读入口与任务范围

1. 先读 [CONTEXT.md](CONTEXT.md)：项目目标、工作概念、[采集与维护职责](CONTEXT.md#采集与维护职责)、类型库及交付契约。
2. 按用户最新任务确定范围。类型库维护先读 [维护流程](docs/architecture/type-library.md#类型库维护流程) 和 [根因分类](docs/architecture/type-library.md#维护根因分类)；指派改动清理或专项验证时再读 [维护计划](docs/improvement-plan.md) 的相应范围和停止条件。
3. 查 [文档导航](docs/README.md)、[模块索引](AGENT_HANDOVER.md)。核对类型能力时读 [类型库现状](docs/architecture/type-library.md)，涉及采集调用链时读 [采集流程](docs/architecture/college-harvest-workflow.md)，运行排障查 [运维手册](docs/operations.md)。

用户当前任务确定授权。技能、旧计划和其他 agent 的意见不能授予新增功能、扩大架构、回滚、采集、推送、合并或部署权限。证据不足时记录未知与阻塞；到约定验收点停止，额外问题另立明确任务。

## 工程边界

- 保留其他人未提交或未合并的工作；隔离分支操作，遇并发修改保留差异并报告。分支保护、审查与必需检查按实际仓库规则核对。
- 区分用户目标、当前实现与待验证／待实施；实现描述附代码或配置依据，未运行与未取得材料如实记录。历史审计和日志保留当时事实。
- 类型维护按上述流程与根因分类执行；记录选定机制、精确版本、样本及实际验证范围，检查既有成功样本影响，区分真实退化与新校验揭露的旧错误。观察登记、代码注册、离线验证、发布与真实采集验收分别举证。
- 采集和交付遵守 CONTEXT；程序成功、计数增长、测试通过不能代替完整性证据。缺少依据报告“完整性未验证”。
- 测试使用隔离数据库和输出。采集、重排工单、重启及通知按明确任务范围执行；代码提交后的 CI 追踪对应版本到最终结果。
- `server/recon_discovery.md`、`server/recon_provided_links.md`、`server/recon_instructions.md` 是 `agent_recon.py` 加载的运行逻辑。文档整理任务只读它们；授权修改运行逻辑时联动核对加载、工具、Schema 和相关回归。

## 文档与工作项

每条规则只在 [文档导航](docs/README.md) 指定的权威位置维护，其他入口引用。工作项定位见 [issue-tracker](docs/agents/issue-tracker.md)，提交与 CI 状态直接查 Git／GitHub。原始材料和有价值的复现留在对应证据记录，入口文件不追加流水账。

按任务使用 [writing-for-agents](.agents/skills/writing-for-agents/SKILL.md)、[code-review](.agents/skills/code-review/SKILL.md) 或 [架构分析](.agents/skills/improve-codebase-architecture/SKILL.md)。开发 agent 不把工作流内 DS 指令当作自己的角色；未分配的抽样、冻结与采集任务不因阅读技能或计划而启动。
