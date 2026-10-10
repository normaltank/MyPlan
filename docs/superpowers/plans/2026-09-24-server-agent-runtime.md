> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行要求见 [项目目标](../../../CONSTITUTION.md)，当前执行见 [改进计划](../../improvement-plan.md)；使用原结论前核对代码版本、样本与原始证据。

# 服务器端 Agent 运行时接入计划（dsh + DeepSeek API）

**目标：** 打通「Web 提单 → 后台直接唤起 Agent 采集 → 大学级交付」的闭环，替换掉对 Antigravity IDE 宿主的人工依赖。

**依据：** [AGENT_HANDOVER.md](../../../AGENT_HANDOVER.md) §十 Plan 2 蓝图（含 2026-09-24 代码级核查的落地状态表）、[AGENTS.md](../../../AGENTS.md) 红线 1（2026-09-24 修订版）。

**技术栈：** `deepseek-harness-sdk`（Python）、DeepSeek API、Playwright MCP、现有 `server/browser_batch_tool.py` 确定性内核。

**范围边界：** 本计划**不改动**红线 2（禁 AI 二次总结简介）与红线 3（双 Sheet 守恒）。模型只做侦察，不接触数据字段。

---

## 一、已确认事实（无需再验证）

| 事实 | 证据 |
| :--- | :--- |
| dsh 是库，不是 GUI 应用 → **"唤醒"问题从根上消失** | 官方 Python SDK 可直接 `harness.run()` |
| Python SDK 存在且无需系统 Node | `python/sdk/README.zh.md`；wheel 自带 `deepseek-harness-runtime-bin` |
| SDK 提供真实完成信号 | `RunResult.finish_reason` ∈ `completed` / `max-tokens` / `error` |
| SDK 保留 subagent 谱系 | `RunResult.notifications` 含根会话与已知后代 |
| 本地已有 dsh 源码 v0.1.7-rc.1 | `D:\Agent Workspace\VSC workshop\deepseek-harness`，git HEAD `46a7f68b09` |
| 本机 Node v22.22.1 / pnpm 11.22.0 | 满足 dsh `engines: ^22.19.0 \|\| >=24.0.0` |
| 链路两端已完整 | Web 入队、租约围栏、采集内核、双 Sheet 交付均可用 |

## 二、未知数台账（2026-09-24 实地核查后更新）

### 已验证（无需再测）

**① profile 选择 —— 已定论，且发现重大缺口**

用 `dsh --profile <name> --dump-default-config` 实测三个 profile 的插件树：

| Profile | subagent | browser | skill | fs |
| :--- | :---: | :---: | :---: | :---: |
| `sdk` | **17** | **0** | ✅ | ✅ |
| `sdk-minimal` | 0 | 0 | ❌ | ❌ |
| `web` | 21 | 0 | ✅ | ✅ |

- **`sdk-minimal` 确认不可用**（无 subagent、无文件系统工具），与文档一致。
- **`sdk` 含 subagent / skill / fs / bash / workflow，是生产正确选择。**
- ⚠️ **但没有任何 profile 默认带浏览器。**（`web` 里命中的那条 `browser` 只是 `openBrowser` 启动项，不是 browser-use。）SDK 文档称 Python 客户端不能选 `web` profile，故**必须给 `sdk` profile 显式挂载浏览器插件**。

**② 浏览器接入方式 —— 已定位到具体包与配置**

> 🚫 **以下这段是当时的错误线索，已作废——不要照抄。** 实测该路径**静默失效**。
> 正确配置见「二·补」与 [`.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml`](../../../.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml)。保留在此仅供排障时识别"又踩回老路"。

包：`@deepseek-ai/dsh-experimental-browser-use-playwright-mcp`（v0.1.7-rc.1，实验状态，仅显式挂载后启用），依赖上游 `@playwright/mcp@0.0.80`。

```yaml
- name: '@deepseek-ai/dsh-browser-use'
- name: '@deepseek-ai/dsh-experimental-browser-use-playwright-mcp'
  config:
    mode: launch          # 或 attach
    headless: true
    executablePath: ...   # 可选，指向已有 Chromium
    toolCallTimeoutMs: ...
```

该包**为每个活动 Session 保持独立浏览器状态**——与「一学院一 Session/Subagent」架构天然对齐。

**③ `mode: attach` 可能直接解决瑞数 WAF 难题**

`attach` 模式可接入**已有浏览器**的 HTTP(S) 调试端点或 WS 端点，并使用其现有标签页与登录状态。这意味着 Phase 3 里那个"绕 WAF 靠 subprocess 拉起 headed Chrome"的 Windows 专有实现，**可能可以整体替换为 attach 到 Xvfb 下的 Chromium**。需在 Phase 3 验证。

**④ CLI 存在原生一次性模式**

`dsh --profile headless "任务描述"` —— 跑一个任务、打印结果、退出。可作为 SDK 之外的最简集成路径或排障手段。

### 仍待验证

1. ~~**模型 ID**~~ → **已核实**。API 返回的实际可用模型只有两个：
   **`deepseek-flash`**（DeepSeek-V4.1-Flash，1M 上下文 / 384K 输出，**支持图片输入**）
   与 **`deepseek-v4-pro`**（DeepSeek-V4-Pro，纯文本）。
   **SDK 文档示例里的 `deepseek-v4-flash` 是错的，该 ID 不存在**；`deepseek-chat` / `deepseek-reasoner` 也已退役。
   `effort` 档位为 `low` / `high` / `max`（默认 `high`）。
   ⚠️ `deepseek-flash` 支持图片输入，但**不得用于图片名录**——海报上的人名属红线 1 禁止出境的数据字段，继续走本地 RapidOCR。
2. ~~**`@playwright/mcp` 是否需独立 Node 运行时**~~ → **是**。MCP server 是 Node 进程（配置里 `command: node`），服务器必须装 Node.js。
3. ~~**`sdk` profile + 浏览器能否产出 plan JSON**~~ → **已打通**（见「二·补」）。
4. **能否加载项目自身的 `SKILL.md`**：`sdk` profile 含 `skill` / `skill-filesystem` / `tool-skill`，机制具备；但**目录约定是否匹配 `.agents/skills/` 仍需实测**。
5. **`dsh --profile headless "任务"` 一次性模式**：已确认存在，但**未实测**；可作为 SDK 之外的最简集成路径或排障手段。
6. **瑞数等 WAF 站点**：`--headless` 下是否被识别、是否需要 Xvfb + headed，**未测**。

### 版本偏差提示

pip 装的 SDK 是 **0.1.5rc1**，本地源码仓库是 **0.1.7-rc.1**。**两个版本号不一致**，排障时需先确认现象属于哪一侧。

## 二·补　Phase 0 实测结论（2026-09-24，浏览器链路**已打通**）

### ✅ 最终结论

**用 dsh 随 profile 交付的正式组件 `dsh-mcp-client` 直接挂 Playwright MCP，绕开 experimental 的 `browser-use` 提供方。**

M0.6 实测（真实站点 `https://icclc.hunnu.edu.cn/szdw/js.htm`）：
`finish_reason=completed`，耗时 **28.7s**，分支覆盖 **3/3 命中**，`_validate_plan` **PASS**，
模型实际调用 `mcp__playwright__*` 工具（24 个），产出含 `card_selector` 的有效计划。

模型还**多找到一个分支** `kzjs.htm`（客座教授）——人工计划只有 3 条。侦察层报出候选、资格层决定去留，
这个分工是**正确**的（CONTEXT.md 排除"无高校教学编制的兼职/客座/讲座教授"）。

### 修复链条（三环，全是配置层，**未改一行 dsh 代码**）

| # | 症状 | 根因 | 修法 |
| :--- | :--- | :--- | :--- |
| 1 | 浏览器工具静默不下发 | experimental `browser-use` 提供方不激活（`inject=['browserUse','agents','tools','systemPrompt']`，Cordis 下任一服务缺失即静默） | **改用正式组件 `dsh-mcp-client`** |
| 2 | `patch: entry "mcp-playwright" not found` | README 的"最小配置"片段**省略了 `insert:` 包裹层**，照抄会被当成"覆盖已存在条目" | 补上 `- insert:` 包裹层 |
| 3 | `Browser "chrome-for-testing" is not installed`（期待 chromium-1243，本机只有 1134/1187） | `@playwright/mcp` 期望的浏览器版本与本地缓存不匹配 | **`--executable-path` 显式指路** |

配置固化于 [`.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml`](../../../.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml)。

### ⚠️ 对早前判断的三处更正（重要）

**更正一：`web_fetch` 全域失败不是 dsh 缺陷。** 本机 DNS 处于**代理工具的 fake-IP 模式**——
所有域名解析到 `198.18.0.0/15`（RFC 2544 保留段）。dsh 的安全守卫"拒绝非公网 IP"**行为完全正确**。
服务器上是正常 DNS，此现象**自然消失**。（旁证：`server/start_server.py` 的 `get_real_lan_ip()` 本就排除 `198.18.`。）

**更正二：`pwsh` 不可用不是 dsh 缺陷。** 本机**未安装 PowerShell 7**（`pwsh` 不在 PATH）；
dsh 的 profile 配置为 win32 启用 `tool-pwsh`、非 win32 启用 `tool-bash`。
Linux 服务器上自动使用 `bash`，此现象**自然消失**。

**更正三：Chrome 硬编码的描述需精确化。** 三处硬编码中 `:162`/`:205` **带 `--headless=new`**；
**只有 `:570`（`_setup_waf_proxy_if_needed`）没有**，且无 `Path.is_file()` 守卫、不在 try/except 内——
Linux 上会**响亮抛出 `FileNotFoundError`**（可接受，租约会自动回收工单）。
另两处（`_fetch_page_via_native_chrome` / `_batch_fetch_profiles_via_native_chrome`）**在 Windows 上今天就已经是死的**：
内部用 `sync_playwright()`，在 asyncio 事件循环内必抛错并被 `except Exception: pass` 吞掉——
每次白白启停一个浏览器并阻塞事件循环 5 秒。这是**与平台无关的 bug**，Linux 迁移不会让它更坏。
正确修法是改成 `async_playwright`（`:579` 就是正确写法）或显式删除。

### ⚠️ 两处需要作废的早期判读

**作废一：M0.3 的 "browser tool use = True"。** 那是**假阳性**——对事件字符串的宽松正则匹配。
该轮实际是**在没有浏览器的情况下**靠 `web_fetch`（对 localhost 可用）拿到 fixture 内容的。
另外 `fixture/roster1.html` 是**合成页面**：除导航外**没有竞争性重复组**，偏袒被测模型。
真实页面（如生命科学学院 143 人那批）有多个并列重复组。M0.3 的结论含金量需打折。

**作废二：早前的"压缩 91.2%"成本数字。** `measure_context.py` 量的是 `extract_faculty_candidates` 的输出——
那是含 **name / title / email / url 四个交付字段**的候选列表，**不是结构骨架**。
标尺本身用错了对象，该数字不得用于任何论证。

### 路线 C 的重新评估

浏览器链路打通后，**路线 C 不再是必需的绕行**。但五方独立审查（见下节）查出的问题**与路线选择无关，依然成立**：

- **`dom_dehydrator.py` 的 `PUBLIC_EMAILS` 是字面违反红线 2**：硬编码邮箱黑名单，且用**子串匹配**
  （本地部分只要**包含** `office@`/`news@` 就被判为空，真实个人邮箱会被静默写成 `""`）；
  `szb@`/`jiaoxueke@` 属逐站加词，正是 ADR 0003 描述的 "Keyword Blacklist Shithouse" 复发。
  项目里已有合规替代：`server/faculty_extractor.py:101-139` 的 `is_personal_email_for_scholar`（拼音亲和度，零关键词黑名单）。
- **`TITLE_KEYWORDS` 当准入过滤用**（`re.match(r'^[一-龥]{2,4}$')` + 职称白名单）会
  **静默丢弃超过 4 个汉字的姓名**（少数民族名、音译名）→ 直接踩红线 3「一人不漏」。
- `dom_dehydrator.py` **全仓无测试、无生产引用**，唯一外部引用就是那个度量脚本。

**若将来仍需路线 C**（例如 dsh 版本风险不可接受），**前置条件**：
新增 ADR 定义「模型可见工件契约」+ 建**可机械证明的泄漏门禁**（骨架文件所有字符串值 ∩ 页面快照文本节点 = ∅），
且**不得复用 `dom_dehydrator`**——应新写一个 `structure_only` 导出器，落点在 `server/` 并配 TDD。

---

## 二·补二　Phase 1–2 已交付：端到端闭环打通（2026-09-24 晚）

### ✅ 交付物

| 模块 | 作用 |
| :--- | :--- |
| `server/agent_recon.py` | **模型与数据之间唯一的闸门**。内部用 `asyncio.to_thread` 卸走阻塞的 `harness.run()`（否则 300s 租约会被冻结的心跳击穿）；产出的 plan 过**双重门禁**：`_validate_plan`（schema）+ `check_plan_hygiene`（红线 1） |
| `server/agent_worker.py` | 常驻队列消费者。整条链在**同一条租约**下：取单 → 侦察 → 采集 → 同步 → 落库 |
| `tests/test_agent_recon_hygiene.py` | 13 条测试，专测红线 1 卫生判据 |

**红线 1 的可执行判据**（`check_plan_hygiene`）：
**CSS 选择器不可能包含 CJK 字符**——故计划 JSON 里任何含 CJK 的字符串，
除非整串等于目标学校/学院名，一律判定为数据出境。另配一条邮箱正则覆盖纯 ASCII 的邮箱。
（测试期间正是这条邮箱判据抓出了 CJK 判据的漏洞：`zhangwei@hunnu.edu.cn` 能穿过 CJK 检查。）

**用法**：

```bash
python -m server.agent_worker            # 常驻（生产）
python -m server.agent_worker --once     # 处理一个工单后退出（排障用）
```

### ✅ 端到端实测（真实工单，非 fixture）

```
前端提单 → tasks.db(queued) → worker 取单 → 侦察(dsh + Playwright MCP)
        → plan JSON → 确定性采集 → 审计 → 交付门禁
```

| 工单 | 侦察 | 采集 | 结果 |
| :--- | :--- | :--- | :--- |
| `3a488c5b` 湖南师范大学·国际汉语文化学院 | 33.2s，4 分支 | 40s，total=27 / with_email=1 | ❌ 交付门禁拦截 |
| `ef2b648e` 湖南中医药大学·信息科学与工程学院 | ✅ | total=86 / with_email=5 / no_email=81 | ❌ 交付门禁拦截 |

**两条都被同一道门禁拦下**：`unresolved=[{status: roster_card_mismatch}]`。
**这是门禁在正确工作**——它发现"可见卡片数 > 提取候选数"，拒绝交付不完整的表。
拦下的根因是 A3（硬编码姓氏表），**不是**本阶段新写的代码。
详见 [`docs/audits/2026-09-24-five-perspective-review.md`](../../audits/2026-09-24-five-perspective-review.md) §A3。

### ⚠️ 五个必须知道的坑（全部已在代码注释中固化）

1. **`harness.run()` 是同步阻塞的**。直接在事件循环里调 → 60s 心跳一次都不触发 → 300s 后租约过期 →
   第二个 worker 合法认领同一学院 → **两台 Chromium 同时打同一站点**。
2. **`session_id` 必须 ASCII 安全**。实测用中文学校/学院名拼出的 session_id 会让整轮以
   `finish_reason='error'` 结束，**且会话根本不被创建**（dsh storages 下无痕迹）。现用 sha256 短哈希。
3. **`_mark_failed` 绝不修改内存里的 `task.status`**。改成 `"failed"` 会让 `task_done()`
   二次调用 `sync_task_result`，而 `lease_token` 已被清空 → 必撞三要素围栏抛 `LeaseConflictError`。
   保持 `"running"`，`task_done()` 自然 no-op。
4. **失败必须显式落库**。不标记失败 → 任务停在 `running` → 租约过期后被反复重领 → 无限重试。
5. **worker 必须常驻**。`--once` 只处理一个工单就退出，Web 端后续提单**不会有人接**
   （这正是"前端提单后一直没人接单"的原因）。生产上需 systemd 守护（Phase 4）。

---

## 三、Phase 0：最小验证（先跑这条）

**设计原则：先隔离变量，再接触真实站点。** 用本地 fixture 把「dsh 能力问题」与「站点/网络问题」分开——若 M0.3 失败就是 dsh 的问题；若 M0.3 过而 M0.4 挂，就是站点的问题。

工作目录：`scratch/dsh-validation/`

### M0.1 环境与凭据核对

- 确认 `DEEPSEEK_API_KEY`（**当前未设置**，为本阶段唯一外部阻塞项）
- `python -m pip install deepseek-harness-sdk`
- 查实际可用模型 ID 并记录
- 建隔离的 `dsh_home`（**绝不复用 `~/.dsh`**）

### M0.2 SDK 冒烟测试（无浏览器）

```
DeepSeekHarness(dsh_home=..., cwd=..., provider="deepseek-official", model=<M0.1 确认的 ID>)
    .run("Say hi.", session_id="smoke-001")
```

**验收：** 进程能启动；`finish_reason == "completed"`；`final_response` 非空。

### M0.2b 给 `sdk` profile 挂载浏览器能力

**这一步不能跳过**——实测确认 `sdk` profile 默认无任何浏览器 provider。

> ⚠️ **不要走 experimental 的 `browser-use` 提供方**（`@deepseek-ai/dsh-browser-use` +
> `@deepseek-ai/dsh-experimental-browser-use-playwright-mcp`）。它声明
> `inject=['browserUse','agents','tools','systemPrompt']`，Cordis 机制下任一服务缺失即
> **静默不激活**——配置树组装成功、无任何报错，但工具就是不下发。
> 实测在 pip SDK 0.1.5rc1 与源码 0.1.7-rc.1 上**均失败**。

**正确做法：用随 profile 交付的正式组件 `dsh-mcp-client` 直接挂 Playwright MCP。**
完整配置见 [`.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml`](../../../.agents/plugins/faculty_harvest_tools/dsh-agent-runtime.cordis.yml)，要点三条：

```yaml
- insert:                                     # ← 包裹层必需，缺了报 entry not found
    - id: mcp-playwright
      name: '@deepseek-ai/dsh-mcp-client'     # ← 正式组件，非 experimental
      config:
        serverName: playwright
        transport: stdio
        command: node
        args: [<cli.js 路径>, '--isolated', '--browser', 'chromium',
               '--executable-path', <chrome 路径>, '--headless']   # ← 必须显式给
        failOnStartupError: true              # ← 让失败响亮而非静默
```

**验收：** 模型工具清单中出现 `mcp__playwright__browser_navigate` 等 24 个工具（**不是** `mcp__playwright-mcp__`）。

### M0.3 本地 fixture 侦察测试（不碰真实网站）

本地起一个静态学院名录页（含多张教师卡片 + 一个「下一页」控件 + 若干导航噪声链接），让 dsh 侦察并产出 plan JSON。

**验收（硬标准）：** 产出的 JSON 能通过 `server/browser_batch_tool.py` 的 `_validate_plan`，即：

- `branches[].url` 存在
- 每个分支**恰有** `single_page` 或 `next_selector` 之一（不得同时给或都不给）
- 每个分支有 `card_selector`
- `max_pages` 在 1–100
- 顶层 `school` / `college` / `source_kind` 正确

**同时记录：** 它认出的 `card_selector` 是否真的命中卡片（而非导航）。

### M0.4 真实学院侦察测试（非 WAF 站点）

目标：湖南师范大学国际汉语文化学院 `https://icclc.hunnu.edu.cn/szdw/js.htm` —— 三个分支均为单页，结构简单，**且已有人工核实的正确答案可对账**（见 `server/outputs/plan_国际汉语文化学院.json`）。

**验收：** plan JSON 通过校验；**与人工资核实的选择器做对比**（`div.list_container ul.teacher > li`）。

> ✅ **已于 2026-09-24 以 M0.6 名义执行并通过**（浏览器链路打通后）。实测结果：
> 28.7s / `finish_reason=completed` / **分支覆盖 3/3** / `_validate_plan` PASS。
> 模型**多找到一个分支** `kzjs.htm`（客座教授）——人工计划只有 3 条；
> 侦察层报出候选、资格层决定去留，这个分工是正确的。
> 模型给出的选择器是 `ul.teacher > li`，**比人工资核实的 `div.list_container ul.teacher > li` 少一层锚定**
> ——功能等价但锚定更弱，正是 A2（`_validate_plan` 无法证明选择器）要防的那类问题。

**记录：** 实际 token 消耗、耗时、模型给出的选择器是否与人工资核实一致。

### M0.5 结论汇总

产出一份实测报告：五项未知数逐条判定「成立 / 不成立 / 需更多测试」，并给出实际 token 与耗时数字。**不做任何生产集成。**

### M0 明确不覆盖

瑞数 WAF 站点、图片名录 OCR、Linux 平台差异、并发与租约续期。

---

## 四、Phase 1：侦察段接线 ✅ 已完成

> **状态**：`server/agent_recon.py` 已交付并端到端验证（见「二·补二」）。

**新建** `server/agent_recon.py`：

- 小接口 `reconnoiter(school, college, url, *, dsh_home, session_id) -> plan_dict`
- 隐藏的复杂度：dsh 生命周期、profile 选择、prompt 组装（注入 `SKILL.md` + 黄金指令）、超时、`finish_reason` 判定、JSON 提取与 `_validate_plan` 校验、失败重试
- 只回传 plan JSON 与紧凑诊断，**完整页面与简介不进主控上下文**（延续现有审计原则）

**关键约束：** 本模块是模型与数据之间唯一的闸门。它必须保证输出里**只有 URL 与选择器**，不含任何学者姓名、职称、邮箱、简介。

## 五、Phase 2：队列消费者 ✅ 已完成

> **状态**：`server/agent_worker.py` 已交付并端到端验证（见「二·补二」）。
> `agent_queue.get_next_task()` / `task_done()` 从「零调用者」变为有真实消费者。

**新建** `server/agent_worker.py`，接线现有的死代码骨架 `agent_queue.get_next_task()` / `task_done()`（`server/agent_queue.py:413-431`）。

编排：`claim → reconnoiter → browser_batch_tool → browser_batch_sync`

**必须处理：**

- **租约续期**：租约仅 300s，而侦察耗时可能更久。必须在 dsh 运行**期间**持续续租，不能等它跑完。
- **失败语义**：`finish_reason != "completed"` 或 plan 校验失败 → 任务标记失败并同步错误载荷（复用 `sync_task_result` 失败分支的三要素围栏）。
- **不信任 Agent 的完成声明**：仍以 `delivery_ready=true` 且 `unresolved=[]` 为交付门禁。

## 六、Phase 3：平台解耦（Linux 迁移）

**新建** `server/platform_paths.py` 或等价配置层，集中处理：

| 阻塞点 | 位置 | 处理 |
| :--- | :--- | :--- |
| 硬编码 Chrome 路径 + 无 `--headless` | `browser_batch_tool.py:154,186,561` | 配置化 + Linux 路径 + Xvfb |
| `netstat`/`taskkill`/`ipconfig` | `start_server.py:22,29,37` | 改为跨平台实现 |
| MCP 配置全 Windows 路径 | `.agents/plugins/faculty_harvest_tools/mcp_config.json` | 重写为 Linux 版 |
| asyncio 内调 `sync_playwright()` | `browser_batch_tool.py:169,206` | 改为 async API（否则 Linux 上由静默降级变抛错） |
| 死依赖 | `requirements.txt:13` `psutil` | 移除 |
| 系统依赖 | — | Playwright Chromium + 系统库、**CJK 字体**、Xvfb、Node.js（若 M0.3 证明 MCP 需要） |

## 七、Phase 4：守护与部署

- systemd unit（替代两个 `.bat`）
- 结构化日志（替代 IDE 界面观战）
- 全局并发上限（当前只有租约与两处 `Semaphore`，无全局闸）
- 离线批量排程（**利用 DeepSeek 峰谷计费：北京时间工作日 09:00–12:00 / 14:00–18:00 为高峰，双倍计价；夜间批量减半**）

## 八、风险与回退

| 风险 | 缓解 |
| :--- | :--- |
| dsh 是 v0.1.7-rc.1，**官方声明会有破坏性变更** | 隔离策略：dsh 只做侦察，确定性内核保持独立。dsh 换代不影响数据管道 |
| Agent 侦察质量不稳定 → 漏采 | 保留人工复核入口；`delivery_ready` 门禁不放宽；M0.4 用已知答案对账 |
| 模型 ID / 定价漂移 | 不把模型 ID 与费率写成硬编码常量，走配置 |
| WAF 站点 headless 被识别 | Phase 3 引入 Xvfb + headed；Phase 0 不覆盖此场景 |

## 九、验收总纲

任一 Phase 完成后必须给出**实测证据**（命令、输出、数字），不得以「应该可以」结项。延续项目既有原则：**不谎报覆盖与节省**。
