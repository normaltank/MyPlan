# PR #50 在 CI 失败时合并的记录

## 事实与责任

2026-10-10，开发智能体在没有确认 PR 最后提交的 CI 全部通过时合并了 [PR #50](https://github.com/normaltank/MyPlan/pull/50)。局部回归和独立代码审查不能替代 CI；此次合并流程不合格。

最后提交为 `024273b9be804f5f86fcaa7e8121712a09c1bf72`，[PR CI](https://github.com/normaltank/MyPlan/actions/runs/38026792819) 失败。北京时间 13:16:29 合并为 `6cf3086e509d9f7dd592928623b90ba424297721`，PR 因合并关闭，并非放弃。[合并后 CI](https://github.com/normaltank/MyPlan/actions/runs/38026952494) 的两项失败为：

- 单元测试 `test_profile_proof_is_retried_when_the_page_had_not_settled`：模拟的正文证明缺少新证据字段。该任务为 1 failed、1226 passed、2 skipped。
- 浏览器测试 `test_script_built_content_and_profile_media_still_block`：旧断言将正文范围证明与失效图片审计混为一体。该任务为 1 failed、172 passed。

## 后续修复及边界

[PR #51](https://github.com/normaltank/MyPlan/pull/51) 补齐模拟证据，并检查新契约：正文范围可以核实，但未知失效图片仍产生 `missing_profile_media_needs_review`，阻断交付；脚本生成正文仍保持阻断。不是通过删除未知媒体门禁让测试通过。[PR #51 CI](https://github.com/normaltank/MyPlan/actions/runs/38027559716) 已通过。

本次核查时 GitHub main 为 `48ab00924e786a7b7bcfccd00906cf62536e59e1`，[该提交 CI](https://github.com/normaltank/MyPlan/actions/runs/38030630579) 的单元测试、浏览器测试和 Ruff 均为 success。#51 合并提交自身的运行被取消，不能把取消当通过；以上结果指向随后实际主线提交。

CI 通过不证明学院完整性或实际工单验收通过。信息工程学院此前 54 条的完整性结论已撤回，任务保留失败状态、既有记录与分页证据，待完整分页修复核验。其他学院也按各自实际审计判断，不据主线绿灯宣称采齐。

## 后续合并要求

合并前核对 PR 当前 head SHA、该提交所有必需检查与本仓库测试工作流；失败、取消、待运行或没有对应检查均不得替代成功。修改代码、合并主线或解决冲突后，以新的 head 重新检查。不得通过删测试、取消未知项门禁或跳过失败样本消除红灯。合并后再核查主线运行；失败时暂停后续合并，先调查并修复或撤回有问题的改动。

当前 PR #52 尚未合并。本记录不宣称服务器已更新或四个学院已完成。
