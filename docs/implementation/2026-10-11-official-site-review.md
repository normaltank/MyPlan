# 学院官网访问异常的任务级人工复核

学院入口或名录的真实访问失败可单独标注为“官网异常，待人工确认”。这是任务级访问问题，保存于 `task.stats.official_site_issue`；不是“无师资”、师资资格未知、普通个人页 404 或单教师字段复核。

登记入口为 `AgentTaskQueue.record_official_site_issue(task_id, observations)`。任务须为失败的学院采集任务，传入已保存的链接、方法、带时区的 `observed_at` 和错误／HTTP 状态；函数不探测网络。链接只取任务学院入口、给定名录以及核心 `source_disposition_evidence` 已确证的 `source_unreadable/unavailable` 来源。核心处置及原件字段不修改，不另造用途或访问原因枚举。示例调用使用保存的材料，不生成真人决定：

```python
queue.record_official_site_issue(task_id, saved_access_observations)
```

皖西当前目标为 `025c3468d4f24f9b9558dc96ba7763de`。本分支只提供登记与复核能力；真实登记由维护任务根据已有连接关闭证据执行，没有在开发测试中写该工单、审计或真人决定。

首页显示官网访问状态和独立复核链接，页面展示原链接、方法、时间和错误。登录员工在 `/review/task/{task_id}/official-site` 保存 `available`、`unavailable` 或 `unknown`，服务端记录真实审核人 ID 与时间。沿用现有登录和 CSRF；旧证据 ID 不接受新的决定。保存后仍为 failed，原 records、审计及错误保留，下载不能恢复；旧“没找到名录”的页面不再给该异常提供无师资结案。

人工确认恢复后，另行调用 `/api/task/{task_id}/official-site/retry` 才会进入原队列。若已有图片文字、邮箱、姓名、身份等登记复核未完成，仍保持阻断；原字段复核的 retry 接口也不能绕开官网确认。通过门禁时沿用原件封存与决定传递，队列保留访问问题及人工决定，随后正常采集独立核验新结果。没有自动重试、定时器、代理、测速、告警或服务重启。

固定隔离数据库的 API 回归覆盖三种结果不入队、记录与原审计字节不变、匿名／CSRF 拒绝、独立 retry 的未完字段门禁、旧 retry 防绕过、个人页 404 和用途未知拒登记，以及核心名录访问证据引用。真实本地浏览器覆盖保存可用仍 failed、再点击显式 retry 才 queued。所有 URL 均为本地路由和合成材料，不代表当前官网或全院采集验收。
