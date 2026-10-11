> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行业务见 [CONTEXT.md](../../CONTEXT.md)，开发任务按 [AGENTS.md](../../AGENTS.md) 分工；使用原结论前核对代码版本、样本与原始证据。

# 底层脚本清单与静态调用者

2026-10-01，记录 server、Opus、采集技能脚本和正式 Windows 启动脚本。表由 Python AST import 关系生成，不执行模块、不初始化数据库。动态加载、CLI 与 Harness 配置不一定出现在静态调用者列；无静态调用不能单独作为删除依据。这是入口清点，不代表每一行代码均已人工核准。修复及验证范围见[专项审查](2026-10-01-foundation-hardening.md)。

共 69 个脚本，其中 65 个 Python 文件通过 AST 解析。

| 脚本 | 角色 | 底层脚本中的静态调用者 |
| --- | --- | --- |
| `.agents/skills/faculty-browser-harvest/scripts/batch_browser_extractor.py` | retired; import/CLI 拒绝执行 | 直接启动/动态调用/无静态调用 |
| `.agents/skills/faculty-browser-harvest/scripts/dom_dehydrator.py` | retired; import/CLI 拒绝执行 | 直接启动/动态调用/无静态调用 |
| `.agents/skills/faculty-browser-harvest/scripts/harvest_template.py` | retired; import/CLI 拒绝执行 | 直接启动/动态调用/无静态调用 |
| `Opus/__init__.py` | module | 直接启动/动态调用/无静态调用 |
| `Opus/demo.py` | 离线合成演示/算法断言 | 直接启动/动态调用/无静态调用 |
| `Opus/email_matching.py` | module | __init__.py, demo.py, test_closure.py, test_production.py, detail_mecc.py, faculty_extractor.py, segmented_roster.py |
| `Opus/iso_mining.py` | module | __init__.py, demo.py, email_matching.py, template_infer.py, test_closure.py, test_production.py, text_lists.py, wrapper_synth.py, faculty_extractor.py, headcount_check.py, name_list.py, runtime_l1.py, segmented_roster.py |
| `Opus/name_labels.py` | module | iso_mining.py, faculty_extractor.py |
| `Opus/template_infer.py` | module | test_production.py, wrapper_synth.py, detail_mecc.py |
| `Opus/test_closure.py` | 离线合成演示/算法断言 | 直接启动/动态调用/无静态调用 |
| `Opus/test_production.py` | 离线合成演示/算法断言 | 直接启动/动态调用/无静态调用 |
| `Opus/text_lists.py` | module | __init__.py, email_matching.py, iso_mining.py, wrapper_synth.py, detail_mecc.py, faculty_extractor.py |
| `Opus/wrapper_synth.py` | module | test_closure.py |
| `scripts/start_local_services.ps1` | Windows 启动入口 | 人工双击/正式脚本 |
| `server/access_control.py` | module | app.py, manage_users.py |
| `server/agent_queue.py` | module | agent_worker.py, app.py, browser_batch_sync.py, faculty_extractor.py, manage_users.py, runtime_l1.py |
| `server/agent_recon.py` | module | agent_worker.py, recon_browser_mcp.py, recon_contract.py, recon_plan_cache.py |
| `server/agent_worker.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/app.py` | CLI | start_server.py |
| `server/auto_dispatcher.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/browser_batch_sync.py` | CLI | agent_worker.py |
| `server/browser_batch_tool.py` | CLI | agent_recon.py, agent_worker.py, recon_contract.py, recon_plan_cache.py |
| `server/browser_context.py` | module | agent_worker.py, browser_batch_tool.py, browser_poster_tool.py, recon_browser_mcp.py, recon_plan_cache.py, recon_templates.py |
| `server/browser_poster_tool.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/browser_readiness.py` | module | browser_batch_tool.py, empty_roster.py, harvest_workflow.py, profile_structure.py |
| `server/delivery_fields.py` | module | agent_queue.py, excel_export.py, university_delivery.py |
| `server/detail_mecc.py` | module | browser_batch_tool.py, faculty_extractor.py, harvest_workflow.py, name_list.py |
| `server/dom_regions.py` | module | detail_mecc.py, faculty_extractor.py |
| `server/empty_roster.py` | module | agent_recon.py, browser_batch_tool.py, harvest_coverage.py, harvest_workflow.py, recon_browser_mcp.py |
| `server/excel_export.py` | module | agent_queue.py, browser_batch_sync.py, browser_batch_tool.py, university_delivery.py |
| `server/faculty_extractor.py` | module | browser_batch_tool.py, harvest_eligibility.py, harvest_workflow.py, headcount_check.py, image_bio_review.py, name_list.py, segmented_roster.py, worker_selfcheck.py |
| `server/faculty_identity.py` | module | agent_queue.py, browser_batch_tool.py, delivery_fields.py, identity_review.py, image_bio_review.py, university_delivery.py |
| `server/field_checks.py` | module | agent_worker.py |
| `server/harvest_benchmark.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/harvest_coverage.py` | module | browser_batch_tool.py |
| `server/harvest_eligibility.py` | module | browser_batch_tool.py |
| `server/harvest_runtime_report.py` | module | agent_worker.py |
| `server/harvest_state.py` | module | agent_worker.py |
| `server/harvest_workflow.py` | module | browser_batch_tool.py |
| `server/headcount_check.py` | module | agent_worker.py, recon_browser_mcp.py |
| `server/identity_review.py` | module | agent_queue.py, app.py, browser_batch_tool.py, faculty_identity.py, university_delivery.py |
| `server/image_bio_review.py` | CLI | app.py, browser_batch_tool.py, image_biography.py, segmented_roster.py, task_image_review.py |
| `server/image_biography.py` | module | browser_batch_tool.py, harvest_workflow.py, image_bio_review.py, profile_content.py, segmented_roster.py |
| `server/intake.py` | module | app.py |
| `server/local_service_config.py` | module | agent_worker.py, auto_dispatcher.py, browser_batch_sync.py, start_server.py |
| `server/manage_users.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/name_list.py` | module | harvest_workflow.py, headcount_check.py, segmented_roster.py |
| `server/name_review.py` | module | app.py, browser_batch_tool.py, harvest_workflow.py |
| `server/profile_content.py` | module | browser_batch_tool.py, faculty_identity.py, profile_structure.py |
| `server/profile_structure.py` | module | harvest_workflow.py, recon_browser_mcp.py |
| `server/recon_browser_mcp.py` | CLI | recon_plan_cache.py, recon_templates.py |
| `server/recon_contract.py` | module | agent_recon.py, recon_browser_mcp.py |
| `server/recon_plan_cache.py` | module | agent_recon.py, agent_worker.py, recon_browser_mcp.py, recon_templates.py |
| `server/recon_pricing.py` | module | agent_recon.py |
| `server/recon_templates.py` | module | agent_recon.py, recon_plan_cache.py |
| `server/recovery_policy.py` | module | agent_worker.py, harvest_runtime_report.py |
| `server/runtime_l1.py` | module; 保留，未激活新缓存 | faculty_extractor.py |
| `server/segmented_roster.py` | module | agent_recon.py, browser_batch_tool.py, harvest_workflow.py, recon_browser_mcp.py |
| `server/site_limits.py` | module | harvest_workflow.py, headcount_check.py |
| `server/site_type_registry.py` | module | agent_queue.py |
| `server/spot_check.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/start_server.py` | CLI | 直接启动/动态调用/无静态调用 |
| `server/task_image_review.py` | module | agent_worker.py, app.py |
| `server/titles.py` | module | browser_batch_tool.py, detail_mecc.py, faculty_extractor.py, field_checks.py, name_list.py |
| `server/university_delivery.py` | module | agent_queue.py, app.py, browser_batch_sync.py |
| `server/worker_selfcheck.py` | module | agent_worker.py |
| `双击启动局域网服务.bat` | Windows 启动入口 | 人工双击/正式脚本 |
| `启动自动巡检接单.bat` | Windows 启动入口 | 人工双击/正式脚本 |
| `重启本地服务.bat` | Windows 启动入口 | 人工双击/正式脚本 |
