# 采集运行、恢复与证据边界

本文用于定位运行材料、复用与恢复的验证边界。现有采集调用链统一见 [采集流程](college-harvest-workflow.md)，类型复用范围见 [类型库架构](type-library.md)，业务见 [CONTEXT.md](../../CONTEXT.md)，维护清理和任务边界见 [维护计划](../improvement-plan.md)，启动与排障见 [运维手册](../operations.md)。代码、配置和实际运行记录共同说明本次行为；旧实验的参数和结果不是当前配置。

## 从入口追踪

| 环节 | 代码入口 | 需要核对的事实 |
| --- | --- | --- |
| 输入、队列和租约 | `server/intake.py`、`server/agent_queue.py`、`server/agent_worker.py` | 实际入队字段、任务范围、租约和资源限制 |
| DS 发现和结构验证 | `server/agent_recon.py`、`server/recon_browser_mcp.py`、三份 `server/recon_*.md` | 本阶段工具、提交格式、来源及结构证明 |
| 结构计划复用 | `server/recon_plan_cache.py` | 契约摘要、候选版本、验证证据及实际复用范围 |
| 类型选择与阶段证明 | `server/page_types.py`、`server/page_stage_types.py`、`server/page_type_rank_grid.py` | 四角色独立绑定、实际执行／原件证明及实现摘要；合同集中见 [类型库架构](type-library.md) |
| 浏览器执行和覆盖 | `server/browser_batch_tool.py`、`server/harvest_workflow.py`、`server/harvest_coverage.py` | 实际访问页面、原件、分页终止和未解决项 |
| 人工复核与恢复 | `server/review_registry.py`、`server/review_evidence.py`、`server/recovery_policy.py` | 决定对应的原件、失效条件、重试入口及保留材料 |
| 汇总、版本和下载 | `server/university_delivery.py` 及网页调用者 | 本批范围、身份和学院关联、当前状态与下载产物一致性 |

这些是查证入口，不表示每条路径已满足全部业务契约。已复现的差异见 [契约审查记录](../audits/2026-10-11-chain-contract-review.md)；开始修复前仍须核对当前 main 和相关 PR。

## 三类复用分别核验

结构计划、已捕获页面和人工决定具有不同的有效性条件。检查具体调用者如何验证版本、摘要、目标、捕获时间及当前页面证据；不要用“缓存可用”概括三者。

复用旧材料时保留其原捕获时间和来源。保存页重放不能证明本轮实时访问成功；旧计划也不能代替当前契约所需的浏览器证明。动态分页、初始点击、重定向或失败补缺是否允许恢复，以当前实现和对应回归为准。

当前代码候选的注册实现／probe／validator 源码参与契约摘要。新阶段证明还绑定所选角色、ID、版本、有效参数和实际 URL／HTML；源文件漂移或原件变更使旧证明失效。HarvestState 为非 legacy 所选实现隔离恢复 namespace；source_review 的人工来源决定也绑定注册源码，不能借旧确认跳过新材料核验。完整结果和多页原件由工具保存、本地 SHA manifest 读取；缺失或篡改失败关闭。具体合同与兼容范围统一见类型库架构。

## 运行材料的关系

任务审计和日志应能关联输入、实际 task ID、加载版本、DS 阶段、计划、捕获原件、未解决项、复核决定和交付版本。默认目录的定位见运维手册，实际路径可能被运行参数覆盖。

`coverage.complete`、人员对账和双 Sheet 守恒各有自己的范围，均不能单独证明全院入口、教师集合和字段全部正确。证据不足时按 CONTEXT 报告“完整性未验证”，不能将计划复用、断点恢复或零未解决项当作采齐。人工无师资也应与旧产物、当前状态和统计保持一致。下载是否被正确阻断必须用对应版本与任务状态验证，不能从设计描述推断。

## 早期设计与实验

本文早期的具体时限、重试次数、并发描述和逐轮结果保存在 [Git 历史版本](https://github.com/normaltank/MyPlan/blob/e8fb3232a61c2ef971989624aaafcca70b99882d/docs/architecture/verifiable-harvest-runtime.md)。复用实验时核对原版本和材料：

- [2026-09-27 运行与恢复实验](../audits/2026-09-27-verifiable-harvest.md)。
- [2026-09-27 随机学院验证](../audits/2026-09-27-random-college-validation.md)。
- [2026-09-30 运行机制实施记录](../implementation/harvest-runtime-improvements.md)。
