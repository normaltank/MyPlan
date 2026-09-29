import os
import sys
import webbrowser
import json
from pathlib import Path
import socket
import uvicorn

# 确保控制台支持 UTF-8 打印
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

def load_web_app():
    """Use package imports even when this file is launched from server/."""
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from server.app import app
    return app


def port_is_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0

def main():
    print("=" * 70)
    print("      高校师资智能全流程采集平台 · 网页服务")
    print("      [Playwright 无头浏览器 + DOM 同构子树聚类 + CS 原理自适应抽取]")
    print("=" * 70)
    print()
    print(f" [✓] 本机访问地址:    http://localhost:8000")
    print(" [★] 多员工访问:  请先通过 HTTPS 反向代理发布服务")
    print()
    print("=" * 70)
    print(" [使用提示]")
    print(" 1. 本机访问：http://localhost:8000；首次打开时按页面提示创建管理员。")
    print(" 2. 员工远程访问须由服务器 HTTPS 入口转发到本机 127.0.0.1:8000。")
    print("=" * 70)
    print()
    print("正在自动唤起默认浏览器并启动后台服务...")
    print()

    # 自动打开本地浏览器
    try:
        webbrowser.open("http://localhost:8000")
    except Exception:
        pass

    # 导入并启动 FastAPI
    app = load_web_app()
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)
    
    if port_is_open(port):
        print(f"端口 {port} 已有服务；请先关闭旧的网页服务窗口，再重新启动。")
        return

    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
