# MyPlan · 高校学院师资采集

输入大学、学院、学院链接及可选师资链接，目标是正确、尽可能完整地采集目标学院的全部师资及姓名、职称、邮箱、大学、学院、个人主页和完整简介，按大学汇总。

**当前完整性与泛化能力仍待验证。** 项目目标、术语和类型库定义见 [CONTEXT.md](CONTEXT.md)；类型库现有能力及差距见 [架构说明](docs/architecture/type-library.md)。当前第一优先是 [清理持续维护以来的全部改动](docs/improvement-plan.md)，开发 agent 从 [AGENTS.md](AGENTS.md) 按用户实际任务工作；本轮文档整理不执行清理审计或追加修复。

## 现有本机使用入口

1. Windows 本机双击 `双击启动局域网服务.bat`，启动后台 worker 和网页。首次启动会要求 DeepSeek API Key，按当前 Windows 账号加密保存在本机运行目录。
2. 使用启动窗口实际打印的地址打开网页，填写姓名。提交学院或从表格粘贴批次；批量入口支持三列和可选第四列，同学院的多条师资链接可分行输入。
3. 查看本浏览器当前请求，旧批次从“历史工单”进入。任务要求复核时，对照来源和证据保存决定，再按页面提供的重采入口继续。
4. 结果页提供可下载的大学版本。当前交付门禁与可信部分交付的目标仍有差距，业务含义见 [CONTEXT.md](CONTEXT.md)，已复现差异见 [契约审查记录](docs/audits/2026-10-11-chain-contract-review.md)；程序完成不等于已经独立核实全院采齐。

现有工作簿通常包含“教师列表”和“无邮箱教师列表”；超长简介使用额外的“完整简介”支持工作表保存全文，依据为 [导出实现](server/excel_export.py) 与 [长简介存储](server/biography_workbook.py)。目前没有预先已知的教师总人数；两表相加、工单完成、导出成功或测试通过只说明各自检查范围。缺少入口、分类、分页和名单条目覆盖依据时，应报告“完整性未验证”。

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
| 持续维护改动清理、根因分类与后续验证 | [维护计划](docs/improvement-plan.md) |
| 类型库当前实现与目标差距 | [类型库架构](docs/architecture/type-library.md) |
| 现有采集调用链与能力边界 | [采集流程](docs/architecture/college-harvest-workflow.md) |
| 代码调用边界与模块 | [模块索引](AGENT_HANDOVER.md) |
| 文档职责、ADR 与历史资料 | [文档导航](docs/README.md) |
| 运行、复核和排障 | [运维手册](docs/operations.md) |

`server/` 承载网页、队列、worker、侦察、采集、审计与交付；`Opus/` 为部分算法实现；`tests/` 保存自动化回归；`scripts/` 保存本机服务脚本。三份 `server/recon_*.md` 是程序加载的运行指令，修改时按运行逻辑验证。
