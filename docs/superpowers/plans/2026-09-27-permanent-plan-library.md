# 永久采集方案库实施计划

**目标：** 永久保存结构版本，每次复用仍须本轮浏览器证据。

**架构：** 保留 `ReconPlanCache` 调用界面，内部改 SQLite；旧 JSON 幂等迁移。worker 记录验证结果，保留现有复用门禁。无 Git 元数据，修改留在当前工作区。

**技术：** Python、SQLite、Playwright、pytest。

## 任务

- [x] 新增 `tests/test_permanent_plan_library.py`：一年后可读、历史版本、迁移原时间和幂等、坏文件拒绝、并发写入、验证记录。
- [x] 运行新增测试确认缺失功能导致失败。
- [x] 修改 `server/recon_plan_cache.py`：SQLite schema/事务、无默认 TTL、版本写入和严格读取、旧文件迁移、验证事件记录。
- [x] 修改 `server/agent_worker.py`：真实浏览器验证后记录结果，不改变失败回退逻辑。
- [x] 原有摘要/非法教师字段测试改为篡改实际数据库载荷，继续保持相同拒绝预期；运行局部测试再跑完整回归。
- [x] 更新运行文档、永久导入生产已有结构方案，空闲队列更新 worker；只读服务和数据库核验，不调用付费模型。

## 验证命令

固定 Python 环境，`HARVEST_TASK_DB=scratch/verification/tasks.db` 隔离生产：

```powershell
& .runtime/worker-venv/Scripts/python.exe -m pytest tests/test_permanent_plan_library.py -q
& .runtime/worker-venv/Scripts/python.exe -m pytest tests/test_recon_plan_cache.py tests/test_harvest_integration_review.py tests/test_permanent_plan_library.py -q
& .runtime/worker-venv/Scripts/python.exe -m pytest tests -q
```

## 完成证据

全量 284 passed、1 skipped（44.14 秒）。真实数据库路径复用通过浏览器核验、0 模型/0 Token；独立复核意见全部闭合。已有结构方案导入，服务空闲更新，详情见本日审计。
