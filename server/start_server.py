import ipaddress
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

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

# Office networks use these ranges. Proxy TUN adapters (Clash uses
# 198.18.0.0/15) and public addresses are outside them, so they are never offered.
_LAN_RANGES = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _is_lan(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.version == 4 and any(ip in network for network in _LAN_RANGES)


def _gateway_addresses() -> list[str] | None:
    """IPv4 addresses on Windows adapters that have a default gateway.

    WSL, Hyper-V and Docker adapters are host-only and have no gateway, so
    this keeps the Wi-Fi or Ethernet address. None where it cannot be asked.
    """
    if sys.platform != "win32":
        return None
    command = ("Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | "
               "ForEach-Object { $_.IPv4Address.IPAddress }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.split() if result.returncode == 0 else None


def _host_addresses() -> list[str]:
    addresses = []
    try:
        addresses += [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            # UDP connect only picks the outgoing interface; nothing is sent.
            probe.connect(("10.255.255.255", 1))
            addresses.append(probe.getsockname()[0])
        except OSError:
            pass
    return addresses


def lan_addresses() -> list[str]:
    """This PC's office-network addresses for the colleagues' link, best first."""
    preferred = [a for a in (_gateway_addresses() or []) if _is_lan(a)]
    found = preferred or [a for a in _host_addresses() if _is_lan(a)]
    return list(dict.fromkeys(found))


def main():
    # Also support launching this file directly from server/.
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from server.local_service_config import load_web_config, local_web_url
    cfg = load_web_config()
    address = local_web_url()
    print("=" * 70)
    print("      高校师资智能全流程采集平台 · 网页服务")
    print("      [Playwright 无头浏览器 + DOM 同构子树聚类 + CS 原理自适应抽取]")
    print("=" * 70)
    print()
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)
    lan = lan_addresses() if host == "0.0.0.0" else []
    print(f" [✓] 本机访问地址:    {address}")
    if len(lan) == 1:
        print(f" [★] 同事访问地址:    http://{lan[0]}:{port}")
    elif lan:
        print(" [★] 同事访问地址:    本机有多个局域网地址，请发和同事同一网络的那个：")
        for candidate in lan:
            print(f"                      http://{candidate}:{port}")
    elif host == "0.0.0.0":
        print(" [★] 同事访问地址:    未找到本机局域网地址，请用 ipconfig 查看")
    else:
        print(" [★] 当前只允许本机访问（server/config.json 的 host 为 127.0.0.1）")
    print()
    print("=" * 70)
    print(" [使用提示]")
    print(" 1. 打开网页时填写姓名即可，工单和每日统计按姓名记录。")
    print(" 2. 首次启动时 Windows 若询问是否允许网络访问，只勾选“专用网络”。")
    print("=" * 70)
    print()
    print("正在自动唤起默认浏览器并启动后台服务...")
    print()

    # 自动打开本地浏览器
    try:
        webbrowser.open(address)
    except Exception:
        pass

    # 导入并启动 FastAPI
    app = load_web_app()
    
    if port_is_open(port):
        print(f"端口 {port} 已有服务；请先关闭旧的网页服务窗口，再重新启动。")
        return

    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
