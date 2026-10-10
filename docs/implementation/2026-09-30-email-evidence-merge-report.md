> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行要求见 [项目目标](../../CONSTITUTION.md)，当前执行见 [改进计划](../improvement-plan.md)；使用原结论前核对代码版本、样本与原始证据。

# 邮箱归属补全与全量回归（2026-09-30，接内核交接）

接 [采集内核交接](2026-09-30-extractor-core-handover.md) 的第 1–3 步。基线是 Codex 当晚的本机工作区（`b0aedb8` 加未提交修改，备份 `extractor-core-wip-20260930-184825`）。本轮只动邮箱归属、页面框架识别和测试，没有加外围功能，也没有调用模型或访问学校网站。

## 一、全量回归先暴露的问题

在 Codex 的工作区上跑全量：2 failed，716 passed。

1. `test_profile_footer_is_noise_and_institutional_rejected`：改成整词识别后，`profile-footer` 这类复合类名不再算页面框架。
2. `test_proof_binding_and_reconciliation_only_exempt_verified_empty_page`：有卡片选择器时提取入口已改为 `extract_with_review`，测试仍替换旧入口 `extract`，所以“空名录证明与实际抽到教师冲突”这条没有被测到。

排查邮箱时又发现一个测试没覆盖的回归，比上面两条影响大：

- 同一学院的个人页模板里都有学院邮箱（如“地址：… 邮箱：学院@…”）时，`b0aedb8` 会把它当公共邮箱，Codex 版有两种错误：
  - 邮箱不在字段开头：所有没有个人邮箱的老师都报 `ambiguous_email_assignment`，整个学院无法交付。
  - 邮箱在字段开头：学院邮箱被分给第一位老师，其余老师报 `ambiguous_shared_email`。
- 原因：模板区邮箱的过滤（detail-df）只作用于旧的 `email` 字段，没有作用于新的 `contact_emails` 和“有地址无证据”判断。

## 二、改动

| 位置 | 改动 |
| --- | --- |
| `server/faculty_extractor.py` 共享多人页 | 按本人独占卡片里的联系字段证据归属：标签、表格标签格、mailto。没有联系字段时，才用拼音匹配。<br>以下情况进入既有复核项，不静默放进无邮箱表：<br>- 同一邮箱在多张卡片里都标为联系邮箱：`ambiguous_shared_email`<br>- 一张卡片有两个标注邮箱：`ambiguous_email_assignment`<br>- 卡片里有地址但没有归属证据：`ambiguous_email_assignment`<br>保留原有判断：<br>- 地址拼出同事姓名：`conflicting_card_email`<br>- 同姓多人时只含姓氏的地址：`ambiguous_surname_email`<br>- 每张卡片都有、又没标注的地址：视为公共邮箱 |
| 同文件 名录卡片 | 卡片文字里有“邮箱：”等标注的地址，归卡片本人；只含一位教师姓名的卡片才采信标注。多卡同标、多个标注、无证据地址的处理与共享页一致。 |
| 同文件 个人页 | 恢复 detail-df：几个同级个人页都出现的邮箱，不作任何人的证据。例外是地址只拼出其中一位教师的姓名，归这位教师。<br>个人页上点名的同事，如果地址拼出这位同事的名字，就不算页面主人的邮箱。 |
| `server/detail_mecc.py` 联系字段 | 补识别以下写法：<br>- `li` 条目<br>- 左侧单独一格的表格标签（这一格只能有标签）<br>- 只在链接里给出地址的 mailto<br>- 同一行内“电话：… 邮箱：…”<br>标签前面不能紧跟汉字或字母，所以“学院公共邮箱”“课题组邮箱”不算本人联系字段。 |
| `server/dom_regions.py` | 复合类名以 footer/nav/menu/sidebar/copyright 结尾时算页面框架，如 `profile-footer`、`left_nav`。<br>`bottom-up-research`、`menu-research` 仍算正文。已核准正文（preserve_content）不受影响。 |
| `tests/test_empty_roster.py` | 替换现行入口 `extract_with_review`，断言不变。 |

## 三、验证

- 全量 `pytest`：**749 passed, 3 skipped, 0 failed**（云端 Linux、Chromium 1134）；`ruff check .`、`git diff --check` 通过。
- 新测试 `tests/test_email_contact_evidence.py`：在 Codex 工作区上失败 16 项，本轮后全部通过。内容：
  - 联系字段的各种写法
  - 共享页、名录卡片的归属与复核
  - 模板邮箱（三种写法）
  - 同事地址
- 新测试 `tests/test_extractor_browser_flow.py`（`@pytest.mark.browser`）：真实 Chromium 跑 `run_plan`，全部请求由测试本地提供。
  - 私有探针里的两项整流程，断言改成“字段正确且可交付”，不再接受“被拦下也算过”。
  - 已核准正文里的 header/footer 元素与类名完整保留。
  - 共享团队页的标注邮箱和 mailto 归属。
  - 共享页有地址无证据时阻断交付。
  - 其中共享页两项在 Codex 工作区上失败。
- 本机回放（仅在本机虚拟环境运行，只输出计数）：
  - 法学院 attempt-4 保存页：
    - 主名录 3 页 9/9/2 全部对上，PDF 页为待核准项。
    - 20 条记录的邮箱与简介，在新旧内核和已产出记录之间逐条一致。
    - 20 个个人页原文确实没有邮箱，无邮箱不是遗漏。
  - 美术与设计学院 recovery 名录页：
    - 新旧内核都得到 26 + 46 = 72 条来源观察，没有未解释卡片。
    - 预检时的 26 对 24 差额已不存在。
    - 没有保存个人页，所以邮箱和简介还没有回放。

**以上都是测试和回放，不是实网采集完成。** 两张工单仍是 failed。

## 四、部署与工单（待执行）

1. 本机 Windows 用 `.runtime/dev-venv` 再跑一次全量（含 browser 标记），确认与云端一致。
2. 确认没有 queued/running/compiling 工单。9 月 30 日 21:17 读 `server/tasks.db` 的快照为 completed 89、failed 32，但 worker 实际用哪个库，需要在本机确认。
3. 备份正式库和日志后，用项目现有启动脚本切换服务，然后核验：
   - Web 页面
   - 队列状态接口
   - worker 心跳
   - 实际加载时间

   `.runtime/restart_after_roster_fix.ps1` 不可运行。
4. 法学院：主名录已过，只差补充 PDF 的核准（PDF 与主名录重叠、冲突情况见交接文档）。需要人工核准，不需要付费侦察。
5. 美术与设计学院：名录对账已过。接下来的两步都要访问学校网站：
   - 沿用已核实计划做详情批采（浏览器即可，不调模型）。
   - 做身份对账（53 个候选姓名，按交接文档核实机构名单范围）。

   执行前请确认。

## 五、文件

- 备份：`.runtime/backups/claude-email-evidence-20260930/`（覆盖前的 4 个文件）。
- 补丁：`.runtime/claude-cloud-export-20260930/0003-claude-extractor-email-evidence.patch`。云端分支 `claude/extractor-email-evidence` 基于 Codex 工作区快照，未合入 main。
