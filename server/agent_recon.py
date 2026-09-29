"""侦察段：让 dsh Agent 核实学院官网结构并产出采集计划 JSON。

本模块是**模型与数据之间唯一的闸门**（见 AGENTS.md 红线 1）：
- 输入：学校、学院、入口 URL
- 输出：只含 URL 与 CSS 选择器的采集计划（经 `_validate_plan` 与红线 1 卫生检查双重门禁）

设计要点：
1. `harness.run()` 是**同步阻塞**调用。若直接在事件循环里调用，会冻结
   `run_with_lease` 的租约心跳（60 秒一次），导致 300 秒租约过期、
   第二个 worker 合法认领同一学院、两台 Chromium 同时打同一站点。
   故 `reconnoiter()` 是 async，并在独立工作线程中卸出阻塞调用。
2. 模型产出物中**任何非 URL、非选择器的字符串都视为可疑**。
   CSS 选择器不可能含 CJK 字符——这条机械判据是红线 1 的可执行形式。
3. 模型在侦察时必然看到页面内容（含姓名），这是允许的；
   被禁止的是把数据字段**带出来**（见红线 1「严禁数据出境」）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
import yaml

__all__ = ["ReconError", "ReconPlanError", "PlanHygieneError", "ReconPhaseLimitExceeded",
           "ReconResult", "reconnoiter"]


class ReconError(RuntimeError):
    """侦察未能产出可用计划（调用失败、超时、无 JSON、schema 不合法）。"""


class ReconBudgetExceeded(ReconError):
    """A completed model request crossed the college's reconnaissance budget."""


class ReconPhaseLimitExceeded(ReconError):
    """One discovery/branch session used more model requests than allowed.

    A structure the plan contract cannot express makes the scout probe the
    same page again and again; a repair round rarely helps, so this is a
    terminal failure that tells operators to look at the page type.
    """


class ReconCancelled(ReconError):
    """The caller stopped waiting (task timeout); end the Harness session now."""


class ReconBudget:
    """Count actual SDK usage across every phase and repair of one college.

    ``max_calls``/``max_tokens`` stop the session (both zero: never stop).
    ``alert_calls``/``alert_tokens`` only report, once, through ``on_alert``:
    a complex college keeps running while an operator sees the anomaly.
    """

    def __init__(self, *, max_calls: int = 0, max_tokens: int = 0,
                 alert_calls: int = 0, alert_tokens: int = 0,
                 on_alert: Callable[[dict[str, Any]], None] | None = None) -> None:
        if min(max_calls, max_tokens, alert_calls, alert_tokens) < 0:
            raise ValueError("recon budget limits must not be negative")
        if (max_calls == 0) != (max_tokens == 0):
            raise ValueError("recon budget limits must be positive")
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.alert_calls = alert_calls
        self.alert_tokens = alert_tokens
        self.on_alert = on_alert
        self.alerts: list[dict[str, Any]] = []
        self.calls = 0
        self.usage = {"uncached_input": 0, "cached_input": 0, "output": 0}

    def snapshot(self) -> dict[str, Any]:
        usage = self.usage.copy()
        return {"calls": self.calls, "token_usage": usage,
                "total_tokens": sum(usage.values()),
                "max_calls": self.max_calls, "max_tokens": self.max_tokens}

    def delta(self, before: dict[str, Any]) -> dict[str, Any]:
        return {"model_calls": self.calls - before["calls"],
                "token_usage": {key: value - before["token_usage"][key]
                                for key, value in self.usage.items()},
                "budget_total": self.snapshot()}

    def seed(self, attempts: list[dict[str, Any]]) -> None:
        for attempt in attempts:
            self.calls += int(attempt.get("model_calls") or 0)
            source = attempt.get("token_usage") or {}
            for key in self.usage:
                self.usage[key] += int(source.get(key) or 0)

    def observe(self, event: dict[str, Any]) -> None:
        if event.get("type") != "assistant/message":
            return
        values = (event.get("data") or {}).get("usage") or {}
        if not values:
            return
        self.calls += 1
        for target, source in (("uncached_input", "inputTokens"),
                               ("cached_input", "cacheReadTokens"),
                               ("output", "outputTokens")):
            self.usage[target] += int(values.get(source) or 0)
        total = sum(self.usage.values())
        if not self.alerts and ((self.alert_calls and self.calls >= self.alert_calls)
                                or (self.alert_tokens and total >= self.alert_tokens)):
            alert = {"calls": self.calls, "total_tokens": total, "token_usage": self.usage.copy(),
                     "alert_calls": self.alert_calls, "alert_tokens": self.alert_tokens,
                     "at": time.time()}
            self.alerts.append(alert)
            if self.on_alert is not None:
                try:
                    self.on_alert(alert)
                except Exception:  # an alert sink must never stop reconnaissance
                    pass
        if self.max_calls and (self.calls >= self.max_calls or total >= self.max_tokens):
            raise ReconBudgetExceeded(
                f"侦察预算耗尽：{self.calls} 次请求、{total} Token；"
                f"上限 {self.max_calls} 次、{self.max_tokens} Token")


class ReconPlanError(ReconError):
    """Model completed but returned no usable structure plan; safe to repair."""


class PlanHygieneError(ReconError):
    """计划中检出了数据字段——违反红线 1「严禁数据出境」。"""


# ── 计划卫生：红线 1 的机械判据 ────────────────────────────────────────────
# 两条判据覆盖两类数据字段：
#   · CJK 判据 —— CSS 选择器不可能含 CJK，故任何含 CJK 的非白名单字符串都是姓名/职称/简介
#   · 邮箱判据 —— 邮箱是纯 ASCII，CJK 判据抓不到，必须单独查
_CJK = re.compile(r"[一-鿿]")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"^https?://", re.I)
# 计划 schema 里允许出现的字面量（非 URL、非选择器的合法值）
_SCHEMA_LITERALS = {"university_faculty_roster", "hospital_doctor_roster", "navigation"}
_MODEL_PLAN_KEYS = {"school", "college", "source_kind", "branches",
                    "profile_concurrency", "profile_ready_selector"}
_MODEL_BRANCH_KEYS = {"url", "kind", "source_kind", "card_selector", "single_page",
                      "next_selector", "max_pages", "initial_click_selector",
                      "child_link_selector", "child_branch", "max_children",
                      "empty_state_selector", "roster_container_selector",
                      "profile_content_selector", "profile_content_start_selector"}
_MODEL_CHILD_KEYS = _MODEL_BRANCH_KEYS - {"url", "kind", "child_link_selector", "child_branch", "max_children"}


def _collect_strings(node: Any, path: str = "$") -> list[tuple[str, str]]:
    """递归收集 JSON 中所有字符串值及其路径。"""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            found.extend(_collect_strings(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found.extend(_collect_strings(v, f"{path}[{i}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


def check_plan_hygiene(plan: dict[str, Any], *, school: str, college: str) -> None:
    """红线 1 门禁：计划只允许含 URL、CSS 选择器与 schema 字面量。

    判据是机械的——**CSS 选择器不可能包含 CJK 字符**。
    因此任何含 CJK 的字符串，除非它整串等于目标学校/学院名，都是数据泄漏。
    """
    allowed_names = {school.strip(), college.strip()}
    violations: list[str] = []

    for path, value in _collect_strings(plan):
        text = value.strip()
        if not text:
            continue
        if text in allowed_names or text in _SCHEMA_LITERALS:
            continue
        if _URL.match(text):
            continue
        if _CJK.search(text):
            violations.append(f"{path} = {text!r}（含中文）")
            continue
        if _EMAIL.search(text):
            violations.append(f"{path} = {text!r}（含邮箱）")

    if violations:
        raise PlanHygieneError(
            "计划含疑似数据字段（红线 1）：" + "; ".join(violations[:5])
        )


def _is_structure_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in {"http", "https"} and parsed.hostname
                    and not parsed.username and not parsed.password
                    and not _EMAIL.search(unquote(value)))
    except ValueError:
        return False


def validate_model_plan(plan: dict[str, Any], *, school: str, college: str) -> None:
    """Accept only the structure-only plan that the server recon agent may emit.

    Manually reviewed poster and appointment evidence belongs to the IDE path,
    not to a model result. This validator runs in addition to the shared batch
    schema because that schema intentionally supports those manual fields.
    """
    if plan.get("school") != school or plan.get("college") != college:
        raise PlanHygieneError("model plan school/college differs from queued target")

    def keys(node: dict[str, Any], allowed: set[str], path: str) -> None:
        extra = set(node) - allowed
        if extra:
            raise PlanHygieneError(f"{path} contains forbidden fields: {sorted(extra)}")

    def selector(node: dict[str, Any], field: str, path: str) -> None:
        if field in node and (not isinstance(node[field], str) or not node[field].strip()):
            raise PlanHygieneError(f"{path}.{field} must be a non-empty CSS selector")

    def branch(node: dict[str, Any], path: str, *, child: bool = False) -> None:
        keys(node, _MODEL_CHILD_KEYS if child else _MODEL_BRANCH_KEYS, path)
        if not child:
            raw_url = node.get("url")
            if not isinstance(raw_url, str):
                raise PlanHygieneError(f"{path}.url must be a URL")
            if not _is_structure_url(raw_url):
                raise PlanHygieneError(f"{path}.url is not a permitted roster URL")
        for field in ("card_selector", "next_selector", "initial_click_selector", "child_link_selector",
                      "empty_state_selector", "roster_container_selector",
                      "profile_content_selector", "profile_content_start_selector"):
            selector(node, field, path)
        if "profile_content_start_selector" in node and not node.get("profile_content_selector"):
            raise PlanHygieneError(f"{path} profile start requires a content selector")
        if "empty_state_selector" in node or "roster_container_selector" in node:
            from server.empty_roster import empty_roster_branch_valid
            if child or not empty_roster_branch_valid(node):
                raise PlanHygieneError(f"{path} empty selectors require an ordinary single-page roster")
        if node.get("kind", "roster") == "roster" or child:
            if not isinstance(node.get("card_selector"), str) or not node["card_selector"].strip():
                raise PlanHygieneError(f"{path}.card_selector is required")
        if "single_page" in node and not isinstance(node["single_page"], bool):
            raise PlanHygieneError(f"{path}.single_page must be boolean")
        for field in ("max_pages", "max_children"):
            if field in node and (type(node[field]) is not int or not 1 <= node[field] <= 100):
                raise PlanHygieneError(f"{path}.{field} must be an integer from 1 to 100")
        if "child_branch" in node:
            if not isinstance(node["child_branch"], dict):
                raise PlanHygieneError(f"{path}.child_branch must be an object")
            branch(node["child_branch"], f"{path}.child_branch", child=True)

    keys(plan, _MODEL_PLAN_KEYS, "$")
    for index, item in enumerate(plan["branches"]):
        branch(item, f"$.branches[{index}]")
    selector(plan, "profile_ready_selector", "$")
    check_plan_hygiene(plan, school=school, college=college)


# ── 模型回复 → JSON ────────────────────────────────────────────────────────
def extract_plan_json(text: str) -> dict[str, Any] | None:
    """从模型回复中抽出第一个 JSON 对象（容忍 markdown 围栏）。"""
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        candidates.append(fenced.group(1))
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for raw in candidates:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def build_repair_feedback(plan: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Pass structure failures back to the scout, never records or narrative text."""
    validate_model_plan(plan, school=plan["school"], college=plan["college"])
    issues = []
    for item in report.get("unresolved", [])[:20]:
        status = item.get("status", "")
        if not isinstance(status, str) or not re.fullmatch(r"[a-z_]{1,80}", status):
            continue
        issue: dict[str, Any] = {"status": status}
        for key in ("url", "directory_url", "branch_url", "page_url"):
            value = item.get(key)
            if (isinstance(value, str) and _URL.match(value)
                    and not _EMAIL.search(unquote(value))):
                issue[key] = value
        value = item.get("selector")
        if (isinstance(value, str) and len(value) <= 500
                and not _CJK.search(value) and not _EMAIL.search(value)):
            issue["selector"] = value
        for key in ("branch_index", "observed_count", "candidate_count", "card_count",
                    "visible_cards", "extracted_candidates", "page"):
            if type(item.get(key)) is int:
                issue[key] = item[key]
        issues.append(issue)
    return {"previous_plan": plan, "issues": issues}


def run_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    usage = {"uncached_input": 0, "cached_input": 0, "output": 0}
    tools: dict[str, int] = {}
    steps = 0
    first_input = None
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "step/start":
            steps += 1
        elif event.get("type") == "tool/call":
            name = data.get("name", "unknown")
            tools[name] = tools.get(name, 0) + 1
        elif event.get("type") == "assistant/message":
            values = data.get("usage") or {}
            if values and first_input is None:
                first_input = int(values.get("inputTokens") or 0) + int(values.get("cacheReadTokens") or 0)
            for output, source in (("uncached_input", "inputTokens"),
                                   ("cached_input", "cacheReadTokens"), ("output", "outputTokens")):
                usage[output] += int(values.get(source) or 0)
    return {"steps": steps, "model_calls": sum(event.get("type") == "assistant/message" and
            bool((event.get("data") or {}).get("usage")) for event in events),
            "tool_calls": sum(tools.values()), "tools": tools,
            "token_usage": usage, "first_request_input": first_input}


def browser_url_aliases(events: list[dict[str, Any]]) -> dict[str, str]:
    """Map redirects only from successful browser results, never URL heuristics."""
    calls = {}
    aliases = {}
    last_verify = None
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "tool/call":
            try:
                calls[data.get("callId")] = (data.get("name"), json.loads(data.get("arguments") or "{}"))
            except (TypeError, ValueError):
                continue
        elif event.get("type") == "tool/result":
            for block in (data.get("message") or {}).get("content", []):
                call = calls.get(block.get("toolCallId"))
                if call is None or block.get("isError"):
                    continue
                name, args = call
                for content in block.get("content", []):
                    if content.get("type") != "text":
                        continue
                    try:
                        result = json.loads(content.get("text", ""))
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(result, dict) or result.get("error"):
                        continue
                    source = None
                    if name == "mcp__playwright__browser_navigate":
                        source = args.get("url")
                        last_verify = None
                    elif name == "mcp__playwright__browser_verify":
                        last_verify = (args, result)
                    elif name == "mcp__playwright__browser_click":
                        if last_verify and last_verify[0].get("next_selector") == args.get("selector"):
                            verified = last_verify[1]
                            if verified.get("nextCount") == 1 and not verified.get("nextDisabled"):
                                source = verified.get("nextUrl")
                                # A same-URL AJAX action is not a redirect of the source roster.
                                if source == verified.get("url"):
                                    source = None
                        last_verify = None
                    destination = result.get("url")
                    if (isinstance(source, str) and _URL.match(source) and isinstance(destination, str)
                            and _URL.match(destination) and source != destination):
                        aliases[source] = destination
    return aliases


def resolved_browser_url(url: str, aliases: dict[str, str]) -> str:
    seen = set()
    while url in aliases and url not in seen:
        seen.add(url)
        url = aliases[url]
    return url


def selector_evidence_issues(plan: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept selectors only when this session's browser verified their matches."""
    calls = {}
    observations = []
    profile_observations = []
    clicks = []
    for index, event in enumerate(events):
        data = event.get("data") or {}
        if event.get("type") == "tool/call" and data.get("name") in (
                "mcp__playwright__browser_verify", "mcp__playwright__browser_click",
                "mcp__playwright__browser_verify_profile"):
            try:
                args = json.loads(data.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            calls[data.get("callId")] = (data["name"], args)
        elif event.get("type") == "tool/result":
            for block in (data.get("message") or {}).get("content", []):
                call = calls.get(block.get("toolCallId"))
                if call is None or block.get("isError"):
                    continue
                name, args = call
                for content in block.get("content", []):
                    if content.get("type") != "text":
                        continue
                    try:
                        result = json.loads(content.get("text", ""))
                    except (TypeError, ValueError):
                        continue
                    if isinstance(result, dict):
                        if name == "mcp__playwright__browser_click":
                            clicks.append((args, index))
                        elif name == "mcp__playwright__browser_verify_profile":
                            profile_observations.append((args, result, index))
                        else:
                            observations.append((args, result, index))
    issues = []
    aliases = browser_url_aliases(events)
    resolve = lambda url: resolved_browser_url(url, aliases)
    for branch in plan["branches"]:
        selector = branch.get("card_selector") or branch.get("child_link_selector")
        if branch.get("empty_state_selector") or branch.get("roster_container_selector"):
            from server.empty_roster import SELECTORS, empty_roster_proof_valid
            verified = any(
                all(args.get(key) == branch.get(key) for key in SELECTORS)
                and resolve(result.get("url")) == resolve(branch["url"])
                and type(result.get("cardCount")) is int and result["cardCount"] == 0
                and (result.get("pagination") or {}).get("total") == 0
                and empty_roster_proof_valid(result.get("empty_roster"), branch,
                                            url=result.get("url"))
                for args, result, _ in observations)
            if not verified:
                issues.append({"status": "empty_roster_not_verified", "url": branch["url"], "selector": selector})
            continue
        matching = [(args, result, index) for args, result, index in observations if (
            args.get("card_selector") == selector and resolve(result.get("url")) == resolve(branch["url"])
            and type(result.get("cardCount")) is int and result["cardCount"] > 0
            and (not branch.get("next_selector") or (
                args.get("next_selector") == branch["next_selector"] and result.get("nextCount") == 1)))]
        if not matching:
            issues.append({"status": "selector_not_verified", "url": branch["url"], "selector": selector})
        elif branch.get("single_page") and not any((result.get("pagination") or {}).get("total") == 0
                                                  for _, result, _ in matching):
            issues.append({"status": "single_page_pagination_unverified", "url": branch["url"], "selector": selector})
        elif branch.get("next_selector") and not any(result.get("nextDisabled") is True for _, result, _ in matching):
            next_urls = {resolve(result.get("nextUrl")) for _, result, _ in matching
                         if result.get("nextUrl") and resolve(result["nextUrl"]) != resolve(branch["url"])}
            changed_ajax = any(
                later_args.get("card_selector") == selector and resolve(later.get("url")) == resolve(branch["url"])
                and later.get("cardCount", 0) > 0 and first.get("cardDigest") and later.get("cardDigest")
                and first["cardDigest"] != later["cardDigest"]
                and any(click.get("selector") == branch["next_selector"] and first_index < clicked < later_index
                        for click, clicked in clicks)
                for _, first, first_index in matching for later_args, later, later_index in observations)
            if not changed_ajax and not any(args.get("card_selector") == selector and resolve(result.get("url")) in next_urls
                       and type(result.get("cardCount")) is int and result["cardCount"] > 0
                       for args, result, _ in observations):
                issues.append({"status": "pagination_not_verified", "url": branch["url"],
                               "selector": branch["next_selector"]})
        samples = list(dict.fromkeys(url for _, result, _ in matching
                       for url in result.get("linkedSamples", result.get("profileSamples", []))
                       if _is_structure_url(url) and resolve(url) != resolve(branch["url"])))[:2]
        if branch.get("kind") == "navigation":
            if not samples:
                issues.append({"status": "child_roster_not_verified", "url": branch["url"]})
                continue
            child = branch.get("child_branch")
            if not isinstance(child, dict):
                issues.append({"status": "child_roster_not_verified", "url": branch["url"]})
                continue
            for child_url in samples:
                child_plan = {**plan, "branches": [{**child, "url": child_url}]}
                issues.extend(selector_evidence_issues(child_plan, events))
            continue
        if samples and not branch.get("profile_content_selector"):
            issues.append({"status": "profile_content_selector_missing", "url": branch["url"]})
        if branch.get("profile_content_selector"):
            if not samples or any(not any(
                    args.get("profile_content_selector") == branch["profile_content_selector"]
                    and args.get("profile_content_start_selector") == branch.get("profile_content_start_selector")
                    and result.get("profile_content_selector") == branch["profile_content_selector"]
                    and result.get("profile_content_start_selector") == branch.get("profile_content_start_selector")
                    and resolve(result.get("url")) == resolve(url)
                    and result.get("confirmed") is True and result.get("container_visible") is True
                    and type(result.get("container_count")) is int and result["container_count"] == 1
                    and result.get("dom_stable") is True and result.get("network_idle") is True
                    and all(isinstance(result.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", result[key])
                            for key in ("html_sha256", "selected_html_sha256"))
                    for args, result, _ in profile_observations) for url in samples):
                issues.append({"status": "profile_content_not_verified", "url": branch["url"],
                               "selector": branch["profile_content_selector"]})
    return issues


def restore_verified_profile_selector(branch: dict[str, Any], plan: dict[str, Any],
                                      events: list[dict[str, Any]]) -> dict[str, Any]:
    """Recover a selector omitted from the final JSON only from unique browser proof.

    The model must have supplied the exact CSS to browser_verify_profile in this
    session. The normal evidence gate then checks every sampled profile again.
    """
    if branch.get("profile_content_selector"):
        return branch
    candidate_pairs: set[tuple[str, str | None]] = set()
    for event in events:
        data = event.get("data") or {}
        if (event.get("type") != "tool/call"
                or data.get("name") != "mcp__playwright__browser_verify_profile"):
            continue
        try:
            args = json.loads(data.get("arguments") or "{}")
        except (TypeError, ValueError):
            continue
        selector = args.get("profile_content_selector")
        start = args.get("profile_content_start_selector")
        if isinstance(selector, str) and selector and (start is None or isinstance(start, str)):
            candidate_pairs.add((selector, start))
    valid = []
    for selector, start in candidate_pairs:
        repaired = {**branch, "profile_content_selector": selector}
        if start is not None:
            repaired["profile_content_start_selector"] = start
        if not selector_evidence_issues({**plan, "branches": [repaired]}, events):
            valid.append(repaired)
    return valid[0] if len(valid) == 1 else branch


def observed_browser_urls(events: list[dict[str, Any]]) -> set[str]:
    calls = set()
    urls = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)
        elif isinstance(node, str) and _URL.match(node):
            urls.add(node)

    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "tool/call" and data.get("name") in (
                "mcp__playwright__browser_navigate", "mcp__playwright__browser_navigation"):
            calls.add(data.get("callId"))
        elif event.get("type") == "tool/result":
            for block in (data.get("message") or {}).get("content", []):
                if block.get("toolCallId") not in calls or block.get("isError"):
                    continue
                for content in block.get("content", []):
                    if content.get("type") == "text":
                        try:
                            collect(json.loads(content.get("text", "")))
                        except (TypeError, ValueError):
                            pass
    return urls


# ── 侦察提示词 ─────────────────────────────────────────────────────────────
PROMPT = Path(__file__).with_name("recon_instructions.md").read_text(encoding="utf-8")
DISCOVERY_PROMPT = Path(__file__).with_name("recon_discovery.md").read_text(encoding="utf-8")


@dataclass
class ReconResult:
    """侦察结果：计划本体 + 供审计使用的诊断信息。"""

    plan: dict[str, Any]
    elapsed_seconds: float
    finish_reason: str | None
    session_id: str
    event_count: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "finish_reason": self.finish_reason,
            "session_id": self.session_id,
            "event_count": self.event_count,
            "branches": len(self.plan.get("branches", [])),
            **self.diagnostics,
        }


def write_runtime_patch(path: Path, *, phase: str = "full", session_id: str = "standalone") -> None:
    """Resolve project resources before the MCP subprocess starts."""
    here = Path(__file__).resolve().parent
    entries = yaml.safe_load((here / "recon_runtime.cordis.yml").read_text(encoding="utf-8"))
    for entry in entries:
        if entry.get("id") == "spill-local":
            entry["config"]["root"] = str(here / "outputs" / "recon-evidence")
    import sys
    entries.append({"insert": [{"id": os.environ.get("DSH_MCP_ID", "faculty-recon-browser"),
        "name": "@deepseek-ai/dsh-mcp-client", "config": {
        "serverName": "playwright", "transport": "stdio", "command": sys.executable,
        "args": ["-m", "server.recon_browser_mcp", "--phase", phase, "--session", session_id], "cwd": str(here.parent),
        "env": {"PYTHONIOENCODING": "utf-8", "FACULTY_RECON_PHASE": phase,
                "FACULTY_RECON_SESSION": session_id}, "failOnStartupError": True}}]})
    path.write_text(yaml.safe_dump(entries, allow_unicode=True), encoding="utf-8")


def _run_harness_blocking(prompt: str, session_id: str, cfg: dict[str, Any]) -> Any:
    """在工作线程中执行阻塞的 dsh 调用。"""
    with tempfile.TemporaryDirectory(prefix="faculty-recon-") as folder:
        patches = list(cfg.get("patches", ()))
        runtime = str(Path(__file__).with_name("recon_runtime.cordis.yml"))
        if runtime in patches:
            resolved = Path(folder) / "runtime.yml"
            write_runtime_patch(resolved)
            cfg = {**cfg, "runtime_patch": str(resolved)}
            patches[patches.index(runtime)] = str(resolved)
        if cfg.get("target"):
            return _run_staged_harness(session_id, cfg, tuple(patches))
        return _execute_harness(prompt, session_id, cfg, tuple(patches))


def _run_staged_harness(session_id: str, cfg: dict[str, Any], patches: tuple[str, ...]) -> Any:
    target = cfg["target"]
    events = []
    phases = []
    deadline = time.monotonic() + cfg["timeout_seconds"]
    feedback = cfg.get("feedback") or {}
    required_urls = [url for url in dict.fromkeys([
        *feedback.get("known_urls", []),
        *(branch["url"] for branch in (feedback.get("previous_plan") or {}).get("branches", [])
          if isinstance(branch, dict) and isinstance(branch.get("url"), str))]) if _is_structure_url(url)]
    urls = []
    last_events = []
    discovery_checkpoint = None
    verified_branches = dict(cfg.get("verified_branches") or {})

    def run(phase: str, url: str, suffix: str) -> dict[str, Any]:
        nonlocal last_events
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReconError("阶段侦察超过总时限")
        phase_cfg = {**cfg, "phase": phase, "timeout_seconds": remaining,
                     "phase_call_limit": (cfg.get("phase_call_limits") or {}).get(phase, 0)}
        template = DISCOVERY_PROMPT if phase == "discovery" else PROMPT
        prompt = template.format(school=target["school"], college=target["college"], url=url)
        if feedback:
            if phase == "discovery":
                scoped = {"known_urls": required_urls}
            else:
                previous = next((branch for branch in
                    (feedback.get("previous_plan") or {}).get("branches", [])
                    if isinstance(branch, dict) and branch.get("url") == url), None)
                issues = [item for item in feedback.get("issues", [])
                          if isinstance(item, dict) and url in
                          (item.get("url"), item.get("directory_url"), item.get("branch_url"))]
                scoped = {"issues": issues}
                if previous:
                    scoped["previous_branch"] = previous
            if scoped.get("issues") or scoped.get("known_urls") or scoped.get("previous_branch"):
                prompt += "\n本阶段结构反馈：" + json.dumps(scoped, ensure_ascii=False, separators=(",", ":"))
        sid = f"{session_id}-{suffix}"
        if cfg.get("runtime_patch"):
            write_runtime_patch(Path(cfg["runtime_patch"]), phase=phase, session_id=sid)
        try:
            result = _execute_harness(prompt, sid, phase_cfg, patches)
        except (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled) as exc:
            exc.phase = phase
            exc.phase_session_id = sid
            raise
        current = getattr(result, "events", []) or []
        last_events = current
        events.extend(current)
        phases.append({"phase": phase, "session_id": sid,
                       "evidence_root": str(Path(__file__).parent / "outputs/recon-evidence" / sid),
                       **run_metrics(current)})
        if getattr(result, "finish_reason", None) != "completed":
            raise ReconError("阶段侦察未正常完成")
        parsed = extract_plan_json(getattr(result, "final_response", "") or "")
        if parsed is None:
            raise ReconPlanError("阶段未返回 JSON")
        return parsed

    try:
        resumed_discovery = cfg.get("resume_discovery")
        if resumed_discovery:
            discovery = resumed_discovery["plan"]
            discovery_events = resumed_discovery["events"]
            discovery_session_id = resumed_discovery["session_id"]
            phases.append({"phase": "discovery", "reused": True,
                           "session_id": discovery_session_id, "model_calls": 0,
                           "evidence_root": str(Path(__file__).parent / "outputs/recon-evidence"
                                                / discovery_session_id)})
        else:
            discovery = run("discovery", target["url"], "discover")
            discovery_events = list(last_events)
            discovery_session_id = f"{session_id}-discover"
        if (set(discovery) != {"school", "college", "source_kind", "branches"}
                or discovery["school"] != target["school"] or discovery["college"] != target["college"]
                or discovery["source_kind"] != "university_faculty_roster"
                or not isinstance(discovery["branches"], list) or not discovery["branches"]):
            raise ReconPlanError("入口发现计划不符合契约")
        check_plan_hygiene(discovery, school=target["school"], college=target["college"])
        observed = observed_browser_urls(discovery_events)
        for branch in discovery["branches"]:
            if not isinstance(branch, dict) or set(branch) != {"url"} or not _is_structure_url(branch["url"]):
                raise ReconPlanError("入口发现只能输出 URL")
            if branch["url"] not in observed:
                raise ReconPlanError("名录 URL 缺少本轮真实浏览器链接证据")
            if branch["url"] not in urls:
                urls.append(branch["url"])
        aliases = browser_url_aliases(discovery_events)
        resolved = {resolved_browser_url(url, aliases) for url in urls}
        omitted = [url for url in required_urls if resolved_browser_url(url, aliases) not in resolved]
        if omitted:
            error = ReconPlanError("补缺发现遗漏已核实名录分支")
            error.safe_feedback = {"known_urls": list(dict.fromkeys([*required_urls, *urls])),
                                   "issues": [{"status": "previous_branch_omitted", "url": url} for url in omitted]}
            raise error
        discovery_checkpoint = {"plan": discovery, "events": discovery_events,
                                "session_id": discovery_session_id}
        plan = {**discovery, "branches": []}
        for index, url in enumerate(urls):
            reuse = verified_branches.get(url)
            if reuse:
                branch = reuse["branch"]
                proof_events = reuse.get("events") or []
                one = {**plan, "branches": [branch]}
                validate_model_plan(one, school=target["school"], college=target["college"])
                if branch.get("url") != url or selector_evidence_issues(one, proof_events):
                    raise ReconPlanError("复用分支缺少本轮真实浏览器证据")
                events.extend(proof_events)
                phases.append({"phase": "branch", "reused": True, "model_calls": 0,
                               "evidence_root": reuse.get("evidence_root"), **run_metrics(proof_events)})
            else:
                branch = run("branch", url, f"branch-{index + 1}")
                proof_events = last_events
                branch = restore_verified_profile_selector(branch, plan, proof_events)
            one = {**plan, "branches": [branch]}
            validate_model_plan(one, school=target["school"], college=target["college"])
            aliases = browser_url_aliases(proof_events)
            if resolved_browser_url(branch.get("url"), aliases) != resolved_browser_url(url, aliases):
                raise ReconPlanError("分支阶段改变了目标入口")
            issues = selector_evidence_issues(one, proof_events)
            if issues:
                error = ReconPlanError("分支缺少选择器或翻页证据")
                error.safe_feedback = {"previous_plan": {**plan, "branches": [*plan["branches"], branch]},
                                       "known_urls": urls, "issues": issues}
                raise error
            plan["branches"].append(branch)
            if not reuse:
                verified_branches[url] = {"branch": branch, "events": proof_events,
                                          "evidence_root": str(Path(__file__).parent /
                                                               "outputs/recon-evidence" /
                                                               f"{session_id}-branch-{index + 1}")}
        return SimpleNamespace(final_response=json.dumps(plan, ensure_ascii=False), finish_reason="completed",
                                events=events, phase_metrics=phases,
                                discovery_session_id=discovery_session_id)
    except ReconError as exc:
        if isinstance(exc, (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled)):
            partial = exc.diagnostics
            completed = run_metrics(events)
            usage = {key: completed["token_usage"][key] + partial["token_usage"][key]
                     for key in completed["token_usage"]}
            phases.append({"phase": exc.phase, "session_id": exc.phase_session_id,
                           **partial})
            exc.diagnostics = {"session_id": session_id,
                               "model_calls": completed["model_calls"] + partial["model_calls"],
                               "token_usage": usage, "phases": phases,
                               "budget_total": partial["budget_total"]}
            raise
        if isinstance(exc, (ReconPlanError, PlanHygieneError)):
            safe = getattr(exc, "safe_feedback", {})
            safe["known_urls"] = [url for url in dict.fromkeys(
                [*required_urls, *urls, *safe.get("known_urls", [])]) if _is_structure_url(url)]
            safe.setdefault("issues", [{"status": "plan_contract_rejected"}])
            exc.safe_feedback = safe
            if discovery_checkpoint is not None:
                exc.discovery_checkpoint = discovery_checkpoint
                exc.verified_branches = verified_branches
        exc.diagnostics = {"session_id": session_id, **run_metrics(events), "phases": phases}
        raise


def check_runtime_composition(cfg: dict[str, Any], patches: tuple[str, ...]) -> None:
    """Check the actual composed plugin tree before spending any model tokens."""
    if cfg.get("dsh_bin"):
        launch = (cfg["dsh_bin"],)
    else:
        from deepseek_harness_runtime import resolve_bundled_launch_args
        launch = resolve_bundled_launch_args()
    args = [*launch, "--profile", cfg["profile"]]
    for patch in patches:
        args.extend(["--patch", patch])
    args.append("--dump-config")
    try:
        result = subprocess.run(args, env={**os.environ, "DSH_HOME": cfg["dsh_home"]},
            cwd=cfg.get("workspace"), capture_output=True, text=True, encoding="utf-8",
            timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        # Preserve unevaluated JS expressions as strings; never execute config code.
        class Loader(yaml.SafeLoader):
            pass
        Loader.add_constructor("tag:yaml.org,2002:js", lambda loader, node: loader.construct_scalar(node))
        tree = yaml.load(result.stdout, Loader=Loader)
    except (OSError, subprocess.TimeoutExpired, yaml.YAMLError) as exc:
        raise ReconError("Harness browser configuration preflight failed") from exc
    if result.returncode or "patch: entry" in result.stderr or not isinstance(tree, list):
        raise ReconError("Harness browser configuration overlays were not applied")
    active = [entry for entry in tree if isinstance(entry, dict) and entry.get("disabled") is not True]
    browsers = [entry for entry in active if entry.get("name") == "@deepseek-ai/dsh-mcp-client"]
    if len(browsers) != 1 or browsers[0].get("config", {}).get("serverName") != "playwright":
        raise ReconError("Harness browser plugin is missing or duplicated")
    if "server.recon_browser_mcp" not in browsers[0].get("config", {}).get("args", []):
        raise ReconError("Harness browser plugin does not use the project scout")
    extra_tools = [entry for entry in active
        if str(entry.get("name", "")).startswith("@deepseek-ai/dsh-tool-")
        and entry.get("name") != "@deepseek-ai/dsh-tool-call-timeout-policy"]
    if extra_tools:
        raise ReconError("Harness unexpected general tool plugins are active")
    if any(entry.get("id") in {"agent-instructions", "skill-filesystem", "tool-skill"} for entry in active):
        raise ReconError("Harness general instruction or skill injection is active")


_PHASE_NAMES = {"discovery": "入口发现", "branch": "分支验证", "full": "侦察"}


def _execute_harness(prompt: str, session_id: str, cfg: dict[str, Any], patches: tuple[str, ...]) -> Any:
    from deepseek_harness import DeepSeekHarness

    try:
        check_runtime_composition(cfg, patches)
    except ReconError as exc:
        exc.usage_unknown = False
        exc.diagnostics = {"session_id": session_id, **run_metrics([])}
        raise
    budget: ReconBudget | None = cfg.get("budget")
    before = budget.snapshot() if budget else None
    phase_limit = int(cfg.get("phase_call_limit") or 0)
    phase_usage = ReconBudget()  # this session only; the budget above spans the college
    cancel_event = cfg.get("cancel_event")
    with DeepSeekHarness(
        dsh_home=cfg["dsh_home"],
        cwd=cfg["workspace"],
        dsh_bin=cfg["dsh_bin"],
        profile=cfg["profile"],
        provider=cfg["provider"],
        model=cfg["model"],
        reasoning_effort=cfg["effort"],
        api_key=cfg["api_key"],
        patches=patches,
        env={"FACULTY_RECON_PHASE": cfg.get("phase", "full"), "FACULTY_RECON_SESSION": session_id},
        request_timeout_seconds=cfg.get("timeout_seconds"),
        initialize_timeout_seconds=90.0,
    ) as harness:
        try:
            run_kwargs: dict[str, Any] = {"session_id": session_id}
            if budget is not None or phase_limit or cancel_event is not None:
                def on_notification(notification: Any) -> None:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ReconCancelled("侦察已被调用方停止（超时）")
                    if (notification.method == "session.event"
                            and notification.payload.get("sessionId") == session_id):
                        event = notification.payload.get("event")
                        if isinstance(event, dict):
                            phase_usage.observe(event)
                            if budget is not None:
                                budget.observe(event)
                            if phase_limit and phase_usage.calls > phase_limit:
                                raise ReconPhaseLimitExceeded(
                                    f"{_PHASE_NAMES.get(cfg.get('phase'), '侦察')}阶段已用 {phase_usage.calls} 次模型请求仍未完成"
                                    f"（上限 {phase_limit}），页面结构可能超出当前计划格式，已停止以免继续消耗 Token")
                run_kwargs["on_notification"] = on_notification
            return harness.run(prompt, **run_kwargs)
        except (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled) as exc:
            exc.diagnostics = budget.delta(before) if budget is not None else {
                "model_calls": phase_usage.calls, "token_usage": phase_usage.usage.copy(),
                "budget_total": phase_usage.snapshot()}
            exc.usage_unknown = False
            raise
        except TimeoutError as exc:
            raise ReconError("Harness 初始化或请求超时；未完成有效侦察") from exc


def _config_from_env() -> dict[str, Any]:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ReconError("DEEPSEEK_API_KEY 未设置")

    here = Path(__file__).resolve().parent
    dsh_bin = os.environ.get("DSH_BIN") or None
    if dsh_bin:
        if not (Path(dsh_bin).is_file() or shutil.which(dsh_bin)):
            raise ReconError("DSH_BIN 不是有效的 dsh 可执行程序")
        dsh_bin = str(Path(shutil.which(dsh_bin) or dsh_bin).resolve())
    else:
        try:
            from deepseek_harness_runtime import bundled_runtime_path
            bundled_runtime_path()
        except (ImportError, FileNotFoundError) as exc:
            raise ReconError("当前 Python 环境缺少 Harness 运行时；请安装锁定依赖或设置 DSH_BIN") from exc
    cwd = os.environ.get("DSH_CWD") or str(here.parent)
    return {
        "dsh_home": os.environ.get("DSH_HOME") or str(here.parent / ".runtime" / "dsh-home"),
        "dsh_bin": dsh_bin,
        "workspace": cwd,
        "profile": os.environ.get("DSH_PROFILE", "sdk"),
        "provider": os.environ.get("DSH_PROVIDER", "deepseek-official"),
        "model": os.environ.get("DSH_MODEL", "deepseek-flash"),
        "effort": os.environ.get("DSH_EFFORT", "high"),
        "api_key": api_key,
        "patches": (str(here / "recon_runtime.cordis.yml"),),
    }


async def reconnoiter(
    school: str,
    college: str,
    url: str,
    *,
    timeout_seconds: float = 900.0,
    session_id: str | None = None,
    config: dict[str, Any] | None = None,
    feedback: dict[str, Any] | None = None,
    verified_branches: dict[str, Any] | None = None,
    resume_discovery: dict[str, Any] | None = None,
    budget: ReconBudget | None = None,
    phase_call_limits: dict[str, int] | None = None,
) -> ReconResult:
    """让 Agent 侦察目标学院，返回**已通过双重门禁**的采集计划。

    本函数是 async 且内部用每次调用独立的执行器卸走阻塞调用——调用方
    直接 ``await`` 即可，租约心跳不会被冻结。

    Raises:
        ReconError: 调用失败、超时、未产出 JSON、或 schema 校验不通过。
        PlanHygieneError: 计划中检出数据字段（红线 1）。
    """
    from server.browser_batch_tool import _validate_plan

    if not url.startswith(("http://", "https://")):
        raise ReconError(f"入口 URL 必须是 http(s)：{url!r}")

    cfg = dict(config or _config_from_env())
    cfg["timeout_seconds"] = timeout_seconds
    cfg["target"] = {"school": school, "college": college, "url": url}
    cfg["feedback"] = feedback
    cfg["verified_branches"] = verified_branches
    cfg["resume_discovery"] = resume_discovery
    cfg["budget"] = budget
    cfg["phase_call_limits"] = phase_call_limits or {}
    cancel_event = threading.Event()
    cfg["cancel_event"] = cancel_event
    budget_before = budget.snapshot() if budget else None
    Path(cfg["workspace"]).mkdir(parents=True, exist_ok=True)

    # session_id 会进入 dsh 的会话存储路径，**必须 ASCII 安全**。
    # 实测：用中文学校/学院名拼出来的 session_id 会让整轮以 finish_reason='error' 结束，
    # 且会话根本不被创建（storages 下无痕迹）。故用短哈希代替可读名。
    if session_id is None:
        digest = hashlib.sha256(f"{school}|{college}|{time.time()}".encode()).hexdigest()[:12]
        session_id = f"recon-{digest}"
    sid = session_id
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", sid):
        raise ReconError("会话 ID 必须是 1～160 位 ASCII 字母、数字、下划线或连字符")
    prompt = PROMPT.format(school=school, college=college, url=url)
    if feedback:
        prompt += ("\n上轮结构审计未通过。请重新用浏览器核实下列结构问题，返回完整替换计划；"
                   "不要复制失败选择器，也不要重复采集或交付。\n"
                   + json.dumps(feedback, ensure_ascii=False, separators=(",", ":")))

    started = time.monotonic()
    # A separate blocking SDK thread per college avoids the shared executor's
    # implicit concurrency cap. The SDK also bounds its subprocess request.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=sid)
    try:
        result = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(executor, _run_harness_blocking, prompt, sid, cfg),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        # The SDK call runs in a thread that asyncio cannot cancel; the flag makes
        # the next Harness event end the session instead of spending until its own deadline.
        cancel_event.set()
        error = ReconError(f"侦察超时（{timeout_seconds:.0f}s）")
        if budget is not None:
            error.diagnostics = {"session_id": sid, **budget.delta(budget_before)}
            error.usage_unknown = False
        raise error from exc
    except asyncio.CancelledError:
        cancel_event.set()  # the whole task timed out or the worker is stopping
        raise
    except (ReconBudgetExceeded, ReconPhaseLimitExceeded) as exc:
        if budget is not None:
            exc.diagnostics = {**getattr(exc, "diagnostics", {}), **budget.delta(budget_before)}
        exc.usage_unknown = False
        raise
    except ReconError:
        raise
    except Exception as exc:  # dsh / SDK 层异常
        raise ReconError(f"dsh 调用失败：{type(exc).__name__}: {exc}") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    elapsed = time.monotonic() - started
    events = getattr(result, "events", []) or []
    diagnostics = {"session_id": sid, "elapsed_seconds": round(elapsed, 1), **run_metrics(events)}
    finish = getattr(result, "finish_reason", None)
    if finish != "completed":
        raise ReconError(f"侦察未正常结束：finish_reason={finish!r}")

    plan = extract_plan_json(getattr(result, "final_response", "") or "")
    if plan is None:
        error = ReconPlanError("未能从回复中解析出结构计划 JSON")
        error.diagnostics = diagnostics
        raise error

    # 门禁一：schema（与采集内核使用同一个校验器）
    try:
        _validate_plan(plan)
    except Exception as exc:
        error = ReconPlanError("计划未通过结构校验")
        error.diagnostics = diagnostics
        raise error from exc

    # 门禁二：红线 1（模型产出物不得含数据字段）
    try:
        validate_model_plan(plan, school=school, college=college)
    except PlanHygieneError as exc:
        exc.diagnostics = diagnostics
        raise
    if str(Path(__file__).with_name("recon_runtime.cordis.yml")) in cfg.get("patches", ()):
        issues = selector_evidence_issues(plan, events)
        if issues:
            error = ReconPlanError("选择器缺少本次真实浏览器验证证据")
            error.diagnostics = diagnostics
            error.safe_feedback = {"previous_plan": plan, "issues": issues}
            raise error

    from server.recon_plan_cache import discovery_evidence
    return ReconResult(
        plan=plan,
        elapsed_seconds=elapsed,
        finish_reason=finish,
        session_id=sid,
        event_count=len(getattr(result, "events", []) or []),
        diagnostics={**run_metrics(events), "phases": getattr(result, "phase_metrics", []),
                     "discovery_pages": discovery_evidence(
                         getattr(result, "discovery_session_id", sid).removesuffix("-discover"))},
    )
