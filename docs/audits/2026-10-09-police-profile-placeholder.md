# 警官学院最新失败：残留图片的边框被当作显示尺寸

目标：山东政法学院警官学院。工单 `c08c645eefe0476a844c6d8b5c70d7b5`，批次 `91a22161`，2026-10-09 11:43（UTC+08）提交。诊断时本地 main 为 `5409f53`。

## 原因

本轮审计保存 31 条观察。第一次采集还有三个个人页正文选择器失配，补缺改成 `div.v_news_content` 后已解决。最后仅两项未解决：

- `profile_content_browser_unverified`：`/info/1101/6662.htm` 正文唯一、可见、DOM 稳定，但一个图片请求返回 404。
- `image_bio_asset_failed`：同页 `/system/editor/ueditor_news/themes/default/images/spacer.gif` 的 404。

两项来自同一图片，未出现上一轮人物照片的 OCR 完整性问题。

PR #26（提交 `954fd02`）已经合入，本次审计也包含它增加的 `unpublished_images` 字段。原规则要求：图片连续两次 404/410、正文有文字、页面未给图片内容指定显示尺寸，才作为未发布的残留媒体跳过并留痕。

实际打开官方问题页后核实：残留 `<img>` 没有 `width`、`height` 或内联 `style`，但主题给 `.img_vsb_content` 加了四边各 1px 的边框。用于测量的已加载 1×1 像素替身，其 CSS 内容区是 1×1，`getBoundingClientRect()` 的外框却是 3×3。旧判定比较外框是否大于 1×1，把装饰边框错误地当成了图片内容的显示空间，导致 `unpublished_images=[]`。

浏览器仅返回 URL、几何和 CSS 事实；没有用模型提取或改写教师姓名、邮箱、职称或正文。

## 修复

修改 `server/profile_structure.py` 的局部测量实现：

- 用已加载替身的 CSS 内容区测量；`border-box` 扣除边框和内边距。
- 替身保持原节点的 DOM 位置与 ID，避免漏掉 ID、`:first-child` 等位置选择器给出的真实尺寸；移除替身事件属性，测量结束始终恢复原节点。
- 测量后再次核对原正文摘要和可见性事实，页面观察器改变正文时仍阻断。
- 保留两次 404/410、文字正文、内容区不大于 1×1 的限制。真实尺寸图片、暂时错误、单次失败、图片型正文和无法测量等不自动放行。没有根据 `spacer.gif` 文件名或学校域名添加生产特殊判断。

## 验证

先新增带主题边框的 Chromium 回归，旧代码复现失败；修复后通过。针对真实尺寸的 ID、位置选择器、`border-box`，以及测量时 MutationObserver 改变正文的反例，均保持阻断。

正文稳定性、正文证据、重复资源失败、图片简介和案例回放的相关回归共 74 passed；`git diff --check` 通过。测试环境仍有既有 NumPy/SciPy 版本范围及 lxml 弃用提示，未修改运行环境依赖。

实网调用当前 `capture_profiles` 与 `audit_profile_media` 复验官方问题页，结果：

- 正文验证 `confirmed=true`，正文错误码为空。
- 媒体错误码为空，保留 1 份实际照片原件。
- 仅该残留图片记录 `image_gone_without_display_area`，其地址仍留在审计中。
- 证据写入本次 `.planning/2026-10-09-police-college-latest-failure/live-profile/`，没有写生产工单、生产决定或成果目录，没有请求模型。

本次实网验证范围是原失败的个人页，不是整个学院的新一轮采集。生产工单仍为 failed；Web/worker 尚未重启，未自动重排工单。重新加载修改后仍需按正常流程重试、审计全部目标分支，再判定本工单及大学交付是否完成。
