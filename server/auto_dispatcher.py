import os
import sys
import time
import asyncio
import argparse
from datetime import datetime
import httpx

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

BASE_API = "http://127.0.0.1:8000"

async def run_auto_dispatcher(interval: int = 5, once: bool = False, base_url: str = BASE_API):
    """Monitor pending work without launching the retired script-only harvester."""
    print("=" * 65)
    print("🤖 【高校师资工单巡检器】启动；学院工单由 IDE Subagent 认领")
    print(f"📡 监听目标: {base_url}/api/queue/pending")
    print(f"⏱️ 巡检周期: 每 {interval} 秒")
    print(f"模式: {'单次检查 (--once)' if once else '常驻守护轮询 (Daemon)'}")
    print("=" * 65 + "\n")

    last_idle_log = 0.0

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            now_ts = time.time()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            try:
                # 1. Probe pending queue
                r = await client.get(f"{base_url}/api/queue/pending")
                if r.status_code == 200:
                    data = r.json()
                    count = data.get("count", 0)

                    if count > 0:
                        if now_ts - last_idle_log >= 20.0:
                            print(f"[{now_str}] 🔔 {count} 个学院工单待 IDE Subagent 认领；巡检器不会运行旧脚本采集。")
                            last_idle_log = now_ts
                    else:
                        # Log heartbeat once every 20 seconds when idle
                        if now_ts - last_idle_log >= 20.0:
                            print(f"[{now_str}] 💤 工单池无待处理任务 (0 queued)，智能体待命中... (周期: {interval}s)")
                            last_idle_log = now_ts
                else:
                    print(f"[{now_str}] ⚠️ 后台服务返回状态异常: HTTP {r.status_code}")

            except httpx.ConnectError:
                if now_ts - last_idle_log >= 20.0:
                    print(f"[{now_str}] ⏳ 本地后台服务 ({base_url}) 未就绪或正在重启，等待连接...")
                    last_idle_log = now_ts
            except Exception as e:
                print(f"[{now_str}] ❌ 巡检过程出现异常: {e}")

            if once:
                print(f"[{now_str}] 🏁 单次巡检任务结束。")
                break

            await asyncio.sleep(interval)

def main():
    parser = argparse.ArgumentParser(description="高校师资后台自动巡检与接单执行器")
    parser.add_argument("--interval", type=int, default=5, help="巡检周期间隔（秒），默认 5 秒")
    parser.add_argument("--once", action="store_true", help="仅执行单次巡检并退出")
    parser.add_argument("--url", type=str, default=BASE_API, help="后台服务根地址，默认 http://127.0.0.1:8000")
    args = parser.parse_args()

    try:
        asyncio.run(run_auto_dispatcher(interval=args.interval, once=args.once, base_url=args.url))
    except KeyboardInterrupt:
        print("\n👋 收到用户中断信号 (Ctrl+C)，自动巡检守护引擎已安全退出。")

if __name__ == "__main__":
    main()
