# 采集工具目录（按页面分支读取）

先读 [SKILL.md](SKILL.md) 及根目录 [AGENTS.md](../../../AGENTS.md)、[CONSTITUTION.md](../../../CONSTITUTION.md)。下文描述当前工具和门禁；业务允许的部分交付、四列入口映射等目标，不能仅因写进文档就视为已经实现。工具所取得的原始页面、完整简介和复核证据保存在本地，不要求结构侦察输出教师字段。

## 浏览器批量工具：名录、分页、个人主页

**触发**：已确认一个或多个本学院官方师资名录 URL，已核实每个分支的结构与分页。第四列给定链接仍须与学院其他师资入口核对；官网明确列为师资的兼职、外聘等分支不能因用工类别被遗漏。

CLI 使用新的 Playwright 浏览器上下文重新访问 URL；解析模块处理该浏览器取得的 HTML。若调用方实际持有可用的 `BrowserContext`，可通过 `run_plan(plan, browser_context, output_dir)` 使用该上下文；不默认继承宿主会话或 Cookie。

先保存计划 JSON，例如：

```json
{
  "school": "样本大学",
  "college": "样本学院",
  "source_kind": "university_faculty_roster",
  "branches": [
    {"url": "https://faculty.example.edu/roster", "next_selector": "main .pagination .next", "card_selector": "main .faculty-card", "profile_content_selector": "article .body"},
    {"url": "https://faculty.example.edu/other", "single_page": true, "card_selector": "main .faculty-card", "profile_content_selector": "article .body"}
  ]
}
```

以上 URL 与 CSS 都是占位示例，必须换成本轮真实验证值。`next_selector` 必须唯一且实际翻页成功；动态 Tab 可加同样已验证且唯一的 `initial_click_selector`。没有个人主页时不填写个人页正文选择器。复杂动态交互先在浏览器核实，不臆造静态选择器。

未被调用方明确限定范围时，省略 `max_pages` 和 `max_children`，按真实终止证据完成采集。它们限制的是访问范围；达到上限不能证明采齐，现有工具会将未完成范围记为未解决。

普通名录使用已验证的 `card_selector`，工具对账卡片、候选和未能解释的条目。`source_kind` 取 `university_faculty_roster` 或 `hospital_doctor_roster`。后者只表示医生来源，不自动证明该人是目标大学、学院的师资；不能为通过资格检查把医生名录伪标为大学师资。

系所等入口页可使用导航分支，将范围限定在实际验证的名录链接中：

```json
{
  "url": "https://faculty.example.edu/departments",
  "kind": "navigation",
  "child_link_selector": "main .departments a.roster",
  "child_branch": {"single_page": true, "card_selector": "main .faculty-card"}
}
```

导航页产生子分支，不直接作为教师记录；先证明链接通往多人名录，再核实子名录的卡片和分页。个人主页链接不能当作导航子名录。不同结构不能未经验证就共用一套选择器。

若个人页由 JavaScript 延迟渲染，可在计划顶层声明经核实的 `profile_ready_selector`。缺少身份依据的详情不能当作正常空邮箱页交付。

已认领工单时，采集期间以同一租约续租；同步完成后再清除环境变量：

```powershell
$env:HARVEST_LEASE_TOKEN = '<认领工单返回的 lease_token>'
python -m server.browser_batch_tool --plan <计划文件.json> --output-dir server/outputs --task-id <task_id> --worker-id <worker_id>
```

已安排的本地试跑可省略 `--task-id` 和 `--worker-id`。失去租约时不得同步完成状态。

命令只打印紧凑 JSON。完整记录在 `records_path`，审计在 `audit_path`，原始页面在 `snapshots` 指向的文件中。当前工具在 `delivery_ready=false` 时不生成 Excel，也不接受同步为完成；已确认部分需要通过经实现和验收的部分交付路径输出，不能通过改写审计标志取得。`coverage_scope=supplied_roster_branches_only` 只说明计划内分支的覆盖范围，不证明学院全部入口都已发现。

## 分段平铺名录：一篇正文里多位教师

**触发**：一篇文章依次平铺多位教师，每人没有独立外层容器，或姓名只写在简历图里、正文仅有邮箱等信息。此时不写 `card_selector`，改用分段分支：

```json
{"url": "https://faculty.example.edu/teachers/article.htm", "kind": "segmented",
 "container_selector": "div.v_news_content", "record_start_selector": "div.v_news_content img",
 "single_page": true}
```

`record_start_selector` 命中每位教师记录的第一个元素，不能笼统包含正文里的所有装饰图。用 `browser_verify_segments` 核实；工具从一个起点切到下一个起点之前，按位置归属文字和邮箱。图片中的姓名由本地 OCR 读取，无法核对的进入图片复核；结构侦察不转写人名。记录链接到个人主页时按普通名录处理。顿号串联的名单使用承载名单的段落或单元格作为普通卡片，不混用分段文章模式。

## 个人页正文范围

**触发**：名录实际链接到个人页。用 `browser_verify_profile` 核实最多两个不同实际样例，只有一个时验证该样例。`profile_content_selector` 须覆盖完整正文，保留履历、联系方式与学术内容；不能只选方便提取的一段。

正文没有独立包装节点时，可配合 `profile_content_start_selector`；它须是唯一正文根的唯一直接子边界。解析保留边界之后直到容器结束的全部 DOM，不按词语或长度裁剪。例如已实际核实结构分隔线后，才声明 `profile_content_selector: ".content"` 与 `profile_content_start_selector: ".content > hr"`。

批量工具逐页复验范围、加载和稳定性，并保存原始页面和结构证明。范围、身份或字段归属冲突进入复核。官网缺少某个字段时如实留空；页面不可访问、正文未加载与官网没有该字段是不同状态，不能混为一谈。没有个人主页时，仍保留官方名单中能够正确归属的字段和来源。

## 图片、PDF 与 OCR

**触发**：浏览器确认正文或名录由图片、PDF 等资源承载。DOM 没有文字不能证明没有师资；核实原始资源已取得及其覆盖范围，OCR 结果须与原件核对。

截图辅助工具：

```powershell
python -m server.browser_poster_tool --url <官方图片名录页> --output-dir server/outputs
```

工具触发懒加载并报告 `screenshot_path`、`ocr_path`、`unloaded_image_count` 和 `warnings`。截图与 OCR 观察可用于判断结构，不能把人名、职称、表头或人工核数写进侦察计划；`verified_candidates`、`poster_reviews` 不属于当前模型计划 Schema。

- 每人一段的图文文章使用上文 `segmented` 分支；若每张原图经验证确为一位教师记录，也可按运行提示词中对应的图片卡片规则核实。
- 整份名单在一张或多张表格图片中时，当前已有 `image_table` 路径：

```json
{"url": "https://faculty.example.edu/teachers/table.htm", "kind": "image_table",
 "card_selector": "main .roster-table img", "single_page": true}
```

先用 `browser_verify` 确认 `image_table.valid=true` 且没有分页候选，选择器仅命中实际名录表格原图。本地流程按格读取并保存证据，无法核对的格子交人工复核。图片张数不是教师人数，结构验证也不保证 OCR 姓名正确。

- 嵌入 PDF 名录在当前采集流程中有专门读取路径，参数以运行提示词和 Schema 为准；检查原件、页数、记录划分和未解决单元格，不能凭“支持 PDF”就宣称全部解析正确。
- 无法按现有结构表达或无法稳定归属的一般海报、附件保留未解决原因和原始证据，不用模型转写字段来绕过当前提取契约。

## 来源身份与人工无师资判定

大学／学院官网明确列为本学院师资的兼职、外聘等人员属于目标范围。仅出现在医院医生、行政机构、新闻、校友或讲座名单中则不足以证明目标学院师资身份。混合来源和任职关系不明确时，保留逐人依据供复核。

当前底层资格模块可读取人工核实的任职证据，但 `verified_appointments`、姓名及任职引文不在结构侦察的 `submit_plan` 契约内，不能要求侦察模型生成这些字段。资格路径是否适配最新业务口径，需要结合当前代码和样本验收，不能靠改 `source_kind` 放行。

人工确认“无师资”使用已有人工判定入口，并保留判定记录。自动零人、访问失败、没有找到名录和一个分支的严格空态证明，都不能自动替代该判定。若旧界面仍写“没有专任教师”，应按最新业务范围核查并列为待修复问题，不能据此排除官网列明的兼职／外聘师资。

## 本地同步与当前交付边界

**触发**：当前审计满足 `delivery_ready=true`、`unresolved=[]`，且官网分支覆盖已经核对。同步工具直接从记录文件读取现有十列数据并提交本地后台，不访问高校网站。

```powershell
python -m server.browser_batch_sync --audit <audit.json> --task-id <task_id> --worker-id <worker_id>
Remove-Item Env:HARVEST_LEASE_TOKEN
```

脚本接受本机 HTTP 后台，默认端口与 worker、Web 启动器共用配置；显式 `--base-url` 也须是本机地址。它检查输出路径、现有字段格式、人数守恒和提供的覆盖证据；这些检查不替代教师正确性、字段归属和全院完整性的人工基准。

同步后检查实际 `delivery_scope.state` 和返回链接。当前大学版本受本批学院完成范围及身份复核门禁约束，`collecting`／`blocked` 不得报告大学完整交付。版本文件位于 `server/outputs/versions/`；以当前审计允许的版本为准，不用旧链接绕过新问题。

业务允许已确认部分先交付，不确定记录单独复核；若当前同步或导出仍整批阻断，应将其作为实现差异处理，保留已确认记录、缺失范围和问题证据。禁止通过删除未解决项、强改 `completed` 或人工伪造无师资来模拟该能力。恢复和版本机制的实现线索见 [可验证运行流程](../../../docs/architecture/verifiable-harvest-runtime.md)，以当前代码与验证结果为准。
