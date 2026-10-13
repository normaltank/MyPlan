# 三明马克思主义学院分页范围维护

## 原始观察

冻结源码 e83a67b46cdc7b7a5f0e4bd0bc646dce60cc13a1 在 2026-10-13T04:05:38Z 读取官方教授页 `https://www.fjsmu.edu.cn/smxyszb/js/list.htm`。实际选择器 `ul.wp_article_list > li.list_item` 命中 6 个条目；WebPlus w6 控件显示当前 1／总页 1、总条目 6、每页 15。旧 terminal_pagers 返回 `network_not_idle`，没有确认末页。

唯一等待请求是图片 `https://www.fjsmu.edu.cn/_upload/article/images/e2/33/0976c69f4b00970dafcc6820792b/bdace7db-f76c-41bd-a6a8-c79f74068def.jpeg`。保存 DOM 中，它位于 `.list-banner` 内的 `#featuredw99` 轮播，未处于名单范围。没有观察到脚本异常或失败请求。

原件与执行证据位于本维护工作树 `.runtime/frozen-source-e83a67b/server/outputs/type-maintenance-20261012/sanming-pager-legacy-diagnosis/`：`before.html` 文件字节 SHA256 `3254e87febbc2f103c71c9a2bb3c389358851e4f0ce6ecaf2568b2135503b9f9`，`after.html` 文件字节 SHA256 `12cba102b56ad13b7fded820eeefaa004e58c241da799f1bb69ac4ad42eea9db`，以及 `result.json`。诊断 JSON 的 before／after SHA 指写文件前的浏览器 HTML 字符串（LF），文件保存时为 CRLF，二者不可混作字节摘要。旧证据保留，字节相同的原件副本进入 `tests/fixtures/pager_scope/sanming_professor.html`，限定该文件 `-text` 防止 Git 换行转换。计数／1页文本不是独立充分的末页证据。

## 改动边界

按类型版本维护规则注册 `pagination.single_page@2`，旧 @1 默认选择和全页等待行为保留。@2 仅适用普通名单的 WebPlus 单页终态机制；同一 widget 的完整名单容器及其分页控件共同限定请求范围。只有 DOM 中每处引用都明确位于两者之外的待加载 img 可以保留；预加载、CSS 背景、名单内和分页内图片、XHR 或其它未知请求继续阻断。

实际前进点击、文档响应对象、URL、控件事实、名单／分页／完整 widget 摘要及三次请求范围采样必须一致。脚本失败仍遵守原有实际 controller 核验；新增失败或实际数据请求不能借用旧 controller 证明。阶段证据、提交校验、采集消费者、缓存复核使用同一注册实现，不能由模型填写通过标记。增加相关依赖到复用契约，运行提示词明确先读取目录、验证名单、再实际执行所选分页阶段。

这是终态判定的范围修复。它不批准来源、教师身份、字段归属，也不解决模型没有覆盖其它名录分支或部分交付尚未闭环的问题。

## 验证记录

本地旧分页及相关浏览器回归 26 项通过；最终类型注册、执行、提交、缓存、运行提示词和新机制组合 175 项通过（含新版本实际 browser_verify_stage → submit_plan → run_plan → 缓存复核，以及原件、嵌套 widget、内部／未知／数据请求和点击改变名单的反例）。这套工具链测试完全拦截网站网络，不产生模型调用。

双向只读审查发现嵌套 widget 错配和阶段摘要漏公共依赖两项，均修复后定向复验关闭；摘要重复的维护性意见已整理。Spec 未关闭 0，Standards 硬违反／未关闭意见 0。实际站点精确版本运行、发布和 CI 仍待取得后补记；不得据此声明三明整院交付成功。
