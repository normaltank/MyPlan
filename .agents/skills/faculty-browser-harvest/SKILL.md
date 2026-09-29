---
name: faculty-browser-harvest
description: Use when claiming university faculty collection tasks, dispatching college browser subagents, harvesting official faculty pages, or delivering dual-sheet workbooks.
---

# 高校师资浏览器采集

采集单位是学院，交付单位是大学。一个学院由一个专属 Subagent 负责；Agent 处理官网入口、页面形态与异常判断，可复用模块处理真实浏览器中的重复访问与本地解析。完整简介保留在本地记录和 Excel 中，不进入 Agent 上下文进行二次总结。

## 五步闭环

1. **认领**：
   - **服务器生产路径**：常驻 worker `python -m server.agent_worker` 自行轮询 `tasks.db` 取单，无需人工干预。`--once` 仅供排障。
     每学院独立 Harness 运行先执行 `server/recon_discovery.md`，再在各分支独立短上下文中执行 `server/recon_instructions.md`；代码强制阶段工具边界与浏览器证据门禁。通用主控文档和技能目录不注入侦察会话。worker 完成采集与审计，无效计划或结构失败用紧凑反馈返回浏览器补缺，最多两轮；该循环全程持有原租约。已核实分支在同工单内复用，同一问题补缺后没有新增证明便停止。固定请求数/Token 熔断默认关闭，可由运维显式设置；每阶段和失败尝试的 Token 均计入审计，不能仅统计成功阶段。
   - **IDE / 手工路径**：读取 `/api/queue/pending`，用 `/api/queue/claim/{task_id}` 认领。保存 `school`、`college`、`url`、`task_id`、`worker_id`、`lease_token`。对全部待采集学院立即各派发一个 Subagent，不设项目级并发上限；宿主拒绝创建时逐项报告并补派。
2. **真实浏览器侦察**：Subagent 提示词首行写 `/browser`。使用当前宿主实际可用的浏览器工具打开官方入口，逐一核实名录分支、分页或 Tab 控件、教师详情链接及图片名录。不要假设 Antigravity 专属工具在其他宿主中存在。
3. **按需选择工具**：普通名录与详情批量访问时，读取 [TOOLS.md](TOOLS.md) 的“浏览器批量工具”；图片名录时，读取其中“截图与 OCR”。单个学院可能同时使用多种页面形态，按页面分支选择，不按整站选择一个黑盒爬虫。计划必须标明 `source_kind`，并为每个名录分支提供已核实的 `card_selector`；导航页必须使用有界子分支。工具只接收已核实的官方 URL 和控件；完整网页与简介写入本地文件，主控仅接收紧凑 JSON。
4. **检查审计与补缺**：查看工具返回的 `branches`、`roster_observations`、候选人数、资格 `eligibility`、失败详情和 `unresolved`。`needs_agent_review` 或未检查的分支/分页都需继续浏览器核对、调整采集计划并重跑。OCR 名单必须对照完整截图核实，并记录可见总人数；有未加载图片或人数不符时不得交付。医院医生只有逐人当前大学任职证据才可进入 `eligible`；无证据进入 `excluded`，疑似但未核实进入 `unresolved`。双 Sheet 守恒仅检验已采集记录的分表，不代替官网覆盖检查。
5. **同步与大学交付**：仅在 `delivery_ready=true`、覆盖账本完整且 `unresolved=[]` 时，按 [TOOLS.md](TOOLS.md) 的“本地同步”提交到 `/api/task/{task_id}/sync`。学院成果同步后，检查 `delivery_scope.state`；本批目标学院全部完成且无冲突才生成大学版本下载。`collecting` 或 `blocked` 不能报告大学交付完成；单院批次只代表该学院范围。两 Sheet 均为标准十列，行互斥。

遇到中断恢复、结构缓存命中、局部补缺或版本下载时，读取 [可验证运行流程](../../../docs/architecture/verifiable-harvest-runtime.md)。服务器已接入这些机制：同工单/完整计划的有效快照恢复不重新请求模型；新任务缓存先由本轮真实浏览器验证；访问失败优先补抓失败页，结构失败才重侦察相关分支。普通 URL 分页可核验最后成功页后续采，不能可靠还原时从入口重走，保留原捕获时间和页数预算。Token 审计包含失败尝试，超时消费未知须明确标记。

有个人页的分支，读取 [TOOLS.md](TOOLS.md) 的“个人页正文范围”，核验完整正文边界和不同详情样例；批量工具逐页复验并保存原始页面及结构证明。

## Subagent 派发最小模板

同名异主页不能仅凭 URL 不同宣称两人，也不能仅凭同名或正文相似自动去重。`distinct_profile_identity_unverified` 必须保留全部观察与全文并进入来源身份复核，不要反复调用只负责结构的模型。旧大学版本可能在重新审计后变为 blocked，下载接口返回 409；不能用旧下载链接绕过当前门禁。空名录和图片名录未核验时同样不得交付。

```text
/browser
负责【{school}－{college}】工单 {task_id}，入口 {url}。
先用当前宿主真实浏览器核实官方名录、全部分支与分页；再按本技能 TOOLS.md 的触发条件选择浏览器批量工具或截图 OCR。
完整简介直接提取，不做 AI 总结；任何未解决访问、分页或正文冲突必须回到浏览器检查，不能导出已完成表。
本地同步后，仅回传一行 JSON：{"task_id":"...","status":"completed|needs_agent_review","total":0,"with_email":0,"audit":"...","university_excel":"..."}
```

## 不可跳过的约束

- 官网页面必须由真实浏览器访问。`batch_browser_extractor.py` 使用独立 HTTP 官网抓取，不能用于工单履约；旧 `harvest_template.py` 含硬编码邮箱过滤，也不作为当前交付工具。
- 本地批量工具的 CLI 会另建 Playwright 浏览器上下文；当前宿主没有可靠的 MCP 页面对象移交接口。不得声称与宿主浏览器共用同一页面或 Cookie。
- 模型（DeepSeek Harness + DeepSeek API）只用于页面结构判断与导航决策；其**产出物只允许含 URL 与 CSS 选择器**，严禁含姓名、职称、邮箱或简介文本，严禁用模型提取/转写/总结/改写这些字段。不为学术简介生成摘要，不用硬编码关键词黑名单清洗正文。
- 图片 OCR、PDF 附件和共享详情页属于特殊分支；无证据或发生冲突时返回 Agent 复核。列表中没有个人主页的教师如实收录，简介与邮箱留空，不因没有详情页而漏掉。
- 下载交付以大学为单位；学院采集工具生成的表是中间件，批次汇总表才是最终交付。
