# MyPlan 文档对齐交付记录

> **来源文档历史快照**：下文忠实保留分享作者在 `5275a150c6c172ac7d99554b7119c1592f0f349b` 的文档对齐结果、检查数字与能力判断，不是四阶段候选的当前验收报告。当前实现合同见 [类型库架构](../architecture/type-library.md)，调用链见 [采集流程](../architecture/college-harvest-workflow.md)。整合只添加本阅读边界，没有重写原实验或将旧检查结果推广到新候选；实际模型、昨晚工单与全部学院仍未因此验收。

日期：2026-10-12（Asia/Singapore）。依据：用户完整确认的七部分文档对齐要求。静态代码基线：`5275a150c6c172ac7d99554b7119c1592f0f349b`；云端隔离分支：`docs/align-type-library-20261012`。

本轮只修改文档。开始时云端仓库无未提交改动；没有读取用户电脑文件，也没有将云端清洁状态推定为本地无改动。生产代码、配置、数据库、既有测试和三份运行提示词均未改；未启动采集、服务、回放或模型调用，未推送、创建 PR、合并或部署。没有将静态核对写成业务验收。

## 类型库的真实现状与代码依据

当前不是统一的分环节可组合能力库，而是已有相关资产的组合：

- [site_type_registry.py](../../server/site_type_registry.py) 保存结构观察及失败分类。观察含原件摘要，审计形态导入目前只支持 PDF 职称分组姓名网格；没有查到观察状态驱动处理器分派的生产闭环。
- [recon_plan_cache.py](../../server/recon_plan_cache.py) 保存同大学／学院／入口的结构计划版本，worker 使用前重新验证。已有 SQLite 存储不是用户尚未选定的完整类型数据库方案。
- [recon_templates.py](../../server/recon_templates.py) 与 [agent_recon.py](../../server/agent_recon.py) 实现同次学院侦察的普通名单模板尝试复用；不等于持久化跨学院类型目录。
- [recon_contract.py](../../server/recon_contract.py) 已允许同学院的普通、导航、分段和图片表格分支及分页／正文参数，属于有限组合；没有独立版本化的入口、名单、分页、详情类型编排契约。

详细实现、调用者、限制及目标元数据差距只维护在 [类型库架构](../architecture/type-library.md)；定义在 [CONTEXT](../../CONTEXT.md#类型库用户确认的目标)，本记录不建立另一份规范。

## 当前实现与目标的差距

| 范围 | 现状／证据 | 与用户目标的差距与边界 |
| --- | --- | --- |
| 类型识别、组合和脚本绑定 | 已有分支 Schema、观察库、同学院缓存和同次侦察模板 | 类型适用／识别／输入输出／处理器／失败／样本／版本未统一绑定；完整数据库、接口和迁移方案未确定，不实施重构 |
| 全院完整性 | [harvest_coverage](../../server/harvest_coverage.py) 明示计划内分支范围及需独立导航证据；worker 交叉核对仅附提示 | 没有预先教师总人数；仍须核对官方入口、分类、分页、名单条目及全部异常集合。本轮未采集或核实全院真值，完整性未验证 |
| 字段与简介 | 已有正文范围、媒体本人范围与 [长简介存储](../../server/biography_workbook.py) | 代码存在不证明每页正文都完整或字段归属正确；个人页正文优先、不能用列表摘要代替，缺失不得编造，仍需原件与人员核验 |
| 发布和人工闭环 | [delivery_scope](../../server/university_delivery.py) 按本批状态阻断；大学身份复核现有 verdict 受限；来源复核可请求特定入口重侦察 | 可信部分交付、争议单列、跨学院同人及通用补入口／补人／改字段不应写成全部实现。旧审计每项缺陷未在本轮重跑，不能自动声称当前仍有或已修复 |
| 维护清理 | 仅确定本轮云端文档核对基线 | 尚未确认持续维护起点、全部 PR／未合并分支及用户本地差异；未执行清理、保留／撤回判断或恢复基线验证 |

## 已消除的文档冲突

| 旧表述或误读风险 | 对齐后的权威位置 |
| --- | --- |
| 根目录缺少用户类型库定义；“类型库”与观察登记／计划缓存混称 | CONTEXT 显著定义类型库；类型库架构分别标明现有资产和未实现目标 |
| 继续追失败学院、按旧修复顺序追加代码，与先清理全部维护改动冲突 | 维护计划确立清理范围和停止点；AGENTS、README、工作项入口及 backlog 同步引用 |
| 通用机制／临时站点例外可被读成对所有新结构硬适配 | 采集流程移除自动迁移／例外指令；维护计划按误选、配置、缺类型、内部缺陷、公共缺陷、访问故障定位责任 |
| 计数自洽、工单成功、导出或测试通过可被读成采齐 | CONTEXT 明确无预知总数及“完整性未验证”；流程、运维和 README 引用该证据边界 |
| 完整简介可用名单摘要补位 | CONTEXT 明确个人页完整正文优先；无个人页只保留本人可归属材料和缺口 |
| 旧五项勾选、原审查发现或 ADR 当前措辞可被读成新任务与验收结果 | 旧计划状态、ADR 0005 和首轮审查添加明确日期／替代阅读边界；原事实不改写 |
| 执行层 JSON 示例缺现行来源必填字段 | TOOLS 的四个示例补齐来源字段，仅表示 Schema 结构，不伪造本轮浏览器证明 |

## 仍未确定的事项

持续维护的开始日期／提交、全量远程 PR 与未合并分支、本地未提交材料及完整历史成功样本原件，未在本轮取得或审计。不能由此提出一个已验证可靠的恢复版本。完整类型库的存储方案、类型生命周期、组合接口和版本兼容规则仍待明确任务处理。

历史链接缺失仅说明本工作区没有材料，不证明原件在别处已丢失。外部 GitHub／官网链接未联网检查；用户本机服务版本、实际配置、当前采集失败率及全院真值未知。

## 链接、路径、命令与实现描述核验

- 对全部项目相关 Markdown 扫描本地链接、Markdown 锚点、文档职责、历史边界及类型／维护／完整性关键词；对本次改动逐项核对引用关系。共核对 733 处本地链接引用，新增失效引用为 0；新增与现行入口的相对路径和锚点有效，基线已有的 41 处历史缺失项见下表。91 处外部链接只查引用形式，不声称访问成功。
- 核对核心实现的 20 个类／函数及生产调用者。实现表附代码路径和固定基线，未将 PageQualification 提案、观察状态或测试文件存在当作已上线能力。
- 保留 README 的 Python 3.12、uv、Playwright、pytest、ruff 命令，并静态对照依赖文件、pyproject 与 CI；核对运维／执行层涉及的 18 个 CLI 参数及 Windows 启动脚本、默认路径来源。运行目录、凭据和日志路径是代码约定，不声称当前实例已有这些文件。
- 四个 JSON 示例能解析，字段集合和来源必填项对照现行 Schema 无误；未执行计划、获取浏览器证明或验收示例页面。
- `git diff --check` 通过；最终逐文件摘要比较未发现生产代码、配置、测试或运行提示词变化。未运行生产测试／采集／回放，未产生 CI 结果。

## 全量 Markdown 处置清单

原有项目相关 Markdown 共 126 份（含项目自有执行层 TOOLS）；修改 24 份，保留 102 份，新增类型库架构与本记录 2 份，最终检查 128 份。另有 112 份 `.agents/`／`.claude/` 的通用技能及参考材料，与项目目标对齐无关，未修改；选用的 writing-for-agents 只作为方法参考。无第三方依赖或自动生成文件改动。

每个文件的处置与理由如下。历史文件中的旧命令、部署状态及数字按原基线保留，不作为当前操作指令；CHANGELOG 的旧章节号引用也保留历史语境。

| 文件 | 处置 | 理由 |
| --- | --- | --- |
| [.agents/skills/faculty-browser-harvest/TOOLS.md](../../.agents/skills/faculty-browser-harvest/TOOLS.md) | 修改 | 项目自有执行层参考；补齐四个示例的现行来源必填字段，明确占位示例无浏览器证明。 |
| [AGENTS.md](../../AGENTS.md) | 修改 | 精简为目标、当前清理优先级、任务边界与按需阅读入口；明确运行提示词属于逻辑。 |
| [AGENT_HANDOVER.md](../../AGENT_HANDOVER.md) | 修改 | 补类型相关资产及代码定位，引用维护计划，不复制类型定义与动态状态。 |
| [CHANGELOG.md](../../CHANGELOG.md) | 保留 | 历史变更记录；保留原版本与当时章节引用，不改写为当前能力。 |
| [CLAUDE.md](../../CLAUDE.md) | 保留 | 已有简短兼容入口正确指向 AGENTS／CONTEXT，无第二套规则。 |
| [CONSTITUTION.md](../../CONSTITUTION.md) | 保留 | 已有简短兼容入口正确指向 AGENTS／CONTEXT，无第二套规则。 |
| [CONTEXT.md](../../CONTEXT.md) | 修改 | 显著声明用户类型库定义、环节组合、完整简介优先和完整性未验证；更新当前第一优先。 |
| [README.md](../../README.md) | 修改 | 对齐入口与项目目标，保留安装／测试命令，补实现依据和完整性证据边界。 |
| [docs/README.md](../README.md) | 修改 | 收拢目标、架构、流程与维护各自的权威位置，说明观察、历史及运行时 Markdown 边界。 |
| [docs/adr/0001-page-qualification-is-a-mandatory-gate.md](../adr/0001-page-qualification-is-a-mandatory-gate.md) | 保留 | 已有历史／兼容边界及目标引用，保留决策事实，不需再次改写。 |
| [docs/adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md](../adr/0002-two-tier-hybrid-harvesting-and-reconciliation.md) | 保留 | 已有历史／兼容边界及目标引用，保留决策事实，不需再次改写。 |
| [docs/adr/0003-cs-principled-deep-module-extraction.md](../adr/0003-cs-principled-deep-module-extraction.md) | 保留 | 已有历史／兼容边界及目标引用，保留决策事实，不需再次改写。 |
| [docs/adr/0004-clinical-faculty-evidence-boundary.md](../adr/0004-clinical-faculty-evidence-boundary.md) | 保留 | 已有历史／兼容边界及目标引用，保留决策事实，不需再次改写。 |
| [docs/adr/0005-plan-driven-browser-harvest.md](../adr/0005-plan-driven-browser-harvest.md) | 修改 | 仅添加新要求的替代阅读说明；保留原工程路线及取舍，不批准新重构。 |
| [docs/agents/domain.md](../agents/domain.md) | 修改 | 将领域入口指向现行目标及维护清理路线，避免旧角色路由继续分配任务。 |
| [docs/agents/issue-tracker.md](../agents/issue-tracker.md) | 修改 | 对齐清理工作项、材料取得状态与后续任务边界，移除默认追失败指引。 |
| [docs/agents/triage-labels.md](../agents/triage-labels.md) | 保留 | 讨论状态词，已说明不代表实际标签／配置；与本轮对齐无冲突。 |
| [docs/architecture/college-harvest-workflow.md](../architecture/college-harvest-workflow.md) | 修改 | 以代码调用链重写现行采集流程，区分实现／目标／待验证，撤去自动迁移与站点例外指引。 |
| [docs/architecture/page-qualification.md](../architecture/page-qualification.md) | 保留 | 已明确为未实施的历史接口提案；不把拟议类或迁移写成生产保证。 |
| [docs/architecture/type-library.md](../architecture/type-library.md) | 新增 | 新增架构说明，核对观察／失败登记、计划缓存、模板复用及独立环节类型的差距。 |
| [docs/architecture/verifiable-harvest-runtime.md](../architecture/verifiable-harvest-runtime.md) | 修改 | 引用权威流程和类型库说明，保持证据／恢复边界并明确完整性未验证。 |
| [docs/audits/2026-09-24-five-perspective-review.md](2026-09-24-five-perspective-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-26-harness-context-audit.md](2026-09-26-harness-context-audit.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-26-harvest-acceptance.md](2026-09-26-harvest-acceptance.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-28-authenticated-image-review.md](2026-09-28-authenticated-image-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-28-live-web-launcher.md](2026-09-28-live-web-launcher.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-28-marxism-live-failure.md](2026-09-28-marxism-live-failure.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-28-web-current-request.md](2026-09-28-web-current-request.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-29-nxist-image-roster.md](2026-09-29-nxist-image-roster.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-29-nxist-recon-cost.md](2026-09-29-nxist-recon-cost.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-29-recon-token-experiment.md](2026-09-29-recon-token-experiment.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-30-core-chain-continuation.md](2026-09-30-core-chain-continuation.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-30-core-chain-review.md](2026-09-30-core-chain-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-30-extractor-core-review.md](2026-09-30-extractor-core-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-30-git-sync-review.md](2026-09-30-git-sync-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-09-30-two-live-college-failures.md](2026-09-30-two-live-college-failures.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-01-foundation-hardening.md](2026-10-01-foundation-hardening.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-01-local-deployment.md](2026-10-01-local-deployment.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-01-production-chain-audit.md](2026-10-01-production-chain-audit.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-01-script-inventory.md](2026-10-01-script-inventory.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-09-wxc-law-name-list.md](2026-10-09-wxc-law-name-list.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/audits/2026-10-11-chain-contract-review.md](2026-10-11-chain-contract-review.md) | 修改 | 仅增加原基线／当前清理的阅读边界，不改原发现、测试数或事实。 |
| [docs/audits/2026-10-12-documentation-alignment.md](2026-10-12-documentation-alignment.md) | 新增 | 新增本次交付证据：全量文档处置、冲突、差距、未知及静态检查边界。 |
| [docs/cleanup-2026-09-29.md](../cleanup-2026-09-29.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/history/2026-09-22-data-assets.md](../history/2026-09-22-data-assets.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/history/2026-09-24-walkthrough-round4.md](../history/2026-09-24-walkthrough-round4.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/history/2026-10-10-backlog-before-guidance-refresh.md](../history/2026-10-10-backlog-before-guidance-refresh.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-09-30-core-chain-handover.md](../implementation/2026-09-30-core-chain-handover.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-09-30-email-evidence-merge-report.md](../implementation/2026-09-30-email-evidence-merge-report.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-09-30-extractor-core-handover.md](../implementation/2026-09-30-extractor-core-handover.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-09-30-token-accuracy-intake-report.md](../implementation/2026-09-30-token-accuracy-intake-report.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-01-foundation-hardening.md](../implementation/2026-10-01-foundation-hardening.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-02-claude-assignment.md](../implementation/2026-10-02-claude-assignment.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-08-l1-and-image-table.md](../implementation/2026-10-08-l1-and-image-table.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-08-misc-case-fixes.md](../implementation/2026-10-08-misc-case-fixes.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-declared-title-fields.md](../implementation/2026-10-10-declared-title-fields.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-empty-profile-evidence.md](../implementation/2026-10-10-empty-profile-evidence.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-full-biography-workbook.md](../implementation/2026-10-10-full-biography-workbook.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-identity-title-reparse.md](../implementation/2026-10-10-identity-title-reparse.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-missing-profile-image-review.md](../implementation/2026-10-10-missing-profile-image-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-moving-pagination.md](../implementation/2026-10-10-moving-pagination.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-native-pdf-email-review.md](../implementation/2026-10-10-native-pdf-email-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-pdf-biography-owner-scope.md](../implementation/2026-10-10-pdf-biography-owner-scope.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-pdf-owner-resume-sections.md](../implementation/2026-10-10-pdf-owner-resume-sections.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-pr50-ci-incident.md](../implementation/2026-10-10-pr50-ci-incident.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-profile-repair-counterexamples.md](../implementation/2026-10-10-profile-repair-counterexamples.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-review-evidence-metadata.md](../implementation/2026-10-10-review-evidence-metadata.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-roster-link-boundary.md](../implementation/2026-10-10-roster-link-boundary.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-source-role-qualification.md](../implementation/2026-10-10-source-role-qualification.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-three-college-structure-fixes.md](../implementation/2026-10-10-three-college-structure-fixes.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-word-toc-hidden-decorations.md](../implementation/2026-10-10-word-toc-hidden-decorations.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-10-wxc-browser-pager-repair.md](../implementation/2026-10-10-wxc-browser-pager-repair.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-11-native-pdf-owner-retry.md](../implementation/2026-10-11-native-pdf-owner-retry.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-11-official-site-review.md](../implementation/2026-10-11-official-site-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-11-placeholder-roster-links.md](../implementation/2026-10-11-placeholder-roster-links.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-11-review-selection-controls.md](../implementation/2026-10-11-review-selection-controls.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/2026-10-11-reviewed-nonperson-card.md](../implementation/2026-10-11-reviewed-nonperson-card.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/implementation/harvest-runtime-improvements.md](../implementation/harvest-runtime-improvements.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/improvement-plan.md](../improvement-plan.md) | 修改 | 确立全部维护改动清理优先级、根因分类与停止条件；旧五项状态保留为历史快照。 |
| [docs/operations.md](../operations.md) | 修改 | 保留启动、复核、排障及回放说明，澄清观察导入限制，旧缺陷状态引用原基线。 |
| [docs/research/2026-09-26-faculty-harvest-cases.md](../research/2026-09-26-faculty-harvest-cases.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/2026-09-26-faculty-harvest-proposal.md](../research/2026-09-26-faculty-harvest-proposal.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/2026-09-28-image-only-faculty-examples.md](../research/2026-09-28-image-only-faculty-examples.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/2026-10-01-real-site-case-library-prep.md](../research/2026-10-01-real-site-case-library-prep.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/case-library/2026-10-01-batch-1.md](../research/case-library/2026-10-01-batch-1.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/case-library/2026-10-01-batch-2.md](../research/case-library/2026-10-01-batch-2.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/case-library/2026-10-02-batch-3.md](../research/case-library/2026-10-02-batch-3.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/research/case-library/2026-10-02-batch-4.md](../research/case-library/2026-10-02-batch-4.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/review-backlog.md](../review-backlog.md) | 修改 | 兼容入口改指当前清理计划，避免旧待办成为另一套执行清单。 |
| [docs/site-types/declared-compound-title-field.md](../site-types/declared-compound-title-field.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/governance-office-directory.md](../site-types/governance-office-directory.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/native-cms-visit-counter.md](../site-types/native-cms-visit-counter.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/nonpagination-forward-controls.md](../site-types/nonpagination-forward-controls.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/pagination-control-position-shift.md](../site-types/pagination-control-position-shift.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/pdf-rank-grouped-name-grid.md](../site-types/pdf-rank-grouped-name-grid.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 另纠正“类型库只存计数”的混称，明确形态特征与登记元数据。 |
| [docs/site-types/pdf-single-owner-biography-sections.md](../site-types/pdf-single-owner-biography-sections.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/source-purpose-and-faculty-relationship.md](../site-types/source-purpose-and-faculty-relationship.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/site-types/word-toc-hidden-decorations.md](../site-types/word-toc-hidden-decorations.md) | 修改 | 增加权威类型库及观察／能力边界链接，保留原样本事实和失败条件。 |
| [docs/superpowers/plans/2026-09-24-browser-assisted-harvest.md](../superpowers/plans/2026-09-24-browser-assisted-harvest.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-24-harvest-reliability.md](../superpowers/plans/2026-09-24-harvest-reliability.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-24-server-agent-runtime.md](../superpowers/plans/2026-09-24-server-agent-runtime.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-26-verifiable-harvest.md](../superpowers/plans/2026-09-26-verifiable-harvest.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-27-pagination-resume.md](../superpowers/plans/2026-09-27-pagination-resume.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-27-permanent-plan-library.md](../superpowers/plans/2026-09-27-permanent-plan-library.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-27-random-college-validation.md](../superpowers/plans/2026-09-27-random-college-validation.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-27-web-current-request.md](../superpowers/plans/2026-09-27-web-current-request.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-28-authenticated-image-review.md](../superpowers/plans/2026-09-28-authenticated-image-review.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-28-employee-isolation.md](../superpowers/plans/2026-09-28-employee-isolation.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-28-image-biography.md](../superpowers/plans/2026-09-28-image-biography.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/plans/2026-09-28-image-review-and-acceptance.md](../superpowers/plans/2026-09-28-image-review-and-acceptance.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-24-harvest-reliability-design.md](../superpowers/specs/2026-09-24-harvest-reliability-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-empty-roster-design.md](../superpowers/specs/2026-09-27-empty-roster-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-pagination-resume-design.md](../superpowers/specs/2026-09-27-pagination-resume-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-permanent-plan-library-design.md](../superpowers/specs/2026-09-27-permanent-plan-library-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-random-college-validation-design.md](../superpowers/specs/2026-09-27-random-college-validation-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-verified-profile-containers-design.md](../superpowers/specs/2026-09-27-verified-profile-containers-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-27-web-current-request-design.md](../superpowers/specs/2026-09-27-web-current-request-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-28-authenticated-image-review-design.md](../superpowers/specs/2026-09-28-authenticated-image-review-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-09-28-employee-isolation-design.md](../superpowers/specs/2026-09-28-employee-isolation-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [docs/superpowers/specs/2026-10-08-ocr-table-and-university-identity-review-design.md](../superpowers/specs/2026-10-08-ocr-table-and-university-identity-review-design.md) | 保留 | 历史审计／实施／设计／研究材料；按日期与基线读取，保留事实、原件路径与验证数。 |
| [server/recon_discovery.md](../../server/recon_discovery.md) | 保留 | 程序加载的运行提示词，按源码只读；改动会改变生产逻辑，超出本轮范围。 |
| [server/recon_instructions.md](../../server/recon_instructions.md) | 保留 | 程序加载的运行提示词，按源码只读；改动会改变生产逻辑，超出本轮范围。 |
| [server/recon_provided_links.md](../../server/recon_provided_links.md) | 保留 | 程序加载的运行提示词，按源码只读；改动会改变生产逻辑，超出本轮范围。 |
| [tests/fixtures/source-qualification/README.md](../../tests/fixtures/source-qualification/README.md) | 保留 | 固定原件说明已区分回归与全院真值，保留既有测试资料。 |

## 历史材料缺失链接

基线已有 41 处本地缺失链接，涉及 6 份历史文档、35 个不同目标。以下保留原始目标字串（作为文本列，不新建失效链接），不删历史证据、不改成假定可访问的路径。现行文档未将它们当作已取得原件。

| 历史文档 | 原始目标 | 缺口 |
| --- | --- | --- |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/dispatch-log.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/cuc/final/scope-diagnostic/result.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/stbu/final-v2-failure-summary.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/summary-baseline.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/summary-after.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/summary-final.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/live-queue-validation/final-service-revalidation.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-college-validation.md](2026-09-27-random-college-validation.md) | `../../scratch/random-college-validation/` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | `../../scratch/random-college-validation/sampling/list.xls` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | `../../scratch/random-college-validation/sampling/excluded.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | `../../scratch/random-college-validation/sampling/` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | `../../scratch/random-college-validation/sampling/reproduce_sampling.py` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-random-sampling-method.md](2026-09-27-random-sampling-method.md) | `../../scratch/random-college-validation/sampling/frozen-schools.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-cache-identity-reviewed/result.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/download-revalidation.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/identity-review.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/icclc-cold.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/icclc-warm.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/final-service-revalidation.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/permanent-plan-library/result.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/final-service-revalidation.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/verified-saved-browser.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/verify_saved_browser_workflow.py` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/verified-workflow-live/20260927-020326/seed/attempt-1/湖南中医药大学_信息科学与工程学院_audit.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/worker-stable-status.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/pagination-and-empty-replay/result.json` | 本工作区不存在；外部保存位置未知 |
| [docs/audits/2026-09-27-verifiable-harvest.md](2026-09-27-verifiable-harvest.md) | `../../scratch/live-queue-validation/final-service-revalidation.json` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-09-22-data-assets.md](../history/2026-09-22-data-assets.md) | `../教师列表.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-backlog-before-guidance-refresh.md](../history/2026-10-10-backlog-before-guidance-refresh.md) | `/mnt/project-files/reviews/code-alignment/2026-10-08-对齐执行方案.md` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `/mnt/project-files/reviews/code-alignment/2026-10-08-代码对齐检查.md` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../%E6%95%99%E5%B8%88%E5%88%97%E8%A1%A8.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/湖南师范大学.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/甘肃中医药大学.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/西安音乐学院.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/绍兴文理学院.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/批量采集教师列表_合集_南京林业大学_江苏大学_18学院_20260922.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/批量采集教师列表_合集_河海大学_江南大学_14学院_20260922.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/批量采集教师列表_合集_20260921_101228.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/西安音乐学院.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/甘肃中医药大学.xlsx` | 本工作区不存在；外部保存位置未知 |
| [docs/history/2026-10-10-handover-before-guidance-refresh.md](../history/2026-10-10-handover-before-guidance-refresh.md) | `../../server/outputs/湖南师范大学.xlsx` | 本工作区不存在；外部保存位置未知 |

## 停止点

本轮文档对齐及静态一致性检查完成即停止。上述能力缺口、材料缺口和历史访问问题均为交付结论，不触发代码修复、类型库重构、数据库改动、追加采集或 PR。
