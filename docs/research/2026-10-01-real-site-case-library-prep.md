# 真实官网案例库：离线准备与现有能力盘点

日期：2026-10-01。任务：真实官网案例驱动的类型库与跨学院模板补全（第一批 10 所高校、各一学院）。

## 实网状态

本会话云端环境的网络策略拒绝全部高校官网，本文件**不含任何实网验收结论**。

- 2026-10-01T19:38:44Z，项目 Playwright Chromium 访问 18 个站点：清华、北大、浙大、上交、南大、武大、华科、川大、厦大、山大、吉大、淮阴师院、华东师大、港中文、NUS、MIT 的 HTTPS 均为 `net::ERR_TUNNEL_CONNECTION_FAILED`；南师大、安大的 HTTP 返回代理 403。
- 19:40 后复测浙大、武大、安大仍被拒绝。浏览器本身启动正常，阻塞来自环境网络权限。
- 用户随后切换了项目默认环境；实网案例由新环境中的续接线程执行。

## 运行环境备注（仅本地环境，不进仓库代码）

- `pip install -r requirements-dev.txt` 若因 Debian 自带 PyYAML 失败，先 `pip install --ignore-installed PyYAML==6.0.3`。
- 仓库锁定 `playwright==1.47.0`，需要 `chromium-1134`；容器只预装 1194。1194 的完整 Chrome 已移除旧 headless，可把 `/opt/pw-browsers/chromium-1134/chrome-linux/chrome` 指向 `chromium_headless_shell-1194` 的 `headless_shell`（同目录其余文件一并软链）。不要 `playwright install`。
- 基线：当前 main（`a25084c`）全量 `pytest` 992 passed、3 skipped。

## 现有能力盘点（依据代码与离线测试，非实网）

| 页面形态 | 现有机制 | 计划写法 | 离线证据 |
| --- | --- | --- | --- |
| 普通链接列表／卡片 | `browser_batch_tool` 卡片对账 + `faculty_extractor` | `card_selector` + `single_page` | `ordinary_full_biography`（合成）；湖南中医药大学信息学院首页保存 HTML（`real_source.json`，60 卡） |
| 表格名录 | 同上，行即卡片 | `card_selector: "tbody > tr"` | `table_roster`（合成） |
| URL 变化分页 | 下一页点击、页数上限、断点续采 | `next_selector` + `max_pages` | `pagination_url_changes`、`test_pagination_resume.py` |
| 同 URL Ajax 分页／轮播 | 卡片变化判定翻页 | `next_selector` | `ajax_same_url`、`test_pagination_change_by_cards.py`、`test_batch_carousel_pagination.py` |
| Tab 切换 | 首次点击后再取卡片 | `initial_click_selector`（缓存计划不复用此交互） | `test_browser_batch_tool.py` |
| 系所／科室导航 | 有界子分支 | `kind: navigation` + `child_link_selector` + `child_branch` | `test_browser_batch_tool.py` |
| 无个人主页名单 | 名录页即主页，简介邮箱留空 | 普通卡片 | `profileless`、`test_supplementary_lists.py` |
| 多人共享详情页 | 共享页字段不静默分配 | 普通卡片 | `shared_detail` |
| 一篇正文平铺多人 | 分段切分，图片姓名本地 OCR | `kind: segmented` + `container_selector` + `record_start_selector` | `test_segmented_roster*.py` |
| 图片名录 | 截图 + 本地 OCR，人工核对人数 | `browser_poster_tool` + `poster_reviews`/`verified_candidates` | `unreviewed_poster`、`test_browser_poster_tool.py` |
| 个人页图片简介 | 原图读取 + OCR，审核页 | 自动 | `test_image_biography.py`、`test_image_bio_review.py` |
| 嵌入／下载 PDF 多人名录 | `pdf_roster`（任务 A，PR #5 已合并） | 普通分支，读取浏览器观察到的 PDF | `test_pdf_roster*.py` |
| 空名录分支 | 严格空态核验 | `empty_state_selector` + `roster_container_selector` | `test_empty_roster*.py` |

已知由其他线程负责、本任务只转交证据不改模块：延迟加载的分页／系所链接与 20 页上限（审计 A2/A1）、学院下载 404（A4）、HTML 邮箱歧义复核（PR #6）、多人 PDF 名录（任务 A 线程）。

## 案例库落点

- 结构观察：`server/site_type_registry.py` 的 `site_type_observations`（`observed/accepted/rejected`）。注意：当前生产代码没有调用 `SiteTypeRegistry.record()`，只调用失败归类；案例入库需要一个写入入口，续接线程实测后再决定是否补最小 CLI。
- 可复用计划：`server/recon_plan_cache.py`（必须有完整发现证据，且每次复用重新由浏览器验证）；验收夹具格式见 `tests/fixtures/harvest_acceptance/cases.json`（`evidence_kind` 区分 `synthetic` 与 `real_saved_html`）。
- 真实证据（原始 HTML、截图、捕获时间）存隔离输出目录，不提交教师个人数据；提交的夹具须脱敏。

## 每个案例的运行流程

1. Playwright 打开官方入口，核实名录分支、分页、页面用途与师资范围，保存 URL、UTC 捕获时间、快照。
2. 写计划 JSON，用 `HARVEST_TASK_DB=<隔离路径>` 运行 `python -m server.browser_batch_tool --plan <plan> --output-dir <隔离目录>`（不带工单参数）。
3. 只依据审计输出的 `branches`、`roster_observations`、`unresolved`、`delivery_ready` 判定是否已支持。
4. 已支持：整理计划与验收案例；未支持：保存最小复现，定位缺口，局部修复加回归，或转交负责线程。
