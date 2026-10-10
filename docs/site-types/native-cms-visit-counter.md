# 原生CMS访问计数元数据

状态：observed；通用原生组件形态，非学院适配器。

证据：来源原HTML含 `/system/resource/js/dynclicks.js` 引用及相邻 `_showDynClicks("wbnews", owner, article)` 调用，写出 `dynclicks_wbnews_<article>_<random>` span的id/name，通过owner/article端点取得数值。控制脚本原件SHA与机制说明见[复核证据机制](../implementation/2026-10-10-review-evidence-metadata.md)。

仅此生成节点的随机标识/访问数可进入严格等价证书；来源URL与article对应、controller/owner、CSS、资源/状态、节点路径以及其余native选区均须一致。先验证既有选择器SHA，再按原source位置截取显式平衡子树比较原字串；当前start边界尚不支持原生映射，拒绝复用。正文内普通数字、任意动态ID、其它CMS组件、不完整原件或不同脚本不能据此忽略。类型不携带教师字段，也不证明师资资格或采集完整性。

实现由 `review_evidence.equivalence` 统一负责，消费者记录原始SHA和old→new证书，保留人类原决定id；不得伪装新原件与旧原件SHA相同。
