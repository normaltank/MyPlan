# MyPlan 运行与排障

本文件保存现有操作入口，不维护逐次开发日志或某台机器的“最新部署状态”。目标与术语见 [CONTEXT.md](../CONTEXT.md)，按 [AGENTS.md](../AGENTS.md) 的任务分工选择操作。操作前核对实际任务范围、运行版本和现有工单，仓库更新不代表进程已加载。

## 1. 核对运行环境

记录代码提交、依赖锁文件、配置、模型及提示词版本、任务数据库、输出位置、网页地址和 worker 心跳。代码版本可用 `git rev-parse HEAD` 核对；进程启动时间、启动日志和实际配置用于确认 worker 是否加载了该版本，不能只看工作区文件。

当前运行时由 `server.agent_worker` 为学院组织侦察、浏览器采集、审计、复核与大学汇总。三份 `server/recon_*.md` 在模块导入时读取；变更代码或运行提示词后，实际试跑要使用加载对应版本的进程。已有租约、旧任务重排及服务更新按本次任务范围处理。

环境信息与缺口记录在本次任务的工单、PR 或已有问题记录；专项审查的抽样材料由审查 agent 更新其计划。历史部署记录只能提供查证线索，当前服务和母表须重新定位。

## 2. Windows 启动与配置

常规入口是根目录 `双击启动局域网服务.bat`；已运行的网页或消费者不会重复启动。实际更新运行环境时使用 `重启本地服务.bat`，先检查现有工单和租约。相关实现见 [start_server.py](../server/start_server.py)、[本机配置](../server/local_service_config.py) 和 `scripts/`。

首次启动要求输入 DeepSeek API Key，输入不回显，按当前 Windows 账号加密保存在 `.runtime/deepseek-api-key.dpapi`。不能把该文件复制到另一账号或服务器当作通用凭据。

| 信息 | 本机位置或入口 |
| --- | --- |
| 启动过程 | `.runtime/launcher.log` |
| worker 输出及错误 | `.runtime/local-worker.out.log`、`.runtime/local-worker.err.log` |
| 固定运行环境 | `.runtime/worker-venv`，依赖 `requirements.lock` |
| 端口及显式浏览器代理 | `.runtime/local-services.json`，不提交到 Git |
| 默认服务配置 | [server/config.json](../server/config.json) |

网页地址以启动窗口打印值为准。端口覆盖示例：

```json
{"port":18000}
```

浏览器与 Harness API 是不同网络客户端，`HTTP_PROXY`／`HTTPS_PROXY` 不自动配置 Chromium。需要官网浏览器代理时，按真实网络配置填写：

```json
{"port":18000,"browser_proxy":{"server":"http://127.0.0.1:7897","bypass":"localhost,127.0.0.1"}}
```

也可显式设置 `HARVEST_BROWSER_PROXY` 与 `HARVEST_BROWSER_PROXY_BYPASS`，环境配置优先。代理凭据通过独立字段提供，不嵌入 URL 或日志。端口、代理配置由启动器校验；离线回放不继承生产代理。详细参数与传递路径查 `local_service_config.py`、`browser_context.py` 及其测试。

## 3. 访问与提交

网页使用启动窗口打印的本机／局域网地址。现有访问方式是填写姓名，没有密码；同名共用身份，所有使用者能查看、复核和下载工单，因此当前实现是内网使用方式，不能直接当成已经具备公网部署条件的认证方案。

提交、复核等写操作需要页面 CSRF 令牌及同源请求；worker 认领、续租和同步仅接受本机直连，反向代理转发头也影响该判断。具体边界见 [access_control.py](../server/access_control.py)。服务器部署和通知不属于本阶段的前置工作。

批量输入每行 `大学 学院 学院链接 [师资链接]`，可从表格粘贴；制表符保留名称中的空格。同学院多个师资链接分行提交，合并成一项学院任务。批次上限从配置读取，不等于教师人数或全局并发上限。

批量解析与校验见 [intake.py](../server/intake.py)，单学院和批量 API 见 [app.py](../server/app.py)。当前存在单入口给定链接传递、空单元格定位等审查缺口，见改进计划引用的审查记录；不要把输入被接口接受当作字段一定正确。抽样批次同时保存原始行和实际入队字段。

网页右侧显示本浏览器当前请求；旧批次从“历史工单”打开。后台心跳离线时，新提交会等待消费者，不代表已开始采集。

## 4. 人工复核与恢复

从具体工单进入复核页，对照来源及证据处理已提供的问题，保存后使用页面的重采入口。决定绑定具体材料；来源、原图、OCR 或记录改变时，旧决定可能失效，不能手工改状态绕过。

| 问题 | 现有入口与证据边界 |
| --- | --- |
| 图片文字、姓名或邮箱 | 保留原图、OCR、本人范围和候选值；处理该问题不代表其他来源或分页已核验 |
| 正文图片失效 | 对照完整正文、来源 URL 和失败状态判断用途；仅在明确是无正文信息的照片时使用对应决定，未知用途继续待核 |
| 学院内身份歧义 | 可选择不同教师、同人或分组，绑定主来源并保留原观察；大学跨学院复核的能力另行核对 |
| 原件封存和旧证据携带 | 使用 [review_evidence.py](../server/review_evidence.py) 及现有复核／恢复模块，不手工伪造摘要或读取成功标记 |
| 人工无师资 | 人工针对官网全师资范围作结论，自动零采集不能代替；会改变任务记录与汇总，旧版本一致性仍有待修复问题 |

失效图片的具体条件见 [对应证据记录](implementation/2026-10-10-missing-profile-image-review.md)，原件封存和携带见 [review-evidence-metadata](implementation/2026-10-10-review-evidence-metadata.md)。记录中的历史通过范围不自动证明当前完整闭环。

现有无师资界面仍有“专任教师”的旧文案，与 CONTEXT 不一致；跨学院同人、多学院导出、旧版本有效性和可信部分交付也在当前修复清单中。已存在的“处理审计项后重跑”并不覆盖通用补入口、补遗漏人员的目标流程。

## 5. Worker、资源与运行参数

固定运行环境示例：

```powershell
uv venv .runtime/worker-venv --python 3.12
uv pip sync --python .runtime/worker-venv/Scripts/python.exe requirements.lock
.runtime/worker-venv/Scripts/python.exe -m playwright install chromium
.runtime/worker-venv/Scripts/python.exe -m server.agent_worker --help
```

启动常驻消费者使用 `.runtime/worker-venv/Scripts/python.exe -m server.agent_worker`。`--once` 只处理一个工单后退出，适合明确限定的一次排障，不能代替常驻服务。运行凭据通过实际环境配置，不写入源码。

worker 启动自检核对算法依赖、OCR、PyMuPDF、Harness、API Key 与 Chromium。自检失败先查看错误日志并修复缺项；参数、默认值和校验以 [agent_worker.py](../server/agent_worker.py) 及 `--help` 为准。

| 调整目的 | 查阅入口 |
| --- | --- |
| 任务总时限、侦察时限、补缺轮数与剩余采集时间 | worker 参数与 `agent_recon.py` |
| 单院请求／Token 保护、用量告警 | worker 参数、`recon_pricing.py`、工单用量记录 |
| 同站并发、节拍与限流冻结 | [site_limits.py](../server/site_limits.py)，共享站点时钟数据库 |
| 接单前内存保护和队列状态 | worker 参数、`agent_queue.py` |
| 模型阶段和推理强度 | `agent_recon.py`、阶段日志及实际环境配置 |

共享站点冻结和节拍可能使任务等待；不要删时钟数据库或移除保护来制造提速结果。默认值可被部署配置覆盖，调试和对照实验必须记录实际值。计划缓存、页面断点、OCR 缓存和复核决定是不同资产，不应整目录清空来代替定位。

## 6. 运行材料与诊断

现有学院任务材料通常在 `server/outputs/tasks/<task_id>/`；侦察材料在 `server/outputs/recon-evidence/`。具体位置可能被参数覆盖，应以任务审计和日志中的路径为准。保存原始观察、计划、分页和正文证据、图片／PDF 原件、错误、未解决项及交付版本，不只保存最终工作簿。

| 情况 | 核对方向 |
| --- | --- |
| 找不到或漏掉师资分支 | 检查发现阶段采用／排除的入口与官方导航；给定链接不是全院范围上限 |
| 分页不动、循环或位置变化 | 检查真实点击、URL／页面前沿、名单与控件证据；案例见 `docs/site-types/` |
| 名录人数相同但个人页遗漏 | 核对卡片边界及个人链接来源；见 [卡片边界记录](implementation/2026-10-10-roster-link-boundary.md) |
| PDF／图片属于谁 | 核对页码、位置、原件摘要及本人范围；见 [PDF 范围记录](implementation/2026-10-10-pdf-owner-resume-sections.md) |
| 长简介看似被截断 | 核对工作簿“完整简介”支持表及 [biography_workbook.py](../server/biography_workbook.py)，再查原始正文范围 |
| 修复后旧任务仍失败 | 核对实际进程版本、证据绑定、缓存条件与缺失资源；区分保存页回放和实网重采 |

字段核对和人数交叉核对会写入审计，但差额可能来自口径、行政名单或历史介绍。检查 `field_check`／`cross_check` 和相关材料，不能从它们直接推出全院真值，也不能忽略未进入输出的人员。

页面类型观察可在隔离库登记：

```sh
python -m server.site_type_registry --audit <审计.json> --db <隔离类型库.sqlite3>
```

它检查对应原件与摘要、记录观察，不修改工单、不批准采集方案。旧审计未识别的类型应先用当前解析器回放，不能补造观察字段。

## 7. 验证、回放与手工入口

自动检查命令统一见 [README](../README.md) 和 [CI 工作流](../.github/workflows/tests.yml)。本地测试使用独立数据库与输出；提交、合并前后核对相应版本的必需检查，失败先定位，不能将“待 CI”长期抄入多个文档。

保存页回放入口：

```sh
python -m server.case_replay <案例目录> --report <比较报告.md>
python -m server.spot_check --help
```

回放需要实际的 `plan.json`、`result.json` 和采集原件。它按大学／学院默认挑最新捕获，主要比较就绪状态和计数；未保存的请求、同学院其他历史版本与人员／字段差异须另外核对。因此保护历史成功能力要使用明确案例版本清单和人工核验差分，不能只接受回放报告“ready 数量增加”。

输出抽检只能检查已输出记录，不能发现整条漏人或遗漏分支。全链路审查 agent 的随机探索、整批保存、人工真值与冻结后未见验证按其 [五项计划](improvement-plan.md) 执行；日常采集维护按当前问题运行相关回归并保存证据。

认领、续租、执行与交付由 worker 组织；DS 侦察按已分配目标返回经核验的结构计划。开发维护 agent 在用户已授权的排障或采集范围内使用现有运维入口，不调用工作流角色技能来接管任务。已认领工单需要当前租约；`auto_dispatcher.py` 仅用于只读统计。浏览器批次 CLI 参数以 `--help` 和现有工具契约为准。

旧部署、历史测试数量和网站实验保留在 Git 与对应的 `docs/audits/`、`docs/implementation/` 文档；需要某次原因时按提交和问题检索，不将其当成当前运行状态。
