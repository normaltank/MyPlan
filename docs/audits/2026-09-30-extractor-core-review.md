# 采集内核审查（2026-09-30）

**后续状态**：审查下文记录修复前证据。用户随后授权修复，当前原始 6 项检查已全部转绿，相关组合 52 passed；最终全量回归与部署尚未完成，不能把下文的失败结果当成最新测试，也不能说已生产验证。实现和剩余计划见 [续接说明](../implementation/2026-09-30-extractor-core-handover.md)。

## 范围与方法

本次审查现有采集内核，不修改生产算法、不重启服务、不重排工单、不请求 DeepSeek。使用 `code-reviewer` 的逻辑/契约审查与 `diagnosing-bugs` 的可重复失败检查。`code-review` 专用于提交差异，本次整体内核审查不套用其分支工作流。

检查 `server/faculty_extractor.py`、`server/detail_mecc.py`、`Opus/iso_mining.py`、`server/harvest_workflow.py` 和 `server/browser_batch_tool.py` 的实际调用链，结合 AGENTS、CONTEXT、ADR 0003/0005。Git 基线为 `b0aedb8`；Git 只能证明代码历史，不能证明某段代码由 Gemini 或其他模型编写。

合成页面使用虚构姓名、邮箱与 `example.edu` 域名；真实 Chromium 的网络请求全部由本地路由提供页面，未知请求中止。检查禁用默认 L1 注册库初始化，避免触及正式任务库。回归探针留在忽略目录 `.runtime/test_extractor_review_20260930.py`，不是生产测试套件已通过的新增测试。

## 确认的问题

### P1：完整正文可被 CSS 类名子串静默删除

位置：`server/detail_mecc.py:123` 的 `is_noise_element`，以及 `server/faculty_extractor.py:154` 的重复规则。

规则把 class/id 含 `bottom`、`menu` 等子串的元素直接视为噪声。合成个人页在可见、已核准的 `#profile` 内包含 `class="bottom-up-research"` 的研究方向段落，正文提取静默删去该段。单页提取与 `run_plan` 的真实 Chromium 检查均复现；完整流程返回 `delivery_ready=true`、`unresolved=[]`。

影响：正文完整性检查没有独立于提取器的预期，单页与批量路径共享同一过滤规则，可能一起漏掉同一段。

处理方向：经本轮浏览器核准的正文范围应完整保留，内部去噪须有具体结构证据；在浏览器正文与输出之间检查覆盖，不能再用相同清洗函数作独立验证。

### P1：个人页明确给出的邮箱因不符合姓名拼音而被静默丢弃

位置：`server/detail_mecc.py:78` / `:350` 与 `server/faculty_extractor.py:115` / `:1049`。

合成独立个人页明确写出唯一的 `电子邮箱：bluebird@example.edu`。邮箱正则成功识别，但姓名拼音/首字母亲和规则拒绝该地址，最终落入“无邮箱教师列表”；没有未解决项。真实 Chromium 运行 `run_plan` 同样允许交付。

影响：将“地址不像姓名拼音”误当成“页面没有个人邮箱”，会产生无声字段遗漏。数字邮箱、拼音邮箱的已有正例不能覆盖此情况。

处理方向：归属首先依据已核准的个人正文范围与联系字段位置，拼音匹配只能提供辅助证据。归属不能确定时记录待处理原因，不能把存在邮箱变成无邮箱；不应改成无条件采用整页第一个邮箱。

### P1：名录选择器已核实，提取仍被页面其它名单覆盖

位置：`server/harvest_workflow.py:402` 先整页 `extract_with_review`，随后过滤；`server/faculty_extractor.py:277` 从全页矿掘结果选择最佳组。

合成页面的 `#faculty > li` 明确含 3 张教师卡片，旁边独立的相关人员区含 6 条链接。算法选择较大的其它名单；后置卡片过滤移除这 6 条，但不重新提取已核实名录，最终留下 0 人，报 `candidate_outside_cards`、`roster_card_mismatch`、`no_candidates_on_roster_page`。

影响：正确的选择器也会失败；当前失败策略将这些代码视为结构问题，允许再次调用侦察模型。反复修改选择器不能修复这种算法与计划的接口冲突。

处理方向：普通卡片名录应以已核实卡片为提取边界，逐卡交代姓名/URL/异常。全页矿掘可继续承担发现与诊断，不应覆盖已核实计划。

### P2：外籍译名卡片被错送结构补缺

位置：`server/faculty_extractor.py:277`、`:360`，以及 `server/recovery_policy.py` 的结构错误分类。

合成名单有一张实际链接卡片，标签是超过四个汉字的外籍译名。姓氏/形状先验拒绝标签，未生成姓名复核项，最终报人数不符与无候选。当前文件中没有 Opus 云端所描述的“普通已核实卡片未知姓名转人工”的完整改动。

处理方向：已核实的一人一卡结构里，姓名识别不确定应保留卡片与 URL 并转姓名确认，不能借此推断没有教师，也不应让结构模型处理姓名字段。

## 验证结果与边界

命令：

```powershell
.\.runtime\dev-venv\Scripts\python.exe -m pytest .runtime/test_extractor_review_20260930.py -q --tb=short
```

当前工作区：**6 failed**（4 个问题检查 + 2 个真实 Chromium 整流程检查），约 6 秒。将 `EXTRACTOR_REVIEW_ROOT` 指向已有 `harvest-baseline` 工作树，重复运行同一命令：**6 failed**。探针在 fixture 中断言实际导入模块所属目录，确认基线使用独立工作树代码。基线同样失败表明这四类问题不是本轮未提交修复新增的回归。

现有相关测试：

```powershell
.\.runtime\dev-venv\Scripts\python.exe -m pytest tests/test_faculty_extractor.py tests/test_harvest_workflow.py tests/test_roster_decorative_symbols.py tests/test_degree_name_labels.py tests/test_name_list_spacing.py -q --tb=short
```

结果 **28 passed**。这说明已有测试覆盖不足，不能由通过数推导实网准确率。没有进行新的全量测试或全国样本统计；合成页面证明失败模式存在，不证明其在真实网站上的发生比例。

## 判断与后续顺序

保留队列、真实浏览器、结构侦察、恢复缓存和大学交付。暂停继续扩大外围流程，先修上述内核缺陷。建议先堵住正文/邮箱静默错误，再把已核实卡片接到确定性提取，最后完善未知姓名转人工。每项先用本探针锁住失败，再用保存页面与有限实网样本验证；新付费运行应等修复与部署核验之后。

源码中的 DOM 同构、MECC、Bayes 等名称不构成正确性证据。姓名概率由人工姓氏排序与固定公式给出，是启发式分数；正文噪声判定仍含类名规则。ADR 0003 的“100%”“零泄漏”等表述超出了当前测试能证明的范围。
