# MyPlan 首轮全链路契约审查

日期：2026-10-11（Asia/Singapore）。代码基线：[`51b5178982efd472eed35851848f92c587320932`](https://github.com/normaltank/MyPlan/commit/51b5178982efd472eed35851848f92c587320932)。需求依据为用户当前确认的目标，统一记录在 [CONTEXT.md](../../CONTEXT.md)。

本记录保存该基线的发现和复现方法；修复状态只更新 [五项任务](../improvement-plan.md) 对应项并链接 PR。本轮沿主调用链检查关键边界，不等于全仓逐行审完，更不等于实际学院采集验收。

## 审查方法与结论

使用仓库内 `code-review` 的两条独立审查轴：Standards 轴查模块职责、规则重复与状态契约；Spec 轴查当前用户目标是否被真实代码实现。结合 `codebase-design`／`improve-codebase-architecture` 的接口、复杂度归属和改动影响分析，不以文件长度判断是否需要重写。

两路审查均使用隔离数据库与合成材料。下述“复现”是本地函数、提取／工作簿或队列／发布链的实际执行；没有访问真实学院、调用付费侦察模型或修改生产队列。

已有页面断点隔离、摘要校验、复核类型登记、恢复策略、长简介保存和大学发布锁值得保留。主要问题集中在**输入列定位、人员观察到交付行的转换，以及任务状态与发布快照的一致性**，适合围绕共同契约修复。

## 确认缺陷与入口契约缺口

| 编号 | 发现 | 证据轴与级别 | 影响 |
| --- | --- | --- | --- |
| F1 | TSV 中间空列被删除，后续字段左移后仍被接受 | Standards，已复现 | 实际入队身份和入口可能偏离母表 |
| F2 | 单学院 API 默默忽略 `roster_urls` | Spec，已复现 | 与批量四列入口行为不一致 |
| F3 | 缺失职称写“教师”，无主页记录写入名录 URL | Spec，提取到 XLSX 已复现 | 未知值被输出成看似有来源的事实 |
| F4 | 不同名录的同名观察缺乏身份依据仍合并 | Spec，已复现 | 争议候选在身份核验前减少 |
| F5 | 同人多学院关联只在 manifest，工作簿只显示第一学院 | Spec，发布到 XLSX 已复现 | 用户结果表丢失关联 |
| F6 | 无师资界面与落库提示仍写“没有专任教师” | Spec，实际代码与现有测试确认 | 员工可能按旧范围作结论 |
| F7 | 人工无师资后，包含旧人员的版本仍通过下载校验 | Standards，队列到发布快照已复现 | 已被否定的旧结果仍可作为成果下载 |
| F8 | 通用完成接口接受无覆盖证据的自动零记录，随后可发布 ready | Standards，兼容接收链已复现 | 不同入口没有同一采集证据契约；未证明标准 worker 走此绕过路径 |
| F9 | 回放只保留同学院最新捕获，摘要不比较具体人员和字段 | Standards，已复现；验收能力缺口 | 无法独自证明历史成功能力未退化 |

### F1／F2：输入必须保持原始列义

[server/intake.py](../../server/intake.py) 的 `_columns` 对 TSV 使用 `if part.strip()`，删掉所有空单元格；`parse_batch` 随后按剩余列数解释输入。[批量 API](../../server/app.py) 直接采用该结果入队。

两个实际反例均返回空 errors／warnings：

```text
U\tC\t\thttps://example.edu/faculty/
U\t\thttps://example.edu/college/\thttps://example.edu/faculty/
```

第一条缺学院链接，却被当成合法三列，师资链接变成学院链接；第二条缺学院名，却把学院 URL 当名称。修复应保留 TSV 的列位置，仅允许可选第四列为空，分别验证前三列，保留正常三／四列及多行同学院行为。

`CollectRequest` 只有 school、college、url，`model_validate` 后 `roster_urls` 消失；`start_single_collection` 也不传该值。批量入口已有支持，因此 F2 是入口不一致，不是全部第四列能力缺失。修复后应核对原始输入、解析结果和实际任务字段。

### F3／F4：人员观察与字段事实不能提前混成一条输出

[server/titles.py](../../server/titles.py) 的 `select_title` 将“教师”用作缺省值；[FacultyHarvestPipeline.harvest_candidates](../../server/faculty_extractor.py) 在无主页候选上仍把 `cand.url` 写入主页。输入仅有 `王芳` 的名录卡片，子链结果 `delivery_ready=true`、`unresolved=[]`，实际 XLSX 为“职称=教师、主页=名录 URL”。

此反例覆盖确定性提取到实际工作簿，**未证明标准 `run_plan` 的全部路径都会保留相同主页值**；编排层另有主页处理，恰好说明转换规则需要统一。修复不能全局删掉“教师”字符串：官网明确写“教师”与未知职称必须区分；名录来源与个人主页也必须分开保留。

[merge_same_name_observations](../../server/faculty_identity.py) 按姓名及 URL 类别合并。两个无个人页的同名观察，一条为教授／数学，另一条为讲师／音乐，输出仅剩一条，规则为 `same_name_lists_without_pages`。反例不宣称现实中它们必定是不同人；缺陷是差异尚未核实就合并。生产 `run_plan` 在后续身份审计之前调用该函数。

[现有测试](../../tests/test_supplementary_lists.py) 的 `test_merge_rule_edges` 明确要求该旧合并行为。因此应修改错误契约并增加同名不同人、证据确认同人、补充名录和多个主页的正反例，保留原始观察及字段来源关系；不能把旧测试当成不可修改的需求。

### F5／F6／F7：人工结论、学院关联与可下载版本应一致

[university_delivery._assemble](../../server/university_delivery.py) 将第二学院加入 `identities[*].colleges`，却仍按首条记录生成导出行。两个学院同名、同 URL、同字段的确认记录发布为一行时，manifest 是 `[A,B]`，实际 XLSX 只有学院 A。[既有版本测试](../../tests/test_delivery_versions.py) 只核对 manifest 关联，未核对用户工作簿中的全部学院表示。

无师资界面由 [no_faculty_review.py](../../server/no_faculty_review.py) 生成，`agent_queue.mark_no_faculty` 保存旧“专任教师”提示并删除该任务记录。应统一当前官网全师资语义，加入仅有兼职／外聘的反例；只改文案不能解决版本一致性。

F7 的复现顺序：发布含 1 人的大学版本 → 对同一已完成任务人工标无师资 → 核对旧 manifest。当前任务已是 0 人、记录已删除，但 `manifest_snapshot_is_safe` 仍为 true，范围仍 ready，旧 XLSX 仍含原人员。`app.download_file` 依赖这两个检查。

原因是旧 manifest 的记录／审计快照未被当前验证充分绑定，当前空任务不能证明旧人员仍有效。应让人工否定使含该旧结果的发布版本失效，保留其追溯证据；同时保护无关学院旧版本及合法的新版本。回归至少覆盖旧下载拒绝、新版本生成和无关任务不受影响。

### F8：生产采集回执与兼容接收应明确区分

标准 [run_plan](../../server/browser_batch_tool.py) 用 `zero_person_delivery_requires_review` 阻止自动零采集交付，`browser_batch_sync.build_payload` 也要求 ready 审计。但 [AgentTaskQueue.complete_task](../../server/agent_queue.py) 只在调用者提供 coverage 时检查它，通用同步入口接收字典回执。

隔离任务可用空 records、零计数、无 coverage、无人工无师资决定完成，随后发布 ready 的 0 人工作簿。这里确认的是入口契约不一致，未证明标准 worker 实际产生过这种回执。现有兼容用途需要先辨明，不能简单删除所有最小回执支持。

改进方向是集中正常生产回执的证据校验；历史导入等兼容入口显式说明来源与核验状态，不把计数自洽直接提升为采齐。worker、同步、入库与发布共同调用必要的验证，避免每层只复制一部分规则。

### F9：回放报告还不能承担历史保护验收

[case_replay.discover](../../server/case_replay.py) 按大学／学院只留最新捕获，即使显式指定同学院的旧、新两个案例也仍只返回新案例。[现有测试](../../tests/test_case_replay.py) 固定了这个行为。

`_summary`／`render` 主要比较 ready、人数、有邮箱人数及错误码。人数不变地将 Alice／原简介换为 Bob／另一简介，两次摘要相同。该工具仍可用于固定材料的流程比较，不能独自证明人员、字段、简介和历史成功案例都被保护。

应以显式案例 ID、捕获版本与原件摘要固定回归分母，保留同学院的多个证据版本，并加入独立核验的人员／字段／完整简介差分。缺原件、失败、未运行全部留在报告。[harvest_benchmark.py](../../server/harvest_benchmark.py) 已区分 collector_ready 与独立核验并保留未运行样本，可复用其统计方式；它并不生成实际母表或人工真值。

## 已确认目标尚未形成完整能力

| 目标 | 当前证据与差距 |
| --- | --- |
| 跨学院、不同个人页的同人合并 | [university_identity_review.py](../../server/university_identity_review.py) 的 verdict 和数据库 CHECK 只有 distinct／insufficient；旧注释称用户尚未决定。用户已确认目标，实施无需重问；学院内已有的分组入口不能代替大学层能力 |
| 可信部分交付、争议记录单列 | `complete_task`、`delivery_scope`、`publish_delivery` 对未解决项或非 ready 范围整体阻断；须联动定义可交付记录、待核记录与未覆盖范围，不能删除门禁伪装实现 |
| 三列输入后的四列入口表 | 在 server／scripts／tests 的输出路径未找到独立四列映射导出。发现结果含 branch URL 不代表用户已收到入口表 |
| 员工补入口／补人／改字段后继续采集 | 现有路由主要处理已生成审计 item 的决定后重跑；未发现通用补新入口、补遗漏人员并保持来源绑定的完整接口 |

这些是目标与实现之间的差距；本次整理文档没有把它们改成已完成。

## 待验证风险与架构改进方向

- **来源资格**：`classify_candidates` 对 `university_faculty_roster` 准入，而当前 discovery 契约以该类别描述来源；来源角色仍需真实证据验证。检查时 [PR #59](https://github.com/normaltank/MyPlan/pull/59) 尚未合并，不能当作基线已修复。
- **发现完整性**：coverage 已明确需要独立导航证据，`coverage_scope` 仍有 `supplied_roster_branches_only` 标记，`_attach_cross_check` 只附核对信息。这些不足以推出全院没有未知分支，须在真实批次核验。
- **人员来源关系分散**：`run_plan` 在多个映射中维护 `(url, name)`、正文、字段和覆盖来源；合并、字段补全、复核及导出有隐含执行顺序。优先让现有人员观察模块拥有这些关联及转换，避免新建仍要求调用者同步所有字典的转发层。
- **计划验证的共性与差异**：Pydantic、模型计划卫生和执行计划验证有重复结构条件，但人工任职证据与模型输出本来就有不同限制。先集中共同约束，保留有理由的边界，不机械合成一个宽松校验器。
- **完整简介与 PDF**：长简介支持表已有实现及测试，值得保护；真实页面正文边界、多人 PDF、OCR 和原件恢复仍需材料验证。检查时 [PR #64](https://github.com/normaltank/MyPlan/pull/64) 尚未合并。
- **完成统计**：任务统计能保存人工无师资，但 completed 总数包含人工结案。界面目前称“完成学院”，本轮没有证据指控它已伪造自动成功率；评测必须单独拆出人工结果。

## 本轮验证与复现

Standards 轴相关非浏览器测试 **90 passed、4 deselected**；Spec 轴相关非浏览器测试 **84 passed**。两组存在交集，不能相加当作 174 个不同测试。通过结果与上述反例并存，说明需要检验测试表达的契约。

已运行的范围：输入、覆盖账本、恢复策略、复核登记、版本交付、人工无师资、租约发布、侦察提交及计划缓存、大学身份复核、职称优先级、长简介存储。

[离线复现辅助脚本](../../scripts/reproduce_contract_audit.py) 保留 F1、F2、F3、F4、F5、F7、F8、F9 的合成反例，从仓库根目录执行：

```sh
.venv/bin/python scripts/reproduce_contract_audit.py
```

脚本创建独立临时数据库和工作簿，输出各反例的观察结果及目录，不访问学校或模型，也不要求缺陷永远存在。修复后观察值应按新契约变化；正式修复仍须加入有意义的回归测试和真实材料验证。

本轮尚未覆盖：Opus 全部算法内部、所有历史／退役入口、完整认证安全链、OCR 图像及版面算法、全部模板 JavaScript、多进程复核压力场景，以及真实页面／模型全链路。当前真实待处理母表、完整原件包和运行环境入口未确认，因此未进行新随机采集或冻结后的未见验收。

## 对文档维护的落实

CONTEXT 统一目标与术语，AGENTS 提供简短工作入口，五项任务维护状态和证据，模块索引只定位代码。旧 CONSTITUTION／backlog 兼容转向，旧 ADR 的提案／历史地位明确。Git 与 PR 保存变更经过，CI 保存实际检查结果；本审查保留有复现价值的发现，不再复制到多个入口成为平行待办。
