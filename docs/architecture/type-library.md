# 类型库：当前合同、执行机制与能力边界

用户确认的目标见 [CONTEXT](../../CONTEXT.md#类型库用户确认的目标)；本文集中维护实现合同与差距。公共调用链见 [采集流程](college-harvest-workflow.md)，根因分类见 [维护计划](../improvement-plan.md)。

核对代码候选：`a81231e8446bd589d5ba5a3042dc151ce3596f49`；2026-10-12 文档整合另有分支运行提示词更新。“已实现”指代码与调用关系，不证明服务已加载、实际模型侦察可用、昨晚工单已重跑或全部学院兼容。来源文档在 `5275a15` 的旧现状结论作为 [历史快照](../audits/2026-10-12-documentation-alignment.md) 保留。

## 执行类型与观察记录

| 资产与代码依据 | 当前行为 | 边界 |
| --- | --- | --- |
| [page_types](../../server/page_types.py)：TypeDefinition、register_type、catalog、normalize_branch、invoke | 可信 Python 注册角色、type_id、implementation_version、严格结构参数模型、适用条件、实现与输入／输出／失败合同；计划选择定义，实际通过分派执行 | 同 ID／版本不可覆盖；未知 ID、版本、角色、不适用结构与非法参数拒绝。计划不带代码／导入路径，不从数据库加载脚本 |
| [SiteTypeRegistry](../../server/site_type_registry.py)：record、record_audit_shapes | SQLite 保存目标、URL、shape_code、结构特征、原件路径／SHA 与 observed 状态；审计导入器目前读取 PDF 职称分组姓名网格 | accepted/rejected 字段没有批准执行类型的生产闭环；观察登记不触发处理器注册／分派，也不证明采齐 |
| site_type_registry.classify_failure／reconcile_failures；调用者 [队列](../../server/agent_queue.py) | 保存失败分类与事件历史 | 诊断类别不是可执行结构类型 |
| [ReconPlanCache](../../server/recon_plan_cache.py)：put/get/record_validation、validate_cached_plan；调用者 [worker](../../server/agent_worker.py) | 同大学／学院／入口保存结构计划版本；使用前核验契约、目标、摘要及当前浏览器来源／结构／所选阶段证明 | 支持无 initial_click_selector 的普通 roster；source_dispositions 须重新发现，navigation 等形态不进入该缓存复用路径。缓存不是跨学院处理器目录 |
| [recon_templates](../../server/recon_templates.py)：structural_fingerprint、verify_template；调用者 [侦察](../../server/agent_recon.py) | 同次侦察尝试按 DOM 指纹复用代表分支，逐跟随分支重验来源与结构；失败回独立侦察 | 保守复用符合条件的普通名单，仍受实际证明约束，不是任意网站识别或持久化跨学院方案 |

SQLite 观察库、计划版本库与可信执行注册层是不同资产。用户曾提出数据库管理类型，但未选定技术方案；当前没有脚本数据库、动态插件装载或任意组合数据库，本次文档整合不实施这些能力。

## 四个角色独立选择与执行

分支可带 `page_types`，四个角色分别填 `{type_id, implementation_version, params}`。绑定可省略，旧 kind／CSS 字段自动解析为精确兼容类型 @1。规范化不改原计划，参数投影到对应结构字段；与已有结构冲突时拒绝。params 只允许该角色结构字段，不接受教师 JSON、来源资格、证明、任意嵌套对象或中文姓名选择器；严格模型和有效参数以 page_types 为准。

| 角色 | 输入／输出 | 实际分派 |
| --- | --- | --- |
| entrance：入口／栏目 | 浏览器上下文与分支 → EntranceCapture(html, source_urls, errors, evidence)，仅准备／发现来源，不采分页 | [harvest_workflow.capture_sources](../../server/harvest_workflow.py) 调用 entrance；公共层对照导航子集合与绑定 |
| pagination：名单分页 | 分支、浏览器与 checkpoint 参数 → (pages, errors, evidence)，pages 保留 URL／HTML | [browser_batch_tool._capture_sources_with_state](../../server/browser_batch_tool.py) 调用 pagination 后保存原件、核验覆盖 |
| roster：名单读取 | CapturedRosterPage、计划／人工姓名决定 → 同步 RosterReconciliation：候选、来源、观察、未解决项 | harvest_workflow.reconcile_rosters 对每页分派所选解析器；不绕过来源与身份门禁 |
| detail：个人详情 | 实际个人 URL、浏览器与正文范围 → ProfileCapture(html, proof, errors, meta) | harvest_workflow.capture_profiles 逐 URL 分派；公共层保留正文证据与来源关联，冲突绑定阻断 |

入口与详情的生产 fallback 已使用显式 DTO。低层接口对旧八类型 @1 保留旧组件形状兼容；新定义不能用 None 加闭包写入代替 ProfileCapture。字段解析、来源审计、身份合并、复核与导出继续由公共链组织。

`kind=roster/navigation/segmented/image_table` 是兼容形态，不等于四角色目录。导航可用旧同构 child_branch（无 URL），或互斥的完整 child_branches；后者每个孩子有自己的 URL、来源、结构与绑定，可递归表达有界异构分支。实际发现 URL 集须与显式集合一致；重复、多配、漏配、跨站与未证明入口保持未解决。当前父导航不支持分页，不凭两个样例证明显式全集。

## 当前可信机制与适用边界

| 类型（均为 @1） | 机制／限制 |
| --- | --- |
| entrance.direct、entrance.navigation | 旧直接来源准备、旧导航链接发现，沿用对应浏览器证明 |
| roster.cards、roster.segmented、roster.image_table | 旧卡片／媒体读取、分段平铺、图片表格；segmented、image_table 与严格空态须各自精确 legacy roster@1 |
| pagination.single_page、pagination.next_control | 旧单页或 next 控件；single_page 仍需真实终止证明 |
| detail.profile | 旧个人页读取与正文范围证明 |
| [roster.rank_grid](../../server/page_type_rank_grid.py) | 按明确职称组、唯一标题起点和名单单元独立解析，单元可多人；每人链接按自身 anchor 归属。未知职称／token／结构及同页同名同主页多位置保留未解决，不调用旧 cards parser 代替 |
| [entrance.scoped_links](../../server/page_stage_types.py) | 当前已加载 navigation DOM 中选定容器的可见同站 descendant anchors；保留完整来源集合，需无未证明父分页，不是任意动态栏目遍历 |
| pagination.verified_next | 普通 roster 的唯一 next 点击链、每页来源与真实变化／末页终止；不支持 initial_click_selector 或同时 single_page，达到上限不能算完成 |
| detail.article_region | 唯一语义 article 正文区域及其可见、稳定、完整范围证明；不覆盖任意布局或 PDF 本人解析 |

三项新 DOM 机制的实现、probe、validator 在 page_stage_types；rank-grid 是独立名单解析器。它们通过注册分派接公共链，不在核心 invoke 按新 ID 添分支。其他 PDF／图片能力继续由现有媒体模块处理，不能写成已全部转换为独立角色类型。

## 选择、实际证明与版本失效

分支侦察先用只读 `page_type_catalog` 查角色／ID／版本与适用、参数、输入／输出／失败合同，再选择实际结构；目录不提供代码／import path。来源资格由 browser_verify_source 独立核验。名单 proof 使用同 ID／版本／有效参数的实际解析器，rank_grid 不能借用旧 cards 证明。

新入口／分页／详情须注册完整 stage_probe、同步 evidence_validator、evidence_contract，并用 `browser_verify_stage` 实际执行所选 handler。工具保存原件与结果 metadata；run_stage_probe、stage_evidence_for_result 校验真实结果，不给旧证据贴标签。精确旧八类型 @1 保留原协议，复制旧定义注册 @2 不继承豁免；目录出现或完整注册仍不免除适用、来源和提交门禁。

阶段证明绑定 role、type_id、implementation_version、effective params、requested_url、final_url、实际 source_html_sha256、implementation_digest 与机制 payload。详情用自身原件，分页保留全部页；不能借名录哈希证明个人正文。超 inline 大小的完整结果以本地文件 SHA manifest 读取校验，缺原件／metadata／完整包或篡改失败关闭。模型只提交结构计划，不手写 confirmed、SHA、stage_evidence 或教师字段。

实现摘要由实际注册 implementation／probe／validator／适用条件／参数模型源码 SHA 与确定性代码字段指纹组成，不用随执行 intern 状态变化的 marshal 编码。registered_implementation_manifest 支持契约缓存与 [source_review](../../server/source_review.py) 决定绑定；selected_implementation_digest 递归选择计划定义，[HarvestState](../../server/harvest_state.py) 为非 legacy 选择隔离恢复 namespace。源码漂移使旧证明／相关复用失效，不是手填相同版本号即可继续用。

## 用户目标与仍待验证的差距

| 用户目标 | 当前已实现 | 待验证或另立任务 |
| --- | --- | --- |
| 适用、输入输出、脚本、失败与版本绑定 | TypeDefinition、四角色合同、目录、结构参数、新机制证明 | 新页面识别准确性、跨站适用、全面样本／生命周期；目录本身不证明会选对 |
| 不同页面／环节可组合替换 | 独立四角色、异构子分支与真实替代处理器 | 当前有界 DOM／兼容结构闭环；更多交互、父导航分页、媒体角色化尚未实现 |
| 回归保护与来源复核 | 注册／执行／侦察／摘要回归文件及来源、人工决定门禁存在 | [历史案例](../site-types/) 原件可达性、人员字段真值与旧成功兼容不能由文件存在证明 |
| 全部官方师资与正确归属 | 公共覆盖、来源、身份、交付门禁继续运行 | 实际模型、新学院、昨晚工单未在本次文档整合验收；未知范围须按 CONTEXT 报告“完整性未验证” |

观察库 accepted/rejected 仍不批准执行能力。没有实现数据库类型管理、任意 CMS 适配或零遗漏保证，也未改变业务交付口径。安装／运维保持原入口，部署和采集按用户明确任务执行。
