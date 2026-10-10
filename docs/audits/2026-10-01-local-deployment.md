> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行业务见 [CONTEXT.md](../../CONTEXT.md)，开发任务按 [AGENTS.md](../../AGENTS.md) 分工；使用原结论前核对代码版本、样本与原始证据。

# 本机部署与四字段输入核验

2026-10-01，用户授权按建议合入 main、同步远端并部署，明确不处理原失败学院，后续由用户选择其他学院测试。内核修复提交 `3b326a0` 已快进合入 main 并推送 origin/main；本次端口修复继续在 main 工作。

## 部署结果

部署前无 queued、running、compiling 工单。使用 SQLite backup API 备份正式 `tasks.db`，并复制 12 份本机日志到 ignored 目录 `.runtime/backups/deploy-20261001-170739/`，manifest 保存文件大小和哈希。未复制明文或加密 API Key。

正式 `scripts/start_local_services.ps1 -Restart` 初次启动 Web 遇到 WinError 10013。本机 IPv4/IPv6 的 TCP 排除范围包含 7998–8097；8000、8001 和 8100 实测无法绑定，18000 可绑定。没有删除系统排除范围，改用 ignored 文件 `.runtime/local-services.json` 的 `{"port":18000}`。

新增 `server/local_service_config.py` 统一加载默认配置与本机端口。网页监听、浏览器打开地址、代理提示、worker 默认回传、手工同步 CLI 和 PowerShell 启动／重启均使用该端口；显式 `--base-url` 保持有效。默认 `server/config.json` 未改动。本机覆盖只允许改变端口，端口须为 1 至 65535 的整数；启动脚本在停止旧服务之前检查配置。

随后正式启动器成功启动 Web 和常驻 worker，本机入口为 **http://127.0.0.1:18000**。运行证据保留在 `.runtime/launcher.log`、`.runtime/local-worker.out.log`、`.runtime/local-worker.err.log` 与 `.runtime/core-chain-continuation/deployment-health.json`。

收尾只读检查：登录页 HTTP 200；worker 启动自检通过，近 30 秒内有 1 个 worker 心跳；正式库仍为 89 completed、32 failed，全部工单 ID 与状态和部署备份一致。启动会更新心跳与自检记录，不能宣称部署后的数据库文件哈希不变。未登录的状态 API 返回 401，未绕过认证；认证后的路由行为由临时数据库集成测试核验。本轮没有新增工单、官网访问或采集验收。

## 四字段已经实装

输入入口是登录后的**批量采集**，列顺序为：大学、学院、学院主页链接、师资列表链接。第四列可选；支持空白或逗号分隔，也支持直接粘贴 Excel 制表符。校名或院名含空格时应使用制表符。对应四列表头可识别并跳过。

每行第四列填写一个列表链接；同一大学、学院与学院主页的多行会合并，列表链接去重。相同学院给出不同主页会报错。可疑跨站链接或同校主页域名不一致时，前端先显示确认提示，通过确认后才入队。

核验链路：`server/templates/index.html` 显示输入说明并发送 `items_text`；`server/intake.py` 解析；`server/app.py` 执行确认门禁；`server/agent_queue.py` 持久化并随认领返回 `roster_urls`；`server/agent_worker.py` 将其作为 `provided_urls` 交给侦察，并避免复用不适合该输入的站点缓存。给定链接仍须本轮真实浏览器核验，不能自动视为覆盖完整。

## 验证与审阅

此前内核最终全量为 857 passed、1 skipped；端口修复后的相关测试为 **61 passed**，覆盖共享配置、非法端口、公开 Web 启动入口、同步 CLI 默认／显式地址、worker 调度与交付、缓存恢复、四字段解析／队列／侦察及登录后的输入说明。新增端口回归先验证失败，再修复通过。Ruff、Git 差异检查和 PowerShell 语法解析通过。

Standards：最后只读核对未发现阻断问题，共享配置、工作目录、重启对象与凭据处理符合项目边界。Spec：最后只读核对未发现可确证问题，端口与输入链路满足本次要求。两轴审阅均未执行生产采集。

职称优先级仍待用户选择；Excel 简介超限与 L1 缓存调整按原计划暂缓。旧失败学院不属于本次后续执行范围。

后续保存节点：用户已批准职称优先级，职称、Excel 门禁及其它底层修复已实装并保存，见[底层调用链审查](2026-10-01-foundation-hardening.md)。本页记录上轮实际部署；后续改动尚未重启服务，不以 Git 同步冒充部署完成。
