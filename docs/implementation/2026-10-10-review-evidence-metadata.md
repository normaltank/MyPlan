# 复核恢复：原件封存与访问计数等价证书

状态：开发实现，尚未提交、CI、生产加载或工单交付。用户已保存决定不应因浏览次数变化而重复复核；这不允许忽略实际来源变化。

## 固定失败及定位

信息任务 `899f5e2d84de4e2e98808c0d48e02cdf` 18:33正常重跑183秒后，最新实际目录为 attempt-1，审计 SHA `f521876becc2ddbef3e0763a58bec566a06ebe1a61784efa4892c6909e34221c`，17个未解决项。逻辑执行轮数不等于目录序号；最新审计由 `task_review_audit` 按真实mtime取得，队列为failed。

原媒体、身份、HTML邮箱、姓名决定文件与重跑前backup逐字相同。15张缺失图片的URL、请求URL、CSS/start、404等对象绑定全部相同；只选区SHA和ID改变。逐页原件在删除明确的原生访问数节点后，其余选区HTML逐字相同。身份4URL中2部门观察完全未变，2个人页只简介内访问数及来源SHA变化；简介分别完整对应各原DOM读取。HTML邮箱候选地址/选项同，只有sourceSHA和一个计数附近context变化，受控处理后完整item相同。

原件内 `_showDynClicks("wbnews", owner, article)` 紧邻生成span。单独真实浏览器读静态控制脚本，200/3501字节，SHA `8b8f631f1a7ff7cfb3957e7a55caf89ded99ec6bb9655e6f2a8095cfeaf018c6`；其代码用随机后缀创建id/name，按owner/article请求点击数并写入span。未重采教师页、未调用模型。具体诊断及全17回放结果保留在本地规划目录，不将真实教师字段写入测试fixture或侦察计划。

## 共用机制

`server/review_evidence.py` 负责不可变来源存储和唯一严格等价判据。`prepare_review_resume` 先封存原audit、HTML文件字节与决定文件，marker记录源bundle；旧marker在carry前补封存，尤其同一attempt随后会覆盖相同snapshot文件名的情况。显式 `register_source(..., source_root=...)` 可登记有完整原件的同任务迁移backup；不会改决定值/id，不会把不可得来源当等价。

对象按SHA保存，manifest原子发布，读取时复算SHA，限制任务归属与路径。Windows snapshot写入CRLF，文本按原`Path.read_text`的通用换行规则读取；同时保存实际文件字节SHA。浏览器HTML文本SHA、选区原SHA与归档文件字节SHA分别记录，不相互冒充。

证书只支持该已观察原生控制器：同来源URL、同CSS、相邻精确controller调用、同wbnews owner/article，生成span只允许id/name与纯数值内容。先从当前原件复算既有消费者的选择器/包装SHA绑定；该解析后SHA不冒充原始片段哈希。再将唯一选中节点的原source坐标交给原生HTMLParser，截取完整、显式平衡的原文字串，不重新序列化属性、引号或实体。未知/不能定位的片段和任何start_selector暂拒绝复用。

脚本引用和节点路径也必须相同，只删除可唯一定位的原生计数span，其余原生片段逐字相同。schema2证书分别记录bound_source_sha256（既有消费者SHA）、raw_sha256（原生片段UTF-8文本SHA）、native_text_span（原件通用换行文本的字符偏移）、original_sha256（完整原件读取文本SHA）和original_file_sha256（封存文件字节SHA），以及受控节点证据、共同内容SHA。Windows原文件CRLF字节SHA及原件保留，不用解析后的选择器SHA冒充它们。

三个消费者共用此判据：

- 缺失图片复核仍逐项校验请求/实际URL、mediaURL、404/410、选择器、start与原人类photo决定；实际请求两次失败的既有门禁保留，当前字段id不得不一致。
- 身份复核仍保持URL集合及所有非简介字段；简介必须精确对应原始DOM读取，变化仅来自已证明访问数。证书保留原人类决定id，以当前观察执行原resolution；`validated_pairs`从归档原件重建证明并核对最终记录，不能仅信证书旗标。
- HTML邮箱复核重读受控原件的context后，整个item（地址候选、owner、可选性、角色问题、上下文、card等）必须相同；仍校验原task/reviewer/决定值，以当前item核对选择可用。审计另记原决定id与证书。

当前有效决定必须仍与封存的原人类决定逐字相同；被修改/撤回的旧决定不能由archive复活。原件缺失、哈希篡改、未知DOM/数字变化、controller/owner/article变化、资源/状态/selector变化均继续失效。此机制不是新的人类决定，不授予来源资格，也不宣称学院全采齐。

## 验证范围

35个合成测试覆盖同attempt覆写保全、原ID保持、正文/owner/resource/status/selector/未知数字变化、原件篡改、决定撤回、当前不确定决定优先、未知controller、Windows换行、身份结果JSON往返重验、邮箱候选变化、迁移task/attempt/原件/rawSHA绑定、外部登记遗漏参数/未知schema、audit/snapshot/decision/ledger四类实际symlink外逃、真实scoped消费者属性顺序变化及不支持start边界拒绝。去除计数节点保留其余原字符串，不能用DOM重新序列化掩盖未知差异。原失败数据在独立task存储回放：media 15/15恢复，identity pending0/1证书，按用户原same决定4观察成为1且证书重验成功，HTML邮箱1恢复；实际registry三类complete=true，prepare/carry成功且原决定文件未改变。没有动生产任务/决定/审计，没有真实学院重跑。

初版相关复核/恢复集合107项通过；快进合入PR54的main `387e6d49e5e31f933417e6ea74f510f300d64a0f` 后，新增模块、职称字段、复核registry及worker缓存接线集合106项通过。远端最终CI仍未执行；不能以固定材料回放代替实际交付/范围验收。旧来源尚未封存的任务需先通过可验证源登记恢复真实原件，不允许手改决定SHA来匹配新内容。

## 原件登记的受限恢复步骤

下列函数调用是审核准备，尚未在生产执行。执行时须使用经过CI和审查的代码版本，在真实任务根目录登记原件，再由已有复核registry、prepare/carry及正常worker恢复继续验证；独立副本的成功不表示生产已经恢复。

```python
register_source(
    task_root=Path("D:/Agent Workspace/Intern/MyPlan/server/outputs/tasks/899f5e2d84de4e2e98808c0d48e02cdf"),
    audit_path=Path("D:/Agent Workspace/Intern/MyPlan/.planning/2026-10-10-four-college-repair/retry-899f5e2d-1791628429951751300/attempt-1/江西理工大学_信息工程学院_audit.json"),
    task_id="899f5e2d84de4e2e98808c0d48e02cdf",
    source_root=Path("D:/Agent Workspace/Intern/MyPlan/.planning/2026-10-10-four-college-repair/retry-899f5e2d-1791628429951751300/attempt-1"),
    expected_audit_sha256="6be4e6f486935c32a8b7b2a4e6f7bb06c9ab9959c1a7f72b10c1d39aeeb65548",
)
```

默认登记源audit必须实际位于解析后的真实task/attempt目录，声明路径也须一致；不能用省略source_root绕过迁移门禁。任何外部迁移须显式给source_root及独立固定audit SHA；原audit声明的tasks/task/attempt必须与目标task及每份原快照一致，决定文件必须符合schema1且ID唯一，所有相关复核项须以原生ID及原件选区/rawSHA核验。缺失原件、其他任务、其他attempt、错误SHA及audit/快照/决定/ledger符号链接外逃均不发布source manifest。仅将原audit、HTML字节、决定文件封存至该真实task的`review-evidence`；不改原audit、人工决定或工单。后续消费者另要求现行决定文件的原id行与封存决定逐字相同，且当前原件重新证明严格等价，登记不直接赋予通过结果。

归档范围是审计引用的profile/directory HTML与全部登记决定文件；尚未扩展到图片/PDF原件的通用封存，不据此声称解决所有复核来源保全。静态控制脚本的真实读取证明此已观察类型的语义，不假定历史快照曾保存其完整脚本版本。
