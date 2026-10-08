# 【Agent 核心交接文档】系统全景、业务闭环、技术架构与交付总表

## 〇、2026-10-08 增补：10-02 之后合并的模块索引（main `ad2d7f3`，均未部署）

> 本节只做索引，告诉接手者这些能力已经存在、入口在哪，避免重写。行为细节以代码和 [CHANGELOG.md](CHANGELOG.md) 对应条目为准；下面旧章节的内容未改动，凡与本节冲突的“当前状态”以本节和代码为准。来源：[代码对齐检查](/mnt/project-files/reviews/code-alignment/2026-10-08-代码对齐检查.md) 的第 1 项（项目共享目录，不在仓库内）。

### 1. PR #17 到 #25 各加了什么

| PR | 内容 | 入口文件 |
| --- | --- | --- |
| #17 | A2 修复（#10）之前已交付学院的只读回查：对照覆盖账本与侦察浏览器证据，找分页提前停、卡片或导航子链接少于侦察所见的分支。只读，不写库 | `server/a2_lookback.py`（`python -m server.a2_lookback [--json]`）；结果记在 `docs/audits/2026-10-01-production-chain-audit.md` |
| #18 | HTML 表格名录：带标签的姓名列不按岗位过滤，备注格与分节标题随候选人和覆盖证据保留；一行多个“姓名”列时每人都收 | `server/faculty_extractor.py`、`server/harvest_workflow.py`、`server/browser_batch_tool.py` |
| #19 | 零散修复：卡片原文写入审计 `eligibility[].roster_context`；完整拉丁字母姓名；个人页 404/410 的 fetch/XHR 按结构核验放行；“邮 箱”字间空白 | `server/harvest_eligibility.py`、`Opus/iso_mining.py`、`server/profile_structure.py`、`server/detail_mecc.py`；说明见 `docs/implementation/2026-10-08-misc-case-fixes.md` |
| #20 | IDE 批量工具拒绝计划里的 `verified_candidates`、`poster_reviews`（模型录入的人名），与服务器侦察一致 | `server/browser_batch_tool.py`、`server/harvest_coverage.py`、`server/harvest_eligibility.py` |
| #21 | 跨学院同名的大学级复核入口（L1）：大学汇总先阻断，管理员判“不同教师”或“证据不足” | `server/university_identity_review.py`、`server/university_delivery.py`、`server/app.py`（`/review/university/{batch_id}`） |
| #22 | 整图表格名录（`kind=image_table`）：按格线切格、逐格本地 OCR，复用 PDF 表格规则；逐格复核页 | `server/image_table_roster.py`（读取）、`server/image_table_browser.py`（浏览器核验）、`server/image_table_review.py`（复核）、`server/image_table_routes.py`（路由）、`server/templates/image_table_review.html` |
| #23 | 失败学院按“失败去向”显示和计数，数据库状态仍是 `failed`，不新增状态 | `server/recovery_policy.py` 的 `failure_disposition`；`server/agent_queue.py` 同名包装、`server/app.py`、`server/templates/index.html` |
| #24 | 同模板名录页只侦察一次（模板指纹看子结构集合）；侦察门禁复用采集端横幅箭头规则；个人页失效模板资源重开再验；补缺反馈按 `profile_sources` 交给所有链接该个人页的分支，补缺须在报错页上证明；证件照按 OCR 检测行数判断；原图类型复核；翻页跳新页先等加载 | `server/recon_templates.py`、`server/agent_recon.py`、`server/browser_batch_tool.py`（`without_banner_arrows`、`profile_sources`）、`server/profile_structure.py`、`server/image_biography.py`、`server/media_kind_review.py` |
| #25 | 本机 Chrome 兜底读取：进程活着就等调试端口就绪，上限 90 秒 | `server/browser_batch_tool.py` |

#15（名单串页角色行教师进复核，`server/name_list.py` 的 `name_list_role_line`）与 #16（查询串里写 `.pdf` 的附件识别为 PDF，`server/image_biography.py` 的 `media_references`）也在 10-02 之后合并。

### 2. 人工复核：7 个模块

单个学院的复核由 `server/task_image_review.py` 汇总：`IMAGE_REVIEW_STATUSES` 决定哪些审计项算待复核，`review_complete` 逐种判断是否复核完，`carry_review_decisions` 把各决策文件带到恢复后的新 attempt 目录。复核页是 `server/app.py` 的 `/review/task/{task_id}`，各类提交走 `/api/task/{task_id}/review/<种类>`。决策文件都在该 attempt 的审计文件旁边。

| 模块 | 处理的状态码（定义处） | 决策文件 |
| --- | --- | --- |
| `server/image_bio_review.py`（简介图 OCR 纠字） | `image_bio_low_confidence`、`image_bio_owner_unresolved`、`segment_name_needs_review`（目前只写在 `task_image_review.py`） | `image_bio_reviews.json` |
| `server/image_bio_review.py`（简介图邮箱冲突） | `image_bio_email_conflict`（同上） | `image_bio_email_reviews.json` |
| `server/name_review.py`（生僻姓、角色行姓名） | `name_review.STATUS` = `rare_surname_needs_review` | `name_review.DECISIONS_FILE` = `name_reviews.json` |
| `server/identity_review.py`（学院内同名多主页） | `identity_review.STATUS` = `distinct_profile_identity_unverified` | `identity_review.DECISIONS_FILE` = `identity_reviews.json` |
| `server/html_email_review.py`（普通 HTML 邮箱歧义） | `html_email_review.STATUSES`（5 个） | `html_email_review.DECISIONS_FILE` = `html_email_reviews.json` |
| `server/media_kind_review.py`（原图是照片还是含文字） | `media_kind_review.STATUS` = `image_bio_ocr_incomplete`，且 `kind == "image"` | `media_kind_review.DECISIONS_FILE` = `media_kind_reviews.json` |
| `server/image_table_review.py`（整图表格逐格纠字、整图人数） | 13 个 `table_*`，由 `server/image_table_roster.py` 产生；`task_image_review.py` 里另手抄了一份 | `image_table_roster.REVIEW_FILE` = `image_table_reviews.json` |

大学级复核另走一套：`server/university_identity_review.py`（跨学院同名），决定按版本存在 `tasks.db` 的 `university_identity_reviews` 表，不用 JSON，也不经过 `task_image_review.py`。

已知问题（见 `docs/review-backlog.md` 的 2026-10-08 代码对齐条目）：每加一种复核要改 `task_image_review.py` 的状态集合、`review_complete` 和 `carry_review_decisions` 三处；`table_*` 状态码有两份。

### 3. 失败去向只在一处定义

审计状态码分到 access / structure / review / stop 四组，只在 `server/recovery_policy.py` 的 `_GROUPS` 里写；新状态码必须加在这里，否则按 stop 处理。失败学院的去向（review / access / structure / recon / failed）只由 `recovery_policy.failure_disposition` 计算；`agent_queue.failure_disposition` 只是先用 `site_type_registry.classify_failure` 取出状态码再调用它，`app.py` 和前台计数都走这个包装。不要在别处再写一份分组或去向判断。

### 4. 姓名规则和邮箱归属规则现在在哪

- 姓名：名录页判定是 `server/name_list.py` 的 `_person_name`，分段名录是 `server/segmented_roster.py` 的 `_name_like`，两份逻辑相同，常量 `NAME_PRIOR_FLOOR`、`_HAN_NAME`、`_MINORITY_NAME` 各定义一次，少数民族姓名正则已不一致（`{2,6}·{2,8}` 与 `{1,6}·{1,8}`）。姓名先验 `name_prob` 和生僻姓判断 `rare_surname_name` 在 `Opus/iso_mining.py`，生僻姓表是 `Opus/rare_surnames.txt`。合并方案见对齐执行方案 B 包，等 badr 定点号规则。
- 邮箱归属：主规则是 `server/detail_mecc.py` 的 `is_personal_email_for_scholar`、`is_institutional_email`（拼音模型在 `Opus/email_matching.py`）。`server/faculty_extractor.py` 在 `detail_mecc` 可用时直接导入这两个函数；不可用时用文件内的降级版（只按纯数字地址认人，和主规则口径不同），L2 算法层导入失败时 `strip_invisible` 也有降级版。`find_emails` 在 `faculty_extractor.py` 和 `detail_mecc.py` 各有一份，两处的 `FREE_MAIL_DOMAINS` 都没有被使用。去重方案见对齐执行方案 D 包。

---

> **2026-10-02 文档治理与 Claude 派工**：根目录 [CONSTITUTION.md](CONSTITUTION.md) 集中稳定原则与变更权限；`AGENTS.md` 保留作业规则，`CLAUDE.md` 为读取入口。OCR 修复及宪法已提交并同步 main（`4fe72c2`、`10370eb`）；服务是否加载仍按下方部署记录判断。用户指定在 [Claude Projects → MyPlan 主对话](https://claude.ai/code/project/chan_012KwoNrMicyn2LoBSKGmCan) 派工，现有两个项目内实现线程：多人 PDF 名录采集、HTML 邮箱歧义复核恢复；具体范围见 [任务规格](docs/implementation/2026-10-02-claude-assignment.md)，要求独立 PR，不自动合并／部署。此前误开的两个独立 Code 会话已显示 Idle，不再继续。PR #4 仅改项目说明，实际与 main 的 AGENTS.md 存在合并冲突，且有过期状态和额外确认要求；本轮只审阅，未修改或合并该 PR。

> **最新交接：2026-10-02 侦察图片 OCR 入口修复，待重启 worker 生效**。`server/recon_browser_mcp.py` 新增按需 `browser_read_image`，复用现有原图读取与本地 OCR，侦察说明已同步；普通 DOM 不触发 OCR，模型计划及教师来源审计边界不变。淮阴师范学院马克思主义学院给定“系部设置”页实为 600×805 PNG，真实浏览器实测已读出 40 行，原件与完整结果在 `server/outputs/tasks/8ee2181b7a0741a1a2b45c7862ed2942/ocr-diagnosis/`。该图混合教研部负责人和办公人员，不能据此认定完整教师名录；原工单转入导师 PDF 后的来源范围门禁仍保留，未重排任何工单。相关回归 129 passed，最后补充的越界读取验证 2 passed，Ruff 与 diff 检查通过。重启前已确认队列空闲，但自动审批拒绝了 worker 停止/重启（仅返回 blocked by policy）；服务仍加载更新前的侦察说明，需通过“重启本地服务.bat”加载更新。

> **最新交接：2026-10-01 底层调用链完善已保存，尚未重启服务**。详见[专项审查](docs/audits/2026-10-01-foundation-hardening.md)、[69 项脚本清单](docs/audits/2026-10-01-script-inventory.md)与[实施计划](docs/implementation/2026-10-01-foundation-hardening.md)。用户已批准“个人页明确职称字段 > 名录具体职称 > 简介职称”，现已实装；同时修复旧脚本入口、恢复缓存、邮箱冲突、收据范围与别名、Excel 完整性及大学计数。全量 967 passed、1 skipped 在最后候选邮箱证据修复之前执行，最新定向结果见专项审查；尚需最新全量和空闲时重启。用户将另选学院验收，不处理旧失败工单。

> **上轮部署：2026-10-01 main 同步与本机服务**。内核修复见[续接验证报告](docs/audits/2026-09-30-core-chain-continuation.md)，部署与四字段输入见[部署记录](docs/audits/2026-10-01-local-deployment.md)。Windows 排除了 8000 所在端口段，本机通过 ignored 配置使用 `http://127.0.0.1:18000`；Web、常驻 worker 与手动同步共用此地址。运行服务仍加载上轮 `fe73ddd`，下面未部署状态均为历史记录。

> **最新交接：2026-09-30 本地 Git 同步**。远端邮箱修复与侦察提速/师资链接输入已合并，邮箱冲突采用较新的联系字段正证据与歧义阻断规则；审核发现的三项人数/输入问题已修复。本机验证及合并取舍见 [同步审核](docs/audits/2026-09-30-git-sync-review.md)。同步开始时未发现项目 Web/worker 进程，本轮不启动服务；历史 PID 与测试数不代表当前状态。两失败工单的范围与恢复边界仍见 [内核续接说明](docs/implementation/2026-09-30-extractor-core-handover.md) 和 [邮箱补全报告](docs/implementation/2026-09-30-email-evidence-merge-report.md)。

> 按日期追加的状态说明已移到 [CHANGELOG.md](CHANGELOG.md)（最新在前）。本文件描述系统本身；§九、§十中 2026-09-24 的“当前阻塞”等表述是当时的记录。

> **致接手本项目的智能体（主控与 Subagent）**：  
> 本文档是项目的系统级知识库与业务交接权威指南。明确定义了业务使命、核心工作流 SOP、底层计算机科学架构（CS Deep Module）、业务经验规则库、当前系统运行状态与历史交付总表。请完整阅读并在操作中严格遵循。

---

## 一、 项目定位与核心使命

本项目是一个**高校二级学院师资队伍智能全流程采集与结构化平台**。
全国高校网站架构极度碎片化（博达CMS、动态JS/AJAX翻页、嵌套表格、PDF/图片海报挂载简历等），传统硬编码规则爬虫极易漏人或抓到一堆导航噪音。
我们打造的系统目标是：**通过真实浏览器智能体（Playwright + `/browser`）深入官网，依托坚固的计算机科学原理算法（DOM同构聚类、盒模型语义分词、最小闭包容器隔离），实现零截断、零硬编码黑名单、全内页穿透的自动化高校师资采集，交付标准化双 Sheet Excel 表格并实时同步至 Web 平台。**

---

## 二、 核心业务闭环工作流（5 步 SOP）

> 💡 **SOP 技能封装**：该五步工作流已被完整封装为专属技能 [`faculty-browser-harvest`](.agents/skills/faculty-browser-harvest/SKILL.md)，遵循渐进式披露原则，在执行采集任务时按需读取。

```
1.【前端提交】用户或同事在本地 Web 网页端 (http://localhost:8000) 输入 1 所或批量 10-20 所高校学院
      │
      ▼
2.【任务入池】FastAPI 后端将请求写入工单池 (server/agent_queue.py / tasks.db)
      │
      ▼
3.【认领】两条轨道（服务器生产走轨 C，IDE 研发走轨 A）
      ├─ 轨 C（生产）：常驻 worker `server/agent_worker.py` 直接轮询 tasks.db 取单，无需 Webhook
      └─ 轨 A（IDE）：智能体主控关注任务池 (GET /api/queue/pending) 并认领 (POST /api/queue/claim/{id})
      │
      ▼
4.【按学院并发派发浏览器子智能体】采集单位是学院，每个学院分配 1 个专属 Subagent (College-Level Dispatching)
      │
      ├─ 全量并行派发：全部待采集学院立即一对一派发，不设项目级并发上限；宿主拒绝创建时逐项报告并补派
      ├─ 强开头与真实浏览器：Prompt 首行携带 /browser，首步用宿主实际提供的浏览器打开并核实目标主页
      ├─ 按需批量工具：核实名录分支与控件后，Subagent 可调用 browser_batch_tool.py；工具用真实 Playwright 浏览器批量访问详情并返回紧凑审计
      ├─ 来源资格与覆盖对账：计划声明 source_kind/card_selector；导航页使用有界子分支，医院医生逐人核验大学当前任职证据
      ├─ 特殊分支：图片名录用浏览器截图及本地 OCR，OCR 结果对照图片核实后才入库
      ├─ 结构化提炼与去噪：浏览器渲染的 HTML 交由 faculty_extractor.py 清洗；访问失败、分页停滞和正文冲突阻断导出
      └─ 学术简介全文保留：直接提取学者个人主页正文全文入库（严禁 AI 二次压缩总结，生成 Token 为 0，履历成果 100% 完整）
      │
      ▼
5.【以大学为单位汇总交付与同步】各学院通过 /api/task/<task_id>/sync 回传真实记录后，按大学合并输出专属双 Sheet 表格（server/outputs/<大学名称>.xlsx），保证双 Sheet 守恒，供网页端一键下载
```

---

## 三、 绝对不可触碰的三大红线

### ⚖️ 红线 1：模型调用只用于侦察，产出物严禁含数据字段
- **唯一调用点**：服务器端 Agent 运行时（DeepSeek Harness + DeepSeek API）是本项目**唯一**的大模型调用入口。`server/faculty_extractor.py`、`server/harvest_workflow.py` 等确定性内核**严禁内嵌任何大模型调用**。
- **唯一用途**：模型只做**页面结构判断与导航决策**——核实名录分支、确认 `card_selector` 与翻页控件、识别图片名录形态、产出采集计划 JSON。所有 URL、选择器与控件必须来自模型在真实浏览器中的**实际核实**，不得凭模型记忆臆造。
- **严禁数据出境**：模型的**产出物**（采集计划 JSON）只允许包含 URL 与 CSS 选择器，**不得含任何**学者姓名、职称、邮箱或简介文本。模型侦察时必然看到页面内容，这是允许的；但**严禁用模型提取、转写、总结、改写**上述数据字段——数据一律由确定性算法直接提取（见红线 2）。这是防幻觉的架构保障，不是建议。

### ❌ 红线 2：学术简介直接提取全文，严禁 AI 二次总结与硬编码黑名单！
- **学术简介全文保留**：定位学者详情页主体正文容器，直接提取并复制纯净正文全文（去除页眉页脚与通用导航等 DOM 噪点后，原汁原味保留学者的全部学习经历、研究方向、论著清单、科研项目与获奖荣誉）。
- **杜绝二次总结**：绝对不使用 AI 进行压缩或二次摘要，既杜绝模型幻觉与信息裁剪，又使文本生成 Token 消耗归零。
- **杜绝硬编码黑名单**：基于 DOM 拓扑结构与正文容器空间几何天然去噪，严禁编写脆弱的 `if "学院概况" in text` 等死板关键词黑名单。

### ❌ 红线 3：双 Sheet 守恒公理与以大学为交付单位
- **以大学为单位交付**：一个大学的所有学院必须统一放在一个表里，表以大学的名字命名（`server/outputs/<大学名称>.xlsx`）。若混杂多个大学多个学院，同样以大学为单位分别输出各个大学的双 Sheet 表。
- **守恒公理**：对每一所大学，$$\text{Sheet 1 (有邮箱)} + \text{Sheet 2 (无邮箱)} = \text{该大学全部已采集专任教师总人数}$$
- 两表标准 10 列字段完全一致、行互斥、一人不漏。

---

## 四、 核心计算机科学底层架构 (CS-Principled Architecture)

### 0. 采集编排与领域策略分层

通用采集路径已经抽出为 `server/harvest_workflow.py` 的三个可测试边界：
`capture_sources(plan, browser_context)` 只执行计划允许的真实浏览器访问，
`reconcile_rosters(captured_pages, plan)` 只做页面候选与可见单元对账，
`commit_records(records, destination, exporter)` 只提交已经通过审计的记录。
聘任资格属于独立的 `server/harvest_eligibility.py` 领域策略。具体院校的入口、选择器、快照和任职证据是计划或验收夹具，不得写成通用采集器中的域名分支。

系统核心抽取引擎基于严格的计算机科学与编译器/排版原理实现（位于 [`server/faculty_extractor.py`](server/faculty_extractor.py)），并通过 TDD 测试防护网（[`tests/test_faculty_extractor.py`](tests/test_faculty_extractor.py)）锁死质量保障：

1. **DOM 同构子树聚类 (Isomorphic Subtree Clustering)**：
   - 从树论与结构熵出发，将列表页中的教师卡片识别为具有相同 DOM 结构指纹（Structural Tag Sequence, STS）的同胞子树最大簇；
   - 数学上自然滤除非教师导航菜单、分页栏、页眉页脚等结构离群点，彻底摆脱死板的人工关键词过滤。
2. **盒模型语义分词 (Layout-Aware Tokenization)**：
   - 遵循 W3C CSS Block/Inline 格式化上下文规范，在块级元素（`div`, `p`, `tr`, `li`, `h1`-`h6`, `br`）边界处发射物理断句；
   - 从排版物理根源杜绝电话号码（如 `0510-8591xxxx`）与邮箱前缀粘连的问题；
   - 100% 保护高校纯数字工号邮箱（如 `8843120002@jiangnan.edu.cn`），禁止任何暴力正则数字剥除。
3. **最小闭包正文容器隔离 (MECC Scoping)**：
   - 详情页个人邮箱提取限定在正文主容器几何范围内；
   - 空间物理隔离页脚（Footer）与联系我们区域，彻底杜绝 `sdy@hhu.edu.cn`、`office@` 等全院公共办公邮箱泄露给普通学者。
4. **端到端双 Sheet 守恒断言**：
   - 在实体提炼阶段实施严苛的 $N_1 + N_2 = N_{total}$ 守恒校验，字段 Schema 严格为 10 列。

---

## 五、 实战宝贵业务经验与领域规则库 (Domain Knowledge & Experience)

在全国 20 余所重点高校、数十所二级单位的实际采集与交付中，提炼出以下核心领域经验：

### 1. 行政服务与留学生管理机构判定（0 编制规则）
- **机构类型**：研究生院、国际教育学院、继续教育学院、网络教育学院、海外教育学院等；
- **业务本质**：属于全校性行政管理服务部门，本身不设立独立的一线专任教学科研编制。其名下导师、外语教师编制均归属于各专业实体学院（如数学、外国语、机械等学院）；
- **履约标准**：如实生成 0 位在职专任学者的标准双 Sheet 表格（$0 + 0 = 0$ 守恒），并在交接中明确记录。严禁虚构学者，杜绝全校师资总表出现重复交叉计数。

### 2. 本科拔尖与荣誉学院判定（0 编制规则）
- **机构类型**：如江苏大学“梦溪学院”、“菁英学院”等；
- **业务本质**：属于高校拔尖创新人才培养基地或实验班管理机构，其导师、任课教授编制全员隶属于各自专业二级学院；
- **履约标准**：按 0 独立编制交付，确保全校教师人头唯一性。

### 3. 合署办公与“一院两牌”组织架构
- **典型场景**：如南京林业大学“信息科学技术学院”与“人工智能学院”（合署）、江苏大学“智能农机重点实验室”与“农业工程学部”（合署）；
- **履约标准**：准确识别人事组织关系。在单院交付时满足两块牌子的命名工单，在全校/跨院合并总表中按 `(学校, 学院, 姓名, 邮箱)` 联合主键自动去重，确保数据全覆盖且不重不漏。

### 4. 无专属主页学者（Profile-less Faculty）处理
- **典型场景**：部分学者仅在学院教师名录列表中列出姓名、职称，未设立可点击进入的二级个人主页；
- **履约标准**：如实收录学者姓名与职称，个人简介留空，主页填入名录列表 URL，直接归入 Sheet 2（无邮箱学者），严禁跨外部搜索引擎盲目检索引入脏数据。

---

## 六、 当前系统运行环境与接口矩阵

1. **本地 Web 后台服务**：
   - 入口文件：[`server/app.py`](server/app.py)（启动脚本：`server/start_server.py`）
   - 监听地址：`http://0.0.0.0:8000`（本地访问：`http://localhost:8000`）
   - 核心 API 矩阵：
     - `GET /api/queue/stats`：实时队列指标（待处理/运行中/已完成/失败）
     - `GET /api/queue/pending`：获取当前待领取的任务
     - `POST /api/queue/claim/{task_id}`：认领指定工单
     - `POST /api/task/{task_id}/sync`：同步抓取结果与 Excel 路径回服务器
     - `POST /api/collect_batch`：前台批量提交学院采集任务
     - `GET /download/{filename}`：前台同事一键下载生成的 Excel 表格

2. **核心代码库定位**：
   - 工单只读巡检器：[`server/auto_dispatcher.py`](server/auto_dispatcher.py)（启动脚本：`启动自动巡检接单.bat`）；不会认领工单
   - 常驻服务器消费者：[`server/agent_worker.py`](server/agent_worker.py)；旧 `ide_dispatch_runner` 已移除
   - 工单队列管理：[`server/agent_queue.py`](server/agent_queue.py)
   - 核心 CS 提取引擎：[`server/faculty_extractor.py`](server/faculty_extractor.py)
   - Subagent 按需浏览器批量工具：[`server/browser_batch_tool.py`](server/browser_batch_tool.py)
   - 图片截图与本地 OCR：[`server/browser_poster_tool.py`](server/browser_poster_tool.py)
   - 本地工单同步：[`server/browser_batch_sync.py`](server/browser_batch_sync.py)
   - 历史 HTTP 提取／DOM helper／导出模板均已退役，旧脚本 import／CLI 拒绝执行；离线渲染 HTML 解析统一走 `faculty_extractor`
   - 浏览器侦察运行时：[`server/agent_recon.py`](server/agent_recon.py)；旧 `autonomous_agent` 已移除
   - 交付产物输出目录：[`server/outputs/`](server/outputs/)

---

## 七、 Subagent 提示词规范与派发模板 (黄金提示词)

当前派发模板以 [faculty-browser-harvest/SKILL.md](.agents/skills/faculty-browser-harvest/SKILL.md) 为准。主控将变量 `{school}`, `{college}`, `{url}`, `{task_id}` 替换后注入最小提示词；具体工具说明只在命中对应页面形态时读取 [TOOLS.md](.agents/skills/faculty-browser-harvest/TOOLS.md)：

```markdown
/browser

负责【{school} - {college}】工单 {task_id}，入口 {url}。
先用宿主真实浏览器核实全部名录分支及分页；再按采集技能的 TOOLS.md 选择浏览器批量工具或截图 OCR。
核对审计中的访问页与未解决项。完整简介只存入本地记录，不用 AI 总结。仅在无未解决项且确认覆盖范围后同步。
向主控仅回传单行紧凑 JSON：{"task_id":"{task_id}","status":"completed|needs_agent_review","total":X,"with_email":Y,"audit":"...","university_excel":"..."}
```

### 阶段边界与 Token 卫生 (/ask-matt Context Hygiene)
- **一个 Batch = 一个 Phase Boundary**：一个批次（5~10 所学院）完成并生成大学合并总表后，当前阶段即告终结。应提示用户新开会话，禁止单会话无休止累积长上下文。
- **次级来源汇报**：Subagent 完成后仅向主控回复单行紧凑 JSON，保护主控上下文卫生。

---

## 八、 交付总表与主知识库资产清册 (Master Delivery Ledger)

### 1. 全量师资总库规模
根目录核心知识库 [`教师列表.xlsx`](%E6%95%99%E5%B8%88%E5%88%97%E8%A1%A8.xlsx) 全量师资总库去重合并后达到 **5,761 位** 真实在职专任学者，100% 严格满足双 Sheet 守恒公理。

### 2. 重点批次交付清单

| 批次 ID | 目标高校 | 学院/单位数 | 实收学者数 | 邮箱覆盖率 | 批次合并总表交付件 |
| :---: | :--- | :---: | :---: | :---: | :--- |
| `1a62151d` | **湖南师范大学 (Hunan Normal University)** | 8 所（7 交付 + 1 覆盖缺口） | **238** | 21.0% (50人有邮箱) | [`湖南师范大学.xlsx`](server/outputs/湖南师范大学.xlsx) |
| `gszy-batch` | **甘肃中医药大学 (Gansu University of Chinese Medicine)** | 12 所 | **953** | 30.6% (292人有邮箱) | [`甘肃中医药大学.xlsx`](server/outputs/甘肃中医药大学.xlsx) |
| `ec535c3f` | **西安音乐学院 (Xi'an Conservatory of Music)** | 10 所 | **562** | 0.0% (562人无邮箱) | [`西安音乐学院.xlsx`](server/outputs/西安音乐学院.xlsx) |
| `6de5e7cd` / `f466f13d` | **绍兴文理学院 (Shaoxing University)** | 11 所 | **449** | 2.4% (11人) | [`绍兴文理学院.xlsx`](server/outputs/绍兴文理学院.xlsx) |
| `4f0dee1a` | **南京林业大学 & 江苏大学** | 18 所 | **1,355** | 61.3% (831人) | [`批量采集教师列表_合集_南京林业大学_江苏大学_18学院_20260922.xlsx`](server/outputs/批量采集教师列表_合集_南京林业大学_江苏大学_18学院_20260922.xlsx) |
| `a01cf3d1` | **河海大学 & 江南大学** | 14 所 | **630** | 91.7% (578人) | [`批量采集教师列表_合集_河海大学_江南大学_14学院_20260922.xlsx`](server/outputs/批量采集教师列表_合集_河海大学_江南大学_14学院_20260922.xlsx) |
| `04d85c4d` | **上海外国语大学** | 6 所 | **204** | 22.5% (46人) | [`批量采集教师列表_合集_20260921_101228.xlsx`](server/outputs/批量采集教师列表_合集_20260921_101228.xlsx) |
| 标杆名校群 | **清华、北大、复旦、浙大、人大、武大、中山、南大** | 12 所重点院系 | **650+** | >95% (标杆满覆盖) | 各学院独立双 Sheet 交付表已全量归档于 `server/outputs/` |
| 工科与环境群 | **同济大学、华东师大、上海师范大学** | 3 所重点学院 | **424** | 88.4% (375人) | 同济环境(197人)、华东师大地理(146人)、上师大环地(81人) |

### 3. 最新批次细分（西安音乐学院 10 学院全量履约）
- **西安音乐学院**（全校 10 院系全量采集交付，涵盖 562 位在职学者）：
  1. 作曲系 (85 位学者)
  2. 民乐系 (144 位学者)
  3. 声乐系 (66 位学者)
  4. 钢琴系 (56 位学者)
  5. 管弦系 (60 位学者)
  6. 人文学院 (51 位学者)
  7. 音乐教育学院 (38 位学者)
  8. 现代音乐学院 (40 位学者，通过本地 RapidOCR 视觉降级流水线与百家姓先验切词 100% 还原海报名单)
  9. 舞蹈学院 (21 位学者)
  10. 马克思主义学院 (1 位学者)
- **双 Sheet 守恒校验与大学表交付**：$0(\text{有邮箱}) + 562(\text{无邮箱}) = 562(\text{总人数})$，100% 严格守恒。以大学命名的全校双 Sheet 总表 [`西安音乐学院.xlsx`](server/outputs/西安音乐学院.xlsx) 涵盖全校 562 位专任学者，已同步回传至 Web 前台。

### 4. 最新批次细分（甘肃中医药大学 12 学院全量履约）
- **甘肃中医药大学**（全校 12 院系全量采集交付，涵盖 953 位在职专任学者）：
  1. 第一临床医学院 (228 位学者：216 有邮箱，12 无邮箱；医院名录中具备大学教研室编制学者)
  2. 第二临床医学院 (6 位学者：0 有邮箱，6 无邮箱；遵循 ADR 0004 经严格教学任职证据链认定)
  3. 中医临床学院 (156 位学者：5 有邮箱，151 无邮箱)
  4. 药学院 (156 位学者：47 有邮箱，109 无邮箱；攻克瑞数 6 WAF 自动化防护)
  5. 护理学院 (100 位学者：8 有邮箱，92 无邮箱)
  6. 基础医学院 (89 位学者：0 有邮箱，89 无邮箱；5 个分页全量覆盖)
  7. 卫生管理学院 (60 位学者：9 有邮箱，51 无邮箱；攻克“南、栗、门”罕见百家姓先验)
  8. 公共卫生学院 (57 位学者：1 有邮箱，56 无邮箱；攻克“修、慕、颉、郁”罕见百家姓先验)
  9. 针灸推拿学院 (44 位学者：6 有邮箱，38 无邮箱)
  10. 医学信息工程学院 (43 位学者：0 有邮箱，43 无邮箱；4 大教研室文本名录“妥、喜”姓氏全量解析)
  11. 马克思主义学院 (14 位学者：0 有邮箱，14 无邮箱)
  12. 继续教育学院 (0 位学者：经核查无独立在职专任教师编制，依 0 编制规则规范交付)
- **双 Sheet 守恒校验与大学表交付**：$292(\text{有邮箱}) + 661(\text{无邮箱}) = 953(\text{总人数})$，100% 严格守恒。以大学命名的全校双 Sheet 总表 [`甘肃中医药大学.xlsx`](server/outputs/甘肃中医药大学.xlsx) 涵盖全校 953 位专任学者，已同步回传至 Web 前台。

### 5. 最新批次细分（湖南师范大学 8 学院，批次 1a62151d）

- **湖南师范大学**（8 个学院工单：7 个已交付，1 个覆盖缺口）：
  1. 国际汉语文化学院 (23 位：1 有邮箱，22 无邮箱；教授 4 + 副教授 10 + 讲师 9，客座教授/导师队伍/行政人员按口径排除)
  2. 历史文化学院 (72 位：3 有邮箱，69 无邮箱；教授 22 + 副教授 20 + 讲师 30，共 7 个名录页；特聘讲座教授/兼职/荣休/人才引进栏目逐一核实后排除)
  3. 生命科学学院 (143 位：46 有邮箱，97 无邮箱；教授 67 + 副教授 50 + 讲师 26，共 11 个名录页)
  4. 继续教育学院 (0 位：真实浏览器全站 144 页核查无师资名录，按 0 编制规则交付；培训中心 272 名专家逐条归属各专业学院)
  5. 世承书院 (0 位：2021 年成立的荣誉住宿制书院，353 名导师逐人核实归属 8 个专业学院，按 0 编制规则交付)
  6. 创新创业学院 (0 位：全站 260 页核查，授课教师由各学院推荐入校级教师库，按 0 编制规则交付)
  7. 教师教育学院 (0 位：全站 700 页核查，教师教育课程由教育科学学院/文学院等开设，本院为统筹管理服务机构)
  8. 树达学院 (**未交付，覆盖缺口**：官网穷尽核查无任何在职专任教师名录；该院 2024-05 已获教育部同意转设为长沙工业学院，工单待同事确认改派或撤单，队列中如实记为 failed)
- **双 Sheet 守恒校验与大学表交付**：$50(\text{有邮箱}) + 188(\text{无邮箱}) = 238(\text{总人数})$，100% 严格守恒。以大学命名的双 Sheet 总表 [`湖南师范大学.xlsx`](server/outputs/湖南师范大学.xlsx) 已同步生成，各已完成学院的下载链接均指向该表。

### 6. 关键架构修复与可靠性加固
1. **纯本地离线 OCR 视觉降级流水线（Zero-API Local Vision Fallback）**：针对艺术/音乐等院校常见的“全海报展板型无文本师资名录”，集成本地轻量 `rapidocr_onnxruntime` 引擎。结合中国百家姓先验概率与 BFS 拓扑切词算法，纯 CPU 离线计算（0 商业 API、0 Token、1.5 秒/图），彻底攻克了图片名录漏采难题，并在 [`requirements.txt`](requirements.txt) 锁定无头服务器环境规范。
2. **高校 CMS 分页末页平滑收敛**：针对 WebPlus 等高校常见 CMS 在末页点击下一页（`javascript:void(0)`）造成内容不变的场景，将其作为分页遍历的自然终结条件，彻底根除了误判为“分页停滞未刷新”并注入 `crawl_errors` 导致交付阻断的顽疾。
3. **非师资专栏与安全上限解耦**：在分支专栏识别中严格过滤“新闻/通知/党建/概况”等非师资栏目，并将分支专栏容纳量提升至 60 个，单专栏 8 页安全上限转为自然收录提示而非致命阻断，保护全院合法学者产出。
4. **工单同步与错误状态闭环**：修复 `ide_dispatch_runner.py` 在任务失败时不发送错误载荷且未校验 HTTP 状态码的缺陷，确保任务无论成功还是失败都能准确同步 SQLite 状态，彻底消除了任务死锁在 `running` 状态导致队列显示无待处理任务的竞态故障。
5. **大学级跨运行态全量聚合**：增强 `export_university_excel` 机制，导出 `<大学名称>.xlsx` 时自动从 `tasks.db` 聚合该校名下已完成的全部学院记录，确保分阶段、断点续传或并发执行时交付的大学总表始终保持一人不漏、双 Sheet 严格守恒。
6. **二级子域 瑞数 6 (Ruishu WAF) 真实浏览器代理穿透机制**：针对部分高校（如甘肃中医药大学二级学院）挂载网宿/瑞数 6 防护、首包返回 HTTP 412 并通过混淆 JS challenge 进行反爬的机制，在 `server/browser_batch_tool.py` 中实现了 `_setup_waf_proxy_if_needed`。通过唤醒真实 Chrome 获取 genuine cookies，自动注入 Playwright 上下文与 route-proxy，彻底打通高防二级站点的自动化渲染。**（2026-09-29 已移除：自动绕过站点 WAF 有合规风险，且把单一院校域名写进了通用采集器；此类站点现按访问失败进入人工处理。原实现见 git 提交 871e480。）**
7. **同构子树挖掘百家姓先验与卡片守恒增强 (Rare Surname Prior Fix)**：在底层核心算法库 `Opus/iso_mining.py` 中，扩展了百家姓先验表 `_SURNAMES`，补齐了“南、栗、门、修、慕、颉、郁、妥、喜”等合法但使用频率较低的正规汉字姓氏，彻底消除了此类合法学者在同构子树聚类中因先验置信度偏低被误滤、进而触发列表卡片数与提取候选数不匹配（`roster_card_mismatch`）的阻断缺陷。
8. **姓名先验表与零宽字符召回修复（湖南师范大学批次现场发现）**：`Opus/iso_mining.py` 的 `_SURNAMES` 缺少“傅、粟、邸、银、容、穰、禹、蔺、邴”等正规姓氏，导致 `name_prob()` 返回 0.08 默认值、真实在职学者在 `p < 0.5` 门禁处被静默丢弃（历史文化学院少 4 人、生命科学学院少 5 人，均由 `roster_card_mismatch` 审计阻断交付而未流入成品）。已补齐姓氏并新增 `strip_invisible()` 归一化 U+200B 等零宽填充字符（`name_prob` 与 `faculty_extractor._clean_name` 双侧），两个学院重跑后 72/72、143/143 全量召回，交付姓名不再携带不可见字符。
9. **邮箱姓名亲和变体补齐（名首字母 + 姓）**：`Opus/email_matching.py::pinyin_variants` 缺少“名首字母+姓”变体（王婉婷 → `wwang`），使 `wwang2026@hunnu.edu.cn` 这类高校常见个人邮箱被拒并落入无邮箱表。已按姓名自身派生补入该变体（非关键词黑名单），`office@`/`admin@`/`service@` 等公用邮箱仍被正确拒收；国际汉语文化学院重跑后邮箱数 0 → 1。
10. **回归防护网扩充**：新增 `tests/test_surname_prior_faculty_recall.py` 与 `tests/test_email_affinity_name_conventions.py`，锁死“罕见姓氏不得静默丢弃、零宽姓名必须归一、公用邮箱不得误收”三项契约；全量套件 109 passed / 1 skipped。同时将 `tests/test_browser_batch_tool.py` 医院夹具期望值更新为当前先验下的 119 候选（审计不变量不变：未解释卡片仍阻断交付）。
11. **原生 Chrome 兜底改为无头 + 测试离线隔离（现场故障修复）**：`server/browser_batch_tool.py` 的 `_fetch_page_via_native_chrome` 与 `_batch_fetch_profiles_via_native_chrome` 原先硬编码 `C:\Program Files\Google\Chrome\Application\chrome.exe` 且不带 `--headless`。当页面在给定浏览器上下文中抓取失败时（典型情形：夹具测试只提供名录页、无个人主页），工具会**弹出真实可见的 Chrome 窗口并联网访问生产站点**——测试套件因此对甘肃医院夹具域名 `www.lzsdyyy.cn` 的医生主页发起了上百次真实请求，单个测试耗时约 94 秒。现已为两处兜底加上 `--headless=new`，并新增环境变量 `HARVEST_DISABLE_NATIVE_CHROME_FALLBACK`（由新增的 `tests/conftest.py` 置位）使测试套件完全离线。修复后浏览器批量测试 18 项 11.8 秒、全量套件 15.3 秒（原约 125 秒），运行期间 chrome.exe 计数为 0；WAF 专用路径 `_setup_waf_proxy_if_needed`（仅 gszy.edu.cn 计划触发）仍按设计使用有头 Chrome 获取 genuine cookies，未改动。
12. **双轴代码复核（Standards / Spec）与随之而来的降级路径修复**：对本批次全部改动做独立复核时，Spec 轴发现一处**真实运行阻断**——`server/faculty_extractor.py` 把 `strip_invisible` 加进了那条带 `except ImportError` 的多名导入，而异常分支只重绑了另外七个名字，于是当算法层不可用时 `_clean_name` 会抛 `NameError`（改动前该降级路径是好的）。现已在异常分支内提供自包含兜底实现，并新增 `tests/test_l2_degraded_path.py` 在子进程中模拟算法层缺失、断言降级路径仍能清洗姓名并完成抽取（已用变异验证：删掉该名字即失败）。同时按复核意见收紧了姓氏先验：只保留现场证据确证的 9 个姓氏与不会作为常用词首字的正规姓氏，删除“宿/索/薄/韶/怀/劳/融/幸”等会误收常见词的条目，并新增精度断言（`宿舍/索引/薄膜/韶山` 等必须低于 p<0.5 门禁）；`HARVEST_DISABLE_NATIVE_CHROME_FALLBACK` 改为显式真值解析（`=0`/false/no/off 不再误判为启用），WAF 代理分支同样纳入该门禁；`name_prob` 与 `_collapse_han` 的重复归一化逻辑合并。复核后全量套件 111 passed / 1 skipped（14.8 秒），生产入口 `python -m server.browser_batch_tool` 实测 4/4 候选、delivery_ready=true，已交付的 238 条记录不依赖任何被删除的姓氏。
13. **附属医院医生专任在职教师聘任资格证据链判定 (ADR 0004)**：严格遵循《ADR 0004》，对附属医院数千名临床医生名录进行资格收录隔离，仅在有公开直接大学教学任职证据（如“甘肃中医药大学...教研室主任/教授”）时才收录入大学临床医学院，并在计划中规范声明已审核排除名单（`reviewed_exclusions`），确保全校教师数据的纯洁性与真实性。

---

## 九、 调度路径架构说明与现存缺口分析 (Execution Path & Technical Gap)

> **关于系统现存执行路径与真实技术边界的说明**：

1. **轨 A：IDE 子智能体与真实浏览器批量工具协作（目标标准工作流）**
   - **触发机制**：主控智能体在 Antigravity 会话中接收指令后认领工单。
   - **运行机制**：全部待采集学院立即各派发一个独立采集任务，不设项目级并发上限。Subagent 首先用宿主真实浏览器探索官网；之后按页面分支调用批准的 Playwright 批量工具、图片 OCR 与本地解析模块。工具返回访问证据和未解决项，Agent 负责补缺。
   - **交付标准**：各学院采集完成后，按大学合并导出双 Sheet 表格（`server/outputs/<大学名称>.xlsx`），一人不漏、守恒成立。

2. **轨 B：已停用的脚本主导浏览器路径**
   - **历史定位**：旧 `ide_dispatch_runner.py`、`run_single_task.py` 与 `app.py` 的服务器模式曾直接调用 `AutonomousBrowserAgent`；它内部使用 Playwright，但没有学院 Subagent 的页面判断与复核。
   - **当前状态**：旧采集入口已阻断；`auto_dispatcher.py` 仅只读巡检待办，不认领工单。Web 后台仅接受 IDE 派发模式。历史代码保留用于对照，不作为履约备用路径。

3. **轨 C：常驻队列消费者（服务器路径，目标生产形态）** ✅ **已打通**
   - **模块**：`server/agent_worker.py`（常驻）→ `server/agent_recon.py`（侦察段，模型与数据之间唯一闸门）。
   - **闭环**：`前端提单 → tasks.db(queued) → worker 取单 → 侦察(dsh + Playwright MCP) → plan JSON → 确定性采集 → 审计 → 交付门禁 → 大学级 Excel → 同步回前台`。
   - **与轨 A 的本质差异**：轨 A 依赖 IDE 会话在前台活动；轨 C 是**独立进程轮询本地队列**，不需要任何反向 Webhook，因此**在服务器上彻底消除了"外部 Web 请求无法通知 Agent"的物理断点**。
   - **启动**：
     ```bash
     python -m server.agent_worker            # 常驻（生产）
     python -m server.agent_worker --once     # 处理一个工单后退出（排障用）
     ```
   - ⚠️ **必须常驻**：`--once` 只处理一个工单就退出；用它跑生产会导致"提单后一直没人接单"。
   - **当时阻塞（2026-09-24，已由后续加固处理，见 CHANGELOG）**：两条首批真实工单均被交付门禁拦下（`roster_card_mismatch`），根因是 A3 硬编码姓氏表，
     详见 [`docs/audits/2026-09-24-five-perspective-review.md`](docs/audits/2026-09-24-five-perspective-review.md) §A3。

4. **Web 入队到 IDE 会话唤醒的真实技术缺口（仅对轨 A 成立）**
   - **客观现状**：前台同事在 Web 网页端（`http://localhost:8000`）点击提交工单后，任务仅写入本地 SQLite `tasks.db`。
   - **阻断真相**：Antigravity IDE 宿主平台**未向外部开放反向 Webhook / Socket 推送接口**以主动唤醒 IDE 会话中的 Agent。**文档绝对不能宣称仅靠修改提示词或 Markdown 规则就能打通这一被动唤醒链路**。
   - ⚠️ **此缺口仅约束轨 A**。轨 C（常驻 worker）是主动轮询，**不受此断点约束**。
   - **轨 A 的实际触发方式**：必须依赖：
     - **方式 1（人工触发）**：用户在 IDE 聊天窗口中明确发送指令（如“开始采集/处理工单”）；
     - **方式 2（会话内定时器）**：在已经开启并处于活动状态的 IDE 会话中，由主控智能体在前置轮次主动调用 IDE 原生 `schedule` 工具挂载定时轮询任务。如果当前会话关闭或未曾调用 `schedule`，Web 端提交的工单只能在数据库中排队，不会自动唤醒 IDE 智能体。

---

## 十、 生产服务器演进蓝图：DeepSeek API + Harness 原生即时派发架构 (Plan 2 Blueprint)

> **核心目标**：脱离本地 Antigravity IDE 部署到独立 Linux 云服务器时，通过 **DeepSeek API（全量 V4 级模型）+ 开源 DeepSeek Harness (dsh)**，实现 Web 端请求原生即时唤起真正的浏览器智能体，彻底摒弃死板脚本，实现 7×24 小时无人值守的高可用 Agent 生产闭环。
>
> **路线裁定：走 API，不走自托管。** 自托管 DeepSeek-V3 级模型需 8×80GB GPU（约 $300K 采购或 >£15K/月租金），盈亏平衡点约 176 亿 output token/月，远超本项目量级；而降级到蒸馏版小模型又会撞上工具调用可靠性下限（首次调用成功率约 87%，DeepSeek-R1 系列蒸馏版尤弱）。API 路线同时规避了这两个问题。

```
[前端同事在 Web 界面提交] (http://your-server:8000)
           │
           ▼
[FastAPI 后端接收 POST /api/collect]
           │
           ▼ (进程内原生调用，零延迟)
[DeepSeek Agent Harness 运行时底座]
   ├─ 注入 System Prompt: .agents/skills/faculty-browser-harvest/SKILL.md
   ├─ 注入黄金指令: "/browser \n【目标】负责【{学校} - {学院}】..."
   │
   ▼
[DeepSeek V4 决策主控 (Host Agent, via DeepSeek API)]
   │
   ├─ 下达标准化 Function Call: call_tool("playwright", "browser_navigate", ...)
   │
   ▼ (驱动工具集)
[标准 MCP 工具层与感知能力]
   ├─ 真实浏览器环境：Playwright MCP (点击、翻页、滚动、等待)
   ├─ CS 感知引擎：DOM 同构子树聚类 (STS) + CSS 盒模型语义断句
   ├─ 视觉降级流水线：本地 RapidOCR + 百家姓拓扑切词 (秒级解析图片展板)
   └─ 隔离与防污染：MECC 正文容器空间界限，阻断页脚公用邮箱
   │
   ▼
[双 Sheet 严格守恒交付]
   └─ 导出 server/outputs/<大学名称>.xlsx，同步回传供 Web 前端一键下载
```

### 资产 100% 复用与继承保障：
1. **技能协议无缝平移**：当前编写的 [`SKILL.md`](.agents/skills/faculty-browser-harvest/SKILL.md) 与 [`AGENTS.md`](AGENTS.md) 规则，直接作为 Agent 运行时的系统指令。
2. **工具层的真实状况**：`faculty_extractor.py`、RapidOCR 等是**本地 Python 模块**，由采集内核直接调用（不经过模型）；Agent 侧真正需要挂载的 MCP **只有 Playwright MCP**，用于浏览器侦察。这些模块**尚未封装为 MCP 工具**。
   ⚠️ **更正**：**不要走 dsh 自带的 experimental `browser-use` 提供方**——实测在 pip SDK 0.1.5rc1 与源码 0.1.7-rc.1 上**均静默失败**（Cordis 的 `inject` 有一项服务缺失即不激活：配置树组装成功、零报错，但工具就是不下发）。
   **正确做法**是用随 profile 交付的正式组件 `dsh-mcp-client` 直挂 Playwright MCP，见同目录的 `dsh-agent-runtime.cordis.yml`。
3. **摆脱 IDE 物理隔离**：在服务器架构下，FastAPI 与 Harness 在同一套应用服务体系内，彻底消除“外部 Web 请求无法通知 IDE 客户端”的物理断点。

### 当前落地状态（截至 2026-09-24 深夜，经代码级核查 + 端到端实测）

**服务器端端到端闭环已打通**：前台提单 → 常驻 worker 取单 → dsh 侦察 → 确定性采集 → 审计 → 落库。
**但当时真实工单尚不能自动交付**——被交付门禁按 A3 姓氏表缺陷正确拦下（见下；2026-09-24 可靠性加固后已处理，见 CHANGELOG）。

| 环节 | 状态 |
| :--- | :--- |
| Web 提单 → 入队（`server/app.py`） | ✅ 完整 |
| 认领 / 租约 / 续租围栏（`server/agent_queue.py`） | ✅ 完整 |
| 采集执行内核（`browser_batch_tool.py` → 浏览器 → Excel） | ✅ 完整 |
| 双 Sheet 守恒与大学级交付 | ✅ 完整 |
| **Agent 运行时（dsh + DeepSeek API）** | ✅ **已验证可用**（`finish_reason=completed`，真实完成信号） |
| **浏览器工具（Playwright MCP）** | ✅ **已验证可用**（经 `dsh-mcp-client`，24 个 `mcp__playwright__*` 工具到达模型） |
| **计划生成（侦察 → plan JSON）** | ✅ **已交付** `server/agent_recon.py`（双重门禁：`_validate_plan` + `check_plan_hygiene`） |
| **常驻队列消费者** | ✅ **已交付** `server/agent_worker.py`（`get_next_task()` 从零调用者变为有真实消费者） |
| **交付门禁（拒绝不完整的表）** | ✅ **正在正确工作**——两条真实工单均被其拦下 |
| **端到端自动交付** | ❌ **被 A3 姓氏表缺陷阻断**（见下） |
| **进程守护（systemd）** | ❌ 无。worker 目前需手工常驻 |
| **Linux 部署产物** | ❌ 无 Dockerfile / systemd / .sh |

#### 🔴 当时的头号阻塞项（2026-09-24，历史记录）：A3 硬编码姓氏表（`Opus/iso_mining.py: _SURNAMES`）

常驻 worker 首批两条真实工单**全部**卡在同一处：

| 工单 | 可见卡片 | 提取候选 | 被丢的人 |
| :--- | :---: | :---: | :--- |
| 湖南师范大学·国际汉语文化学院 `kzjs.htm` | 5 | 4 | 储诚志 |
| 湖南中医药大学·信息科学与工程学院 `szdw.htm` | 60 | 57 | 况玲 / 占艳 / 神孟秋 |

采集本身**跑完了**（分别提取出 27 人与 86 人），但对账门禁发现"可见卡片 > 提取候选"，
**正确地**拒绝交付——这正是红线 3「一人不漏」的守门行为。
（注意：被拦下的工单以 `failed` 落库**且不写入记录**——门禁是硬阻断，不是警告。）
根因是 `name_prob()` 的单字先验表对生僻姓打分过低，在 `p < 0.5` 处静默丢弃。

**性质**：不修它，服务器无人值守路线**一步都走不动**——采集能跑，交付永远被拦。
已试过 4 种修法全部失败并回退（见审计 A3）。**正解方向是把"同构兄弟的置信度"作为证据并入打分，而非继续调词法参数。**

**已交付的三个新模块**（详见 [`docs/superpowers/plans/2026-09-24-server-agent-runtime.md`](docs/superpowers/plans/2026-09-24-server-agent-runtime.md) §二·补二）：

| 模块 | 作用 |
| :--- | :--- |
| `server/agent_recon.py` | 模型与数据之间**唯一的闸门**；`check_plan_hygiene` 是红线 1 的可执行判据 |
| `server/agent_worker.py` | 常驻队列消费者，整条链在同一租约覆盖下 |
| `tests/test_agent_recon_hygiene.py` | 13 条测试，专测红线 1 卫生判据 |

**配置文件**：浏览器工具挂载见 [`.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml`](.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml)
（内含三个必须遵守的坑：用 `dsh-mcp-client` 而非 experimental 的 `browser-use`、`insert:` 包裹层必需、`--executable-path` 必须显式给）。

**迁移到 Linux 必须先处理的平台阻塞点**（均已经代码级核实）：

1. `server/browser_batch_tool.py:570`（`_setup_waf_proxy_if_needed`）硬编码 `C:\Program Files\Google\Chrome\Application\chrome.exe`，**无 `--headless`、无 `Path.is_file()` 守卫、不在 try/except 内**。Linux 上会**响亮抛出 `FileNotFoundError`**（可接受，租约会自动回收工单）。需改配置化路径 + 决定 Xvfb 还是 `--headless=new`（注意后者可能削弱绕 WAF 效果，须实测）。
   ⚠️ **精确化**：另两处 `:157`/`:194` **带 `--headless=new`**，且**在 Windows 上今天就已经是死的**——内部用 `sync_playwright()`，在 asyncio 事件循环内必抛错并被 `except Exception: pass` 吞掉，每次白白启停一个浏览器并阻塞事件循环 5 秒。**这是与平台无关的 bug，Linux 迁移不会让它更坏。**
2. `server/start_server.py:22,29,37` 使用 `netstat` / `taskkill` / `ipconfig`，Windows 专有；异常被吞掉，Linux 上端口清理与局域网 IP 探测**静默失效**。
3. `.agents/plugins/faculty_harvest_tools/mcp_config.json`（Antigravity 时代产物）全部为 Windows 绝对路径；dsh 时代改用同目录的 `dsh-agent-runtime.cordis.yml`。
4. `requirements.txt` **全是单边下界**（`>=`）——一次 `pip install` 可能拉到新主版本，**静默改变 DOM 解析行为**，而临床表现与"采集少了几个人"**一模一样**。上线前必须换 lockfile 或全量 `==`。
5. `psutil`（`requirements.txt:13`）为死依赖，全仓 0 处 import；`real_chrome_user_data*/`、`tmp_chrome_profile*/` 等目录为残留物，无需迁移。
6. Linux 需额外安装：Node.js（MCP server 运行时）、Playwright Chromium + 系统依赖、**CJK 字体**（缺字体将导致截图 OCR 整体失效）、Xvfb。

> **📋 另有 1 份审计记录**：五方对抗性审查挖出的**守恒级缺陷**（`single_page` 静默截断、`_validate_plan` 无法证明选择器、硬编码姓氏表已致 7 人漏采）与**红线 2 活体违规**（`dom_dehydrator.py` 的 `PUBLIC_EMAILS`、`is_noise_element` 的 `PRUNE_CLASSES`）见
> [`docs/audits/2026-09-24-five-perspective-review.md`](docs/audits/2026-09-24-five-perspective-review.md)。

---

## 十一、 当前阶段落地实现：IDE 内部工单巡检哨兵 (Plan 1 Implementation)

在当前尚未部署独立服务器 Harness 的研发阶段，若希望在 IDE 会话内部实现近实时的自动感知，可采用 **Antigravity 原生定时哨兵机制 (Cron Schedule Sentinel)**：

1. **运行前提与限制**：该机制要求当前 IDE 会话处于打开且活动状态，且主控已在会话中主动配置了定时任务，非独立后台常驻守护。
2. **闭环链路**：
   - 网页端录入任务 $\to$ 进入 `tasks.db` 排队；
   - IDE 内部 Cron 到点自动向主控智能体发送高优先级唤醒消息；
   - 主控智能体调用接口读取 `/api/queue/pending`；
   - 发现待处理工单后，对全部待采集学院立即一对一派发专属子智能体，不设项目级并发上限；
   - IDE 界面弹出 Subagent UI 卡片，持 `/browser` 展开真实浏览器采集；
   - 全量完成后按大学导出 `<大学名称>.xlsx` 交付总表。

---

## 十二、2026-09-28 图片承载个人简介的处理现状

本节记录较新的专项进展；上文标注“截至 2026-09-24”的状态属于当时快照，不能据此判断今天的图片正文能力。

- 已核实两类官网实例：湖南科技大学信息与电气工程学院连续 JPG 导师简介，以及西北民族大学舞蹈学院扫描 PDF 附件。样本、访问限制和证据见 [`docs/research/2026-09-28-image-only-faculty-examples.md`](docs/research/2026-09-28-image-only-faculty-examples.md)。
- 已接入 `server/image_biography.py`：在浏览器核实的正文范围内发现图片/PDF 引用，用浏览器上下文读取原件，本地解析 PDF 文字层或用 RapidOCR 逐图/逐页转写；保存原件、哈希、OCR 行及置信度，并把无法读取、身份归属不清、OCR 缺页和低置信的情况交给现有 `unresolved` 门禁。模型仍只负责结构侦察，不提取或改写教师字段。
- 批量流程已阻止“正文只有图片但 OCR 没有可归属文本”的静默交付；普通 HTML 简介中的附带 PDF 不会自动当成图片简历。后者若确有额外履历，需要人工核实正文范围。扫描 PDF 只有少量页码文字层但主体为整页图像时，仍执行本地 OCR。
- 湖南科技大学 45 图页面的只读全页试跑产生 45 个候选和 43 份图片证据，因 2 次资源访问失败、1 项 OCR 低置信及覆盖未核齐而正确阻止交付；两个失败资源后续单独重试取得，但未做整页重跑。西北民族大学附件在新建 Chromium 会话返回验证码 HTML，当前不自动绕过。两处均不可记为已完整采集。
- 回归验证：图片正文/范围/批量/验收测试 105 项通过；侦察、缓存、结构证据与 worker 交付测试 63 项通过。实现步骤及限制见 [`docs/superpowers/plans/2026-09-28-image-biography.md`](docs/superpowers/plans/2026-09-28-image-biography.md)。

**同日后续更新（人工复核前快照）**：上面的 43 份证据是历史试跑结果。本轮已对该官网页完成 45/45 张原图和 OCR 的真实浏览器复跑；重复运行时 45/45 OCR 命中经原件哈希校验的缓存，耗时 19.528 秒。当时还有 2 张图片各 1 行 OCR 置信度不足，审计仍挡住交付。本地逐行原图复核服务 `server/image_bio_review.py` 已接入批量流程，复核决定仅对同一原件 SHA、页、行、原 OCR 生效，原始 OCR 不覆盖。页面运行于 `http://127.0.0.1:8765/`，复核文件保存到该次 `scratch/image-bio-acceptance-rerun` 输出目录。服务关闭后可按 [`docs/superpowers/plans/2026-09-28-image-review-and-acceptance.md`](docs/superpowers/plans/2026-09-28-image-review-and-acceptance.md) 中的命令重启。另一所高校“可在新浏览器会话直接下载的扫描 PDF”仍未找到；西北民族大学原件的扫描形态已证实，但新会话受到验证码限制。专项新增测试 56 + 27 项通过。

**人工复核完成后的最新状态**：用户已确认 2 条 OCR 行，并在同一个本地页面从原图证据支持的 2 个邮箱中选择 1 个作为表格邮箱。选择由新增的 `image_bio_email_reviews.json` 保存并绑定本轮原件、OCR、教师归属和候选集合。最终真实浏览器复跑的 45/45 张图片全部归属，覆盖审计和双 Sheet 结构校验通过，`unresolved=0`，临时测试表 29+16=45 人，10 列，85 项相关测试通过。**这只验证图片采集机制**：来源是 2021 年导师介绍，专项计划手动设置的 `university_faculty_roster` 不能证明这些人都是当前专任教师；临时 Excel 留在 `scratch`，严禁作为湖南科技大学正式大学版交付。第二所可在新会话直接下载的扫描 PDF 样本仍未找到。
