# 学院采集流程：当前调用链与目标边界

本文件是现有采集编排的权威说明；目标、字段和完整性契约见 [CONTEXT](../../CONTEXT.md)，类型能力现状见 [类型库架构](type-library.md)，维护优先级与根因分类见 [维护计划](../improvement-plan.md)，运行命令见 [运维手册](../operations.md)。

核对代码候选：`a81231e8446bd589d5ba5a3042dc151ce3596f49`，2026-10-12。当前已实现表示有代码依据，不证明服务已加载、实际模型侦察或真实学院完成验收。来源文档的 `5275a15` 现状与此前实验作为历史记录保留，不分配本轮任务。

## 当前已实现：执行顺序

```text
网页／批量输入 → 学院队列 → worker
                            ↓
               入口发现与来源证明 → 分支结构计划
                            ↓
                 浏览器捕获 → 人员／字段读取
                            ↓
               来源、身份、媒体及计划内覆盖审计
                  ↓ 未解决项          ↓ 现有门禁通过
             有界重试／补缺／复核     大学汇总与版本
```

| 环节 | 代码依据 | 当前行为与限制 |
| --- | --- | --- |
| 输入与任务 | [intake](../../server/intake.py)、[app](../../server/app.py)、[agent_queue](../../server/agent_queue.py) | 批量入口保存大学、学院、入口及可选师资链接，合并同学院多行；各 API 的字段传递仍须分别核对 |
| 侦察与结构计划 | [agent_recon](../../server/agent_recon.py)、[recon_contract](../../server/recon_contract.py)、[scout](../../server/recon_browser_mcp.py) | 发现相关来源；分支阶段查类型目录、独立选四角色，验证来源及实际名单／阶段证明。Schema 只传目标与结构，教师字段从原件读取 |
| 计划与模板复用 | [recon_plan_cache](../../server/recon_plan_cache.py)、[recon_templates](../../server/recon_templates.py) 与其 worker／侦察调用者 | 候选计划或同次侦察代表分支仍须浏览器重验；复用范围与缺口统一见类型库架构，不等于所有页面共享一个类型 |
| 来源与分页捕获 | [harvest_workflow.capture_sources](../../server/harvest_workflow.py)、[browser_batch_tool](../../server/browser_batch_tool.py) | entrance 分派返回 EntranceCapture，公共层核对显式子集合；pagination 分派采名单页、保存原件与终止证据。入口不采分页，导航不直接输出教师 |
| 名单与详情 | `harvest_workflow.reconcile_rosters/capture_profiles`、[faculty_extractor](../../server/faculty_extractor.py)、[faculty_identity](../../server/faculty_identity.py) | 每页 roster 分派解析观察，逐 URL detail 分派返回 ProfileCapture，公共层保留来源与正文证明；确定性解析不能自动证明字段归属或身份合并正确 |
| 图片与 PDF | [image_biography](../../server/image_biography.py)、[pdf_roster](../../server/pdf_roster.py)、[pdf_biography_owner](../../server/pdf_biography_owner.py) | 读取和保存原件、位置与本人范围；不支持或不确定材料保留未解决项，不能用列表摘要填补完整简介 |
| 覆盖与门禁 | [harvest_coverage.build_coverage_ledger](../../server/harvest_coverage.py)、`browser_batch_tool.run_plan` | 覆盖明确限于计划内已核实分支；`run_plan` 将提取就绪、未解决项及覆盖联合决定 `delivery_ready`，没有独立证明全院没有未知入口 |
| 恢复与复核 | [agent_worker](../../server/agent_worker.py)、[recovery_policy](../../server/recovery_policy.py)、[review_registry](../../server/review_registry.py)、[review_evidence](../../server/review_evidence.py) 及各复核模块 | 访问、结构、人工复核和终止分别路由，既有决定绑定来源证据；具体恢复范围须查实际消费者，不是无限补缺 |
| 汇总与下载 | [university_delivery](../../server/university_delivery.py)、[excel_export](../../server/excel_export.py)、[biography_workbook](../../server/biography_workbook.py)、网页下载路由 | 按大学汇总、身份冲突和本批学院状态控制发布，双表导出和长简介支持表已有代码；旧版本有效性及部分交付仍有契约差距 |

实际函数参数、DTO、状态码和重试上限以代码为准。`capture_sources`、`reconcile_rosters`、`commit_records` 是现有函数，不应把旧文档的概念签名当成完整公开 API，也不能据文件名推断所有调用路径已统一。

## 用户确认的目标如何约束流程

采集方案应按每个页面／环节选择和组合能力，优先复用已有类型；真正缺少结构能力时扩展相应类型及脚本。方案中的大学／学院、URL、CSS 和本次观察属于运行材料，不能自动提升为跨站规则。类型误选、配置差异、内部缺陷与公共流程缺陷须先诊断，不能以“根因修复”为由把所有新结构硬塞进公共流程。

范围和字段以 CONTEXT 为准：给定师资链接不限制全院范围；官方正式列明的兼职、外聘仍属于目标；名单出现、医生头衔、组织成员或同名不能单独证明目标师资关系。个人页完整正文优先，缺失字段及读取失败分别报告。

## 待验证或待实施

- **全院完整性**：`coverage.complete` 只检查当前计划范围。账本明确记录 `discovery_completeness=requires_independent_navigation_evidence`；worker 的人数／字段交叉核对是提示，不改变交付状态。官方入口、分类、分页、全部名单条目及异常集合缺少独立核对时报告“完整性未验证”。
- **类型泛化与完整性**：四角色合同、可信处理器绑定、版本与实际分派已实现，具体支持边界见类型库架构；父导航分页、更多动态机制及媒体角色化没有因此完成。数据库类型管理未确定，真实新学院泛化与旧成功案例兼容仍需相应证据。
- **交付与复核闭环**：当前 `delivery_scope` 对本批未完成、失败或未解决任务保持 collecting／blocked；可信部分交付、争议记录单列、通用补入口／补人／改字段的目标不能视为全部完成。已有来源复核的重新侦察入口只覆盖特定问题。
- **历史资料的有效性**：[2026-10-11 契约审查](../audits/2026-10-11-chain-contract-review.md) 是原基线发现，不能把每项缺陷自动沿用到当前版本；本轮未重跑其反例，未执行全部维护变更清理。

本文件不安排接口迁移、重构、采集或部署。后续改动先满足维护计划的范围与证据要求，再在明确任务内实施并核对已有成功样本。
