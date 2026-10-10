# MyPlan · 高校学院师资采集

输入大学、学院、学院链接及可选师资链接，采集有来源证据的官方师资信息，按大学汇总。

**当前仍在改进采集准确度与完整性，多个学院尚不能可靠完成，未进入常规业务验收。** 项目目标与术语见 [CONTEXT.md](CONTEXT.md)。开发 agent 从 [AGENTS.md](AGENTS.md) 按任务分工：采集维护处理实际报错与脚本优化；全链路审查 agent 执行其 [五项计划](docs/improvement-plan.md)。

## 现有本机使用入口

1. Windows 本机双击 `双击启动局域网服务.bat`，启动后台 worker 和网页。首次启动会要求 DeepSeek API Key，按当前 Windows 账号加密保存在本机运行目录。
2. 使用启动窗口实际打印的地址打开网页，填写姓名。提交学院或从表格粘贴批次；批量入口支持三列和可选第四列，同学院的多条师资链接可分行输入。
3. 查看本浏览器当前请求，旧批次从“历史工单”进入。任务要求复核时，对照来源和证据保存决定，再按页面提供的重采入口继续。
4. 结果页提供可下载的大学版本。当前交付门禁与可信部分交付的目标仍有差距，具体见改进计划；程序完成不等于已经独立核实全院采齐。

现有工作簿通常包含“教师列表”和“无邮箱教师列表”；超长简介使用额外的“完整简介”支持工作表保存全文。两张教师表的人数相加只核对本次输出集合，不证明全院没有漏人。

启动、配置、环境核对、复核与排障方法统一见 [运维手册](docs/operations.md)。仓库更新不等于本机进程已经加载新版本；实际更新运行环境时按任务范围操作并记录版本。

## 开发与验证

使用 Python 3.12 和锁定的开发依赖。以下为 Linux／CI 对应命令，Windows 将解释器路径换为 `.venv/Scripts/python.exe`：

```sh
uv venv --python 3.12
uv pip sync requirements-dev.lock
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -m "not browser" -q
.venv/bin/python -m pytest -m browser -q
.venv/bin/python -m ruff check .
```

运行依赖来自 `requirements.txt`／`requirements.lock`，开发与测试依赖来自 `requirements-dev.txt`／`requirements-dev.lock`。变更依赖时同步重建对应锁文件；命令及实际工具配置见 [pyproject.toml](pyproject.toml) 和 [CI 工作流](.github/workflows/tests.yml)。

启动真实 Chromium 的测试须标记 `browser`，单元测试任务会用无浏览器环境验证这一边界。提交后的必需检查、当前 head 与最新 main 对齐状态直接查 GitHub；测试约定也须对照当前需求，不能以旧测试通过替代业务验收。

## 按需导航

| 需要了解 | 入口 |
| --- | --- |
| 目标、术语与交付含义 | [CONTEXT.md](CONTEXT.md) |
| 全链路审查 agent 的五项工作与证据 | [专项计划](docs/improvement-plan.md) |
| 代码调用边界与模块 | [模块索引](AGENT_HANDOVER.md) |
| 文档职责、ADR 与历史资料 | [文档导航](docs/README.md) |
| 运行、复核和排障 | [运维手册](docs/operations.md) |

`server/` 承载网页、队列、worker、侦察、采集、审计与交付；`Opus/` 为部分算法实现；`tests/` 保存自动化回归；`scripts/` 保存本机服务脚本。三份 `server/recon_*.md` 是程序加载的运行指令，修改时按运行逻辑验证。
