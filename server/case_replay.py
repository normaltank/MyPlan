"""Replay saved real-site cases offline and report which colleges are ready.

A case is a directory holding the plan a college was harvested with
(``plan.json``), the harvest's ``out/`` directory (audit, snapshots, original
media, OCR cache) and its ``result.json``. Replay runs the current
``browser_batch_tool.run_plan`` on the same plan in a real browser whose every
request is answered from the saved files: a captured page or medium is served
as captured, anything else gets 404. Nothing reaches the network and no model is
called; OCR is local and reuses the case's cache.

The report puts the saved result next to the replayed one, so a code change
shows directly whether the number of delivery-ready colleges went up. What the
replay cannot show is limited by what was saved:

* a page the original run never opened is not in the case, so a change that
  makes the harvest open new pages shows up as ``missing`` requests, not as a
  regression of the site;
* sub-resources (scripts, styles, counters) were not saved and answer 404, which
  the profile checks treat as permanently missing on the site.

Usage::

    python -m server.case_replay CASES_DIR [--case NAME ...] [--out DIR]
        [--report report.md] [--chromium PATH]
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass, field
import json
import mimetypes
from pathlib import Path
import shutil
import tempfile
from typing import Any
from urllib.parse import urlsplit

# (URL key, path key) pairs in an audit that name a captured original.
_CAPTURED = (("url", "path"), ("media_url", "original_path"))
# Requests whose absence changes what the harvest sees (pages, data, media).
_CONTENT_TYPES = {"document", "xhr", "fetch"}


def _key(url: str) -> str:
    parts = urlsplit(url)
    return parts._replace(scheme=parts.scheme.lower(), netloc=parts.netloc.lower(), fragment="").geturl()


def _local(case_dir: Path, recorded: str) -> Path | None:
    """Map a path recorded during the original run into the case's out/."""
    marker = "/out/"
    index = recorded.replace("\\", "/").rfind(marker)
    if index < 0:
        return None
    path = case_dir / "out" / recorded.replace("\\", "/")[index + len(marker):]
    return path if path.is_file() else None


def captured_files(case_dir: Path, audit: dict) -> dict[str, Path]:
    """URL → saved file for every page and medium the audit says was captured."""
    found: dict[str, Path] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for url_key, path_key in _CAPTURED:
                url, recorded = value.get(url_key), value.get(path_key)
                if isinstance(url, str) and isinstance(recorded, str):
                    local = _local(case_dir, recorded)
                    if local is not None:
                        found.setdefault(_key(url), local)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(audit)
    return found


@dataclass
class Case:
    name: str
    directory: Path
    plan: dict
    audit: dict
    saved: dict

    @property
    def college(self) -> tuple[str, str]:
        return self.plan.get("school", ""), self.plan.get("college", "")


def load_case(directory: Path) -> Case | None:
    plan_path, result_path = directory / "plan.json", directory / "result.json"
    audits = sorted((directory / "out").glob("*_audit.json"))
    if not (plan_path.is_file() and result_path.is_file() and audits):
        return None
    return Case(directory.name, directory, json.loads(plan_path.read_text(encoding="utf-8")),
                json.loads(audits[0].read_text(encoding="utf-8")),
                json.loads(result_path.read_text(encoding="utf-8")))


def discover(root: Path, names: list[str] | None = None) -> list[Case]:
    """One case per college; a re-run of the same college keeps the newest capture."""
    cases = [case for path in sorted(root.iterdir()) if path.is_dir()
             and (not names or path.name in names) and (case := load_case(path))]
    newest: dict[tuple[str, str], Case] = {}
    for case in cases:
        held = newest.get(case.college)
        if held is None or _captured_at(case) > _captured_at(held):
            newest[case.college] = case
    return sorted(newest.values(), key=lambda case: case.name)


def _captured_at(case: Case) -> str:
    stamp = case.directory / "captured_at"
    return stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""


def _content_type(path: Path) -> str:
    if path.suffix.lower() in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


@dataclass
class _Response:
    status: int
    data: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    async def body(self) -> bytes:
        return self.data


@dataclass
class _Request:
    files: dict[str, Path]
    missing: list[str]

    async def get(self, url: str, **_: Any) -> _Response:
        path = self.files.get(_key(url))
        if path is None:
            self.missing.append(url)
            return _Response(404)
        return _Response(200, path.read_bytes())


@dataclass
class ReplayContext:
    """A browser context whose pages and asset reads come only from a case."""
    context: Any
    files: dict[str, Path]
    missing: list[str] = field(default_factory=list)

    async def install(self) -> None:
        await self.context.route("**/*", self._serve)

    async def _serve(self, route: Any) -> None:
        request = route.request
        path = self.files.get(_key(request.url))
        if path is None:
            if request.resource_type in _CONTENT_TYPES:
                self.missing.append(request.url)
            await route.fulfill(status=404, body="")
            return
        await route.fulfill(status=200, body=path.read_bytes(), content_type=_content_type(path))

    @property
    def request(self) -> _Request:
        return _Request(self.files, self.missing)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.context, name)


def _summary(result: dict) -> dict:
    unresolved = result.get("unresolved")
    if isinstance(unresolved, list):
        codes = Counter(str(item.get("status") or item.get("reason") or "unknown")
                        for item in unresolved if isinstance(item, dict))
        count = len(unresolved)
    else:
        codes, count = Counter(), result.get("unresolved_count") or 0
    return {"status": result.get("status"), "delivery_ready": bool(result.get("delivery_ready")),
            "total": result.get("total"), "with_email": result.get("with_email"),
            "unresolved": count, "codes": dict(codes.most_common())}


async def replay_case(case: Case, browser: Any, out_dir: Path, *, timeout: float = 900) -> dict:
    from server.browser_batch_tool import run_plan
    from server.browser_context import new_browser_context

    target = out_dir / case.name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    cache = case.directory / "out" / "ocr_cache"
    if cache.is_dir():
        shutil.copytree(cache, target / "ocr_cache")
    context = await new_browser_context(browser)
    replay = ReplayContext(context, captured_files(case.directory, case.audit))
    try:
        await replay.install()
        result = _summary(await asyncio.wait_for(run_plan(case.plan, replay, target), timeout))
    except Exception as exc:  # one broken case must not stop the batch
        result = {"status": "replay_error", "delivery_ready": False, "total": None, "with_email": None,
                  "unresolved": None, "codes": {}, "error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        await context.close()
    result["missing"] = sorted(set(replay.missing))
    return result


async def replay_all(cases: list[Case], out_dir: Path, *, chromium: str | None = None,
                     timeout: float = 900) -> list[dict]:
    from playwright.async_api import async_playwright

    rows = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**({"executable_path": chromium} if chromium else {}))
        try:
            for case in cases:
                school, college = case.college
                rows.append({"case": case.name, "school": school, "college": college,
                             "saved": _summary(case.saved),
                             "replay": await replay_case(case, browser, out_dir, timeout=timeout)})
        finally:
            await browser.close()
    return rows


def render(rows: list[dict]) -> str:
    saved_ready = sum(row["saved"]["delivery_ready"] for row in rows)
    replay_ready = sum(row["replay"]["delivery_ready"] for row in rows)
    lines = [f"Ready: saved {saved_ready}/{len(rows)}, replay {replay_ready}/{len(rows)}", "",
             "| case | college | saved ready/total/unresolved | replay ready/total/unresolved | "
             "replay unresolved codes | missing |",
             "| --- | --- | --- | --- | --- | --- |"]

    def cell(item: dict) -> str:
        return f"{'yes' if item['delivery_ready'] else 'no'} / {item['total']} / {item['unresolved']}"

    for row in rows:
        replay = row["replay"]
        codes = ", ".join(f"{code} {count}" for code, count in replay["codes"].items())
        if replay.get("error"):
            codes = replay["error"].replace("|", "/")
        lines.append(f"| {row['case']} | {row['school']} {row['college']} | {cell(row['saved'])} | "
                     f"{cell(replay)} | {codes} | {len(replay['missing'])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay saved real-site cases offline")
    parser.add_argument("cases", type=Path, help="directory of case directories")
    parser.add_argument("--case", action="append", help="only these case directory names")
    # Outside server/outputs, so replays never touch the production OCR cache.
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "myplan-case-replay")
    parser.add_argument("--report", type=Path, help="write the Markdown table here")
    parser.add_argument("--chromium", help="Chromium executable, when not Playwright's own")
    parser.add_argument("--timeout", type=float, default=900, help="seconds per case")
    args = parser.parse_args()
    rows = asyncio.run(replay_all(discover(args.cases, args.case), args.out,
                                  chromium=args.chromium, timeout=args.timeout))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "replay.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    table = render(rows)
    if args.report:
        args.report.write_text(table, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
