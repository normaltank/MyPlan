> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行业务见 [CONTEXT.md](../../CONTEXT.md)，开发任务按 [AGENTS.md](../../AGENTS.md) 分工；使用原结论前核对代码版本、样本与原始证据。

# 五方对抗性审查发现（2026-09-24）

> **后续进展**：本审计保留发现时的代码与真实工单证据。2026-09-24 的可靠性加固已对 A1 增加有界分页探查、对 A2 的服务器模型计划要求 `card_selector` 并在采集对账时拦截缺失选择器、对 A3 增加同构卡片组证据及回归。离线锁定环境全量测试为 137 passed / 1 skipped；真实网站覆盖仍须重新验收。

> 由五个立场互异的审查者独立完成：交付工程师 / 平台架构师 / 红线守门人 / 运维与成本 / 根因怀疑者。
> 本文只收录**有代码证据、且经实测或多人独立确认**的发现。每条给出可核验的位置与修法。

---

## A. 守恒级缺陷（方向最危险，零争议）

### A1. `single_page` 是未经验证的断言 → 静默截断

- **位置**：`server/browser_batch_tool.py`，计划字段 `branches[].single_page`
- **问题**：`single_page: true` 是 Agent 的**断言**，没有任何机制证明它。若断言错误，采集会在第一页结束，
  记为 `end="agent_asserted_single_page"`——**无 `unresolved`、无报错、`delivery_ready` 仍为 true**。
- **为什么最危险**：这是当前设计里**唯一一条无报错的守恒漏洞**。双 Sheet 守恒只证明"候选人内部一人不漏地分了表"，
  **不证明"候选人就是全员"**。漏掉整页 → 守恒照样"成立" → 违反红线 3 且不可检测。
- **修法（怀疑者给出，零 token）**：**不要"识别"下一页，要"测试"下一页。**
  工具里已有验证器——`_capture_branch` 点击后调 `_wait_for_change`，用 `_roster_signature` 要求名录**真的变了**，
  否则报 `pagination_unchanged`。故可枚举候选控件（`a[rel=next]`、箭头/数字链接、末尾重复链接组），
  **接受第一个"点击后名录发生位移"的**。没有任何候选能改变名录，才允许 `single_page=true`。
  语义识别问题被降级为**带确定性验证器的搜索问题**。

### A2. `_validate_plan` 无法证明 `card_selector` 存在过

- **位置**：`server/browser_batch_tool.py:71-72`
  ```python
  if "card_selector" in branch and not str(branch["card_selector"]).strip():
      raise ValueError("card_selector must be non-empty")
  ```
- **问题**：对 roster 分支 `card_selector` **本身可选**；存在时**只校验非空**。
  校验了 URL scheme/host、`single_page`/`next_selector` 互斥、`max_pages` 范围——
  **没有任何一条能证明这个选择器在真实页面上存在过**。
  一份**全盘编造的计划可以带着 `_validate_plan: PASS` 通过**。
- **现状为何还算安全**：拦住它的是确定性内核抓回 0 个候选 → `no_candidates_on_roster_page` → `unresolved` → `delivery_ready=False`。
  **是内核拦住的，不是 `_validate_plan`。** 若哪天有人让 Agent 用浏览器自己抽取字段，这张安全网就没了。
- **修法**：增加一致性校验——**选择器命中元素数须等于聚类得出的卡片数**。

### A3. 硬编码姓氏表导致真实教师被静默丢弃

- **位置**：`Opus/iso_mining.py` 的 `_SURNAMES` 先验表 → 在 `server/faculty_extractor.py:266`
  ```python
  if p < 0.5:
      continue        # 静默丢弃
  ```
- **证据**：`scratch/hunnu/REVIEW_DIFF.md:31-36, 161` 记载 **7 位真实在职教师被丢**：
  `傅新球 / 粟壬波 / 邸宏霆`（历史文化学院）、`银海强 / 容明强 / 穰杰 / 禹琪芳`（生命科学学院）。
  **这正是本批次湖南师范大学历史文化学院工单卡在 `roster_card_mismatch` 的根因**
  （审计中 `unaccounted_cards: [{"index": 9, "text": "傅新球"}]`）。
- **为什么违反红线**：修复方式是"把名单加长"——与 `PUBLIC_EMAILS` 的 `szb@`/`jiaoxueke@` 逐站加词是**同一种反模式**。
  ADR 0003 宣称 "Completely eliminated 60+ keyword blacklist words"，而它换了个位置复发。
  失效模式正是红线 2 的立项理由，后果落在红线 3。
- **修法**：模块自己的 docstring 已给出正解——改用字符级模型或结构性判据，而非枚举表。

#### A3 已升级为**服务器自动化路线的头号阻塞项**（2026-09-24 晚实测更新）

常驻 worker（`server/agent_worker.py`）端到端跑通后，首批真实工单**全部卡死在同一道门禁**——
对账门禁（`roster_card_mismatch`）**正确地**发现"可见卡片数 > 提取候选数"并拒绝交付：

| 工单 | 分支 | 可见卡片 | 提取候选 | 被丢的人 |
| :--- | :--- | :---: | :---: | :--- |
| `3a488c5b` 湖南师范大学·国际汉语文化学院 | `icclc.hunnu.edu.cn/szdw/kzjs.htm` | 5 | 4 | **储诚志** |
| `ef2b648e` 湖南中医药大学·信息科学与工程学院 | `xxgc.hnucm.edu.cn/szdw.htm` | 60 | 57 | **况玲 / 占艳 / 神孟秋** |

实测新增 4 例（`况玲`/`占艳`/`神孟秋` 由 `roster_observations[].unaccounted_cards` 直接给出），
累计已知 **11 位真实在职教师**被静默丢弃。

**性质变化**：此前它的后果是"少几个人"；现在它是**自动交付的充分阻断**——
采集跑完却交付不了，工单以 `failed` 落库。**不修它，服务器无人值守路线一步都走不动。**

**已试过且失败的修法**（全部回退，勿重复踩）：

1. 把 `p < 0.5` 门禁移到聚类**之后**——仍无法区分，且破坏了「学院概况」类页面的精度。
2. `COHERENCE_LR = 3.0` 的组内一致性似然比加权——调参无法同时满足两个反例。
3. 用组内已置信成员推导先验（`confident / len(p_names)`）——对"整组都是生僻姓"无效。
4. 姓名长度一致性检查——无效。

**根因**：纯词法判据在此问题上**原则上不可分**。
`name_prob("储诚志") == name_prob("储蓄") == 0.08`——因为 `储` 之后跟什么字，单字先验表看不出来。
**正解方向**：把"同构兄弟的置信度"作为**证据**并入打分（聚类结构是已知正确的强信号），
而非继续在词法层调参。这是**结构性判据**，不违反红线 2。

**另一个已定位的叠加缺陷**：`_merge_same_sig` 丢弃一条单例会**虚增竞争组的效用**，
导致择优选中错误的组（实测：4 元组 `U=3.854` 打败含该人的 5 元组 `U=3.28`）。
即使词法门禁放宽，这个组选择缺陷仍会独立导致漏采。

---

## B. 红线 2 活体违规（投产代码路径上）

### B1. `dom_dehydrator.py` 的硬编码黑名单

- **位置**：`.agents/skills/faculty-browser-harvest/scripts/dom_dehydrator.py:13-16, 73`
  ```python
  TITLE_KEYWORDS = ['教授','副教授','讲师','助理教授','研究员',...]
  PUBLIC_EMAILS  = {'office@','dean@','admin@','info@','contact@','yjs@','news@',
                    'admission@','webmaster@','szb@','jiaoxueke@'}
  is_name = bool(re.match(r'^[一-龥]{2,4}$', text))   # ← 姓名形状正则
  ```
- **`PUBLIC_EMAILS` 是字面违反红线 2**，且比导航黑名单**更重**：
  - 它决定 `email` 字段的值（`:80`），错了就是**直接写错一个交付字段**
  - 用**子串匹配**：本地部分只要**包含** `office@`/`news@` 就被判为空，
    **一个真实个人邮箱会被静默写成 `""`**
  - `szb@`/`jiaoxueke@` 是逐站加词 → 正是 ADR 0003 描述的 "Keyword Blacklist Shithouse"
  - 项目里**已有合规替代**：`server/faculty_extractor.py:101-139` 的
    `is_personal_email_for_scholar`（拼音/命名约定亲和度，零关键词黑名单）
- **`TITLE_KEYWORDS` 当准入过滤用**（`:73-76`）会**静默丢弃超过 4 个汉字的姓名**（少数民族名、音译名）→ 踩红线 3。
  它还被当**数据字段赋值器**用（`:74/:83`）——职称不在封闭词表里就静默变成 `"教师"`，
  **用一个错误值冒充数据**。
- **该模块的状态**：**全仓无测试、无生产引用**（唯一外部引用是已被作废的度量脚本）。
  它不是"已投产的 CS 模块"——投产的是 `server/faculty_extractor.py` 与 `server/detail_mecc.py`。
- **修法**：**不要重构它，直接不调用它。** 若必须保留，先删掉上述三样。

### B2. `is_noise_element` 是生产正文路径上唯一会丢内容的黑名单

- **位置**：`server/faculty_extractor.py:81-85`（`PRUNE_CLASSES`）+ `:141-155`，
  在 `LayoutAwareTokenizer.get_clean_text` 的 `:546` 处生效
- **问题**：类名/id 含 `bottom`/`sidebar`/`copyright` 的**正文容器会被整块丢弃**
  （`header` 仅在同时含 profile/teacher/author/person/faculty 时豁免）
- **定位**：属 ADR 0003 结论与代码现状之间的**未记载落差**
- **修法**：要么补 ADR 说明这是"结构 chrome 语义"而非内容关键词，要么换成结构/几何判据

### B3. `CONTENT_SELECTORS` 硬编码选择器清单

- **位置**：`server/faculty_extractor.py:575-580`，与 `dom_dehydrator.py:33` **重复且不一致**
- **修法**：合并为单一事实来源

### B4. 死代码（不违规，但易误读）

- `server/faculty_extractor.py:95-99` 的 `FREE_MAIL_DOMAINS`：`:130-132` 两个分支都 `return True`，
  **当前无任何过滤效果**——是死代码，不是活体违规
- `.agents/skills/.../dom_dehydrator.py:38-45` 的 `clean_html_to_compact_url(html, max_chars)`：
  "暴力截断规则"，当前只因无调用方传 `max_chars` 而惰性存在

---

## C. 无人值守运维缺陷（7×24 上线前必办）

### C1. 没有 supervisor —— 这是真正的 7×24 缺口

- `server/ide_dispatch_runner.py:68` 第一句即 `raise RuntimeError("retired")`
- `server/auto_dispatcher.py` 只轮询打印，明确"不会运行旧脚本采集"
- `server/agent_queue.py:413, 421` 的 `get_next_task()` / `task_done()` 曾**零调用者**

> ✅ **2026-09-24 深夜更新：队列消费者已交付**（`server/agent_worker.py`，`get_next_task()` 已有真实消费者）。
> **但本条未完全关闭**——交付的是**消费者**，不是 **supervisor**：
> 进程崩了不会自动拉起、没有开机自启、没有日志落盘、没有并发上限。
> **剩余缺口**：systemd unit + 结构化日志 + 全局并发闸（即计划的 Phase 4）。

### C2. `harness.run()` 是同步阻塞调用 → 会击穿租约心跳

- **位置**：`server/browser_batch_tool.py:499` 的 `run_with_lease()`（60 秒心跳，**可直接复用**）
- **问题**：它只包住 `run_plan`，**侦察段完全没有租约覆盖**。
  再者 `harness.run()` 是同步阻塞的；若直接塞进 `run_with_lease` 的 `harvest` 参数，
  那个 `asyncio.create_task(heartbeat())`（`:523`）**一次都不会跑** → 300 秒后租约过期 → 重复认领 →
  两台 Chromium 同时打同一站点。
- **修法**：`await asyncio.to_thread(harness.run, ...)`，一行。但必须有人想到。

> ✅ **2026-09-24 深夜更新：已修复**。`server/agent_recon.py:reconnoiter()` 即 async，
> 内部用 `asyncio.to_thread(_run_harness_blocking, ...)` 卸走阻塞调用；
> `server/agent_worker.py` 的整条链（侦察+采集）都包在 `run_with_lease()` 之内，心跳全程有效。

### C3. 没有绝对最大运行时长 / 预算熔断

- 现状：没有最大步数、没有最大挂钟时长、没有花费上限
- 风险：卡死的 Chromium 会把工单**永久钉住**，队列吞吐静默归零，
  **且没有任何告警会响**——因为租约续得好好的，状态一直显示 `running`
- 另：饱和的模型循环约 **$15–20/小时**（实测 m03 ≈ 2.7 秒/步，1M 上下文）
- **修法**：绝对最大运行时长（建议 45 分钟）+ 每工单最大尝试次数 + 每会话最大步数 + 每日花费告警

> ⚠️ **2026-09-24 深夜更新：部分修复**。
> ✅ 绝对最大运行时长已实现（`agent_worker.py:DEFAULT_MAX_TASK_SECONDS = 45*60`，超时即判卡死并落库失败）。
> ❌ **每工单最大尝试次数、每会话最大步数、每日花费告警仍缺**。
> 另注意：`agent_worker` 明确**不自动重试**（失败即终态交人工），所以"无限重试"这一支已堵住，
> 但代价是没有退避重试能力。

### C4. `requirements.txt` 全是单边下界

- 全是 `>=`（`playwright>=1.42.0`、`pandas>=2.2.0`、`beautifulsoup4>=4.12.0` …）
- 风险：服务器上一次 `pip install` 可能拉到新主版本，**静默改变 DOM 解析行为**，
  而临床表现与"采集少了几个人"**一模一样**——最难查的一类故障
- **修法**：换 lockfile 或全量 `==` 固定

### C5. 死依赖与未使用导入

- `requirements.txt:13` 的 `psutil` —— **全仓 0 处 import**
- `server/app.py:17` 的 `BackgroundTasks` —— 导入后从未使用

### C6. `server/start_server.py` 在 Linux 上静默失效

- `:22` `netstat -ano` / `:29` `taskkill` / `:37` `ipconfig` + `encoding="gbk"`
- 全部被 `except Exception: pass` 吞掉 → 端口清理不工作、局域网 IP 错误打印为 `127.0.0.1`
- **修法**：改用 systemd `Restart=always` + `ExecStartPre` 清端口

### C7. `browser_batch_sync.py` 的回环硬锁

- `:113-115` 强制 sync 端点必须是 `127.0.0.1`/`localhost`/`::1`
- **含义**：worker **必须**与 web 同机。因此 Chromium 的 OOM kill 会**连带打掉前台同事的 Web 界面**
- **修法**：worker 放入独立 systemd unit 并设 `MemoryMax`

---

## D. 规模与成本（结论文档化，避免重复争论）

- **成本不是路线决策依据。** 100 学院量级下两条路线中位差距约 **$10**（C 中位 $0.49 / A 中位 $10.62）。
  差距对 output 定价不敏感（±2× 变动只让差距从 $9.86 变到 $10.68）。
- **真正的成本风险是重试循环**，不是单价。控制手段是熔断器，不是省单价。
- **峰谷计费的正确用法是排程，不是选路线**：高峰仅工作日 09:00–12:00 / 14:00–18:00（占全周 20.8%），
  其余半价。但真正的排程理由是**夜间高校站点负载低、WAF 触发概率小**，折扣只是搭便车。

---

## E. 审计方法学（值得记住的一条）

> **"模型看过页面"不构成独立通道。** 模型读的是**同一个 DOM**（还经过一层有损脱水），
> 只是**换了读者，不是换了方法**。测量学上，独立性来自**不相关的方法或不相关的来源**。

据此，项目里**真正独立**的计数来源只有三个：

1. `find_groups` 式纯结构分桶（不同特征、**无姓氏先验**）
2. 站点自报"共 N 条"
3. **截图 / OCR**（像素 ≠ DOM）——这正是图片名录路径**正当**的原因

而 `coverage_scope: supplied_roster_branches_only` 保护的只是**页内**，
**漏掉一整个分支时一个信号都不会发**——这才是最大的覆盖漏洞。
