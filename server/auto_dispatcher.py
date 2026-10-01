"""Optional read-only local queue counter; harvesting belongs to agent_worker."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys
import time

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from server.local_service_config import local_web_url  # noqa: E402 - direct-file CLI supports any working directory


def pending_college_count(db_path: str | Path | None = None) -> int | None:
    """Count queued college work, never create/migrate a DB or read teacher rows."""
    # Importing agent_queue would initialize its global queue, which can write.
    # Follow its default path convention without importing that mutable module.
    path = Path(db_path or os.environ.get("HARVEST_TASK_DB") or _ROOT / "server/tasks.db").resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='queued' AND task_type='harvest'"
        ).fetchone()[0]


async def run_auto_dispatcher(interval: int = 5, once: bool = False, base_url: str | None = None,
                              *, db_path: str | Path | None = None) -> None:
    """Display aggregate queue counts; neither HTTP access nor dispatch occurs."""
    if interval < 1:
        raise ValueError("monitor interval must be positive")
    address = base_url or local_web_url()
    print("高校师资工单只读巡检器：本机 SQLite 总数，不读取教师记录。")
    print(f"前台地址: {address}；采集由常驻 python -m server.agent_worker 执行。")
    last_log = None
    while True:
        now = time.monotonic()
        if last_log is None or now - last_log >= 20:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                count = await asyncio.to_thread(pending_college_count, db_path)
                message = "本机任务库尚未创建，等待正式服务启动。" if count is None else f"{count} 个学院工单待认领。"
            except sqlite3.Error as exc:
                message = f"本机任务库暂不可读（{type(exc).__name__}），未修改任务。"
            print(f"[{stamp}] {message}", flush=True)
            last_log = now
        if once:
            return
        await asyncio.sleep(interval)


def main() -> None:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本机只读工单计数；不认领、不采集")
    parser.add_argument("--interval", type=int, default=5, help="巡检间隔秒数，必须为正整数")
    parser.add_argument("--once", action="store_true", help="只读一次并退出")
    parser.add_argument("--url", default=None, help="仅显示的前台地址；默认读取本机服务端口配置")
    parser.add_argument("--db-path", default=None, help="本机任务库；默认 HARVEST_TASK_DB 或 server/tasks.db")
    args = parser.parse_args()
    try:
        asyncio.run(run_auto_dispatcher(args.interval, args.once, args.url, db_path=args.db_path))
    except KeyboardInterrupt:
        print("只读巡检器已退出。")


if __name__ == "__main__":
    main()
