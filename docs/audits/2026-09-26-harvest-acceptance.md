> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行要求见 [项目目标](../../CONSTITUTION.md)，当前执行见 [改进计划](../improvement-plan.md)；使用原结论前核对代码版本、样本与原始证据。

# 离线教师采集验收集 · 2026-09-26

## 样本与证据范围

本集包含 **10 个合成结构场景、1 个真实保存名录页场景**，对应 11 个测试。合成场景没有真实学校、教师身份或浏览器侦察证据；所有姓名与邮箱仅用于确定性测试，`acceptance.example.edu` 是测试入口。JSON 是离线测试输入，包含固定教师预期，**不是**模型侦察产出或可提交的线上结构计划。

真实样本为已有 `scratch/harness-baseline-output/湖南中医药大学_信息科学与工程学院_snapshots/directory_001_39b65de1d21c.html` 的原字节复制，入口 `https://xxgc.hnucm.edu.cn/szdw.htm`。`real_source.json` 保存来源路径、SHA-256 和固定 60 个姓名/个人主页身份。参考集合来自直接查看保存 DOM 中 `ul.list-name > li` 的锚文本及链接后固定转录，未调用采集实现生成预期，未使用历史 records.json 作为人数或身份真值。

真实断言仅核对这一保存页的 60 个卡片、60 个固定候选身份及无未解释卡片。**不代表**已经人工验证 10–20 所真实学院，不证明该学院全量分支、实时页面、教师资格、邮箱正确性或完整履历；保存页的采集时间未单独核实。完整 89 人/4 页真实学院重放属于集成阶段的另一验收范围。

## 固定契约

每个合成样本预先固定人数、邮箱/无邮箱人数、身份集合、邮箱归属及可交付状态。测试同时核对标准十列、两 Sheet 行数和身份互斥；阻断场景不得导出 Excel。预期不从 `run_plan`、提取器或其报告计算，失败时不修改真值以适配实现。

| 场景 | 固定总人数 | 有/无邮箱 | 完整交付预期 | 重点 |
| --- | ---: | --- | --- | --- |
| ordinary_full_biography | 3 | 2 / 1 | 可交付 | 每人四段完整履历，保留末段，无导航页脚 |
| profileless | 3 | 0 / 3 | 可交付 | 无个人主页的教师仍守恒 |
| homonyms_distinct_profiles | 3 | 3 / 0 | 可交付 | 同名不同主页与邮箱不可合并 |
| shared_detail | 3 | 3 / 0 | 可交付 | 同一详情页三人分别归属邮箱和简介 |
| table_roster | 3 | 3 / 0 | 可交付 | 表格行对应候选教师 |
| hospital_appointment | 1 | 1 / 0 | 可交付 | 两候选，一项现任高校资格证据，一项已复核排除 |
| pagination_url_changes | 6 | 6 / 0 | 可交付 | 两页名录，翻页后 URL 改变 |
| ajax_same_url | 6 | 6 / 0 | 可交付 | 两个 DOM 状态共用一个 URL |
| failed_profile | 2 | 2 / 0 | 阻断 | 第三人主页失败；已成功记录仍保留，失败不可落无邮箱表 |
| unreviewed_poster | 1 | 0 / 1 | 阻断 | 合成候选证据占位，不具备完整原图核对或人数证明 |

失败主页的 `total=2` 是当前已成功记录数，其候选人数为 3；完整交付必须阻断，不能将双 Sheet 守恒误写成全体候选均已履约。

主页合成 HTML 具有 `article`、姓名标题、职称、四段履历及独立邮箱（无邮箱教师除外）。每段带教师姓名，包含学习经历、博士经历、项目/论文/课程及末段。它们没有学校模板段，也不是仅用几字构造的稀薄正文。共享详情使用独立 `section` 容器；它必须保留个人完整内容，并避免把同页他人的正文当作本人简介。

## 重放方式与边界

使用 `.runtime/worker-venv/Scripts/python.exe -m pytest tests/test_harvest_acceptance.py -q --tb=short`。浏览器替身仅返回固定 HTML、改变翻页 URL/DOM 状态或抛出指定访问失败。禁止 native Chrome 恢复；输出写入 pytest 临时目录。本集无 API 调用，无线上访问，无生产数据库写入。

翻页替身不是 Playwright 实机验证；图片占位不是图片/OCR准确性验证。未测范围包括验证码、复杂跨域 iframe、浏览器重定向与 Cookie、真实 AJAX 等待时序、更多真实站型、图片原图完整加载及 OCR 人数核对。

## 首轮结果与已暴露差异

2026-09-26，修复测试自身对 openpyxl context-manager 的误用后：**4 通过、7 失败**（11 项）。通过项：无主页、医院资格、未核实图片阻断、真实 60 卡片身份集合。既有 BS4/lxml 产生 71 条 `strip_cdata` 弃用告警，不影响人数差异判断。

失败节点均为 `tests/test_harvest_acceptance.py::test_fixed_harvest_acceptance_contract[场景名]`：

| 场景 | 实际观测 | 需求差异 |
| --- | --- | --- |
| ordinary_full_biography | 总3/有邮箱2，1项 `profile_body_disagreement` | 第三人单页正文120字，但跨页结果只保留2字姓名；完整履历不应丢失 |
| homonyms_distinct_profiles | 总3/有邮箱0，3项 `profile_body_disagreement` | 固定预期3独立邮箱及完整履历 |
| shared_detail | 总3/有邮箱3，3项 `profile_body_disagreement` | 单页全体正文156字与每人52/52/50字的正确隔离正文比较造成误阻断 |
| table_roster | 总3/有邮箱0，3项 `profile_body_disagreement` | 单页各有1邮箱及151/151/146字正文，跨页结果只保留3/3/2字姓名 |
| pagination_url_changes | 总6/有邮箱0，6项 `profile_body_disagreement` | 页数2正确，固定预期6邮箱及完整正文 |
| ajax_same_url | 总6/有邮箱0，6项 `profile_body_disagreement` | 两状态采集正确，固定预期6邮箱及完整正文 |
| failed_profile | 总2/有邮箱0，主页失败与正文差异阻断 | 阻断正确，但成功两人邮箱被遗漏（固定预期2） |

跨页正文差异疑似来自 `server/detail_mecc.py` 的同模板推断；共享详情误阻断位于 `server/browser_batch_tool.py` 的 `profile_body_disagreement` 检查。此验收任务未修改生产实现，也未以 xfail、skip 或减少履历段落隐藏失败。以上是首轮结果，生产修复后须重新运行并追加实测结果。

## 2026-09-27 断点与覆盖集成契约

追加 4 项集成测试，仍复用上述合成页面，没有新增真实学院证据：

1. 顺序采集时第二个人页等待中取消：第一页必须已经落盘；同工单同完整计划重启后只访问缺失的第二、第三个人页，完整目录应恢复。
2. 同 URL AJAX 分支完整采集后重启：两个不同名录状态和六个个人页全部恢复，浏览器访问为零，最终身份集合不变。
3. AJAX 分页点击等待中取消：目录尚无终页证明，恢复必须重新访问该分支，不能把第一页当成完整分支。
4. `max_pages` 改变意味着完整计划摘要不同，不能恢复旧分支或个人页；新计划重新访问目录及六个个人页。

所有验收测试显式设置 `HARVEST_TASK_DB=scratch/verification/tasks.db`，本集仍不调用队列写入；页状态和采集输出使用 pytest 临时目录。覆盖报告要求 `scope=verified_roster_branches`，保留 `discovery_completeness=requires_independent_navigation_evidence`，不能把已核实计划分支覆盖解释为全站已发现完毕。报告 JSON 文件必须与返回账本一致；AJAX 两状态的 `state_id` 必须不同；失败与未核实图片应有阻断 issue。

新增断点接口首次运行（生产 `run_plan` 尚未接入 `state` 时）：15 项中 4 通过、11 失败，其中 4 个新增失败均为 `unexpected keyword argument 'state'`。该失败记录用于确认集成契约尚未落地，不作为最终修复结果。

接口及正文修复后的再次运行：**13 通过、2 失败**（15 项，4.18 秒，154 条既有 BS4/lxml 弃用告警）。普通完整履历、同名不同主页、共享详情、表格、分页与 AJAX 的固定人数、邮箱归属与正文要求均已通过；四项恢复集成也通过，包括原始个人页 `captured_at` 未刷新。剩余失败为 `failed_profile` 与 `unreviewed_poster`：两者 `coverage.complete=False` 且交付阻断正确，但 `coverage.issues=[]`，未将未解决原因写进账本。测试继续要求可审计的阻断原因，不弱化为仅核对布尔状态。

## 最终验收结果（2026-09-27）

修复账本缺失阻断原因后，同一固定环境与命令实测：**15 通过、0 失败，3.73 秒**；154 条 BS4/lxml `strip_cdata` 弃用告警。未使用 xfail/skip，未减少固定教师人数、邮箱归属或完整履历预期。

此结果为 **10 个合成结构场景、1 个真实保存名录页、4 项合成恢复集成** 的离线契约通过。真实样本仍仅覆盖一个保存页的固定 60 身份，不能据此扩大为全校/全学院完整采集验证或多校人工普查。通过结果包括：失败主页与未核实图片不导出、覆盖账本列出原因、完整 AJAX 状态恢复、部分目录重新访问、计划变更隔离、成功个人页恢复与原捕获时间守恒。
