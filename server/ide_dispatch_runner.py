import os
import sys
import json
import time
import asyncio
from contextlib import suppress
import httpx
import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SERVER_DIR)
for p in (_SERVER_DIR, _ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from autonomous_agent import AutonomousBrowserAgent
from agent_queue import AgentTask

BASE_API = "http://127.0.0.1:8000"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def export_university_excel(school: str, records: list, output_dir: str = OUTPUT_DIR) -> str:
    """Export all college records of a university into a single dual-sheet Excel file named after the university (<大学名称>.xlsx)."""
    safe_school = "".join(c for c in school if c not in r'\/:*?"<>|').strip() or "高校"
    out_filename = f"{safe_school}.xlsx"
    out_filepath = os.path.join(output_dir, out_filename)

    # Fetch all records across all completed colleges for this university from database
    import sqlite3
    db_path = os.path.join(_SERVER_DIR, "tasks.db")
    db_records = []
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT r.payload_json FROM records r
                    JOIN tasks t ON r.task_id = t.task_id
                    WHERE t.school = ? AND t.status = 'completed'
                    ORDER BY t.created_at, r.seq
                """, (school,))
                for row in cursor.fetchall():
                    if row[0]:
                        db_records.append(json.loads(row[0]))
        except Exception as e:
            print(f"⚠️ [大学交付] 读取全校记录提示: {e}")

    final_records = db_records if len(db_records) >= len(records) else records
    agent = AutonomousBrowserAgent()
    agent.export_dual_sheet_excel(final_records, out_filepath)
    with_email = sum(1 for r in final_records if (r.get("邮箱") or r.get("email") or "").strip())
    no_email = len(final_records) - with_email
    colleges = set(r.get("学院") or r.get("college") for r in final_records if (r.get("学院") or r.get("college")))
    print(f"🎉 [大学交付] 已导出【{school}】教师名录双Sheet总表: {out_filename} (涵盖 {len(colleges)} 个学院, 总人数: {len(final_records)} 位, 有邮箱: {with_email}, 无邮箱: {no_email})")
    return out_filename


async def check_and_execute_queue():
    """Check pending tasks from server queue and execute them grouped by university with AutonomousBrowserAgent."""
    raise RuntimeError(
        "Legacy script-only queue execution is retired. Claim a college task in the IDE, "
        "follow faculty-browser-harvest/SKILL.md, and use the browser batch and sync tools."
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{BASE_API}/api/queue/pending")
            if r.status_code != 200:
                print(f"[Dispatch] 无法连接到本地服务: HTTP {r.status_code}")
                return {"status": "error", "message": f"HTTP {r.status_code}"}
            data = r.json()
            tasks = data.get("tasks", [])
            if not tasks:
                print("💤 [Dispatch] 工单队列暂无排队任务 (0 queued)，智能体持续待命。")
                return {"status": "idle", "count": 0}
            
            # Group tasks by university (school)
            tasks_by_school: dict[str, list[dict]] = {}
            for t in tasks:
                if t.get("task_type", "harvest") != "harvest":
                    continue  # L3 compile tickets are handled by the local compiler.
                sch = (t.get("school") or "未命名高校").strip()
                tasks_by_school.setdefault(sch, []).append(t)

            print(f"🔔 [Dispatch] 侦测到 {len(tasks)} 个排队工单，按大学单元调动：共 {len(tasks_by_school)} 所大学！")
            
            agent = AutonomousBrowserAgent()
            total_executed = 0

            for school, school_task_list in tasks_by_school.items():
                print(f"\n=======================================================")
                print(f"🏛️ [大学调度单元] 启动大学专属任务：【{school}】（涵盖 {len(school_task_list)} 个学院）")
                print(f"=======================================================")
                school_all_records = []

                for task_data in school_task_list:
                    task_id = task_data["task_id"]
                    college = task_data["college"]
                    url = task_data.get("url")

                    # Claim task
                    claim_res = await client.post(f"{BASE_API}/api/queue/claim/{task_id}")
                    if claim_res.status_code != 200:
                        print(f"⚠️ [Dispatch] 认领工单 {task_id} 失败: {claim_res.text}")
                        continue

                    task_resp = claim_res.json()["task"]
                    worker_id = task_resp["worker_id"]
                    lease_token = task_resp.get("lease_token")

                    async def keep_lease_alive():
                        while True:
                            await asyncio.sleep(60)
                            renewal = await client.post(
                                f"{BASE_API}/api/queue/renew/{task_id}",
                                json={"worker_id": worker_id, "lease_seconds": 300, "lease_token": lease_token})
                            if renewal.status_code != 200:
                                return

                    heartbeat = asyncio.create_task(keep_lease_alive())

                    print(f"🚀 [Antigravity 接单] 正在深度采集：【{school} - {college}】(Task ID: {task_id})")

                    local_task = AgentTask(
                        task_id=task_id,
                        school=school,
                        college=college,
                        url=url,
                        mode=task_data.get("mode", "single"),
                        batch_id=task_data.get("batch_id")
                    )
                    local_task.status = "running"
                    local_task.started_at = time.time()

                    try:
                        await agent.execute_task(local_task, OUTPUT_DIR)

                        if local_task.status == "failed":
                            print(f"❌ [Antigravity 阻断] 【{school} - {college}】采集未通过守恒或阻断: {local_task.error}")
                            sync_payload = {
                                "records": [],
                                "stats": local_task.stats,
                                "download_url": "",
                                "error": local_task.error or "采集未通过守恒或阻断",
                                "progress": 0,
                                "worker_id": worker_id,
                                "lease_token": lease_token
                            }
                            sync_res = await client.post(f"{BASE_API}/api/task/{task_id}/sync", json=sync_payload)
                            if sync_res.status_code != 200:
                                print(f"⚠️ [Dispatch] 阻断状态同步异常: HTTP {sync_res.status_code} - {sync_res.text}")
                            continue

                        print(f"✅ [Antigravity 完成] 【{school} - {college}】采集成效：")
                        print(f"   入库总数: {local_task.stats.get('total', 0)} 位 | 有邮箱: {local_task.stats.get('with_email', 0)} 位")
                        print(f"   交付表: {local_task.download_url}")

                        if local_task.records:
                            school_all_records.extend(local_task.records)

                        # Sync back to server
                        sync_payload = {
                            "records": local_task.records,
                            "stats": local_task.stats,
                            "download_url": local_task.download_url,
                            "progress": 100,
                            "worker_id": worker_id,
                            "lease_token": lease_token
                        }
                        sync_res = await client.post(f"{BASE_API}/api/task/{task_id}/sync", json=sync_payload)
                        if sync_res.status_code == 200:
                            print(f"📡 [Dispatch] 成果与状态已实时回传至网页端。")
                        else:
                            print(f"⚠️ [Dispatch] 回传成果失败: HTTP {sync_res.status_code} - {sync_res.text}")

                    except Exception as e:
                        print(f"❌ [Antigravity 异常] 任务执行失败: {e}")
                        sync_payload = {
                            "records": [],
                            "stats": {},
                            "download_url": "",
                            "error": str(e),
                            "progress": 0,
                            "worker_id": worker_id,
                            "lease_token": lease_token
                        }
                        sync_res = await client.post(f"{BASE_API}/api/task/{task_id}/sync", json=sync_payload)
                        if sync_res.status_code != 200:
                            print(f"⚠️ [Dispatch] 异常同步失败: HTTP {sync_res.status_code} - {sync_res.text}")

                    finally:
                        heartbeat.cancel()
                        with suppress(asyncio.CancelledError):
                            await heartbeat
                    total_executed += 1

                # Export single university workbook
                if school_all_records:
                    export_university_excel(school, school_all_records, OUTPUT_DIR)

            return {"status": "executed", "count": total_executed}

        except httpx.ConnectError:
            print("⚠️ [Dispatch] 本地后台服务 (0.0.0.0:8000) 尚未启动。")
            return {"status": "service_down"}

if __name__ == "__main__":
    asyncio.run(check_and_execute_queue())
