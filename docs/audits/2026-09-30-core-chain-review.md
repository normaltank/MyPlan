> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行要求见 [项目目标](../../CONSTITUTION.md)，当前执行见 [改进计划](../improvement-plan.md)；使用原结论前核对代码版本、样本与原始证据。

# 采集内核调用链审查（2026-09-30）

> **续接结果（2026-10-01）**：本报告记录原始审查。修复及 Windows 验证见[续接验证报告](2026-09-30-core-chain-continuation.md)：事件循环阻塞与结构缓存已修；本报告提出的邮箱大小写截断因会破坏合法地址，已改成 DOM 字段边界证据。职称规则仍待用户决定，生产交付尚未执行。

基线：本机 `main` 的 `f5fa1a3`（远端同步合并后）。改动在分支 `claude/core-chain-review`，未合入 `main`，未部署。

本轮只用本地页面、合成用例和隔离数据库做测试。没有调用 DeepSeek，没有访问学校网站，也没有改动生产工单。对本机正式库只做了只读统计（SQLite `immutable` 模式），输出只有计数，不含姓名或邮箱。

用户额度将尽，按要求在完成架构梳理后收尾提交。已确认但未修的问题列在第四节。

## 一、实际调用链

以下按代码实际执行路径梳理，不按文档描述。

```
server/app.py  POST 提单 ─▶ server/agent_queue.py  tasks.db（租约、三要素围栏）
                                   │ claim_task
server/agent_worker.py  run_forever ─▶ _process ─▶ run_with_lease(_harvest_with_repair)
   │  侦察（唯一模型调用）：agent_recon.py → DeepSeek Harness + recon_browser_mcp.py（真实浏览器）
   │  产出：只含 URL / CSS 的计划 JSON（check_plan_hygiene）
   ▼
server/browser_batch_tool.py  run_plan（在 worker 的事件循环里执行）
   1 源页面   harvest_workflow.capture_sources → _capture_branch（翻页：_wait_for_change）
                                             → _discover_children（导航型名录）
   2 名录对账 harvest_workflow.reconcile_rosters
                ├ 有 card_selector：FacultyDirectoryExtractor._verified_card_candidates（逐卡）
                ├ 顿号名单：name_list.reconcile_name_list
                ├ 分段平铺：segmented_roster.reconcile_page
                └ 无选择器：FacultyDirectoryExtractor.extract
                     L1 runtime_l1（wrappers.db 缓存）→ L2 Opus/iso_mining + text_lists → 旧式结构聚类兜底
              faculty_identity.merge_same_name_observations（唯一同名即合并）
   3 个人页   capture_profiles（profile_structure 浏览器正文证明）→ resolve_broken_profile_aliases
              → harvest_eligibility.classify_candidates → _scope_captured_profiles
   4 图片     image_biography.audit_profile_media（本地 OCR，线程池）
   5 字段     FacultyHarvestPipeline.harvest_candidates
                ├ detail_mecc.extract_cluster（Opus/template_infer 跨页模板 → MECC 正文）
                ├ 邮箱：contact_email_candidates（标签/表格/mailto）→ is_personal_email_for_scholar（拼音）
                └ 共享多人页：_resolve_shared_page_candidates
   6 收尾     _enrich_extracted_records → identity_review → harvest_coverage.build_coverage_ledger
              → excel_export（学院表）
   ▼
agent_worker._sync → browser_batch_sync.sync_report → app /api/task/{id}/sync
   → agent_queue.complete_task（守恒、重复、身份检查）→ university_delivery.publish_delivery → excel_export（大学表）
```

邮箱归属的判断是整条链路共用的一个函数：`is_personal_email_for_scholar`（地址是否拼出该教师的姓名）。它用于以下场景：

- 个人页没有邮箱标签时
- 共享多人页
- 名录卡片
- OCR 图片简介

因此它的精度直接决定 Sheet 1 里邮箱的正确率。

## 二、确认的问题（按严重程度）

每项都给出证据、根因和改进方向。“证据”一律是可复现用例或本机只读统计。

### P1-1 测试会写入正式队列库（已修）

- **证据**：本机正式 `server/tasks.db` 的 `worker_selfcheck` 行是一次假的失败结果，内容为 `["[拼音] 缺少 pypinyin"]`，写入时间 2026-09-30 22:23:32，正是远端同步审查在 Windows 上跑全量测试的时刻。worker 离线时，网页会显示这条假的自检失败。另外：
  - `server/outputs/recon-evidence/standalone/` 留有测试产生的证据文件。
  - 云端克隆跑一遍全量测试后，会在 `server/` 下新建 `tasks.db` 和 `wrappers.db`。
- **根因**：以下几处都会回落到 `server/` 下的默认路径，而测试没有隔离：
  - `AgentTaskQueue()`，以及 worker `main()` 的 `--db` 默认值
  - `get_default_l1_executor()`：`FacultyDirectoryExtractor.extract` 未传 executor 时调用
  - MCP 子进程的证据目录
- **修复**：
  - 新增 `agent_queue.default_db_path()`：优先读 `HARVEST_TASK_DB`，否则用 `server/tasks.db`。`AgentTaskQueue`、`runtime_l1`（`wrappers.db` 放在队列库旁）、`browser_batch_sync` 都改用它。
  - 新增 `agent_recon.recon_evidence_root()`：读 `FACULTY_RECON_EVIDENCE_ROOT`，侦察证据的四处读写统一走这里。
  - `tests/conftest.py` 在导入任何 server 模块之前，把这两个环境变量强制指向临时目录。用赋值而不是 setdefault，防止运维 shell 里导出的正式路径漏进测试。
  - 生产默认路径不变。
- **回归**：`tests/test_production_isolation.py`（6 项，修复前全部失败）。全量跑完后，`server/` 下不再新增数据库文件。
- **本机遗留**：正式库里那条假的自检记录没有清理。下次正常启动 worker 时，自检会覆盖它。

### P1-2 邮箱“拼音归属”过宽，会把别人或办公室的邮箱静默交付（已修）

- **证据**：合成个人页中，下面 5 个都被静默放进 Sheet 1，`unresolved` 为空（`tests/test_email_affinity_precision.py`）：

  | 地址 | 教师 | 实际是谁的 |
  | --- | --- | --- |
  | `wanglei@` | 王芳 | 同姓他人 |
  | `jwc@` | 金伟 | 教务处 |
  | `zsb@` | 张三 | 招生办 |
  | `yjs@` | 杨杰 | 研究生院 |
  | `wangfei@` | 王芬 | 他人 |

- **根因**：
  - `_pinyin_match` 对所有 ≥4 字母的变体都接受“前缀＋任意后缀”。裸姓氏 `wang`、`zhang` 也在变体里，所以所有王姓地址都算王芳的。
  - `is_personal_email_for_scholar` 另外把“两位首字母＋任意后缀”也算作拼写。
- **修复**：
  - 只有拼出了完整名字的形式才允许带后缀，例如 `wangfanghnu`。
  - 名字被截断时，至少要有一个完整音节，例如 `wangxiaof` 可以对应王小芳。
  - 三字名的三位首字母可以带后缀，例如 `lsjwork` 对应刘圣洁。这条保留了既有测试的约定。
  - 裸姓氏、两位首字母、姓＋首字母等形式必须精确匹配，只忽略数字和分隔符。
  - `faculty_extractor` 不再保留一份重复实现，统一引用 `detail_mecc` 的版本。
- **效果**：不能证明归属的地址按既有规则进入 `ambiguous_email_assignment` 复核，不再静默交付。
- **影响面（本机只读统计）**：已交付的 385 个非空邮箱中，11 个只在旧的宽松规则下成立：
  - 多数是“姓氏＋其他字母”，有 2 个是“两位首字母＋后缀”，大多是免费邮箱。
  - 另有 1 个旧规则下成立的有本地快照，确认是标签字段，不受影响。
  - 其余 11 个都是早期工单，没有本地快照，无法判断对错。
  - 今后遇到同类情况，如果没有标签，会变成复核项而不是直接交付。这会增加复核量，但不放宽门禁。

### P1-3 CPU 密集的字段提取阻塞 worker 事件循环（未修）

- **证据**：
  - `run_plan` 在 worker 的事件循环里同步执行 `harvest_candidates`。
  - 没有正文选择器的个人页要做跨页模板推断，耗时随页数平方增长。合成 6KB 页面：50 页 5.9 秒，100 页 25.7 秒。
  - 性能剖析显示 69% 的时间花在反复构建兄弟页结构（`template_infer._Page`，60 页构建了 3600 次）。
  - 本机 20 份真实计划中有 5 份没有正文选择器，真实学院有 45–120 个个人页，页面比合成页大数倍。
- **风险**：阻塞期间 worker 心跳和租约续期都停住（租约 300 秒）。
  - 大学院有可能租约过期，整个学院的结果（包括已付费的侦察）作废。
  - 同进程的其他学院也会被卡住。
- **方向**：
  - 用 `asyncio.to_thread` 包住 `harvest_candidates` 和 `_enrich_extracted_records`。
  - 在 `extract_cluster` 里按页面缓存 `_Page`。输出完全相同，预计约快 3 倍。
  - 还没有实现和测试。

### P2-4 翻页判断依赖姓名挖掘，误报“翻页未变化”并触发付费补缺（已修）

- **证据**：第二页只有生僻姓氏教师（禤海燕、亓明珠，这类姓名按规则送人工复核）时，姓名签名为空。结果是：
  - 分支停在 `pagination_unchanged`，第二页没有采到。
  - 按 `recovery_policy`，这个状态码会送回付费的结构补缺，而选择器其实是对的。
- **修复**：有已核实的卡片或分段起点选择器时，`_wait_for_change` 改为比较卡片内容：文字、链接，无文字的卡片再加声明的图片地址。懒加载只改图片属性，不算翻页。没有选择器时，仍按原来的姓名签名判断。计划缓存复核也用同样的判断。
- **回归**：`tests/test_pagination_change_by_cards.py`：
  - 生僻姓氏页，修复前失败。
  - 只有页面其他部分变化的情况，仍判为未变化。
  - 懒加载图片的情况，仍判为未变化。

### P2-5 邮箱后紧跟英文标签时被粘连（已修）

- **证据**：`<span>Email: lihua@…edu.cn</span><span>Tel: …</span>` 提取成 `lihua@….edu.cnTel` 并交付。本机历史数据中没有出现，属于潜在问题。
- **根因**：内联元素之间没有块边界，而顶级域名的匹配是贪婪的，吞掉了后面的字母。
- **修复**：`text_lists.email_spans` 在顶级域名内出现“小写后接大写”或“大写后接大写再接小写”时截断。全大写的地址不受影响。截断后区间保持一致，因此标签证据仍然有效。
- **回归**：`tests/test_email_glued_label.py`。

### P2-6 职称以个人页中最早出现的职称词为准，会覆盖名录卡片上写明的职称（未修）

- **证据**：`resolve_title("曾任副教授，现任教授")` 得到“副教授”。而 `harvest_candidates` 中，只要个人页识别出任何职称，就覆盖卡片上的职称。
- **方向**：优先顺序改为：个人页带标签的“职称：”字段 > 卡片职称 > 正文中最早出现的职称词。修改前应先用本地快照回放，量化会改变多少条记录。

### P3（记录，未修）

- **L1 缓存实际处于闲置状态**：本机 `wrappers.db` 为空，项目里也没有安装缓存的入口，但每次调用 `extract` 仍会查询它。
- **`faculty_extractor` 里有一份重复的 `LayoutAwareTokenizer`**：它覆盖了从 `detail_mecc` 导入的版本。共享页卡片的文字提取因此不识别行内样式 `display:block`。
- **医院来源在 `profile_unavailable` 时不进入 `unresolved`**：学院显示完成，但大学级交付被资格状态挡住，前后状态不一致。
- **Excel 单元格上限**：Excel 单元格上限 32,767 字符。本机最长简介为 20,186 字，暂无超限；一旦超限，Excel 打开时会修复并截断。
- **历史数据**：9 月 23 日的 438 条记录中，`records.email` 列存成了字符串 `"None"`。交付用的 payload 是正确的，这一列没有被交付使用。
- **同一名录页上的无主页同名卡片**：按“唯一同名即合并”规则会被合并，但审计记录里没有留下这次合并。

## 三、验证

- 修改前，云端全量测试：799 passed、3 skipped。
- 修改后，云端全量测试（含 browser 标记）：842 passed、1 failed、3 skipped。失败的那一项是 `test_recon_cli_phase` 里写死的证据路径断言；按隔离目录修正后单独重跑通过，合计 843 passed。环境：Linux、Python 3.12、Playwright 1.47，用 chrome-headless-shell 141 代替 Chromium 1134。
- 本轮没有在 Windows 上重跑。`ruff check .` 与 `git diff --check` 通过；全量测试后 `server/` 下没有新增数据库，`server/outputs` 为空。
- 新增回归 4 个文件共 44 项，其中 20 项在修改前失败（隔离 6、邮箱归属 7、粘连 6、翻页 1）。

## 四、剩余事项与验证边界

1. 先修 P1-3（`to_thread` 和 `_Page` 缓存），再在本机用 `.runtime\dev-venv` 跑一次全量测试。
2. P2-6 先做本地快照回放统计，再决定职称的优先级。
3. 邮箱规则收紧后，建议回放已保存的学院页面，看新增的复核项数量。
4. 以上都是测试和只读统计，不代表实网采集完成。两张失败工单的状态没有变化。
