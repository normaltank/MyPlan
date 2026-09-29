# Verifiable Faculty Harvest Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded independent tasks, and test-driven implementation with integration review in this session.

**Goal:** 实施已批准的覆盖账本、大学交付版本、页面恢复、局部补缺与经过本轮浏览器验证的方案复用。

**Architecture:** 保留 FastAPI/SQLite/Harness/Playwright 及确定性提取内核。工单是学院任务，浏览器状态按工单持久保存，结构方案按学院和入口缓存，大学成果按交付范围与版本生成。模型仍仅输出结构计划，全部学院立即独立派发。

**Tech Stack:** Python 3.12、SQLite、Playwright、pytest、现有 Excel exporter；不新增执行框架。当前目录无 Git 元数据，采用文件修改与回归验证，不创建提交。

## Task 1: 代表性离线验收集

Files: `tests/fixtures/harvest_acceptance/`、`tests/test_harvest_acceptance.py`、`docs/audits/2026-09-26-harvest-acceptance.md`。

- [x] 建立可重放的结构样本：普通卡片、无主页、同名、共享主页、表格、混合人员来源、分页、AJAX、失败主页、图片证据；已有真实快照作为独立真实案例。
- [x] 写测试并确认失败，使用固定人工/样本预期值，不从被测函数计算预期。
  ```python
  assert report['total'] == case['expected_total']
  assert report['with_email'] + report['no_email'] == report['total']
  assert report['delivery_ready'] is case['delivery_ready']
  ```
- [x] 运行验收集，记录合成样本与真实样本、已核实与未核实区别，不宣称完成跨校人工普查。

## Task 2: 大学交付范围与快照版本

Files: `server/agent_queue.py`、`server/browser_batch_sync.py`、`server/app.py` 相关提交/同步/批次状态、对应 tests。

- [x] 先验证两学院尚有待采集时不得把第一学院结果作为完整大学批次成果；重复学院重新采集不得累加旧行；失败学院阻断本批完整成果。
- [x] 确立 batch_id+school 的目标学院集合，记录可审计交付 manifest 和大学版本文件。
- [x] 大学当前结果按每学院最近完成快照选取，保留旧工单记录；同姓名不同主页保留，只有相同姓名和个人主页的充分相同来源才去重并保留学院关联。
- [x] 大学文件原子发布，前台返回交付范围与状态；保留既有单院调用兼容。

## Task 3: 页面状态、恢复与局部访问补缺

Files: `server/harvest_state.py`、`server/harvest_workflow.py`、`server/browser_batch_tool.py`、`server/agent_worker.py`、对应 tests。

- [x] 写断点测试：成功个人页保存后中断，恢复只重试失败页；不同计划不能复用已不适用结果；AJAX 同 URL 状态区分。
- [x] SQLite 存储任务内页面状态及本地 HTML，绑定完整计划摘要、采集任务和证据文件摘要；每成功页立即保存。存储接口：
  ```python
  state = HarvestState(directory, plan)
  state.save_page(kind, url, html)
  html = state.load_page(kind, url)
  ```
- [x] 覆盖账本记录计划分支、实际页、卡片、候选、资格决策、主页及失败；既有清点不足时阻断交付，明确范围为已核实名录。
- [x] 访问超时优先恢复同一计划，不重复模型侦察；结构错误返回对应分支，已确认分支不能删除；证据冲突明确复核。

## Task 4: 本轮验证后的结构方案复用

Files: `server/recon_plan_cache.py`、`server/agent_recon.py`、`server/agent_worker.py`、对应 tests。

- [x] 先写浏览器复用测试：相同页面方案命中；新增导航链接、卡片失效、分页变化必须转为模型侦察；不接受未经完整发现证据的缓存。
- [x] 保存结构计划和完整发现页面 URL/链接证据，禁止保存教师字段进结构缓存。缓存键为学校+学院+入口及契约版本。
- [x] 本轮 Playwright 重新访问发现页面和所有分支，验证导航、卡片、分页操作并保存新证据；通过才跳过模型调用。
- [x] 未通过时执行现有 Harness，保存新版本；结构修复仅重侦察出错分支，入口发现仍检查已确认范围。

## Task 5: 集成与运行验证

- [x] 跑新用例的失败/成功过程和全量 `pytest tests/ -q`；检查所有不完整结果均不得发布完整交付。
- [x] 重放已核实 89 人真实样本，比较 `姓名`+`主页` 身份集合；保存覆盖与恢复元数据。
- [x] 实时官网缓存核验：国际汉语文化学院复采通过本轮导航、卡片、实际分页检查，模型调用/Token 0；身份门禁修复后再次实时访问 68 次文档，45 条观察保留、14 组待复核、无 Excel。湖南中医药大学连接仍关闭/重置。缓存复用验证通过，不代表身份裁决或所有网站交付完成。
- [x] 更新 README、采集 skill、AGENTS/交接说明；独立复核完成后在空闲队列替换本地 worker，固定环境启动。

## 完成标准

工单租约继续有效；全量学院派发不加上限；教师字段仍由确定性代码提取且十列完整；未解决项阻断完整大学交付；历史任务和文件不被删除；缓存及恢复只在明确范围和真实证据下生效。验收范围和未测站型写入交付记录。

实施结果与实时访问受阻证据见 [2026-09-27 审计](../../audits/2026-09-27-verifiable-harvest.md)。
