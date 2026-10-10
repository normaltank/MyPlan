# 原生 PDF 归属技术错误的正常重试

用户已保存邮箱与身份决定后，土木任务点击“复核完成”仍返回 409。固定 attempt-3 审计 SHA 为 `47ebf3ace14e5e391e2911814abacf67ae3906569181d5787b52b7066020642f`。3 项邮箱和罗凯的原身份决定都能通过当前校验；剩余阻断来自 12 项旧 `image_bio_owner_unresolved`。这些 PDF 保存完整文字层、没有待改 OCR 行，`image_bio_text` 因而收集 0 项。另 9 项 `image_bio_unreadable_profile` 不属于该复核类型，本身不持有重试门禁。

`image_bio_review.native_pdf_owner_rerun_ready` 只处理该类型全部问题均为原生 PDF 归属失败的情况。使用实际 audit 文件路径限制原件与扫描缓存，核验二者 SHA 绑定、唯一已接受的个人页姓名，再由现有 `inspect_owner_scope` 重算每项归属。全部当前结果为 `single_owner` 才采用现有 `rerun_only` 语义允许重新运行；未知第二人、低置信度文字问题、非原生页、缺来源摘要或原件变化仍阻断。

`ReviewKind.rerun_only` 统一接收 `(audit_path, audit)`，HTML 邮箱的旧证据重跑规则保留。没有修改旧审计、删问题、自动选择邮箱或改身份证明。新一轮采集仍从 `audit_profile_media` 重新判断归属、正文完整性及交付范围；允许重试不等于任务完成。

已验证原生 PDF／登录重试接口的正例红→绿，以及未知第二人、混合图片低置信度、源文件变化和扫描 SHA 缺失四类反例。相关 42 项本地测试与静态检查通过。实际保存材料在临时 task 存储验证 12 项归属重算、普通 registry 门禁与 prepare/carry，保留原决定字节；证据在隔离工作区 `.planning/native-pdf-owner-retry/`。

本分支不写生产任务或真实决定，不执行付费侦察。当前版的独立审查、CI、服务加载和真实重跑由后续流程分别确认。PR59 的 `source_relationship` 类型合入时须适配相同双参数回调，不降低其来源复核规则。
