# 三明栏目图标失败与单页分页候选

## 原始运行和因果边界

repair4 的五个分页失败分支均保存同一 `icon_column_2_hover.gif` 的 image/404，主文档为 200，来源已通过。旧材料没有脚本错误计数和全部样式原件，不能据此确认历史五次失败的直接路径；它们仍保留在原失败批次中。两个分页通过的旧分支也未整院交付，不能作为已采齐证据。

冻结已发布 main `400267890cd02340417092430f3dc27af1001b9f`，在基本原理教研室重新运行实际来源、名单及 pagination.single_page@2 工具，保存新的 HTML、请求、脚本计数、CSS 正文与 CSSOM。来源及分页捕获页的脚本错误均为 0，唯一失败为上述图标 404；真实阶段仍为 `stage_capture_unverified`／`page_load_failed`。随后另一独立实验仅给该精确图片 URL 提供合成正常 GIF/200，其余官网请求和代码保持，实际阶段证明产生且 terminal confirmed。两次名单、分页与完整 widget 的三组摘要，以及 11 份 CSS URL／正文摘要完全一致。两次分别 7.656 秒和 9.641 秒，模型调用均为 0。

原件位于本维护工作树 `.runtime/frozen-plan-transport-4002678/server/outputs/type-maintenance-20261012/sanming-failed-pager-request-evidence` 和同名 `-single-image-200` 目录；`comparison.json` 保存摘要对照。真实 CSS 指向侧栏的 `a:hover`／`a.selected`；当时匹配的 selected 节点位于名单和分页之外。正常图片实验是隔离反事实，没有修复官网或生产代码，亦未验证整院、教师身份与字段。

后续重新取得的可跨环境原件包见 [sanming-basic/manifest.json](../../tests/fixtures/pager_failed_images/sanming-basic/manifest.json)：原始 HTTP 文档与 11 份 CSS 保留字节、URL、状态及 SHA，浏览器序列化 HTML 单独标记。包内真实单页名单为 6 项、页码 1/1；这些人数及字段不构成整院资格结论。`test_failed_image_originals.py` 独立检查摘要、名单和分页结构、HTML 解析差异及失败图片引用范围，其他环境无需依赖上述本机路径执行此回归。

## 本地候选的能力

以 main `fce9242f161a257d534b92fb0aa10262cafdcb32` 为基线，候选为同一机制的新实现 `pagination.single_page@3`。原 `@1`／`@2` 不升级、不改变默认选用和承诺；共同依赖变更后，其旧阶段证明及缓存须按实现摘要失效复验。

延续 @2 的普通 DOM 名单、相同 WebPlus widget、原生计数、可见性、实际前进点击、文档身份及三次稳定范围摘要。新增范围只涉及已观测永久失败的图片（404／410）。逐一核对 DOM 图片引用、srcset、所有可读样式及内联声明；隐藏引用也检查。只支持字面 background-image URL，以及简单标签／class／ID、后代／子关系、hover／focus／active／link 状态和 before／after 伪元素。状态选择器按本次文档中可能匹配的元素保守计算，不能以当前未 hover 为由跳过名单内引用。祖先、完整 news widget 或 pager 中的引用均阻断；不可读／无法解析／未定位的范围不被认定为装饰图。

豁免失败图片前还要验证服务器原始名单及分页与实际 DOM 一致，并在所属分页组件执行空页码、越界页码两个不同的响应探测，再执行真实前进点击；不能依靠一页计数或无响应点击获准。新失败、脚本异常、XHR、文档更换、弹窗及名单／分页／样式引用变化保持拒绝。无失败图片时仍遵守原终态要求。

样式响应在新页导航前安装观察器，按主文档请求代次收取；CSS HTTP 正文与实际 CSSOM 分别保存、摘要绑定到阶段原件。CSSOM 是浏览器规范化规则，不能冒称网络字节原件。validator 从两组样式原件及保存 HTML 独立重建受支持的完整引用图，再核对声明的 URL、属性、CSS rule/location 和节点关系，不能只相信“在范围外”标签。包含 input image、video poster 和 source srcset；存在 HTML 原生 background 属性时，本次失败图片范围机制由浏览器捕获与注册消费者两侧明确拒绝，不尝试按不同 URL 解析器证明该属性无关。不支持的嵌入文档／SVG／base 仍拒绝。再执行既有 @2 来源及完整终态校验。材料没有进入模型可填参数。实现摘要绑定新旧处理器、请求、采集和来源等相关消费者，不可把旧 @2 证明改版本号继续使用。

## 限制与验收

本次捕获及真实点击前后的可观察状态是证明范围，不承诺网站未来任意状态。跨域不可读 CSS、未定位 preload／SVG 等引用、复杂或转义 CSS URL／选择器、未知属性、shadow DOM、大规模规则及暂时性失败仍未支持；它们保留未验证，可选择其他已有有效机制。图片失败不能因此授予师资资格，也不能掩盖名单内简介图片错误。

首候选 `13d6561` 未发布：审核发现图片属性遗漏和原件图绑定不足，已保留失败候选与补修反例。首候选官网范围判定通过，但服务器／控制器检查仍拒绝；诊断原件显示名单摘要一致，分页唯一差异来自 html.parser 将服务器不规范闭合的两个 input 解析为嵌套，浏览器按 HTML void 元素解析为平级。修订仅在新图片机制中统一用 lxml 比较完整服务器与渲染 widget，不删除结构／值检查；默认 @1／@2 的解析及行为保持。void 元素正例及输入值、名单内容变化反例均验证。

已建立隔离反例覆盖名单／分页内复用同资源、隐藏和 srcset 引用、伪元素／未 hover 状态、跨域不可读 CSS、未知选择器、XHR 和脚本失败、真实多页及无响应控制器。另有实际 BrowserScout 来源 → 注册 @3 阶段 → submit_plan → run_plan 调用链，以及重写摘要后的缺范围／材料／错误节点反例。当前仍为本地候选；真实候选冻结回归、双轴审查、对应版本 CI、发布和整院自动恢复分别举证，不能以用例数量宣布已完成。

## 精确候选验证与历史对账更正

2026-10-13 在集成反馈修复 main `13e991d7662008f302aff2af8717707705745349` 后，冻结候选代码 `6e77ac359e0aee64304d7b2973084072a5350f8b`，按 Git archive 排除未提交职称试验。111 份运行源码摘要在官网调用前后核验；五个相关回归文件合计 92 项通过（224.07 秒，64 项来自 lxml 依赖的弃用警告）。Spec／Standards 两轴无未关闭项。该结论只对应记录的代码、调用与边界，发布检查和服务加载仍分别核对。

基本原理教研室实际官网重新调用 `browser_verify_source`、名单核验及注册 `browser_verify_stage`：@3 获得 terminal confirmed，文档 200、脚本错误 0，保存 24 条响应材料，12.656 秒、模型调用 0。不是沿用旧候选的官网证明。失败图片与完整 news 根、pager 及其祖先的所有受支持引用必须处于范围外；控制器同时比较完整服务器与渲染 widget。native background 属性在捕获与独立消费者两侧拒绝，普通 BODY 正例变体实证同一失败 URL；反斜杠别名只作为不支持形式的反例，不能把 Node URL 解释当作 Windows Chromium 的实际 HTTPS 请求。

另外以旧两名单计划仅显式选 @3，真实来源、阶段、个人页样例及两次 submit 全部调用，最终两份 accepted 收据为 true。实际 `run_plan` 读取 2 个名单页和 14 个个人页，保存 14 条／11 个姓名、23 项未解决，`delivery_ready=false`。这不是内部模型自主发现或整院完整性验收。

历史对账保留旧 `history-diff.json` 并新增 `history-diff-corrected.json`：旧字段名“教师主页”不存在，该列原比较无效。更正脚本断言双方每条记录均存在姓名、职称、邮箱、学校、学院、主页、简介七字段，使用实际“主页”直接取值比较多重集合；14 条全部相同，主页非空 14 条，无增删。双方 records JSON 的字节 SHA256 也相同：`15d54a467ebf9525e98c1ecba0811ba83127ec72a2de29b9f9ec04f24a95491e`。旧对账 SHA256 `1a479af301fbdc035c9f6ab301bd5b704c4d4b514788d4c8918f60df7f67aeec` 保留，不覆盖错误历史。

新运行及更正对账保存在本维护工作树 `.runtime/frozen-pager-v3-6e77ac3/server/outputs/type-maintenance-20261012/` 的 `sanming-failed-image-v3-live` 与 `sanming-two-rosters-v3`；基线为已发布 `4002678` 的 `sanming-two-rosters-encoded-4002678`。这些是本机受控执行材料；可跨环境运行的原件包仍是上面的已跟踪 manifest 与 HTTP／CSS 字节 fixture。历史输出不是独立真值，23 项待核及其旧失败关系保持，未启动新付费整院采集。

目录列示源码中的精确注册定义。对应发布与 CI 状态从本改动的 GitHub PR、合并提交及必需检查读取；不能由目录条目、已合并代码或上述零模型受控回归推断前台已加载、600 万 Token 异常已消除或整院可交付。
