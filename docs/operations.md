# 运维手册

README 只保留项目简介和快速开始；本文件收录部署、参数、排障与历史验收记录。内容自 2026-09-29 起从 README 原样迁入，未改动含义。

## 1. 启动与重启（Windows 本机）

```powershell
.runtime/worker-venv/Scripts/python.exe -m server.start_server
```
Windows 本机也可双击 `双击启动局域网服务.bat`。它会检查后台接单心跳，缺少在线消费者时启动常驻 `server.agent_worker`，然后启动网页；已在线的消费者或已监听的网页不会重复启动。两者都已在运行时，窗口会打开网页并提示后等待回车，不会一闪而过。worker 启动自检未通过时，网页顶部会显示"后台接单离线 · 启动自检未通过"和第一条原因。**更新代码后请双击 `重启本地服务.bat`**：它只结束本项目虚拟环境里的接单进程与网页进程（含子进程），再按同样流程重新启动；旧的网页窗口会显示已退出，直接关闭即可。每次启动的过程记录在 `.runtime/launcher.log`。首次启动接单服务时，窗口会要求输入 DeepSeek API Key（输入不回显），并用当前 Windows 账号加密保存在本机 `.runtime/deepseek-api-key.dpapi`；此文件不能复制到其他账号或服务器当作凭据。当前账号后续双击无需重输。worker 日志在 `.runtime/local-worker.out.log` 和 `.runtime/local-worker.err.log`。网页默认监听 `http://127.0.0.1:8000`。

## 2. 员工账号与访问

首次从服务器本机打开网页时，按页面提示创建管理员账号；也可以在本机终端执行 `python -m server.manage_users create-admin`（密码不回显，也不作为命令行参数）。管理员登录后在页面创建员工账号，员工可自行修改初始密码。每个员工只看自己的工单、批次、交付版本与每日统计；管理员可查看全部并按员工查看每日提交学院数、完成学院数和通过审计的教师记录行数。旧工单没有可靠的提交人信息，只有管理员可见。

网页的员工账号登录仅允许本机 HTTP 或 HTTPS。默认服务只监听 `127.0.0.1:8000`；多台员工电脑访问时，应在服务器配置 HTTPS 反向代理，并将它转发到本机端口。不要把 8000 端口的 HTTP 服务直接开放到局域网或互联网。教师记录数按完成工单统计，复采会再次计入，因此它表示工作量，不表示跨工单去重的人数。

### 反向代理部署

网页按请求来源判断"是否本机请求"：首次创建管理员、worker 续租与同步接口只接受本机。反向代理也从 127.0.0.1 连进来，所以程序把带有 `X-Forwarded-For`、`X-Forwarded-Proto`、`X-Forwarded-Host`、`X-Real-IP` 或 `Forwarded` 头的请求一律当作远程请求。代理必须设置这些头：

- 缺少 `X-Forwarded-For` 等转发头时，远程访问会被当成本机请求，能调用只限本机的接口；
- 缺少 `X-Forwarded-Proto` 时，登录会被当成明文 HTTP 拒绝。

nginx 示例（证书路径与域名按实际填写）：

```nginx
server {
    listen 443 ssl;
    server_name faculty.example.lan;
    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

网页服务（uvicorn）默认只信任来自 127.0.0.1 的转发头。代理不在本机时，启动前设置环境变量 `FORWARDED_ALLOW_IPS=<代理地址>`，并让 8000 端口只对代理开放。续租接口的 `lease_seconds` 限定在 30 到 900 秒之间。

`双击启动局域网服务.bat` 的"局域网"是历史叫法：它默认只监听本机 127.0.0.1，多台电脑访问请按上面的方式部署代理。

## 3. 图片文字与邮箱复核

工单因图片简介的低置信 OCR 行或邮箱冲突被挡住时，工单进度会显示“核对图片文字与邮箱”。提交员工可对照原图保存决定；管理员可查看并复核全部工单。完成该工单的全部图片复核后，点击“重新采集此工单”会使原工单重新入队，保留提交人和原始证据，重新执行确定性审计。原图或 OCR 已变化时，旧决定不会直接套用。旧的 `scratch` 验收材料没有员工归属，管理员可从网页中的“查看历史图片复核材料”进入；`8765` 是先前的本机独立页面，不再作为新员工工单入口。图片复核只解决其对应的证据问题，其他分支、身份或覆盖审计未通过时仍会阻断交付。

分段平铺名录（一篇文章里依次平铺多位教师，或姓名只写在简历图里）使用同一复核页：只在图片中的姓名由本地 OCR 读取，能与该教师自己的文字邮箱全拼核对上时自动采信，核对不上时页面会出现标注“姓名行”的卡片，对照原图改正后保存即可。

网页右侧默认显示本浏览器的当前请求；没有活动请求时显示“待提交”。已结束的旧批次可从“历史工单”主动查看，不会自动占据新任务进度。顶部“后台自动接单在线”取自常驻 worker 的实时心跳；显示离线时提交会入队等待，可重新双击上述 BAT 启动消费者。提交单院或批量任务都必须填写有效的学院官网入口 URL，缺少入口的批量请求整批不会入队。

## 4. 常驻 worker（服务部署或排障）

```powershell
$env:DEEPSEEK_API_KEY = "<你的 Key>"
python -m server.agent_worker
```
worker 常驻轮询 `server/tasks.db`；使用锁定依赖中的 Harness 可执行程序与项目独立 `.runtime/dsh-home`，自动插入项目的 Python Playwright MCP 插件。先安装本环境的 Chromium。`DSH_BIN`、`DSH_HOME` 仅用于明确的自定义部署，不再自动拾取 PATH 上其它版本的 dsh 或用户全局 profile。
它不需要任何反向 Webhook，因此不受 IDE 宿主平台的唤醒限制。
按最新调度要求，worker 对全部待采集学院并行启动独立 Harness 会话，不设置项目级并发上限。每个工单的中间文件隔离在 `server/outputs/tasks/<task_id>/`，同一大学的最终 Excel 写入按序执行。服务器会话只负责结构侦察，确定性采集与交付由 worker 执行；这与 IDE 原生 Subagent 的完整履约职责不同。

侦察由代码分阶段执行：先读取 `server/recon_discovery.md` 核实全部名录 URL，再为每个分支用新的短上下文执行 `server/recon_instructions.md`。阶段只提供对应的真实浏览器工具；每个 URL、卡片选择器与实际下一页都须有本轮浏览器证据。确定性代码合并通过门禁的分支计划，再执行采集。无效计划或未解决审计最多返回侦察补缺两轮（`--max-recon-repairs`），全程保留原工单租约。

侦察默认不按固定请求数或 Token 数截断正常学院。入口发现只提交实际承载名录的页面，避免把菜单页和个人详情页重复当成名录分支；分支验证失败后保留同一工单内已核实的其他分支，只补有问题的分支。补缺反馈只传给对应阶段；若连续两轮仍是同一问题且没有新增已核实分支，停止无进展的重试。运维如需额外费用保险，可同时设置 worker 参数 `--max-recon-calls` 与 `--max-recon-tokens`；两者默认均为 `0`（关闭）。启用后按学院累计计数，缓存输入也计入 Token，达到阈值时失败并保留用量审计。Token 在每次请求完成后检查，因此不是严格费用上限。

**总时限与补缺**：单个学院工单最长 45 分钟（`--max-task-seconds`），单次侦察最长 900 秒（`--recon-timeout`），审计失败后最多补缺两轮。每轮补缺开始前，worker 会为之后的确定性采集预留 600 秒（`--harvest-reserve-seconds`）：剩余时间不够一轮侦察（至少 180 秒）加采集时，不再开始新一轮补缺，工单以 `repair_skipped_task_deadline` 结束，而不是在侦察中途被总时限腰斩；够用但不足 900 秒时，这一轮侦察按剩余时间缩短。

空闲时 worker 查询队列的间隔从 0.2 秒逐步放宽到 2 秒，有新工单后恢复；没有可领工单时不占数据库写锁。

## 5. 资源与用量保护

以下各项 2026-09-29 起默认开启，均可关闭。全部学院仍同时开工，以下几项只限制共享资源：
- 按站点限流：同一大学站点（如 `cs.x.edu.cn` 与 `law.x.edu.cn` 同属 `x.edu.cn`）上，本 worker 所有学院同时打开的详情页合计不超过 8 个。环境变量 `HARVEST_PER_SITE_CONCURRENCY` 可调，设为 `0` 关闭。单院 `profile_concurrency` 仍然有效。
- 内存保护：已有学院在跑、且可用物理内存低于 1024MB 时，worker 暂缓领取新工单，内存恢复后继续；第一个学院不受影响。参数 `--min-free-memory-mb`，`0` 关闭。
- 侦察用量告警：单学院侦察累计超过 100 万 Token 或 150 次模型请求时，只告警、不中断。告警写入 worker 日志和任务目录的 `recon-usage-alert.json`，并随工单统计保存为 `recon_usage_alerts`。参数 `--recon-alert-tokens`、`--recon-alert-calls`，`0` 关闭。需要硬性熔断时，仍使用上面的 `--max-recon-calls` 与 `--max-recon-tokens`。
- 批量提交上限：单次最多 30 个学院（`server/config.json` 的 `max_batch_colleges`），同一学院重复出现的批次会整批拒绝。
- 单会话请求上限：入口发现会话最多 40 次模型请求，单个分支验证会话最多 25 次。超过时判定页面结构超出当前计划格式，该学院直接失败、不再补缺，审计状态为 `recon_phase_limit`。参数 `--max-discovery-calls`、`--max-branch-calls`，`0` 关闭。
- 侦察超时会立即结束对应的 Harness 会话，已发生的用量照常记入 `recon-attempts.json`；以前超时后会话仍在后台继续消耗，且用量记为未知。
- 侦察截图只发送当前视口或指定元素的 JPEG（高不超过 2000px），不再发送整页 PNG；同一页面的重复 HTML 快照只保存一份。

## 6. 启动自检与 Excel 文本安全

常驻 worker 领取工单前，会检查算法层、pypinyin、OCR、PyMuPDF、Harness 运行时、`DEEPSEEK_API_KEY` 和 Playwright Chromium 是否齐全。任一缺失时，worker 在 `.runtime/local-worker.err.log` 写明原因并退出（退出码 2），不会带着降级的依赖继续采集；双击启动脚本会等待自检结果并提示。排障时可加 `--skip-self-check` 跳过。Excel 导出时，抓取到的文本一律按文本写入（以 `=` 开头的内容不会变成公式），xlsx 无法保存的控制字符会被去掉，不再导致整份大学表导出失败。

## 7. 侦察浏览器工具与运行时

`server/recon_browser_mcp.py` 提供真实 Playwright 的精简 MCP 工具，文本按范围返回且单次最多 8000 UTF-8 字节；导航单次最多读取 64 个链接，超过返回预算仍明确给出未读范围。完整 HTML/JSON/截图保存到 `server/outputs/recon-evidence/<phase_session_id>/`。此预算只作用于侦察上下文，教师完整简介与 Excel 不受影响。运行时叠加 `server/recon_runtime.cordis.yml`，关闭无关技能目录、工程文件工具与通用指令注入，并保存每阶段及补缺轮次的真实 Token 元数据。实测结果见 `docs/audits/2026-09-26-harness-context-audit.md`；本次异常用量与修订见 `docs/audits/2026-09-29-nxist-recon-cost.md`。

运行补丁用 `insert` 安装 `dsh-mcp-client`，默认独立 ID 为 `faculty-recon-browser`，无需事先手工配置浏览器插件。阶段与证据会话通过启动参数显式传递。每次模型运行前先检查实际合成配置：浏览器缺失/重复、无关工具或通用指令注入仍启用、补丁未生效时直接失败，不花 Token 试错。自定义 profile 也须通过此检查。先在该环境运行 `python -m playwright install chromium`。编辑侦察 MD 或 Python 后需重启常驻 worker。

发现阶段的 `browser_navigate` 同时返回页面摘要，本会话已返回过的链接和重复的菜单、页脚文字不再重发；`browser_navigation` 可用 `query` 按关键词筛链接；`browser_survey` 一次查看最多 8 个候选页。个人页正文验证未通过时返回 `reason`（如 `hidden_content`、`network_not_idle`）和阻断请求的类型。

## 8. 固定运行环境

常驻进程使用固定环境，不能从会随启动器退出而清理的临时环境启动后台子进程。本机已使用 `.runtime/worker-venv`，依赖来自 `requirements.lock`：
```powershell
uv venv .runtime/worker-venv --python 3.12
uv pip sync --python .runtime/worker-venv/Scripts/python.exe requirements.lock
.runtime/worker-venv/Scripts/python.exe -m playwright install chromium
.runtime/worker-venv/Scripts/python.exe -m server.agent_worker
```
启动前在进程环境设置 `DEEPSEEK_API_KEY`；不要写入项目文件。

| 命令 | 用途 |
| :--- | :--- |
| `python -m server.agent_worker` | **常驻**（生产形态） |
| `python -m server.agent_worker --once` | 只处理一个工单后退出（**排障用，勿用于生产**） |

> ⚠️ **用 `--once` 跑生产会导致"提单后一直没人接单"**——它处理一个工单就退出。
> 生产环境应由 systemd 守护（Phase 4，尚未交付）。

## 9. 恢复、续采与严格空名录

2026-09-27 新流程已增加覆盖账本、工单页面断点、经本轮浏览器核验的结构方案复用、局部分支补缺及大学交付版本。方案现永久保存于 `server/outputs/plan-cache/plans.sqlite3`，保留历史版本，每次复用仍须本轮浏览器验证。运行条件与文件含义见 [可验证运行流程](architecture/verifiable-harvest-runtime.md)，实测范围见 [实现审计](audits/2026-09-27-verifiable-harvest.md)。

普通 URL 分页中断后可重新验证最后成功页，再续采后续页；同 URL AJAX、变化或损坏的前沿会从分支入口重走。普通单页名录只有明确可见的空态提示、独占容器及本轮独立浏览器证据，才允许确认为空；空白页面、隐藏内容、未加载图片或仍在请求的页面继续阻断。全学院零人结果进入复核，不自动导出，也不重复调用模型补结构。侦察协议更新后，旧结构方案永久保留，但必须符合当前契约才能复用。

## 10. 测试与历史回归记录

```powershell
uv run --no-project --with-requirements requirements-dev.lock --python 3.12 python -m pytest -q
```
测试依赖在 `requirements-dev.lock`，运行环境的 `requirements.lock` 不含 pytest；浏览器测试的标记、ruff 与 CI 见 README“开发与测试”。2026-09-29 起全量为 **536 passed、3 skipped**，其中启动真实 Chromium 的 66 项标记为 `browser`。2026-09-24 的隔离环境回归结果为 **137 passed、1 skipped**。开发机原有 NumPy 2.5.3 超出 SciPy 1.15.2 声明的 `<2.5` 范围，因此以锁定环境结果为准。

最新 2026-09-27 固定环境全量回归为 **365 passed、1 skipped**，生产队列与测试数据库隔离；包含分页续采、严格空态、永久方案库、逐详情正文范围证明、浏览器脚本隔离和交付门禁。唯一跳过来自缺少历史源码归档。六所预先冻结的新学校随机学院真实测试为 1 校独立通过、5 校保留阻断；本轮没有 DeepSeek API 请求，不能等同 Harness 自主侦察或全国适配率。见 [随机学院验证](audits/2026-09-27-random-college-validation.md) 和 [先前实现审计](audits/2026-09-27-verifiable-harvest.md)。

2026-09-28 前端更新后的固定环境全量回归为 **376 passed、1 skipped**；本地浏览器验证默认页、历史待复核和重置行为，旧问题版本下载仍返回 409。此轮没有提交生产工单或调用 DeepSeek；详情见 [前端与接单状态审计](audits/2026-09-28-web-current-request.md)。

## 11. 手工/排障：学院工单采集

> 常规履约走上面的常驻 worker。手工路径用于排障，或由 IDE 主控按 [`faculty-browser-harvest/SKILL.md`](../.agents/skills/faculty-browser-harvest/SKILL.md) 派发一学院一 Subagent。旧 `auto_dispatcher.py` 现仅巡检，不会认领或采集；旧 HTTP 批量脚本不用于工单履约。
```powershell
python -m server.browser_batch_tool --plan <已核实计划.json> --output-dir server/outputs --task-id <task_id> --worker-id <worker_id>
```
已认领工单须先设置 `HARVEST_LEASE_TOKEN` 环境变量；详细步骤见工具目录。

> **当前验收边界**：已知生僻姓案例和同构卡片中的未列姓氏已有离线回归；可疑分页与缺少卡片选择器会阻断交付。真实网站的完整分支覆盖、WAF 站点和 Linux 常驻运行仍需单独端到端验证。历史 `roster_card_mismatch` 记录见 [五方审计](audits/2026-09-24-five-perspective-review.md) §A3。
