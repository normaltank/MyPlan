"""Temporary pytest plugin: log the native Chrome fallback during the browser suite."""
import subprocess
import tempfile
import time

from server import browser_batch_tool as b

_launch, _collect = b._launch_native_chrome, b._collect_native_pages
_popen = subprocess.Popen


def _logging_popen(args, *rest, **kwargs):
    if args and "chrome" in str(args[0]) and "--remote-debugging-port=0" in args:
        log = tempfile.NamedTemporaryFile("w", prefix="chrome_err_", suffix=".log", delete=False)
        args = [args[0], "--enable-logging=stderr", "--v=1", *args[1:]]
        kwargs["stderr"] = log
        proc = _popen(args, *rest, **kwargs)
        proc.diag_log = log.name
        return proc
    return _popen(args, *rest, **kwargs)


subprocess.Popen = _logging_popen


def _logged_launch(chrome_exe, urls):
    t = time.monotonic()
    proc, user_dir, port = _launch(chrome_exe, urls)
    print("\n[diag] launch", urls, round(time.monotonic() - t, 2), "port", port, "rc", proc.poll(), flush=True)
    log = getattr(proc, "diag_log", None)
    if log:
        lines = open(log, errors="replace").read().splitlines()
        print("[diag] stderr lines", len(lines), flush=True)
        for line in lines[:40] + ["..."] + lines[-40:]:
            print("[diag]   ", line[:220], flush=True)
    return proc, user_dir, port


def _logged_collect(port, started, settle_seconds):
    pages = _collect(port, started, settle_seconds)
    print("[diag] collect", port, round(time.monotonic() - started, 2), len(pages), flush=True)
    return pages


b._launch_native_chrome, b._collect_native_pages = _logged_launch, _logged_collect
