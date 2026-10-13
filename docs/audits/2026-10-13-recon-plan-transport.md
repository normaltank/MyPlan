# 三明单院约六百万 Token、零采集：提交传输故障

## 已复现的事实

新主线 `3e677a191286b89679694e22397f2cdec1b064c7` 通过 PR90、PR91 及各自主线 CI 后，冻结并核对 108 源码文件。任务 `d52af94816e948d5ae7471ecb0d0e6b7` 使用项目 AgentWorker／agent_recon 内部模型（请求配置 deepseek-official、deepseek-flash、high）真实重跑三明学院马克思主义学院，740.532 秒后失败，未进入数据采集。模型后端精确修订未记录，不能由别名推断。

首次跨预算的快照为 161 次请求、6,005,194 Token；收尾保存的 165 条 usage 累计 6,095,320 Token，差额是阈值触发后收到的 4 条用量，不是两份完整账单。最终未知用量调用 4 个，账单不可得。三阶段已收用量分别是：第一次发现 58 次／3,265,633 Token；第二次发现 44 次／1,690,839 Token；第二次分支阶段合计 63 次／1,138,848 Token。第二次发现通过后，分支核验仍因预算／取消而结束；不能把本项传输修复等同于所有后续分支问题已解决。

第一次发现的 10 次 browser_verify_source 全部 confirmed，包括 8 个 faculty_roster 来源、行政来源的 unverified_people_source 和 508 来源的 organizational_membership。随后 15 次 submit 均只返回 `$:schema_model_type`，0 接受、0 stop：12 次 arguments.plan 为字符串（其中 1 次解一层仍为字符串）、2 次参数平铺缺 plan、1 次原始 arguments JSON 多出结尾。最后一次的服务端恢复参数未保存，不能猜测其实际输入。

首次拒绝后还收到 19 条 usage／1,778,376 程序 Token，没有新来源证明。15 次提交跨度 316.563 秒；运行结束没有 digest-bound accepted receipt，require_submission=true 使阶段报“阶段未返回 JSON”，上层保存为泛 plan_contract_rejected。拒绝信息回传在冻结 PR90 后的 decoder 已正常工作；这次不是再次丢失反馈，也没有证据归因于浏览器来源资格或某个恶意 agent。

发现阶段的 phase_tools 不暴露 page_type_catalog，但通用 submit 描述要求查询它；记录中出现两次错误目录工具调用及不支持的 file URL。目录调用、重新开页、旧原件与范围变化分别统计，不能把所有重复一概说成同一种死循环。

## 修复范围

提交入口接受严格结构对象或其单层 JSON 编码。字符串只作传输表示：解码后继续执行原完整 Schema、目标学校学院、来源和当前浏览器证明门禁。不会猜包裹、修坏 JSON、删除未知字段、填缺失参数或批准教师数据。重复键、双编码、非有限数（含溢出）、不可编码 Unicode、过大及异常深度 JSON 均给出受控错误条件和修复需要。

accepted 原件保存规范对象；事件消费恢复同一规范对象并核对摘要。正常中文计划的原 UTF-8 canonical digest 保留。等价对象、JSON 编码及空白变化使用同一摘要，不重置无进展拒绝次数。发现阶段的提交说明与实际工具面一致，类型选择和目录查询留给分支阶段。修改属于公共传输与反馈流程，不改变类型识别或教师资格协议。

## 已取得的验证

75 项提交、阶段结束、拒绝传输、停止计数及实际浏览器回归通过，ruff／diff 检查通过。Spec 指出的 Unicode 和浮点溢出两项边界已修复并最小复验；Spec／Standards 无未关闭意见。测试完全隔离，无新模型请求。

真实第 209 条工具调用的参数对象提取到 `tests/fixtures/recon_receipts/repair4_encoded_discovery_arguments.json`；其中 plan 字符串原值保留。对应原 arguments SHA256 为 `5a539bbfdc7c6a8f15f9a537367f89cbf8bd076b08f3ec644b1f3d6dbbcfb02a`。提取参数对象不是整个 SDK 原件的字节副本。

旧 SDK 工具事件离线回放（不读取助手正文／隐藏推理、不打开网站、不调用模型）显示：第 209 条经过解码后仍因 `non_faculty_purpose_unverified` 被拒；第 219、224、229、234、274 条能通过既有证明门禁。最早可通过的第 219 条保留 10 个来源分支；其他后续模型提案仍可能缩小范围，不能为通过而替换它们的事实。非法外层 JSON保持不可回放，平铺／双编码／未知字段／缺证明等提案继续拒绝。这是旧调用链条件对照，不能假称已经发生了较低费用的整院运行。

## 原件与运行关系

原失败运行保留于本维护工作树 `.runtime/frozen-source-3e677a1/server/outputs/`，对应 `run-manifest-001-repair-4-sanming.json`、`batch-001-repair-4-sanming-execution.json`、该任务 recon-attempts.json 和 recon-evidence。输出经 junction 保存在旧 ee5 物理目录，加载源码／模型／提示配置为 3e，路径桶名不代表代码版本。

辅助只读事实包位于本地项目 `.planning/collection-failure-repair-20261013/repair4-tool-facts/`：10 个 SDK 会话、630 个输入 SHA 前后未变，20 个交付文件已本地逐个核对；delivery-manifest.json SHA256 `c118fc417ade29f8c0c5a1380c23e5abd55b8cb959435d609b427323bda4b666`。本维护工作树 `.planning/2026-10-12-autonomous-type-library/encoded-submission-replay/` 保存候选回放与方法。

当前任务仍 failed／0 数据／完整性未验证。发布、对应版本 CI、实际模型重新采集、分支加载失败及部分交付闭环仍需分别验证；不得以测试或传输回放宣称整院采集成功。未扩大侦察预算，也未覆盖旧运行。
