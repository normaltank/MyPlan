/browser
你负责已确认的单个师资名录分支。代码已完成全学院入口发现；此阶段仅验证当前入口的卡片结构、分页和动态控件。完成此分支的 JSON 后立即结束；worker 执行数据采集、审计与交付。

按顺序执行：
1. 用 browser_navigate 打开本入口，直接复用返回的重复子树候选。
2. 用 browser_verify 检查容器锚定的卡片选择器，通常命中数必须大于零且只匹配教师条目；只有结构不足时调用 browser_structure。完整 HTML、教师正文保留在本地。若官网有明确、真实可见的空名录提示，可按下面的严格空态流程验证；零卡片或完全空 DOM 本身不能证明名录为空。
   browser_verify 的 linkedSamples 只是卡片上的链接样例，并不证明目标就是个人主页。若卡片实际只是通往另一张多人名录的入口，不要把它当教师卡片；改用下面的 navigation 分支，并在真实浏览器打开目标名录核实。
3. 正常名录在实际 DOM 上验证带容器锚定的 card_selector 只命中教师条目且数量大于零；明确空态按下面的独立流程验证。next_selector 必须唯一，并实际进入下一页验证同构卡片。无分页证据才可使用 single_page。
   若本入口只是通往一张或多张同构多人名录的目录，输出 kind:"navigation"、child_link_selector 和 child_branch。先用 browser_verify 的 card_selector 参数验证 child_link_selector 只命中这些真实名录链接；再打开最多两个不同子名录，用 browser_verify 核实 child_branch 的 card_selector 与分页。子名录有真正的个人主页时仍须按第 4 步核实正文范围。child_branch 不含 url，只有实际验证过的 CSS 和分页字段。若链接指向个人主页，本入口仍是普通名录，不使用 navigation。
   若名单与逐人简介图片同页，先用真实浏览器核实每张图确为一人条目，再把只命中这些原图的 CSS 作为 card_selector 验证；worker 会对原图 OCR、姓名归属及图片数与去重人数逐项对账。图片只含头像或图与人无法一一对应时，保留未解决项，不编造文本卡片。
   最终 JSON 的选择器必须与成功 browser_verify 调用中的字符串完全一致。添加父容器、改写路径或调整任何字符后必须重新 verify；不能把近似选择器当作已验证选择器。
   navigate/verify 返回的 pagination 是当前真实 DOM 的控件候选。pagination.total > 0 时不能直接宣称 single_page；必须核实这些控件，找到有效下一页并验证。控件候选含实际 CSS 路径，不用再逐项搜索整页 HTML。
4. 名录 verify 返回实际 linkedSamples。先核实链接目标是否为个人主页；若是，打开最多两个不同样例，核实完整学术正文的唯一容器，并用 browser_verify_profile 传入 profile_content_selector；它须覆盖全部履历、联系方式和学术内容，不能只选一个段落或删去共享正文。若正文由图片或嵌入 PDF 承载，仍核实覆盖全部原始资源的唯一容器；只提交 CSS，由 worker 本地读取原件和 OCR，无法取得原件会阻断交付。两个样例须使用同一 CSS 且工具 confirmed:true。若正文没有独立包装容器，可加 profile_content_start_selector，必须是唯一容器内的唯一直接子边界：复制边界之后到容器结束的全部 DOM，不按词语裁剪。只返回 CSS，禁止转写任何姓名、职称、邮箱或简介。没有个人主页的名录不用该字段；不要把名录页当个人页验证。更多个人页由 worker 批量访问并逐页检查范围，缺失或冲突会阻断。
5. 保留已核实事实供最终计划使用，避免再次访问已验证页面或逐项重复查询同一 DOM。增加检查时针对尚未核实的具体问题。JS href 占位符可能承载 AJAX，不可将所有 javascript:void 链接一概视为禁用；必须核实真实翻页行为及末页，选择器可限定实际页面中真正的下一页链接。

优先使用内置结构工具，避免重复编写长 JS：structure 的 selector 默认 body，offset 默认 0，limit 默认 24；返回 nextOffset 且当前候选不足时继续查看结构范围。改用具体容器减少无关项。verify 接收 card_selector 与可选 next_selector；翻页用 browser_click 的 selector。必要的特殊问题才用 browser_evaluate。
严格空态流程仅适用于普通单页名录：先实际确认官网空态提示及完整名录容器，再用 browser_verify 同时传入 card_selector、empty_state_selector、roster_container_selector；三个 CSS 字符串必须与最终计划完全一致。只有工具返回 empty_roster.confirmed:true、cardCount:0 且 pagination.total:0 时才可提交空分支。工具要求容器与提示唯一可见、提示位于容器内、没有隐藏教师内容、图片或背景图、嵌入页面、其它链接/控件/条目、busy、待加载和分页；网络与 DOM 必须稳定。任何失败或超时、没有明确可见提示、分页或图片名录都需复核，不得自行以零卡片绕过门禁。不得输出空态文字、人数或 confirmed 标志；独立采集浏览器会重新验证。
探针只提供结构候选，仍须判断条目性质并实际翻页；全部名录入口由发现阶段负责，不在本阶段重新调查。

文本返回有 8000 UTF-8 字节预算；完整证据保存在本地。nextOffset、complete:false、result_too_large 或省略/Omitted 表示尚未覆盖全部内容。必须在原页面做定向查询，缩小容器或 limit 后继续；不能把预览当完整证据。禁止返回整页 outerHTML、innerText 或全部教师条目。已检查范围与未检查范围需分别处理，直至覆盖相关导航。完整教师数据由 worker 读取，不受此预览预算影响。

完成条件：本入口的卡片选择器已验证、分页控件唯一且实际下一页已验证，或已确认确实没有分页；实际个人页样例还须正文范围核验。达到条件后立即输出 JSON。不要再发现其他学院入口、巡视介绍/新闻或重复访问已验证的页面。未核实的 URL 或近似选择器不能加入计划。

普通名录只输出本分支的一个 JSON 对象：url、card_selector，恰好给 single_page:true 或 next_selector；有分页时给 max_pages。目录型名录改用 url、kind:"navigation"、child_link_selector、child_branch，子分支按普通名录填写 card_selector 与分页字段，但不填 url。严格空态验证通过的普通单页分支可成对加 empty_state_selector 与 roster_container_selector，保留 card_selector、single_page:true，不能添加 next_selector。url 必须等于本入口 URL。动态 Tab 可加 initial_click_selector。CSS 使用容器锚定路径，例如 div.list_container ul.teacher > li。不要输出学校、学院、branches 数组或其他入口。
有实际个人主页时另给已核实的 profile_content_selector，可选 profile_content_start_selector。CSS 必须与成功 browser_verify_profile 调用完全一致，两个不同样例都须验证；只有一个个人主页时验证该实际样例即可。正文工具只回传结构与摘要，所有教师字段仍由本地确定性代码提取。
禁止额外添加 name、label、count、说明或备注字段；名录分支只用 URL 标识。
模型输出只含目标标识、URL、CSS 选择器和结构控制值，严禁姓名、职称、邮箱、简介及其总结；数据由确定性代码原样提取。

本工单：
目标学校：{school}
目标学院：{college}
入口 URL：{url}
