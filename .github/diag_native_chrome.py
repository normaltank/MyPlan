"""Temporary CI diagnostic for the native Chrome fallback; removed before merge."""
import concurrent.futures
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import browser_batch_tool as b  # noqa: E402

exe = b._native_chrome_executable()
print("exe", exe, flush=True)
print(subprocess.run([str(exe), "--version"], capture_output=True, text=True).stdout, flush=True)

real_launch, real_collect = b._launch_native_chrome, b._collect_native_pages


def launch(chrome_exe, urls):
    t = time.monotonic()
    proc, user_dir, port = real_launch(chrome_exe, urls)
    print(urls, "launch", round(time.monotonic() - t, 2), "port", port, "rc", proc.poll(),
          "port_file", (user_dir / "DevToolsActivePort").exists(), flush=True)
    return proc, user_dir, port


def collect(port, started, settle_seconds):
    try:
        pages = real_collect(port, started, settle_seconds)
    except Exception as exc:
        print(port, "collect raised", repr(exc), flush=True)
        raise
    print(port, "collect", round(time.monotonic() - started, 2), {k: v[:80] for k, v in pages.items()}, flush=True)
    return pages


b._launch_native_chrome, b._collect_native_pages = launch, collect
d = pathlib.Path(tempfile.mkdtemp())
urls = []
for i in range(3):
    page = d / f"college{i}.html"
    page.write_text(f"<html><body><h1>college-{i}</h1></body></html>", encoding="utf-8")
    urls.append(page.as_uri())
for round_ in range(2):
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        print("round", round_, [(r or "NONE")[:70] for r in pool.map(b._fetch_page_via_native_chrome, urls)], flush=True)
print("single", (b._fetch_page_via_native_chrome(urls[0]) or "NONE")[:70], flush=True)
