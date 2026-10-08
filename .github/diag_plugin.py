"""Temporary pytest plugin: log the native Chrome fallback during the browser suite."""
import subprocess
import time
import traceback

from server import browser_batch_tool as b

_launch, _collect = b._launch_native_chrome, b._collect_native_pages


def _logged_launch(chrome_exe, urls):
    ps = subprocess.run(["ps", "-eo", "comm,pcpu,etimes"], capture_output=True, text=True).stdout.splitlines()
    busy = [line for line in ps[1:] if float(line.split()[-2]) > 5]
    print("\n[diag] chrome procs", sum("chrom" in line for line in ps), "busy", busy[:15],
          "load", open("/proc/loadavg").read().strip(), flush=True)
    t = time.monotonic()
    proc, user_dir, port = _launch(chrome_exe, urls)
    print("\n[diag] launch", urls, round(time.monotonic() - t, 2), "port", port, "rc", proc.poll(), flush=True)
    return proc, user_dir, port


def _logged_collect(port, started, settle_seconds):
    try:
        pages = _collect(port, started, settle_seconds)
    except BaseException:
        print("[diag] collect raised on", port, traceback.format_exc(), flush=True)
        raise
    print("[diag] collect", port, round(time.monotonic() - started, 2), {k: v[:60] for k, v in pages.items()}, flush=True)
    return pages


b._launch_native_chrome, b._collect_native_pages = _logged_launch, _logged_collect
