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

## 第一次精确官网回归及提交衔接

冻结 `8b0d18014fabb5d0e7e35c6abe1c4298aefde2b0`，108 源码文件摘要逐个核对，隔离数据库／输出／站点节流库，0 模型调用。只为历史两个名单分支显式选择 @2，保留原有配置及全部未知来源。两个来源核验、实际分页阶段通过；run_plan 读取两个名单页、14 个个人页，保留 14 条／11 姓名，仍不可整院交付。未解决项从 e83 相同计划的 25 项降为 23 项，分页终态问题消失，其余身份、简介图片归属、个人页读取及来源范围问题继续保留。

首次工具提交仍返回 `stage_type_evidence_unverified`，并要求尚未执行的个人页样本核验。进一步比对发现，来源工具与分页 handler 独立读取时整页 SHA 不同，但 selected_scope、role_scope、context_scope 三组摘要完全相同；handler 的已保存 metadata 内含本次原件自己真实执行得到的来源证明。

衔接修复仍要求本轮最新独立来源证明有效且 URL 相同。在分页阶段独立原件整页 SHA 不同时，必须取得该阶段已绑定 metadata 内的当前来源证明，来源参数、名单单位和三组范围摘要与独立证明一致，再在本次原件及目标学校学院上重新核实当前证明；缺证据、来源／范围变化、最新核验失败均不准入。阶段原件和实现摘要继续按本次捕获绑定，未把旧证明贴到新文件。动态无关计数的实际工具链先复现拒绝，再验证修复；重算 metadata 文件摘要也不能使缺失、未确认、错误单位／范围／原件的来源证明通过。

最后加固：@2 的 fresh 来源证明摘要进入机制 payload，实际 metadata 必须具备当次已确认来源证明，删除整个 source_roles 也不能借整页摘要相同通过。分页 metadata 含来源观察时始终核实 fresh 证明；阶段调用按发起序预置未证明状态，最新 pending、错误或损坏结果阻断旧证明，迟到的旧成功也不能复活它；另一分支失败不撤销当前分支证据。固定／变化 HTML 的完整工具链各验证 9 种 metadata 篡改及阶段失败反例。最后阶段／来源／实际消费者组合 18 项通过，增量双向审查问题均关闭，0 模型调用。

本机完整第一轮原件和执行证据：`.runtime/frozen-pager-v2-candidate/server/outputs/type-maintenance-20261012/sanming-two-rosters-v2/`。该旧运行保留，后续修复用新冻结目录产生新运行。它不是自动模型侦察，也不是全院人数真值。
