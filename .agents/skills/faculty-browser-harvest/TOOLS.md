# 采集工具目录（按页面分支读取）

Subagent 先用宿主真实浏览器确认官方页面和控件，再打开本文件对应段落。工具之间共享统一的学院任务与本地审计文件，不要求模型读取完整 HTML 或简介。

## 浏览器批量工具：名录、分页、个人主页

**触发**：已确认一个或多个教师名录 URL，并能判断每个分支是单页还是有唯一的下一页控件。工具命令行使用新的真实 Playwright 浏览器上下文重新访问这些 URL；Python 解析模块只处理该浏览器取得的 HTML。若宿主未来能移交 Playwright `BrowserContext`，也可直接调用 `run_plan(plan, browser_context, output_dir)` 复用该上下文。

先保存计划 JSON，例如：

```json
{
  "school": "样本大学",
  "college": "样本学院",
  "profile_concurrency": 4,
  "source_kind": "university_faculty_roster",
  "branches": [
    {"url": "https://faculty.example.edu/roster", "next_selector": ".pagination .next", "max_pages": 20, "card_selector": "main .faculty-card", "profile_content_selector": "article .body"},
    {"url": "https://faculty.example.edu/other", "single_page": true, "card_selector": "main .faculty-card", "profile_content_selector": "article .body"}
  ]
}
```

`next_selector` 必须由 Agent 在真实页面核实为唯一目标。动态 Tab 可在分支中另填 `initial_click_selector`；此选择器同样必须唯一。`max_pages` 是安全上限，不是“采集完毕”的证明。不能确定页尾时，保留 `unresolved` 并交回 Agent。若页面依赖滚动加载或更复杂交互，先由 Agent 用浏览器核实并处理，不要臆造一个静态翻页选择器。

每个名录分支应提供 Agent 在浏览器中核实的 `card_selector`，工具会对账可见卡片数、提取候选数和无法解释的卡片。`source_kind` 取 `university_faculty_roster` 或 `hospital_doctor_roster`；后者只抽取候选，不默认取得大学教师资格。

科室或系所入口页使用有限的导航分支，不让工具递归整站：

```json
{
  "url": "https://hospital.example.cn/departments",
  "kind": "navigation",
  "child_link_selector": ".departments a",
  "max_children": 30,
  "child_branch": {"single_page": true, "card_selector": "main .doctor-card", "source_kind": "hospital_doctor_roster"}
}
```

导航页只产生子分支和快照，不直接作为教师名录；子分支数量超过上限或选择器为空会阻止交付。

如果个人主页由 JavaScript 延迟渲染，可在计划顶层填写 `profile_ready_selector`，指定正文加载完成后才出现的元素。缺少人名的详情页会被标为 `profile_identity_not_found`，不能作为空邮箱页直接交付。

已认领工单时，采集期间需要用同一租约续租。环境变量在同步完成后再清除：

```powershell
$env:HARVEST_LEASE_TOKEN = '<认领工单返回的 lease_token>'
python -m server.browser_batch_tool --plan <计划文件.json> --output-dir server/outputs --task-id <task_id> --worker-id <worker_id>
```

没有工单的本地试跑可省略 `--task-id` 和 `--worker-id`。失去租约时，工具将 `delivery_ready` 改为 `false`，不得同步。

命令只打印紧凑 JSON。完整数据在 `records_path`，访问证据及未解决项在 `audit_path`，渲染后的原始页面在 `snapshots` 指向的文件中。`delivery_ready=false` 时不生成 Excel，也不准同步完成状态。`coverage_scope=supplied_roster_branches_only` 表示工具仅核对计划给出的分支；Agent 仍须确认官网没有其他教师分支。

## 分段平铺名录：一篇正文里多位教师

**触发**：一篇文章里依次平铺多位教师，每人没有独立外层容器（照片或简历图、姓名、简介段落、邮箱行是同级段落），或姓名只写在简历图里、正文只有邮箱行。此时不写 `card_selector`，改用分段分支：

```json
{"url": "https://www.example.edu.cn/jxgc/info/1232/15867.htm", "kind": "segmented",
 "container_selector": "div.v_news_content", "record_start_selector": "div.v_news_content img",
 "single_page": true}
```

`record_start_selector` 必须命中每位教师记录的第一个元素（姓名写在照片前时选姓名行）。工具从一个起点切到下一个起点之前，简介原文与唯一邮箱按位置归属；图片中的姓名本地 OCR 读取，不能与本段邮箱全拼核对时进入图片审核页，由人核对原图姓名行后才可交付。记录链接到个人主页时不用本分支，按普通名录处理。

## 个人页正文范围

**触发**：名录实际链接到个人页。打开工具给出的最多两个不同详情样例，核实完整正文 `profile_content_selector` 并逐一执行 `browser_verify_profile`；URL 与 CSS 都用本轮实际证据。没有个人页的名单保持如实留空。

正文没有独立包装节点时可成对声明 `profile_content_start_selector`，必须是唯一正文根的唯一直接子节点；解析完整保留其后全部节点。例如核实到结构分隔线后，声明 `profile_content_selector: ".content"` 与 `profile_content_start_selector: ".content > hr"`，不按文字或长度裁剪。

批量工具逐详情复验计算样式、范围、加载和稳定性，有范围声明的缓存也须本轮访问。无证明、隐藏、空或范围冲突时保留原 HTML 与观察并阻断，原生 HTML 兜底不能放行。审计包含范围 CSS、原始与所选 HTML 摘要及实际验证时间；完整简介留在本地记录。

## 截图与 OCR：图片名录

**触发**：浏览器确认名录由图片承载，DOM 名录提取无候选人。使用浏览器截图和本地 RapidOCR，只取得原始 OCR 行，不自动决定人名或职称。

```powershell
python -m server.browser_poster_tool --url <官方图片名录页> --output-dir server/outputs
```

工具滚动页面以触发懒加载，等待图片载入，再给出 `screenshot_path`、`ocr_path`、`unloaded_image_count` 和 `warnings`。截图与 OCR 原文只用来判断名录结构，Subagent 不把其中的人名、职称写进计划。批量工具与服务器侦察使用同一口径，计划出现 `verified_candidates` 或 `poster_reviews` 会直接报错。

- 每人一段的图文记录（照片或姓名图片在前、简介在后）按上文 `segmented` 分支提交，姓名由本地 OCR 读取，不能核对的进入图片审核页由人核对原图。
- 整张海报承载的名单，目前没有确定性读取模块。该分支保持未解决（`no_candidates_on_roster_page`），学院不能导出完整成果；在结果中标明原因，不凭 OCR 行或截图补人。

医院医生名录若要计入大学教师表，计划还必须逐人提供当前个人页中的大学任职证据：

```json
{
  "verified_appointments": [
    {"name": "张富有", "profile_url": "https://hospital.example.cn/p/1", "quote": "现任样本大学样本学院教授", "academic_title": "教授"}
  ]
}
```

仅有医生、硕士生导师、兼职教授或医院与大学共建关系不能计入；没有证据的医院医生会进入 `excluded`，证据疑似但未核实的进入 `unresolved`。

## 本地同步：完整记录不经过模型上下文

**触发**：审计文件显示 `delivery_ready=true`、`unresolved=[]`，且学院官网分支覆盖已由 Subagent 核对。同步工具直接从 `records_path` 读取十列数据，并 POST 到本地后台；它不会访问高校网站。

```powershell
python -m server.browser_batch_sync --audit <audit.json> --task-id <task_id> --worker-id <worker_id>
Remove-Item Env:HARVEST_LEASE_TOKEN
```

脚本仅接受本机 HTTP 后台，默认地址与 worker、Web 启动器共同读取端口配置（仓库默认 8000，可由忽略文件 `.runtime/local-services.json` 覆盖）；显式 `--base-url` 也必须使用本机地址。校验文件位于 `server/outputs`、十列字段与人数守恒，以及提供的覆盖账本无未解决项。同步后检查 `delivery_scope.state`：只有本批目标学院全部完成且审计通过，才返回大学版本的 `university_download_url` 与 `university_excel`。`collecting` / `blocked` 时仅学院中间成果已同步。大学版本保存在 `server/outputs/versions/`，当前最近完成学院汇总另存为 `<大学名称>.xlsx`；前台本批下载指向版本文件。旧租约被拒绝，原大学表保留。运行与恢复的具体边界见 [可验证运行流程](../../../docs/architecture/verifiable-harvest-runtime.md)。旧 HTTP 工单脚本的导入和命令行入口均已阻断，不能作为备用路径。
