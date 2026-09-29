"""Startup self-check for the resident worker.

Several dependencies used to degrade silently: without the L2 algorithm layer
extraction quietly falls back, and without pypinyin every personal e-mail
fails the name-affinity check and lands in the no-email sheet. Before claiming
any ticket, the worker now verifies what a college run will need and refuses
to start with a readable reason instead of producing weaker deliveries.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Callable

Check = Callable[[], "str | None"]


def _l2_algorithms() -> str | None:
    from server import faculty_extractor
    if not faculty_extractor.L2_AVAILABLE:
        return "L2 算法层（Opus/iso_mining 等）导入失败，名录抽取会降级"
    return None


def _pinyin() -> str | None:
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return "缺少 pypinyin：个人邮箱与姓名无法匹配，会全部落入无邮箱表"
    if lazy_pinyin("张三") != ["zhang", "san"]:
        return "pypinyin 工作异常：姓名拼音转换结果不正确"
    return None


def _ocr() -> str | None:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except Exception as exc:  # onnxruntime raises various errors on broken installs
        return f"图片简介 OCR 不可用（rapidocr_onnxruntime）：{type(exc).__name__}: {exc}"
    return None


def _pdf() -> str | None:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return "缺少 PyMuPDF：PDF 简介无法读取"
    return None


def _harness_runtime() -> str | None:
    configured = os.environ.get("DSH_BIN")
    if configured:
        if not (Path(configured).is_file() or shutil.which(configured)):
            return f"DSH_BIN 指向的 Harness 程序不存在：{configured}"
        return None
    try:
        from deepseek_harness_runtime import bundled_runtime_path
        bundled_runtime_path()
    except Exception as exc:
        return f"当前环境缺少 Harness 运行时（请按 requirements.lock 安装或设置 DSH_BIN）：{type(exc).__name__}"
    return None


def _api_key() -> str | None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        return "未设置 DEEPSEEK_API_KEY：每个工单都会在侦察阶段失败"
    return None


def _browser() -> str | None:
    async def launch() -> None:
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            await browser.close()

    try:
        asyncio.run(asyncio.wait_for(launch(), timeout=60))
    except Exception as exc:
        return ("Playwright Chromium 无法启动（请运行 python -m playwright install chromium）："
                f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
    return None


CHECKS: tuple[tuple[str, Check], ...] = (
    ("算法层", _l2_algorithms),
    ("拼音", _pinyin),
    ("OCR", _ocr),
    ("PDF", _pdf),
    ("Harness", _harness_runtime),
    ("API Key", _api_key),
    ("浏览器", _browser),
)


def run_self_check(checks: tuple[tuple[str, Check], ...] = CHECKS) -> list[str]:
    """Return one readable problem per failed check (empty list: ready)."""
    problems = []
    for name, check in checks:
        try:
            problem = check()
        except Exception as exc:  # a broken check is itself a failure
            problem = f"自检异常：{type(exc).__name__}: {exc}"
        if problem:
            problems.append(f"[{name}] {problem}")
    return problems
