# 分页途中续采实施计划

**目标：** 已成功页逐页恢复，可靠重验证分页前沿，再继续采集。

**架构：** 任务内 `HarvestState` 增加分支游标保存与连续记录读取；`_capture_branch` 支持前沿验证；`run_plan` 接入恢复及审计统计。无法还原则完整重走。

## 执行

- [x] `tests/test_pagination_resume.py`：先写真实浏览器中断/恢复、前沿变化/AJAX/损坏、取消、上限阻断用例，确认失败。
- [x] `server/harvest_state.py`：保存分支连续快照引用和读取验证，保持时间/完整计划隔离。
- [x] `server/browser_batch_tool.py`：恢复参数、卡片与下一控件结构签名、最后页重新访问验证、run_plan进度及审计接入。
- [x] 跑恢复集成/全量回归，独立复核。
- [x] 更新运行文档和交接；队列空闲时更新服务，核验无新模型请求/工单。

固定环境运行：`python -m pytest tests/test_pagination_resume.py tests/test_harvest_state.py tests/test_browser_batch_tool.py tests/test_harvest_integration_review.py -q`；最终 `python -m pytest tests -q`。生产数据库保持隔离。

验收：独立分页复核 9 passed；最终全量 306 passed、1 skipped。本地真实浏览器回放续采只访问最后成功页、后续页和缺少的个人页。12:29:54 服务更新，主页 200、旧问题文件 409、生产队列无待处理，模型请求与新工单均为 0。详见本日审计。
