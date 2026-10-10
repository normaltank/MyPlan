# 采集运行、恢复与证据边界

本文用于定位当前调用链和验证边界。业务含义见 [CONTEXT.md](../../CONTEXT.md)，开发职责按 [AGENTS.md](../../AGENTS.md)，启动与排障见 [运维手册](../operations.md)。代码、配置和实际运行记录共同说明本次行为；旧实验的参数和结果不是当前配置。

## 从入口追踪

| 环节 | 代码入口 | 需要核对的事实 |
| --- | --- | --- |
| 输入、队列和租约 | `server/intake.py`、`server/agent_queue.py`、`server/agent_worker.py` | 实际入队字段、任务范围、租约和资源限制 |
| DS 发现和结构验证 | `server/agent_recon.py`、`server/recon_browser_mcp.py`、三份 `server/recon_*.md` | 本阶段工具、提交格式、来源及结构证明 |
| 结构计划复用 | `server/recon_plan_cache.py` | 契约摘要、候选版本、验证证据及实际复用范围 |
| 浏览器执行和覆盖 | `server/browser_batch_tool.py`、`server/harvest_workflow.py`、`server/harvest_coverage.py` | 实际访问页面、原件、分页终止和未解决项 |
| 人工复核与恢复 | `server/review_registry.py`、`server/review_evidence.py`、`server/recovery_policy.py` | 决定对应的原件、失效条件、重试入口及保留材料 |
| 汇总、版本和下载 | `server/university_delivery.py` 及网页调用者 | 本批范围、身份和学院关联、当前状态与下载产物一致性 |

这些是查证入口，不表示每条路径已满足全部业务契约。已复现的差异见 [契约审查记录](../audits/2026-10-11-chain-contract-review.md)；开始修复前仍须核对当前 main 和相关 PR。

## 三类复用分别核验

结构计划、已捕获页面和人工决定具有不同的有效性条件。检查具体调用者如何验证版本、摘要、目标、捕获时间及当前页面证据；不要用“缓存可用”概括三者。

复用旧材料时保留其原捕获时间和来源。保存页重放不能证明本轮实时访问成功；旧计划也不能代替当前契约所需的浏览器证明。动态分页、初始点击、重定向或失败补缺是否允许恢复，以当前实现和对应回归为准。

## 运行材料的关系

任务审计和日志应能关联输入、实际 task ID、加载版本、DS 阶段、计划、捕获原件、未解决项、复核决定和交付版本。默认目录的定位见运维手册，实际路径可能被运行参数覆盖。

`coverage.complete`、人员对账和双 Sheet 守恒各有自己的范围，均不能单独证明全院入口、教师集合和字段全部正确。人工无师资也应与旧产物、当前状态和统计保持一致。下载是否被正确阻断必须用对应版本与任务状态验证，不能从设计描述推断。

## 早期设计与实验

本文早期的具体时限、重试次数、并发描述和逐轮结果保存在 [Git 历史版本](https://github.com/normaltank/MyPlan/blob/e8fb3232a61c2ef971989624aaafcca70b99882d/docs/architecture/verifiable-harvest-runtime.md)。复用实验时核对原版本和材料：

- [2026-09-27 运行与恢复实验](../audits/2026-09-27-verifiable-harvest.md)。
- [2026-09-27 随机学院验证](../audits/2026-09-27-random-college-validation.md)。
- [2026-09-30 运行机制实施记录](../implementation/harvest-runtime-improvements.md)。
