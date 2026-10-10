# MyPlan agent 工作入口

## 开始工作

1. 读 [CONTEXT.md](CONTEXT.md)，对齐项目目标、术语和交付契约。
2. 读 [五项改进任务](docs/improvement-plan.md)，定位本次任务及证据缺口。
3. 按任务查 [模块索引](AGENT_HANDOVER.md)、[文档导航](docs/README.md) 和实际代码；提交、开放 PR、检查状态直接查 GitHub。

用户最新明确要求优先。用户已授权审查和修改未经其审核的旧 Markdown；技能、历史记录和其他 agent 的意见提供方法或线索，不增加授权，也不使旧架构不可修改。已获授权的维护继续执行，真正缺失的信息才向用户提出具体问题。

## 工作边界与完成条件

- 保留其他人的未合并改动；按问题组织可审阅提交，遵守实际分支保护、审查和必需检查，不降低保护来完成合并。
- 用代码、原件或可复现情形说明问题。区分需求、当前实现、静态判断、自动测试、保存页回放和真实学院验收；未运行、失败及未知项如实保留。
- 采集范围、字段归属、身份合并、部分交付和完整性遵守 CONTEXT 的契约。修复同时保护相关历史成功案例；不得靠删失败样本或弱化核验提高表面成功率。
- 测试使用隔离数据库和输出目录。实际采集按已指定批次及预算执行，保留整批证据；部署、重启、旧工单重排和通知按用户任务范围执行。
- 运行提示词属于程序逻辑。修改时联动核对加载路径、工具、Schema 和相关测试；根目录文档更新不证明运行进程已经加载。
- 提交后跟踪对应版本的 CI 到最终结果。变更 head 或解决合并冲突后重新验证；合并后核对主线 CI。运行中、取消或失败均不算通过。

## 按需使用技能

真正执行学院采集、认领或交付时读 [faculty-browser-harvest](.agents/skills/faculty-browser-harvest/SKILL.md) 及其工具说明。文档和代码审查不自动变成采集任务。

整理指导可用 [writing-for-agents](.agents/skills/writing-for-agents/SKILL.md)；代码审查可用 [code-review](.agents/skills/code-review/SKILL.md)；架构分析可用 [improve-codebase-architecture](.agents/skills/improve-codebase-architecture/SKILL.md)。按任务选用，不加载全部技能；工作项的持久化入口见 [issue-tracker](docs/agents/issue-tracker.md)。

## 维护文档

同一规则只在其负责文件修改，其他入口保留链接。职责及 ADR 维护方法见 [文档导航](docs/README.md)。本轮状态更新到五项任务的对应项；提交经过、逐次测试数量和临时日志留在 PR、CI 或相关证据记录，不追加到本文件或模块索引。
