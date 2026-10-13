"""侦察段：让 dsh Agent 核实学院官网结构并产出采集计划 JSON。

输入大学、学院、入口与可选师资 URL；输出符合当前结构 Schema 的计划。
模型可以观察页面内容，输出须通过结构验证和计划卫生检查；人员字段由
采集链路读取保存的原件。详细契约见本模块验证器及 server/recon_*.md。

`harness.run()` 是同步阻塞调用，`reconnoiter()` 将它卸到工作线程，
避免冻结租约心跳。结构验证、模型数据卫生和浏览器证据承担不同检查。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
import yaml

__all__ = ["ReconError", "ReconPlanError", "PlanHygieneError", "ReconPhaseLimitExceeded",
           "ReconGateStopped", "ReconResult", "reconnoiter"]


class ReconError(RuntimeError):
    """侦察未能产出可用计划（调用失败、超时、无 JSON、schema 不合法）。"""


class ReconBudgetExceeded(ReconError):
    """A completed model request crossed the college's reconnaissance budget."""


class ReconPhaseLimitExceeded(ReconError):
    """An explicitly configured phase ceiling was reached.

    This does not prove an unsupported page: a valid complex website can need
    more steps. Defaults leave these ceilings disabled.
    """


class ReconCancelled(ReconError):
    """The caller stopped waiting (task timeout); end the Harness session now."""


class _ReconSubmissionComplete(Exception):
    """Unwind this phase's SDK run after a digest-bound scout submission."""

    def __init__(self, plan: dict[str, Any]):
        self.plan = plan


class ReconBudget:
    """Count actual SDK usage across every phase and repair of one college.

    ``max_calls``/``max_tokens`` stop the session (both zero: never stop).
    ``alert_calls``/``alert_tokens`` only report, once, through ``on_alert``:
    a complex college keeps running while an operator sees the anomaly.
    """

    def __init__(self, *, max_calls: int = 0, max_tokens: int = 0,
                 alert_calls: int = 0, alert_tokens: int = 0,
                 alert_cost_usd: float = 0, provider: str = "deepseek-official",
                 model: str = "deepseek-flash", price_override: dict | None = None,
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
        if not math.isfinite(alert_cost_usd) or alert_cost_usd < 0:
            raise ValueError("cost alert must be finite and non-negative")
        self.alert_cost_usd = alert_cost_usd
        self.provider, self.model, self.price_override = provider, model, price_override
        self.known_cost_usd = 0.0
        self.unknown_cost_calls = 0
        self._lock = threading.RLock()
        self.alerts: list[dict[str, Any]] = []
        self.calls = 0
        self.usage = {"uncached_input": 0, "cached_input": 0, "output": 0}

    def snapshot(self) -> dict[str, Any]:
        from server.recon_pricing import PRICE_DATE, PRICE_SOURCE
        with self._lock:
            usage = self.usage.copy()
            return {"calls": self.calls, "token_usage": usage,
                "total_tokens": sum(usage.values()),
                "max_calls": self.max_calls, "max_tokens": self.max_tokens,
                "cost": {"estimated_usd": round(self.known_cost_usd, 9) if not self.unknown_cost_calls else None,
                         "known_usd": round(self.known_cost_usd, 9), "unknown_calls": self.unknown_cost_calls,
                         "provider": self.provider, "model": self.model, "price_date": PRICE_DATE,
                         "price_source": PRICE_SOURCE, "price_override": self.price_override,
                         "time_basis": "usage_notification_received_at", "invoice": False}}

    def delta(self, before: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            prior = before.get("cost") or {}
            unknown = self.unknown_cost_calls - prior.get("unknown_calls", 0)
            known = self.known_cost_usd - prior.get("known_usd", 0)
            return {"model_calls": self.calls - before["calls"],
                "token_usage": {key: value - before["token_usage"][key]
                                for key, value in self.usage.items()},
                "cost": {**self.snapshot()["cost"], "known_usd": round(known, 9),
                         "estimated_usd": round(known, 9) if not unknown else None, "unknown_calls": unknown},
                "budget_total": self.snapshot()}

    def seed(self, attempts: list[dict[str, Any]]) -> None:
        with self._lock:
            for attempt in attempts:
                calls = int(attempt.get("model_calls") or 0)
                self.calls += calls
                source = attempt.get("token_usage") or {}
                for key in self.usage:
                    self.usage[key] += int(source.get(key) or 0)
                cost = attempt.get("cost")
                if cost:
                    self.known_cost_usd += cost.get("known_usd", 0)
                    self.unknown_cost_calls += cost.get("unknown_calls", 0)
                else:
                    self.unknown_cost_calls += calls

    def mark_usage_unknown(self) -> None:
        """An interrupted request may be charged without a completed usage event."""
        with self._lock:
            self.unknown_cost_calls += 1

    def observe(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._observe_locked(event)

    def _observe_locked(self, event: dict[str, Any]) -> None:
        if event.get("type") != "assistant/message":
            return
        values = (event.get("data") or {}).get("usage") or {}
        if not values:
            return
        self.calls += 1
        increment = {}
        for target, source in (("uncached_input", "inputTokens"),
                               ("cached_input", "cacheReadTokens"),
                               ("output", "outputTokens")):
            increment[target] = int(values.get(source) or 0)
            self.usage[target] += increment[target]
        from server.recon_pricing import estimate
        cost = estimate(increment, self.provider, self.model, time.time(), self.price_override)
        if cost is None:
            self.unknown_cost_calls += 1
        else:
            self.known_cost_usd += cost
        total = sum(self.usage.values())
        if not self.alerts and ((self.alert_calls and self.calls >= self.alert_calls)
                                or (self.alert_tokens and total >= self.alert_tokens)
                                or (self.alert_cost_usd and self.known_cost_usd >= self.alert_cost_usd)):
            alert = {"calls": self.calls, "total_tokens": total, "token_usage": self.usage.copy(),
                     "alert_calls": self.alert_calls, "alert_tokens": self.alert_tokens,
                     "alert_cost_usd": self.alert_cost_usd, "cost": self.snapshot()["cost"],
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


class ReconGateStopped(ReconPlanError):
    """The submission gate ended a session that resubmitting could not pass:
    every failure was a page fact, or the scout kept resubmitting unchanged."""

    def __init__(self, code: str, issues: list[dict[str, Any]]):
        super().__init__(f"提交门禁停止侦察：{code}")
        self.code = code
        by_url: dict[Any, list[dict[str, Any]]] = {}
        for item in issues:
            by_url.setdefault(item.get("url"), []).append(item)
        self.safe_feedback = {"issues": [{"status": code, "url": url, "gate_issues": items}
                                         for url, items in by_url.items()]}


def _watch_gate(event: dict[str, Any], submit_calls: set, rejections: dict | None,
                session_id: str) -> ReconGateStopped | None:
    """Follow submit_plan replies: keep the latest rejection, and stop when the gate says so."""
    from server.recon_contract import decode_submission_reply
    data = event.get("data") or {}
    if event.get('type') == 'tool/call':
        call_id = data.get('callId')
        if data.get('name') == 'mcp__playwright__submit_plan' and isinstance(call_id, str) and call_id:
            submit_calls.add(call_id)
        else:
            submit_calls.discard(call_id)
        return None
    if event.get("type") != "tool/result":
        return None
    for block in (data.get("message") or {}).get("content", []):
        if block.get("toolCallId") not in submit_calls:
            continue
        for content in block.get("content", []):
            reply = (decode_submission_reply(content.get('text', ''), is_error=block.get('isError') is True)
                     if content.get('type') == 'text' else None)
            if not isinstance(reply, dict):
                continue
            if reply.get("accepted") is True:
                if rejections is not None:
                    rejections.pop(session_id, None)
            elif isinstance(reply.get("issues"), list):
                issues = [item for item in reply["issues"] if isinstance(item, dict)]
                if rejections is not None:
                    rejections[session_id] = issues
                if reply.get("stop") in {"recon_page_unverifiable", "recon_gate_stalled"}:
                    return ReconGateStopped(reply["stop"], issues)
    return None


def last_gate_rejections(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """The latest rejection of each session still unaccepted, for a failure that has no plan."""
    rejections = cfg.get("gate_rejections") or {}
    contexts = cfg.get('gate_rejection_contexts') or {}
    return [{"session_id": sid, **contexts.get(sid, {}), "issues": issues}
            for sid, issues in list(rejections.items())]


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
_SCHEMA_LITERALS = {"university_faculty_roster", "hospital_doctor_roster", "navigation", "segmented", "image_table",
                    "faculty_roster", "organizational_membership", "unverified_people_source"}
_MODEL_PLAN_KEYS = {"school", "college", "source_kind", "branches", "source_dispositions",
                    "profile_concurrency", "profile_ready_selector"}
_MODEL_BRANCH_KEYS = {"url", "kind", "source_kind", "card_selector", "single_page",
                      "next_selector", "max_pages", "initial_click_selector",
                      "child_link_selector", "child_branch", "child_branches", "max_children", "page_types",
                      "empty_state_selector", "roster_container_selector",
                      "profile_content_selector", "profile_content_start_selector",
                      "profile_ready_selector", "container_selector", "record_start_selector"}
from server.source_qualification import (SOURCE_FIELDS, source_claim_valid, source_event_issues,  # noqa: E402
                                         source_event_proof)
_MODEL_BRANCH_KEYS.update(SOURCE_FIELDS)
_MODEL_CHILD_KEYS = _MODEL_BRANCH_KEYS - {"url", "kind", "child_link_selector", "child_branch", "child_branches", "max_children",
                                          "container_selector", "record_start_selector"}


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


def _explicit_navigation_pagination_supported(branch: dict[str, Any]) -> bool:
    """An explicit child set is bound only to one current rendered parent."""
    if branch.get('kind') != 'navigation' or 'child_branches' not in branch:
        return True
    from server.page_types import binding_for
    pagination = binding_for(branch, 'pagination')
    return (not branch.get('next_selector') and pagination.type_id == 'pagination.single_page'
            and pagination.implementation_version == 1)


def check_plan_hygiene(plan: dict[str, Any], *, school: str, college: str) -> None:
    """红线 1 门禁：计划只允许含 URL、CSS 选择器与 schema 字面量。

    判据是机械的——**CSS 选择器不可能包含 CJK 字符**。
    因此任何含 CJK 的字符串，除非它整串等于目标学校/学院名，都是数据泄漏。
    """
    plan = _normalize_model_plan(plan)
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


def _normalize_model_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Resolve trusted bindings before checking projected structure and hygiene."""
    from server.page_types import normalize_branch
    try:
        return {**plan, 'branches': [normalize_branch(branch) for branch in plan.get('branches', [])]}
    except (ValueError, TypeError) as exc:
        # Pydantic errors can contain the rejected value; public scout feedback
        # must not copy a teacher field from the binding back to the model.
        raise PlanHygieneError('invalid or conflicting page type binding') from exc


def _required_source_urls(cfg: dict[str, Any]) -> list[str]:
    """Keep supplied leads and previous sources as explicit discovery obligations."""
    feedback = cfg.get('feedback') or {}
    previous = feedback.get('previous_plan') or {}
    return [url for url in dict.fromkeys([
        *(cfg.get('provided_urls') or []),
        *feedback.get('known_urls', []),
        *(branch.get('url') for branch in [*previous.get('branches', []),
                                         *previous.get('source_dispositions', [])]
          if isinstance(branch, dict))]) if _is_structure_url(url)]


def _required_source_issues(plan: dict[str, Any], required_urls: list[str],
                            events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A prompt or successful sibling cannot settle an omitted supplied source."""
    aliases = browser_url_aliases(events)
    def branch_urls(branch):
        yield branch['url']
        for child in branch.get('child_branches', []):
            yield from branch_urls(child)
    represented = {resolved_browser_url(url, aliases) for branch in plan.get('branches', [])
                   for url in branch_urls(branch)}
    represented.update(resolved_browser_url(item['url'], aliases)
                       for item in plan.get('source_dispositions', []))
    return [{'path': '$.branches', 'url': url, 'status': 'previous_branch_omitted'}
            for url in required_urls if resolved_browser_url(url, aliases) not in represented]


def validate_model_plan(plan: dict[str, Any], *, school: str, college: str) -> None:
    """Accept only the structure-only plan that the server recon agent may emit.

    Teacher/appointment data belongs to captured evidence and review decisions,
    never to model or manually filled structure plans. The shared batch path
    enforces the same boundary.
    """
    if plan.get("school") != school or plan.get("college") != college:
        raise PlanHygieneError("model plan school/college differs from queued target")
    plan = _normalize_model_plan(plan)

    from server.page_types import validate_production_bindings
    try:
        for branch in plan.get('branches', []):
            validate_production_bindings(branch)
    except ValueError as exc:
        raise PlanHygieneError('page type evidence contract is not supported for production selection') from exc

    def keys(node: dict[str, Any], allowed: set[str], path: str) -> None:
        extra = set(node) - allowed
        if extra:
            raise PlanHygieneError(f"{path} contains forbidden fields: {sorted(extra)}")

    def selector(node: dict[str, Any], field: str, path: str) -> None:
        if field in node and (not isinstance(node[field], str) or not node[field].strip()):
            raise PlanHygieneError(f"{path}.{field} must be a non-empty CSS selector")

    def branch(node: dict[str, Any], path: str, *, child: bool = False) -> None:
        keys(node, _MODEL_CHILD_KEYS if child else _MODEL_BRANCH_KEYS, path)
        if not source_claim_valid(node):
            raise PlanHygieneError(f'{path} requires a controlled source role and complete witness selectors')
        if not child:
            raw_url = node.get("url")
            if not isinstance(raw_url, str):
                raise PlanHygieneError(f"{path}.url must be a URL")
            if not _is_structure_url(raw_url):
                raise PlanHygieneError(f"{path}.url is not a permitted roster URL")
        for name in ("card_selector", "next_selector", "initial_click_selector", "child_link_selector",
                      "empty_state_selector", "roster_container_selector",
                      "profile_content_selector", "profile_content_start_selector",
                      "profile_ready_selector", "container_selector", "record_start_selector"):
            selector(node, name, path)
        for name in SOURCE_FIELDS - {'source_role'}:
            selector(node, name, path)
        if node.get("kind") == "segmented":
            from server.segmented_roster import branch_valid
            if child or not branch_valid(node) or node.get("single_page") is not True:
                raise PlanHygieneError(f"{path} segmented branch requires container_selector, "
                                       "record_start_selector and single_page true only")
            return
        if node.get("kind") == "image_table":
            if (child or node.get("single_page") is not True or not node.get("card_selector")
                    or set(node) - ({"url", "kind", "source_kind", "card_selector", "single_page", "page_types"} | SOURCE_FIELDS)):
                raise PlanHygieneError("image_table allows only a single-page image selector")
            return
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
        for name in ("max_pages", "max_children"):
            if name in node and (type(node[name]) is not int or node[name] < 1):
                raise PlanHygieneError(f"{path}.{name} must be a positive integer")
        if node.get('kind') == 'navigation':
            if not node.get('child_link_selector'):
                raise PlanHygieneError(f'{path}.child_link_selector is required')
            if ('child_branch' in node) == ('child_branches' in node):
                raise PlanHygieneError(f'{path} requires exactly one child specification')
            if not _explicit_navigation_pagination_supported(node):
                raise PlanHygieneError(f'{path} explicit navigation pagination is not applicable; '
                                       'only pagination.single_page@1 without next_selector is supported')
        elif 'child_branches' in node or 'child_branch' in node:
            raise PlanHygieneError(f'{path} child specification requires navigation')
        if "child_branch" in node:
            if not isinstance(node["child_branch"], dict):
                raise PlanHygieneError(f"{path}.child_branch must be an object")
            branch(node["child_branch"], f"{path}.child_branch", child=True)
        if 'child_branches' in node:
            children = node['child_branches']
            if not isinstance(children, list) or not children:
                raise PlanHygieneError(f'{path}.child_branches must be a non-empty array')
            child_urls = [item.get('url') for item in children if isinstance(item, dict)]
            if (len(child_urls) != len(children) or not all(_is_structure_url(url) for url in child_urls)
                    or len(set(child_urls)) != len(child_urls)):
                raise PlanHygieneError(f'{path}.child_branches requires unique complete URLs')
            for index, item in enumerate(children):
                branch(item, f'{path}.child_branches[{index}]')

    keys(plan, _MODEL_PLAN_KEYS, "$")
    from server.recon_contract import validate_source_dispositions
    try:
        validate_source_dispositions(plan)
    except ValueError as exc:
        raise PlanHygieneError('invalid source dispositions') from exc
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
    for item in report.get("unresolved", []):
        status = item.get("status", "")
        if not isinstance(status, str) or not re.fullmatch(r"[a-z_]{1,80}", status):
            continue
        issue: dict[str, Any] = {"status": status}
        for key in ("url", "directory_url", "branch_url", "page_url"):
            value = item.get(key)
            if (isinstance(value, str) and _URL.match(value)
                    and not _EMAIL.search(unquote(value))):
                issue[key] = value
        samples = [url for url in item.get("sample_urls") or []
                   if isinstance(url, str) and _URL.match(url) and not _EMAIL.search(unquote(url))][:4]
        if samples:
            issue["sample_urls"] = samples
        value = item.get("selector")
        if (isinstance(value, str) and len(value) <= 500
                and not _CJK.search(value) and not _EMAIL.search(value)):
            issue["selector"] = value
        for key in ("branch_index", "observed_count", "candidate_count", "card_count",
                    "visible_cards", "extracted_candidates", "page", "segment", "extra_name_lines",
                    "container_count", "email_count"):
            if type(item.get(key)) is int:
                issue[key] = item[key]
        owner = _profile_issue_branch(plan, issue)
        if owner:
            issue["branch_url"] = owner
        issues.append(issue)
    return {"previous_plan": plan, "issues": issues, "captured_issues": list(issues),
            "phase_rejections": []}


def _unique_repair_entries(*groups) -> list[dict]:
    """Keep distinct controlled diagnostics without making provenance a retry key."""
    entries = {}
    for group in groups:
        for item in group if isinstance(group, list) else []:
            if isinstance(item, dict):
                entries[json.dumps(item, sort_keys=True, ensure_ascii=False)] = item
    return list(entries.values())


def merge_repair_feedback(previous: dict | None, incoming: dict) -> dict:
    """A rejected proposal adds constraints; it cannot erase captured failures.

    Both inputs are controlled structural feedback, never the model's rejected
    response or exception text. A later successful capture replaces this set
    with its own current audit; only that can settle a captured counterexample.
    """
    previous = previous or {}
    merged = {**previous, **incoming}
    merged['issues'] = _unique_repair_entries(previous.get('issues'), incoming.get('issues'))
    history = _unique_repair_entries(previous.get('phase_rejection_history'),
        previous.get('phase_rejections'), incoming.get('phase_rejection_history'), incoming.get('phase_rejections'))
    if history:
        # A retired refusal must never become legacy URL feedback after its
        # active snapshot is replaced. Keep its origin for suppression only.
        merged['phase_rejection_history'] = history
    if 'captured_issues' in previous or 'captured_issues' in incoming:
        merged['captured_issues'] = _unique_repair_entries(previous.get('captured_issues'), incoming.get('captured_issues'))
    if 'known_urls' in previous or 'known_urls' in incoming:
        merged['known_urls'] = list(dict.fromkeys(url for url in [
            *previous.get('known_urls', []), *incoming.get('known_urls', [])] if _is_structure_url(url)))
    prior_plan = previous.get('previous_plan')
    proposed = incoming.get('previous_plan')
    if prior_plan:
        try:
            if proposed is not None:
                validate_model_plan(proposed, school=prior_plan['school'], college=prior_plan['college'])
        except (PlanHygieneError, KeyError, TypeError):
            proposed = None
        prior_urls = {branch['url'] for branch in [*prior_plan.get('branches', []),
                                                  *prior_plan.get('source_dispositions', [])]}
        proposed_urls = {branch.get('url') for branch in [*(proposed or {}).get('branches', []),
                                                        *(proposed or {}).get('source_dispositions', [])]}
        # A failed phase may return only its accepted sibling branches. That is
        # not a replacement for the complete plan whose audit found failures.
        if not prior_urls <= proposed_urls:
            merged['previous_plan'] = prior_plan
    return merged


def _phase_rejection(phase: str, session_id: str, url: str, issues: list[dict]) -> dict:
    """Bind controlled issues to the phase invocation that produced them."""
    return {'phase': phase, 'session_id': session_id, 'target_url': url, 'issues': issues}


def _phase_repair_issues(feedback: dict, phase: str, url: str, branch_urls=()) -> list[dict]:
    """Route controlled errors with their actual phase/session/target provenance.

    Contextual refusals are the latest failed attempt's snapshot. Older gate
    envelopes remain in the audit; captured structural failures retain their
    existing URL ownership. Legacy URL-free schema feedback is discovery-only;
    a branch error needs an exact URL or execution-owned provenance.
    """
    text_fields = ('path', 'need', 'selector')
    code_fields = ('reason', 'failed_condition', 'role')
    url_fields = ('url', 'directory_url', 'branch_url', 'page_url')
    count_fields = ('branch_index', 'observed_count', 'candidate_count', 'card_count',
                    'visible_cards', 'extracted_candidates', 'page', 'segment', 'extra_name_lines',
                    'container_count', 'email_count')
    branches = set(branch_urls)

    def project(item, context, owner=None, depth=0):
        if not isinstance(item, dict) or depth > 4:
            return None
        status = item.get('status')
        if not isinstance(status, str) or not re.fullmatch(r'[a-z_]{1,80}', status):
            return None
        context = {**context, **{key: item[key] for key in ('phase', 'session_id', 'target_url') if key in item}}
        origin_phase = context.get('phase')
        target_url = context.get('target_url')
        if origin_phase is not None and origin_phase != phase:
            return None
        if origin_phase is not None and (not _is_structure_url(target_url) or target_url != url):
            return None
        declared = item.get('branch_url') or item.get('directory_url')
        page_url = item.get('url')
        if declared and not _is_structure_url(declared):
            return None
        if page_url is not None and not _is_structure_url(page_url):
            return None
        bound = target_url if origin_phase == 'branch' else owner
        if bound and (declared and declared != bound or page_url in branches and page_url != bound):
            return None
        owned = declared or bound or page_url
        selected = {'status': status}
        for key in url_fields:
            if _is_structure_url(item.get(key)):
                selected[key] = item[key]
        for key in text_fields:
            if isinstance(item.get(key), str) and not _EMAIL.search(item[key]):
                selected[key] = item[key]
        for key in code_fields:
            if isinstance(item.get(key), str) and re.fullmatch(r'[a-z_]{1,80}', item[key]):
                selected[key] = item[key]
        for key in count_fields:
            if type(item.get(key)) is int:
                selected[key] = item[key]
        if type(item.get('page_only')) is bool:
            selected['page_only'] = item['page_only']
        if isinstance(item.get('failed_conditions'), list):
            selected['failed_conditions'] = [value for value in item['failed_conditions']
                if isinstance(value, str) and re.fullmatch(r'[a-z_]{1,80}', value)]
        if isinstance(item.get('sample_urls'), list):
            selected['sample_urls'] = [value for value in item['sample_urls'] if _is_structure_url(value)]
        if origin_phase is not None:
            selected.update(phase=origin_phase, target_url=target_url)
        sid = context.get('session_id')
        if isinstance(sid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}(?:-discover|-branch-[0-9]+)?', sid):
            selected['session_id'] = sid
        children = item.get('gate_issues')
        if isinstance(children, list):
            children = [value for child in children
                        if (value := project(child, context, owned, depth + 1)) is not None]
            if not children:
                return None
            selected['gate_issues'] = children
        elif phase == 'branch':
            if owned != url:
                return None
        elif origin_phase != 'discovery' and (owned or not (status.startswith('schema_')
                                                           or status == 'plan_contract_rejected')):
            return None
        return selected

    contexts = feedback.get('phase_rejections')
    routed = []
    contextual_issues = set()
    for record in _unique_repair_entries(feedback.get('phase_rejection_history')):
        contextual_issues.update(json.dumps(item, sort_keys=True, ensure_ascii=False)
            for item in _unique_repair_entries(record.get('issues')))
    for item in _unique_repair_entries(feedback.get('captured_issues')):
        if value := project(item, {}):
            routed.append(value)
    for record in contexts if isinstance(contexts, list) else []:
        if not isinstance(record, dict):
            continue
        if record.get('phase') not in {'discovery', 'branch'}:
            continue
        context = {key: record[key] for key in ('phase', 'session_id', 'target_url') if key in record}
        items = record.get('issues')
        for item in items if isinstance(items, list) else []:
            contextual_issues.add(json.dumps(item, sort_keys=True, ensure_ascii=False))
            if value := project(item, context):
                routed.append(value)
    items = feedback.get('issues')
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or json.dumps(item, sort_keys=True, ensure_ascii=False) in contextual_issues:
            continue
        if contexts is not None and 'gate_issues' in item and 'phase' not in item:
            continue
        if value := project(item, {}):
            routed.append(value)
    return _unique_repair_entries(routed)


def _profile_issue_branch(plan: dict[str, Any], issue: dict[str, Any]) -> str | None:
    """The one branch whose verified profile selector a profile-page issue names.

    Profile issues carry the profile URL, not the roster. When exactly one branch
    declared the failing selector, the repair can stay inside that branch.
    """
    plan = _normalize_model_plan(plan)
    urls = {branch.get("url") for branch in plan.get("branches", []) if isinstance(branch, dict)}
    if any(issue.get(key) in urls for key in ("url", "directory_url", "branch_url")):
        return None
    selector = issue.get("selector")
    if not selector:
        return None
    def owns(branch):
        return (selector == branch.get('profile_content_selector')
                or (isinstance(branch.get('child_branch'), dict) and owns(branch['child_branch']))
                or any(owns(child) for child in branch.get('child_branches', [])))
    owners = [branch['url'] for branch in plan.get('branches', []) if owns(branch)]
    return owners[0] if len(owners) == 1 else None


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
                    if name == "mcp__playwright__browser_verify_profile":
                        # Samples opened by the proof tool itself, including redirects.
                        for item in result.get("results") or []:
                            if not isinstance(item, dict) or item.get("error"):
                                continue
                            source, destination = item.get("requested_url"), item.get("url")
                            if (isinstance(source, str) and _URL.match(source) and isinstance(destination, str)
                                    and _URL.match(destination) and source != destination):
                                aliases[source] = destination
                        if result.get("results"):
                            last_verify = None
                        continue
                    if name == "mcp__playwright__browser_survey":
                        for item in (result.get("pages") or {}).get("items", []):
                            if not isinstance(item, dict) or item.get("error"):
                                continue
                            source, destination = item.get("url"), item.get("final_url")
                            if (isinstance(source, str) and _URL.match(source) and isinstance(destination, str)
                                    and _URL.match(destination) and source != destination):
                                aliases[source] = destination
                        last_verify = None
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


def _read_stage_originals(result: dict[str, Any]) -> list[tuple[str, str]] | None:
    """Recheck local originals; an envelope's claimed hash is not its witness."""
    originals = result.get('originals')
    if not isinstance(originals, list) or not originals:
        return None
    documents = []
    for original in originals:
        if (not isinstance(original, dict)
                or set(original) != {'url', 'source_html_sha256', 'evidence_path'}
                or not _is_structure_url(original.get('url'))
                or not isinstance(original.get('source_html_sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', original['source_html_sha256'])
                or not isinstance(original.get('evidence_path'), str)):
            return None
        path = Path(original['evidence_path'])
        if path.suffix != '.html':
            return None
        try:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != original['source_html_sha256']:
                return None
            html = raw.decode('utf-8')
        except (OSError, UnicodeError, ValueError):
            return None
        if not html.strip():
            return None
        documents.append((original['url'], html))
    return documents


def _read_stage_packet(packet: dict[str, Any], role: str) -> dict[str, Any] | None:
    """Expand a stage-only hashed transport reference without truncating its evidence."""
    if packet.get('role') != role:
        return None
    if 'full_stage_result' in packet:
        reference = packet['full_stage_result']
        if (set(packet) != {'role', 'full_stage_result'} or not isinstance(reference, dict)
                or set(reference) != {'sha256', 'evidence_path'}
                or not isinstance(reference['sha256'], str)
                or not re.fullmatch(r'[a-f0-9]{64}', reference['sha256'])
                or not isinstance(reference['evidence_path'], str)):
            return None
        path = Path(reference['evidence_path'])
        if path.suffix != '.json':
            return None
        try:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != reference['sha256']:
                return None
            packet = json.loads(raw)
        except (OSError, UnicodeError, ValueError):
            return None
    if (not isinstance(packet, dict) or set(packet) != {'role', 'results'}
            or packet['role'] != role or not isinstance(packet['results'], list)
            or not packet['results'] or not all(isinstance(item, dict) for item in packet['results'])):
        return None
    return packet


def _read_stage_capture(role: str, observation: dict[str, Any], documents: list[tuple[str, str]]):
    """Rebuild the universal DTO; mechanism-specific payloads stay with their validator."""
    from server.page_types import EntranceCapture, ProfileCapture
    metadata = observation.get('capture_metadata')
    if (not isinstance(metadata, dict) or set(metadata) != {'sha256', 'evidence_path'}
            or not isinstance(metadata.get('sha256'), str)
            or not re.fullmatch(r'[a-f0-9]{64}', metadata['sha256'])
            or not isinstance(metadata.get('evidence_path'), str)):
        return None
    path = Path(metadata['evidence_path'])
    if path.suffix != '.json':
        return None
    try:
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            return None
        data = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get('role') != role or not isinstance(data.get('errors'), list):
        return None
    if role == 'pagination' and set(data) == {'role', 'errors', 'evidence'}:
        return documents, data['errors'], data['evidence']
    if len(documents) != 1:
        return None
    if role == 'entrance' and set(data) == {'role', 'source_urls', 'errors', 'evidence'}:
        return EntranceCapture(documents[0][1], data['source_urls'], data['errors'], data['evidence'])
    if role == 'detail' and set(data) == {'role', 'errors', 'proof', 'meta'}:
        return ProfileCapture(documents[0][1], data['proof'], data['errors'], data['meta'])
    return None


def selector_evidence_issues(plan: dict[str, Any], events: list[dict[str, Any]],
                             repair_profiles: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """Accept selectors only when this session's browser verified their matches.

    repair_profiles maps a branch to the profile pages where the harvest found
    its previous profile selector wrong; a repaired selector must hold there too.
    """
    try:
        plan = _normalize_model_plan(plan)
    except PlanHygieneError:
        return [{'status': 'plan_contract_rejected', 'failed_condition': 'invalid_page_type_binding'}]
    calls = {}
    observations = []
    profile_observations = []
    segment_observations = []
    stage_observations = []
    stage_positions = {}
    clicks = []
    for index, event in enumerate(events):
        data = event.get("data") or {}
        if event.get("type") == "tool/call" and data.get("name") in (
                "mcp__playwright__browser_verify", "mcp__playwright__browser_click",
                "mcp__playwright__browser_verify_profile", "mcp__playwright__browser_verify_segments",
                'mcp__playwright__browser_verify_stage'):
            try:
                args = json.loads(data.get("arguments") or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(args, dict):
                continue
            calls[data.get("callId")] = (data["name"], args)
            if data['name'] == 'mcp__playwright__browser_verify_stage':
                requested = (args.get('urls') if args.get('role') == 'detail'
                             else [(args.get('branch') or {}).get('url')])
                positions = {}
                for url in requested if isinstance(requested, list) else []:
                    if isinstance(url, str) and url:
                        positions[url] = len(stage_observations)
                        # Pending, failed and malformed latest calls retire old evidence.
                        stage_observations.append((args, {'requested_url': url}, index))
                stage_positions[data.get('callId')] = positions
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
                        elif name == "mcp__playwright__browser_verify_segments":
                            segment_observations.append((args, result, index))
                        elif name == "mcp__playwright__browser_verify_profile":
                            # One call may open several samples; each keeps its own proof.
                            samples = result.get("results") if isinstance(result.get("results"), list) else [result]
                            profile_observations.extend((args, item, index) for item in samples
                                                        if isinstance(item, dict))
                        elif name == 'mcp__playwright__browser_verify_stage':
                            packet = _read_stage_packet(result, args.get('role'))
                            if packet is not None:
                                positions = stage_positions.get(block.get('toolCallId'), {})
                                for item in packet['results']:
                                    position = positions.get(item.get('requested_url'))
                                    if position is not None:
                                        stage_observations[position] = (args, item, stage_observations[position][2])
                        else:
                            observations.append((args, result, index))
    from server.pager_evidence import pagination_termination_valid
    issues = []
    aliases = browser_url_aliases(events)
    resolve = lambda url: resolved_browser_url(url, aliases)

    def tried(url, found):
        return {"proofs_on_url": sum(resolve(result.get("url")) == resolve(url) for _, result, _ in found)}

    def completed(result, index, selector, found):
        from server.nonpagination_controls import nonpagination_evidence_valid
        # An earlier absence observation cannot erase a later forward control.
        if not pagination_termination_valid(result, selector) or any(
            later_index > index and resolve(later.get('url')) == resolve(result.get('url'))
            and (args.get('card_selector') or args.get('container_selector')) == selector
            for args, later, later_index in found):
            return False
        # Omitting a previously observed selector is not evidence that its
        # control disappeared. Its latest actual recheck must show the end.
        controls = {}
        for args, observed, observed_index in found:
            if (observed_index <= index and resolve(observed.get('url')) == resolve(result.get('url'))
                    and (args.get('card_selector') or args.get('container_selector')) == selector
                    and args.get('next_selector')):
                controls[args['next_selector']] = observed
        current_controls = {item['selector']: item for item in
                            (result.get('pagination_termination') or {}).get('observed_forward_controls', [])}
        return all(observed.get('nextCount') == 0 or observed.get('nextDisabled') is True
                   or (key in current_controls and (current_controls[key].get('count') == 0
                       or current_controls[key].get('disabled') is True
                       or current_controls[key].get('terminal') is True
                       or nonpagination_evidence_valid(current_controls[key], result.get('url'), selector,
                           result['pagination_termination']['source_sha256'])))
                   for key, observed in controls.items())

    def stage_proof(branch, role, requested_url):
        from server.page_types import binding_for, validate_stage_evidence
        binding = binding_for(branch, role)
        found = [(args, result, index) for args, result, index in stage_observations
                 if args.get('role') == role and result.get('requested_url') == requested_url
                 and (args.get('branch') or {}).get('url') == branch['url']]
        if not found:
            return None
        args, result, _ = found[-1]
        try:
            used = binding_for(args['branch'], role)
        except (ValueError, TypeError, KeyError):
            return None
        documents = _read_stage_originals(result)
        if used != binding or not documents:
            return None
        final_url, html = documents[0]
        actual_hash = hashlib.sha256(html.encode()).hexdigest()
        reconstructed = _read_stage_capture(role, result, documents)
        if reconstructed is None:
            return None
        if role != 'detail':
            source = source_event_proof(branch, events)
            if source is None or source.get('url') != final_url:
                return None
            if role == 'pagination' and 'source_roles' in reconstructed[2]:
                # Pagination opens its own document and verifies its source there.
                # Retain the latest dedicated qualification, while binding the
                # stage to that new original; unrelated page chrome may differ.
                from server.source_qualification import capture_preserves_source
                source_roles = reconstructed[2].get('source_roles')
                if not isinstance(source_roles, dict):
                    return None
                fresh = source_roles.get(final_url)
                if not capture_preserves_source(html, final_url, branch, plan, source, fresh):
                    return None
            elif source.get('source_html_sha256') != actual_hash:
                return None
        proof = result.get('stage_evidence')
        if not validate_stage_evidence(role, branch, proof, requested_url=requested_url,
                final_url=final_url, source_html_sha256=actual_hash, source_html=html, result=reconstructed):
            return None
        return proof, documents, reconstructed

    def stage_required(role, branch):
        from server.page_types import requires_stage_evidence
        try:
            return requires_stage_evidence(role, branch)
        except ValueError:
            # A registered implementation without a proof contract remains
            # unproved, including callers of this low-level evidence checker.
            return True

    def explicit_child_proof(branch, matching, selector):
        if stage_required('entrance', branch):
            verified = stage_proof(branch, 'entrance', branch['url'])
            return verified[2].source_urls if verified is not None else None
        if not matching:
            return None
        args, result, index = matching[-1]
        proof = result.get('child_url_evidence') or {}
        urls = result.get('child_urls')
        termination = result.get('pagination_termination') or {}
        source_hash = proof.get('source_sha256')
        role_proof = source_event_proof(branch, events)
        if (args.get('include_child_urls') is not True or proof.get('complete') is not True
                or proof.get('discovery_scope') != 'current_rendered_navigation_links'
                or proof.get('selector') != selector or proof.get('url') != result.get('url')
                or not isinstance(source_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', source_hash)
                or source_hash != termination.get('source_sha256')
                or role_proof is None or role_proof.get('source_html_sha256') != source_hash
                or proof.get('invalid_links') != 0
                or type(proof.get('selected_count')) is not int
                or proof['selected_count'] != result.get('cardCount')
                or not isinstance(urls, list) or not urls or not all(_is_structure_url(url) for url in urls)
                or len(urls) != len(set(urls)) or len(urls) > proof['selected_count']
                or not completed(result, index, selector, observations)):
            return None
        return urls

    def roster_type_proved(branch, matching):
        from server.page_types import binding_for, implementation_digest, ROLE_PARAMETERS
        binding = binding_for(branch, 'roster')
        if (binding.type_id, binding.implementation_version) in {
                ('roster.cards', 1), ('roster.segmented', 1), ('roster.image_table', 1)}:
            return True
        if not matching:
            return False
        args, result, _ = matching[-1]
        proof = result.get('roster_type_evidence') or {}
        reading = proof.get('reading') or {}
        role_proof = source_event_proof(branch, events)
        try:
            refs = (args.get('page_types') or {})
            used = binding_for({**{key: value for key, value in args.items() if key != 'page_types'},
                'url': branch['url'], 'page_types': {'roster': refs['roster']} if 'roster' in refs else {}}, 'roster')
        except (ValueError, TypeError):
            return False
        selectors = {key: branch[key] for key in ROLE_PARAMETERS['roster'] if key in branch}
        counts = ('listed_items', 'extracted_candidates', 'awaiting_review', 'reviewed_exclusions')
        from server.name_review import STATUS as NAME_REVIEW_STATUS
        from server.page_types import resolve_type
        pending_name_review = (NAME_REVIEW_STATUS in resolve_type(binding, 'roster').failure_contract
                               and reading.get('issues') == [NAME_REVIEW_STATUS]
                               and type(reading.get('awaiting_review')) is int and reading['awaiting_review'] > 0)
        return (used.type_id == binding.type_id and used.implementation_version == binding.implementation_version
                and proof.get('type_id') == binding.type_id
                and proof.get('implementation_version') == binding.implementation_version
                and proof.get('implementation_digest') == implementation_digest(binding, 'roster')
                and proof.get('selectors') == selectors
                and role_proof is not None and proof.get('source_sha256') == role_proof.get('source_html_sha256')
                and proof.get('source_sha256') == (result.get('pagination_termination') or {}).get('source_sha256')
                and all(type(reading.get(key)) is int and reading[key] >= 0 for key in counts)
                and reading['listed_items'] > 0 and (reading.get('issues') == [] or pending_name_review)
                and reading['listed_items'] == sum(reading[key] for key in counts[1:]))

    for branch in plan["branches"]:
        issues.extend(source_event_issues(branch, events))
        typed_stages = {}
        for role in ('entrance', 'pagination'):
            if stage_required(role, branch):
                typed_stages[role] = stage_proof(branch, role, branch['url'])
                if typed_stages[role] is None:
                    issues.append({'status': 'stage_type_evidence_unverified', 'role': role, 'url': branch['url'],
                        'failed_condition': 'current_stage_proof_missing',
                        'need': 'browser_verify_stage with this exact registered binding. Its actual handler '
                                'originals, current source hash, URLs, parameters and implementation digest '
                                'must pass the dedicated evidence contract.'})
        if not _explicit_navigation_pagination_supported(branch):
            issues.append({'status': 'plan_contract_rejected', 'url': branch['url'],
                           'failed_condition': 'explicit_navigation_pagination_not_applicable',
                           'need': 'Explicit navigation supports only pagination.single_page@1 without '
                                   'next_selector; its child set proves only the current rendered parent.'})
            continue
        if branch.get('kind') == 'navigation' and 'entrance' in typed_stages:
            # A dedicated entrance capture certifies its own selection. It is
            # not a roster-card observation and need not use that old probe.
            verified = typed_stages['entrance']
            if verified is None:
                continue
            selector = branch['child_link_selector']
            from server.faculty_extractor import FacultyDirectoryExtractor
            introductions = FacultyDirectoryExtractor.profile_introduction_links(
                verified[1][0][1], verified[1][0][0], selector)
            if introductions:
                issues.append({'status': 'child_roster_not_verified', 'url': branch['url'],
                    'selector': selector, **_gate_detail('navigation_links_are_profiles',
                    {'profile_intro_link_count': len(introductions)},
                    'Selected personal introductions require profile-role verification, '
                    'rather than navigation to child rosters.')})
                continue
            urls = verified[2].source_urls
            if (not urls or not all(_is_structure_url(url) for url in urls)
                    or len(urls) != len(set(urls)) or branch['url'] in urls):
                issues.append({'status': 'child_roster_not_verified', 'url': branch['url'],
                    'selector': selector, **_gate_detail('explicit_child_set_unverified',
                    {'observed_children': len(urls)},
                    'An actual entrance must return unique child source URLs, without '
                    'invalid URLs or a link back to this parent.')})
                continue
            if 'child_branches' in branch:
                children = branch['child_branches']
                declared = [child['url'] for child in children]
                if (len(declared) != len(set(declared)) or set(declared) != set(urls)
                        or branch['url'] in declared):
                    issues.append({'status': 'child_roster_not_verified', 'url': branch['url'],
                        'selector': selector, **_gate_detail('explicit_child_set_unverified',
                        {'declared_children': len(children), 'observed_children': len(urls)},
                        'Bind each actual entrance source URL exactly once, and verify '
                        'each child with its own current source and selectors.')})
                    continue
            else:
                child = branch.get('child_branch')
                if not urls or not isinstance(child, dict):
                    issues.append({'status': 'child_roster_not_verified', 'url': branch['url'],
                        **_gate_detail('child_roster_missing', {'linked_samples': len(urls)},
                        'The selected entrance sources require a verified child branch.')})
                    continue
                children = [{**child, 'url': url} for url in urls]
            for child in children:
                issues.extend(selector_evidence_issues({**plan, 'branches': [child]}, events, repair_profiles))
            continue
        from server.page_types import binding_for
        roster_binding = binding_for(branch, 'roster')
        specialized_type = {'image_table': 'roster.image_table', 'segmented': 'roster.segmented'}.get(
            branch.get('kind'))
        if branch.get('empty_state_selector') or branch.get('roster_container_selector'):
            specialized_type = 'roster.cards'
        if specialized_type and (roster_binding.type_id, roster_binding.implementation_version) != (specialized_type, 1):
            # These dedicated browser proofs are contracts of their original
            # implementations. No generic typed reading can certify a new
            # image/segment/empty mechanism without a corresponding probe.
            issues.append({'status': 'roster_type_evidence_unverified', 'url': branch['url'],
                           'failed_condition': 'specialized_roster_version_not_applicable',
                           'need': 'This dedicated image/segment/empty proof supports only its exact legacy '
                                   'roster type at version 1; a new version needs a reviewed browser proof.'})
            continue
        if branch.get("kind") == "image_table":
            verified = any(
                args.get("card_selector") == branch.get("card_selector")
                and resolve(result.get("url")) == resolve(branch["url"])
                and (result.get("image_table") or {}).get("valid") is True
                and type((result.get("image_table") or {}).get("image_count")) is int
                and (result["image_table"]["image_count"] > 0)
                and result["image_table"]["image_count"] == result.get("cardCount")
                and len(result["image_table"].get("image_urls", [])) == result["image_table"]["image_count"]
                and completed(result, index, branch.get("card_selector"), observations)
                for args, result, index in observations)
            if not verified:
                issues.append({"status": "table_image_selector_unverified", "url": branch["url"],
                               "selector": branch["card_selector"],
                               **_gate_detail("table_image_proof_missing", tried(branch["url"], observations),
                                              "browser_verify this card_selector on this url: every match must be a "
                                              "valid table image, with confirmed pagination_termination.")})
            continue
        if branch.get("kind") == "segmented":
            verified = any(
                args.get("container_selector") == branch.get("container_selector")
                and args.get("record_start_selector") == branch.get("record_start_selector")
                and result.get("container_selector") == branch.get("container_selector")
                and result.get("record_start_selector") == branch.get("record_start_selector")
                and resolve(result.get("url")) == resolve(branch["url"])
                and result.get("structure_ok") is True
                and type(result.get("segment_count")) is int and result["segment_count"] > 0
                and completed(result, index, branch.get("container_selector"), segment_observations)
                for args, result, index in segment_observations)
            if not verified:
                issues.append({"status": "segments_not_verified", "url": branch["url"],
                               "selector": branch.get("record_start_selector"),
                               **_gate_detail("segment_proof_missing", tried(branch["url"], segment_observations),
                                              "browser_verify_segments on this url with the submitted container and "
                                              "record start selectors must report structure_ok, segments > 0 and "
                                              "confirmed pagination_termination.")})
            continue
        selector = branch.get("card_selector") or branch.get("child_link_selector")
        if branch.get("empty_state_selector") or branch.get("roster_container_selector"):
            from server.empty_roster import SELECTORS, empty_roster_proof_valid
            verified = any(
                all(args.get(key) == branch.get(key) for key in SELECTORS)
                and resolve(result.get("url")) == resolve(branch["url"])
                and type(result.get("cardCount")) is int and result["cardCount"] == 0
                and completed(result, index, selector, observations)
                and empty_roster_proof_valid(result.get("empty_roster"), branch,
                                            url=result.get("url"))
                for args, result, index in observations)
            if not verified:
                issues.append({"status": "empty_roster_not_verified", "url": branch["url"], "selector": selector,
                               **_gate_detail("empty_roster_proof_missing", tried(branch["url"], observations),
                                              "browser_verify on this url with exactly the submitted empty-state "
                                              "selectors must show 0 cards, confirmed pagination_termination and a valid "
                                              "empty-roster proof.")})
            continue
        matching = [(args, result, index) for args, result, index in observations if (
            args.get("card_selector") == selector and resolve(result.get("url")) == resolve(branch["url"])
            and type(result.get("cardCount")) is int and result["cardCount"] > 0
            and (not branch.get("next_selector") or (
                args.get("next_selector") == branch["next_selector"] and result.get("nextCount") == 1)))]
        if not roster_type_proved(branch, matching):
            issues.append({'status': 'roster_type_evidence_unverified', 'url': branch['url'],
                           'selector': selector, 'failed_condition': 'current_roster_type_proof_missing',
                           'need': 'browser_verify with this exact registered roster binding and all its '
                                   'structural selectors. Its current-source token dispositions must conserve '
                                   'every listed item without structural reading failures; acknowledged '
                                   'rare-surname items must remain pending for the harvest name-review gate.'})
        if not matching:
            issues.append({"status": "selector_not_verified", "url": branch["url"], "selector": selector,
                           **_card_detail(branch, selector, observations, resolve)})
        elif (branch.get("single_page") and 'pagination' not in typed_stages
              and not completed(matching[-1][1], matching[-1][2], selector, observations)):
            issues.append({"status": "single_page_pagination_unverified", "url": branch["url"], "selector": selector,
                           **_gate_detail("pagination_termination_unverified", {"pagination_total": (
                               matching[-1][1].get("pagination") or {}).get("total")},
                               "Verify a unique next_selector and its next page, or browser_verify the roster "
                               "with confirmed pagination_termination; candidate total 0 alone is insufficient.")})
        elif (branch.get("next_selector") and 'pagination' not in typed_stages
              and not any(result.get("nextDisabled") is True for _, result, _ in matching)):
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
                               "selector": branch["next_selector"],
                               **_gate_detail("next_page_not_verified", {"next_urls": len(next_urls)},
                                              "Open the next page (its nextUrl, or browser_click the next_selector) "
                                              "and browser_verify the same card_selector there with cards > 0; on "
                                              "the last page, nextDisabled true also proves the end.")})
        explicit_navigation = branch.get('kind') == 'navigation' and 'child_branches' in branch
        verified_child_urls = explicit_child_proof(branch, matching, selector) if explicit_navigation else None
        if (matching and (mismatch := matching[-1][1].get("card_mismatch"))
                and not (explicit_navigation and verified_child_urls is not None)):
            if type(mismatch.get('outside_profile_links')) is int and mismatch['outside_profile_links'] > 0:
                issues.append({'status':'roster_profile_link_outside_card','url':branch['url'],'selector':selector,
                    **_gate_detail('profile_link_outside_card',
                        {'outside_profile_links':mismatch['outside_profile_links']},
                        'The selected text leaves exclude their enclosing links. Verify the enclosing anchor '
                        'or complete card and its personal-page scope; do not drop or guess these links.')})
                continue
            # The harvest compares the same cards with the people it reads and
            # stops on a difference; settle it here, while the scout can still act.
            issues.append({"status": "roster_card_mismatch", "url": branch["url"], "selector": selector,
                           **_gate_detail("cards_not_read", {key: mismatch.get(key) for key in (
                               "visible_cards", "extracted_candidates")},
                               "The harvest reads extracted_candidates people from visible_cards cards with this "
                               "card_selector. Verify a card_selector whose every card is one person the harvest "
                               "reads, or leave this page out if it is not a roster of this college's teachers.")})
        samples = list(dict.fromkeys(url for _, result, _ in matching
                       for url in result.get("linkedSamples", result.get("profileSamples", []))
                       if _is_structure_url(url) and resolve(url) != resolve(branch["url"])))[:2]
        # Profiles linked from this roster's own verified pages: the entry page
        # and the pages reached through its verified next links.
        chain, linked = {resolve(branch["url"])}, []
        for args, result, _ in observations:
            if (args.get("card_selector") != selector or resolve(result.get("url")) not in chain
                    or type(result.get("cardCount")) is not int or result["cardCount"] <= 0):
                continue
            if result.get("nextUrl"):
                chain.add(resolve(result["nextUrl"]))
            linked.extend(url for url in result.get("linkedSamples", result.get("profileSamples", []))
                          if _is_structure_url(url) and resolve(url) not in chain and url not in linked)
        if stage_required('detail', branch):
            # Use actual typed paging originals to include linked profiles on
            # every captured page, without exporting people into scout JSON.
            documents = typed_stages.get('pagination')
            documents = documents[1] if documents is not None else []
            if not documents and (source := source_event_proof(branch, events)) and source.get('original_path'):
                documents = _read_stage_originals({'originals': [{'url': source['url'],
                    'source_html_sha256': source['source_html_sha256'], 'evidence_path': source['original_path']}]}) or []
            if documents and branch.get('kind') != 'navigation':
                from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
                try:
                    parsed = reconcile_rosters([CapturedRosterPage(url, html, branch) for url, html in documents], plan)
                    linked = list(dict.fromkeys(candidate.url for candidate in parsed.candidates
                        if _is_structure_url(candidate.url) and candidate.url not in {url for url, _ in documents}))
                except (ValueError, TypeError, KeyError):
                    linked = []
        if branch.get("kind") == "navigation":
            if matching and matching[-1][1].get("profile_intro_link_count", 0) > 0:
                issues.append({"status": "child_roster_not_verified", "url": branch["url"],
                    "selector": selector, **_gate_detail("navigation_links_are_profiles",
                    {"profile_intro_link_count": matching[-1][1]["profile_intro_link_count"]},
                    "Selected links are labelled as personal introductions, conflicting with navigation. "
                    "Open their targets and verify whether they are profiles or child rosters. "
                    "For profiles, verify this entrance as an ordinary roster and prove the profile_content_selector. "
                    "For mixed roles, verify separate branches; do not omit or auto-convert the selected links.")})
                continue
            if explicit_navigation:
                children = branch['child_branches']
                declared = [resolve(child['url']) for child in children]
                if (verified_child_urls is None or len(declared) != len(set(declared))
                        or set(declared) != {resolve(url) for url in verified_child_urls}
                        or resolve(branch['url']) in declared):
                    issues.append({'status': 'child_roster_not_verified', 'url': branch['url'],
                        'selector': selector, **_gate_detail('explicit_child_set_unverified',
                            {'declared_children': len(children),
                             'observed_children': len(verified_child_urls) if verified_child_urls is not None else 0},
                            'browser_verify this parent selector with include_child_urls true and confirmed '
                            'pagination_termination. Bind exactly every current-rendered selected URL once; '
                            'samples or a changed document hash cannot prove this set.')})
                    continue
                # Each declared page owns its type parameters and evidence.
                # Parent selectors are never inherited by explicit children.
                for child in children:
                    issues.extend(selector_evidence_issues({**plan, 'branches': [child]}, events, repair_profiles))
                continue
            if not samples or not isinstance(child := branch.get("child_branch"), dict):
                issues.append({"status": "child_roster_not_verified", "url": branch["url"],
                               **_gate_detail("child_roster_missing" if samples else "child_links_not_verified",
                                              {"linked_samples": len(samples)},
                                              "browser_verify the child_link_selector on this url so it links child "
                                              "rosters, and submit the child_branch verified on those rosters.")})
                continue
            for child_url in samples:
                child_plan = {**plan, "branches": [{**child, "url": child_url}]}
                issues.extend(selector_evidence_issues(child_plan, events))
            continue
        if samples and not branch.get("profile_content_selector"):
            issues.append({"status": "profile_content_selector_missing", "url": branch["url"],
                           "sample_urls": linked[:4],
                           **_gate_detail("profile_selector_missing", {"linked_profiles": len(linked)},
                                          "Cards link to profile pages: verify a profile_content_selector with "
                                          "browser_verify_profile on two of sample_urls and submit it.")})
        if branch.get("profile_content_selector"):
            # Any two distinct profiles linked from verified cards prove the
            # scope; a scout that sampled page 2 rather than page 1 has not
            # verified less. The harvest re-verifies every profile anyway.
            def proved(url):
                if stage_required('detail', branch):
                    return stage_proof(branch, 'detail', url) is not None
                return any(
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
                    for args, result, _ in profile_observations)
            if stage_required('detail', branch):
                for args, result, _ in stage_observations:
                    if args.get('role') != 'detail' or (args.get('branch') or {}).get('url') != branch['url']:
                        continue
                    profile_url = result.get('requested_url')
                    if profile_url not in linked or not proved(profile_url):
                        issues.append({'status': 'stage_type_evidence_unverified', 'role': 'detail',
                            'url': branch['url'], 'sample_urls': [profile_url] if _is_structure_url(profile_url) else [],
                            'failed_condition': 'current_linked_profile_stage_unverified',
                            'need': 'Each requested detail proof must own an exact linked profile URL, its '
                                    'actual profile original and this registered binding; a roster hash or '
                                    'another profile document cannot prove it.'})
            required = min(2, len(linked))
            pair = (branch["profile_content_selector"], branch.get("profile_content_start_selector"))
            if not linked or sum(proved(url) for url in linked) < required:
                blocked = sum(_profile_page_blocked(url, profile_observations, resolve) for url in linked)
                issues.append({"status": "profile_content_not_verified", "url": branch["url"],
                               "selector": branch["profile_content_selector"],
                               "sample_urls": linked[:4],
                               **_profile_detail(linked[:4], pair, profile_observations, resolve,
                                                 page_only=bool(linked) and len(linked) - blocked < required,
                                                 need=f"Prove this selector pair with browser_verify_profile on "
                                                      f"{required} of the profile pages linked from this roster.")})
            elif unproved := [url for key, pages in (repair_profiles or {}).items()
                              if resolve(key) == resolve(branch["url"]) for url in pages if not proved(url)]:
                issues.append({"status": "profile_content_not_verified", "url": branch["url"],
                               "selector": branch["profile_content_selector"],
                               "sample_urls": unproved[:4],
                               **_profile_detail(unproved[:4], pair, profile_observations, resolve,
                                                 page_only=any(_profile_page_blocked(url, profile_observations, resolve)
                                                               for url in unproved),
                                                 need="The harvest found the previous profile selector wrong on "
                                                      "sample_urls: prove this selector pair on every one of them.")})
    return issues


def _gate_detail(condition: str, closest: dict[str, Any], need: str, *,
                 page_only: bool = False) -> dict[str, Any]:
    """Why a gate check failed, as controlled codes, counts and the next proof.

    ``page_only`` means no plan can pass this check on this page: the scout
    cannot fix it, so the session should end instead of resubmitting.
    """
    return {"failed_condition": condition, "closest": closest, "need": need,
            **({"page_only": True} if page_only else {})}


def _card_detail(branch: dict[str, Any], selector: str | None, observations: list,
                 resolve: Any) -> dict[str, Any]:
    here = [result for _, result, _ in observations if resolve(result.get("url")) == resolve(branch["url"])]
    same = [result for args, result, _ in observations if args.get("card_selector") == selector
            and resolve(result.get("url")) == resolve(branch["url"])]
    cards = [result for result in same if type(result.get("cardCount")) is int and result["cardCount"] > 0]
    if not here:
        return _gate_detail("url_never_verified", {"proofs_on_url": 0},
                            "Open this url and browser_verify the submitted card_selector there.")
    if not same:
        return _gate_detail("selector_differs_from_verified", {"proofs_on_url": len(here)},
                            "browser_verify ran on this url only with other card selectors: verify the "
                            "submitted card_selector here, or submit the selector you verified.")
    if not cards:
        return _gate_detail("card_count_zero", {"cardCount": same[-1].get("cardCount")},
                            "The submitted card_selector matched no cards on this url: verify a selector that "
                            "matches the roster cards.")
    return _gate_detail("next_control_not_verified", {"nextCount": cards[-1].get("nextCount")},
                        "Cards matched, but no browser_verify on this url used the submitted next_selector "
                        "with exactly one match (nextCount 1).")


def _latest_profile(url: str, observations: list, resolve: Any) -> dict[str, Any] | None:
    found = [result for _, result, _ in observations if resolve(result.get("url")) == resolve(url)]
    return found[-1] if found else None


def _profile_page_blocked(url: str, observations: list, resolve: Any) -> bool:
    """The latest open of this page failed requests that block every container, on both opens."""
    latest = _latest_profile(url, observations, resolve)
    return bool(latest and latest.get("page_blocking_requests"))


def _profile_detail(urls: list[str], pair: tuple[str, str | None], observations: list, resolve: Any,
                    *, page_only: bool, need: str) -> dict[str, Any]:
    samples = []
    for url in urls:
        latest = _latest_profile(url, observations, resolve)
        same = [result for args, result, _ in observations
                if (args.get("profile_content_selector"), args.get("profile_content_start_selector")) == pair
                and resolve(result.get("url")) == resolve(url)]
        if latest is None:
            sample = {"url": url, "condition": "not_opened"}
        elif not same:
            sample = {"url": url, "condition": "selector_pair_not_tried"}
        else:
            last = same[-1]
            sample = {"url": url, "condition": last.get("reason") or last.get("error_type") or "proof_incomplete",
                      **{key: last[key] for key in ("container_count", "dom_stable", "network_idle",
                                                    "blocking_requests") if key in last}}
        if latest and latest.get("page_blocking_requests"):
            sample["page_blocking_requests"] = latest["page_blocking_requests"]
        samples.append(sample)
    return _gate_detail("profile_proof_missing", {"samples": samples}, need, page_only=page_only)


def restore_verified_profile_selector(branch: dict[str, Any], plan: dict[str, Any],
                                      events: list[dict[str, Any]]) -> dict[str, Any]:
    """Recover a selector omitted from the final JSON only from unique browser proof.

    The model must have supplied the exact CSS to browser_verify_profile in this
    session. The normal evidence gate then checks every sampled profile again.
    """
    if branch.get("profile_content_selector") or branch.get("kind") == "segmented":
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
                "mcp__playwright__browser_navigate", "mcp__playwright__browser_navigation",
                "mcp__playwright__browser_survey"):
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
PROVIDED_LINKS_PROMPT = Path(__file__).with_name("recon_provided_links.md").read_text(encoding="utf-8")


@dataclass
class ReconResult:
    """侦察结果：计划本体 + 供审计使用的诊断信息。"""

    plan: dict[str, Any]
    elapsed_seconds: float
    finish_reason: str | None
    session_id: str
    event_count: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)
    # Accepted discovery (plan, browser events, session) for a branch-only repair.
    discovery_checkpoint: dict[str, Any] | None = field(default=None, repr=False, compare=False)

    def summary(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "finish_reason": self.finish_reason,
            "session_id": self.session_id,
            "event_count": self.event_count,
            "branches": len(self.plan.get("branches", [])),
            "source_dispositions": self.plan.get('source_dispositions', []),
            **self.diagnostics,
        }


def recon_evidence_root() -> Path:
    """Where browser-tool evidence is written and read back: one root for every reader.

    ``FACULTY_RECON_EVIDENCE_ROOT`` moves it (the test suite does, so that MCP
    subprocesses never write into the production server/outputs directory).
    """
    override = os.environ.get("FACULTY_RECON_EVIDENCE_ROOT")
    return Path(override) if override else Path(__file__).resolve().parent / "outputs" / "recon-evidence"


def write_runtime_patch(path: Path, *, phase: str = "full", session_id: str = "standalone",
                        target: dict | None = None) -> None:
    """Resolve project resources before the MCP subprocess starts."""
    here = Path(__file__).resolve().parent
    entries = yaml.safe_load((here / "recon_runtime.cordis.yml").read_text(encoding="utf-8"))
    for entry in entries:
        if entry.get("id") == "spill-local":
            entry["config"]["root"] = str(recon_evidence_root())
    import sys
    args = ["-m", "server.recon_browser_mcp", "--phase", phase, "--session", session_id]
    if target is not None:
        args.extend(["--target", json.dumps(target, ensure_ascii=False)])
    entries.append({"insert": [{"id": os.environ.get("DSH_MCP_ID", "faculty-recon-browser"),
        "name": "@deepseek-ai/dsh-mcp-client", "config": {
        "serverName": "playwright", "transport": "stdio", "command": sys.executable,
        "args": args, "cwd": str(here.parent),
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
            write_runtime_patch(resolved, target=cfg.get("target"))
            cfg = {**cfg, "runtime_patch": str(resolved)}
            patches[patches.index(runtime)] = str(resolved)
        if cfg.get("target"):
            return _run_staged_harness(session_id, cfg, tuple(patches))
        return _execute_harness(prompt, session_id, cfg, tuple(patches))


def _run_staged_harness(session_id: str, cfg: dict[str, Any], patches: tuple[str, ...]) -> Any:
    # Caller cancellation and a sibling's budget stop have different outcomes.
    cfg = {**cfg, 'branch_stop_event': threading.Event()}
    target = cfg["target"]
    events = []
    phases = []
    deadline = time.monotonic() + cfg["timeout_seconds"]
    feedback = cfg.get("feedback") or {}
    required_urls = _required_source_urls(cfg)
    urls = []
    parallel = cfg.get("parallel_branches", False)
    template_reuse = cfg.get("template_reuse", False)
    discovery_checkpoint = None
    verified_branches = dict(cfg.get("verified_branches") or {})
    errors = []

    def scoped_issues(url: str) -> list[dict]:
        return _phase_repair_issues(feedback, 'branch', url, urls)

    def repair_profiles(url: str) -> dict[str, list[str]]:
        """Profile pages where the harvest found this branch's profile selector wrong."""
        pages = list(dict.fromkeys(
            item["url"] for item in scoped_issues(url)
            if item.get("branch_url") == url and isinstance(item.get("url"), str) and item["url"] != url
            and str(item.get("status", "")).startswith("profile_content_")))
        return {url: pages} if pages else {}

    def run(phase: str, url: str, suffix: str) -> tuple[dict, list, dict]:
        remaining = deadline - time.monotonic()
        if ((cfg.get("cancel_event") is not None and cfg["cancel_event"].is_set())
                or cfg['branch_stop_event'].is_set()):
            error = ReconCancelled("侦察已被停止")
            error.phase, error.phase_session_id = phase, f"{session_id}-{suffix}"
            error.diagnostics = {"model_calls": 0, "token_usage": dict.fromkeys(
                ("uncached_input", "cached_input", "output"), 0),
                "budget_total": cfg["budget"].snapshot() if cfg.get("budget") else {}}
            raise error
        if remaining <= 0:
            raise ReconError("阶段侦察超过总时限")
        phase_cfg = {**cfg, "phase": phase, "timeout_seconds": remaining,
                     "effort": (cfg.get("phase_efforts") or {}).get(phase) or cfg.get("effort"),
                     "phase_call_limit": (cfg.get("phase_call_limits") or {}).get(phase, 0)}
        template = DISCOVERY_PROMPT if phase == "discovery" else PROMPT
        prompt = template.format(school=target["school"], college=target["college"], url=url)
        if phase == 'branch' and discovery_checkpoint:
            discovered = next((b for b in discovery_checkpoint['plan']['branches'] if b['url'] == url), None)
            if discovered:
                prompt += '\n本入口已观察的来源结构（须本阶段重新验证）：' + json.dumps(discovered, ensure_ascii=False)
        provided = [item for item in cfg.get("provided_urls") or [] if _is_structure_url(item)]
        if phase == "discovery" and provided:
            prompt += "\n" + PROVIDED_LINKS_PROMPT + "给定链接：\n" + "\n".join(provided)
        if feedback:
            if phase == "discovery":
                scoped = {"known_urls": required_urls,
                          "issues": _phase_repair_issues(feedback, phase, url, urls)}
            else:
                previous = next((branch for branch in
                    (feedback.get("previous_plan") or {}).get("branches", [])
                    if isinstance(branch, dict) and branch.get("url") == url), None)
                scoped = {"issues": scoped_issues(url)}
                if previous:
                    scoped["previous_branch"] = previous
            if scoped.get("issues") or scoped.get("known_urls") or scoped.get("previous_branch"):
                prompt += "\n本阶段结构反馈：" + json.dumps(scoped, ensure_ascii=False, separators=(",", ":"))
        sid = f"{session_id}-{suffix}"
        if 'gate_rejection_contexts' in cfg:
            cfg['gate_rejection_contexts'][sid] = {'phase': phase, 'target_url': url}
        phase_patches = patches
        if cfg.get("runtime_patch"):
            original = Path(cfg["runtime_patch"])
            patch = original.with_name(f"{original.stem}-{suffix}{original.suffix}") if parallel else original
            write_runtime_patch(patch, phase=phase, session_id=sid,
                                target={**target, "url": url, "required_urls": required_urls,
                                        **({"repair_profiles": repair_profiles(url)}
                                           if phase == "branch" and repair_profiles(url) else {})})
            phase_patches = tuple(str(patch) if value == str(original) else value for value in patches)
        try:
            result = _execute_harness(prompt, sid, phase_cfg, phase_patches)
        except (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled) as exc:
            exc.phase = phase
            exc.phase_session_id = sid
            raise
        except ReconGateStopped as exc:
            # The stop is about this session's branch; the gate issues keep their own pages.
            exc.safe_feedback = {"issues": [{"status": exc.code, "url": url, "gate_issues": [
                gate for item in exc.safe_feedback["issues"] for gate in item["gate_issues"]]}]}
            exc.safe_feedback['phase_rejections'] = [_phase_rejection(phase, sid, url,
                exc.safe_feedback['issues'])]
            exc.phase_metric = {"phase": phase, "session_id": sid, "effort": phase_cfg.get("effort"),
                                "evidence_root": str(recon_evidence_root() / sid), "gate_stop": exc.code,
                                **run_metrics(exc.phase_events)}
            raise
        current = getattr(result, "events", []) or []
        metric = {"phase": phase, "session_id": sid, "effort": phase_cfg.get("effort"),
                       "evidence_root": str(recon_evidence_root() / sid),
                       **run_metrics(current)}
        if getattr(result, 'completion_reason', None) == 'accepted_submission':
            metric.update(completion_reason=result.completion_reason,
                          usage_unknown=result.usage_unknown)
        if getattr(result, "finish_reason", None) != "completed":
            error = ReconError("阶段侦察未正常完成")
            error.phase_events, error.phase_metric = current, metric
            raise error
        from server.recon_contract import submitted_plan
        parsed = submitted_plan(current)
        if parsed is None and not cfg.get("require_submission"):
            parsed = extract_plan_json(getattr(result, "final_response", "") or "")
        if parsed is None:
            error = ReconPlanError("阶段未返回 JSON")
            error.phase_events, error.phase_metric = current, metric
            raise error
        return parsed, current, metric

    try:
        resumed_discovery = cfg.get("resume_discovery")
        if resumed_discovery:
            discovery = resumed_discovery["plan"]
            discovery_events = resumed_discovery["events"]
            discovery_session_id = resumed_discovery["session_id"]
            phases.append({"phase": "discovery", "reused": True,
                           "session_id": discovery_session_id, "model_calls": 0,
                           "evidence_root": str(recon_evidence_root() / discovery_session_id)})
        else:
            discovery, discovery_events, metric = run("discovery", target["url"], "discover")
            phases.append(metric)
            discovery_session_id = f"{session_id}-discover"
        events.extend(discovery_events)
        from server.recon_contract import validate_submission
        issues = validate_submission(discovery, 'discovery', {**target, 'required_urls': required_urls},
                                     discovery_events)
        if issues:
            error = ReconPlanError('入口发现计划不符合契约或缺少当前来源证据')
            # The worker routes contract failures as structural repair while
            # preserving the same controlled schema diagnostics the scout saw.
            routed = [{'status': 'plan_contract_rejected', 'gate_issues': issues}] if all(
                item['status'].startswith('schema_') for item in issues) else issues
            error.safe_feedback = {'known_urls': required_urls, 'issues': routed}
            error.safe_feedback['phase_rejections'] = [_phase_rejection('discovery',
                discovery_session_id, target['url'], routed)]
            raise error
        urls = list(dict.fromkeys(branch['url'] for branch in discovery['branches']))
        discovery_checkpoint = {"plan": discovery, "events": discovery_events,
                                "session_id": discovery_session_id}
        plan = {**discovery, "branches": []}
        from server.recon_templates import inspect_templates, verify_template
        evidence_root = recon_evidence_root() / session_id
        fingerprints = (inspect_templates(urls, evidence_root / "templates",
                        max(0.01, min(60, deadline - time.monotonic())))
                        if template_reuse and len(urls) > 1 else {})
        accepted = {}

        def verify(index, url, representative=None):
            started = time.monotonic()
            reuse = verified_branches.get(url)
            if reuse:
                from server.source_qualification import SOURCE_FIELDS
                current = next(claim for claim in discovery['branches'] if claim['url'] == url)
                if any(reuse['branch'].get(key) != current.get(key) for key in SOURCE_FIELDS):
                    reuse = None  # A fresh discovery claim needs its own branch proof.
            if reuse:
                branch = reuse["branch"]
                proof_events = reuse.get("events") or []
                one = {**plan, "branches": [branch]}
                validate_model_plan(one, school=target["school"], college=target["college"])
                if branch.get("url") != url or selector_evidence_issues(one, proof_events):
                    raise ReconPlanError("复用分支缺少本轮真实浏览器证据")
                metric = {"phase": "branch", "reused": True,
                          "evidence_root": reuse.get("evidence_root"), **run_metrics(proof_events)}
            else:
                proof = None
                # Interactive, image, segmented and empty rosters keep independent scouting.
                if (representative and representative.get("kind", "roster") == "roster"
                        and not representative.get("initial_click_selector")
                        and not representative.get("empty_state_selector")):
                    from server.source_qualification import SOURCE_FIELDS
                    current = next(claim for claim in discovery['branches'] if claim['url'] == url)
                    branch = {**representative, "url": url,
                              **{key: current[key] for key in SOURCE_FIELDS}}
                    if current.get('source_kind') is not None:
                        branch['source_kind'] = current['source_kind']
                    else:
                        branch.pop('source_kind', None)
                    proof = verify_template(branch, target, evidence_root / f"template-{index + 1}",
                                            max(0.01, min(120, deadline - time.monotonic())), fingerprints[url])
                if proof and proof.get("valid"):
                    proof_events = proof["events"]
                    metric = {"phase": "branch", "template_reused": True,
                              "representative_url": representative["url"],
                              "evidence_root": proof["evidence_root"], **run_metrics(proof_events)}
                else:
                    branch, proof_events, metric = run("branch", url, f"branch-{index + 1}")
                    branch = restore_verified_profile_selector(branch, plan, proof_events)
                    if proof is not None:
                        metric["template_fallback"] = proof.get("reason", "verification_failed")
            metric["url"] = url
            metric["elapsed_seconds"] = round(time.monotonic() - started, 3)
            try:
                one = {**plan, "branches": [branch]}
                validate_model_plan(one, school=target["school"], college=target["college"])
                aliases = browser_url_aliases(proof_events)
                if resolved_browser_url(branch.get("url"), aliases) != resolved_browser_url(url, aliases):
                    raise ReconPlanError("分支阶段改变了目标入口")
                issues = selector_evidence_issues(one, proof_events, repair_profiles(url))
                if issues:
                    error = ReconPlanError("分支缺少选择器或翻页证据")
                    error.safe_feedback = {"previous_plan": {**plan, "branches": [branch]},
                                           "known_urls": urls, "issues": issues}
                    raise error
            except ReconError as error:
                error.phase_events, error.phase_metric = proof_events, metric
                raise
            return branch, proof_events, metric

        def batch(jobs):
            def collect(index, url, operation):
                try:
                    branch, proof, metric = operation()
                    events.extend(proof)
                    phases.append(metric)
                    accepted[index] = branch
                    verified_branches[url] = {"branch": branch, "events": proof,
                                              "evidence_root": metric.get("evidence_root")}
                except ReconError as error:
                    events.extend(getattr(error, "phase_events", []))
                    if getattr(error, "phase_metric", None):
                        phases.append(error.phase_metric)
                    elif isinstance(error, (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled)):
                        phases.append({'phase': getattr(error, 'phase', 'branch'), 'url': url,
                            'session_id': getattr(error, 'phase_session_id', None),
                            'stop_reason': ('recon_budget_exceeded' if isinstance(error, ReconBudgetExceeded)
                                            else 'recon_phase_limit' if isinstance(error, ReconPhaseLimitExceeded)
                                            else 'recon_cancelled'), **error.diagnostics})
                    errors.append(error)
                    if isinstance(error, (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled)):
                        cfg['branch_stop_event'].set()
            if parallel and len(jobs) > 1:
                with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix=session_id) as pool:
                    pending = {pool.submit(verify, *job): job for job in jobs}
                    for future in as_completed(pending):
                        index, url, *_ = pending[future]
                        collect(index, url, future.result)
            else:
                for job in jobs:
                    collect(job[0], job[1], lambda job=job: verify(*job))

        leaders = {}
        first, followers = [], []
        for index, url in enumerate(urls):
            fingerprint = fingerprints.get(url)
            # A branch the harvest found wrong gets its own scout with its issues;
            # copying a sibling's selectors would reproduce the same failure.
            # It still leads its template, so its siblings wait for that scout.
            if scoped_issues(url) or not fingerprint or fingerprint not in leaders or url in verified_branches:
                first.append((index, url))
                if fingerprint and (fingerprint not in leaders or url in verified_branches):
                    leaders[fingerprint] = index
            else:
                followers.append((index, url, leaders[fingerprint]))
        batch(first)
        if not any(isinstance(error, (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled))
                   for error in errors):
            # One scout per template: siblings of a template whose scout failed
            # wait for the repair round instead of each repeating that scout.
            batch([(index, url, accepted[leader]) for index, url, leader in followers if leader in accepted])
        plan["branches"] = [accepted[index] for index in sorted(accepted)]
        partial_metrics = None
        stop_reason = None
        if errors:
            fatal = next((error for error in errors if isinstance(error, (
                ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled))), None)
            error = fatal or errors[0]
            if not fatal:
                feedback_issues = [item for failure in errors for item in
                                   getattr(failure, "safe_feedback", {}).get("issues", [])]
                error.safe_feedback = {"previous_plan": plan, "known_urls": urls,
                                       "issues": feedback_issues or [{"status": "plan_contract_rejected"}]}
                error.safe_feedback['phase_rejections'] = [record for failure in errors
                    for record in getattr(failure, 'safe_feedback', {}).get('phase_rejections', [])]
            # Aggregate all completed sibling usage, including failed-phase usage.
            if fatal:
                partials = [failure.diagnostics for failure in errors if isinstance(failure, (
                    ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled))]
                complete = run_metrics(events)
                error.diagnostics = {**complete, "session_id": session_id, "phases": phases,
                    "model_calls": complete["model_calls"] + sum(p["model_calls"] for p in partials),
                    "token_usage": {key: complete["token_usage"][key] +
                                    sum(p["token_usage"][key] for p in partials)
                                    for key in complete["token_usage"]},
                    "budget_total": (cfg["budget"].snapshot() if cfg.get("budget")
                                     else partials[-1]["budget_total"])}
                error.aggregated = True
                partial_metrics = error.diagnostics
            # A failed sibling does not invalidate a branch that passed all
            # schema, source, mechanism and actual-browser proof gates. Keep
            # every failed/unstarted source pending and run the proved subset.
            # Explicit cancellation and hygiene failures still abort the run.
            can_capture_subset = bool(accepted) and not any(isinstance(failure, PlanHygieneError)
                for failure in errors) and not (cfg.get('cancel_event') and cfg['cancel_event'].is_set()) and (
                not any(isinstance(failure, ReconCancelled)
                for failure in errors) or any(isinstance(failure, (ReconBudgetExceeded, ReconPhaseLimitExceeded))
                                             for failure in errors))
            if can_capture_subset:
                from server.recon_contract import disposition_event_issues, navigation_observations
                visits = navigation_observations(events)
                pending_sources = []
                for index, url in enumerate(urls):
                    if index in accepted:
                        continue
                    visit = visits.get(url, {})
                    unreadable = bool(visit.get('error') or (
                        type(visit.get('status')) is int and visit['status'] >= 400))
                    pending_sources.append({'url': url, 'reason': (
                        'source_unreadable' if unreadable else 'source_structure_unresolved')})
                plan['source_dispositions'] = [*plan.get('source_dispositions', []), *pending_sources]
                # Pending means there is retained actual observation, never
                # an invented reason to omit an unvisited page.
                if disposition_event_issues(plan, events):
                    raise error
                if fatal:
                    stop_reason = ('recon_budget_exceeded' if isinstance(fatal, ReconBudgetExceeded)
                                   else 'recon_phase_limit')
                phases.append({'phase': 'branch_completion', 'partial': True,
                    'verified_branch_count': len(accepted), 'pending_source_count': len(pending_sources)})
            else:
                raise error
        if omitted := _required_source_issues(plan, required_urls, events):
            error = ReconPlanError('最终计划遗漏给定或先前来源')
            error.safe_feedback = {'previous_plan': plan, 'issues': omitted}
            raise error
        return SimpleNamespace(final_response=json.dumps(plan, ensure_ascii=False), finish_reason="completed",
                                events=events, phase_metrics=phases,
                                usage_unknown=any(phase.get('usage_unknown', False) for phase in phases),
                                partial_metrics=partial_metrics,
                                stop_reason=stop_reason,
                                discovery_session_id=discovery_session_id,
                                discovery_checkpoint=discovery_checkpoint)
    except ReconError as exc:
        if isinstance(exc, (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled)):
            if getattr(exc, "aggregated", False):
                raise
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
        if getattr(exc, "phase_events", None) and not errors:
            events.extend(exc.phase_events)
            phases.append(exc.phase_metric)
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
    phase_limit = int(cfg.get("phase_call_limit") or 0)
    phase_usage = ReconBudget()  # this session only; the budget above spans the college
    cancel_event = cfg.get("cancel_event")
    branch_stop_event = cfg.get('branch_stop_event')
    seen_events: list[dict[str, Any]] = []
    submit_calls: set = set()
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
            def on_notification(notification: Any) -> None:
                accepted_plan = None
                gate_stop = None
                if (notification.method == "session.event"
                        and notification.payload.get("sessionId") == session_id):
                    event = notification.payload.get("event")
                    if isinstance(event, dict):
                        seen_events.append(event)
                        gate_stop = _watch_gate(event, submit_calls, cfg.get("gate_rejections"), session_id)
                        phase_usage.observe(event)
                        if budget is not None:
                            budget.observe(event)
                        if phase_limit and phase_usage.calls > phase_limit:
                            raise ReconPhaseLimitExceeded(
                                f"{_PHASE_NAMES.get(cfg.get('phase'), '侦察')}阶段已用 {phase_usage.calls} 次模型请求仍未完成"
                                f"（显式设置上限 {phase_limit}），已停止；复杂正常网站也可能需要更多步骤")
                        if (cfg.get('require_submission') and event.get('type') == 'tool/result'
                                and any(block.get('toolCallId') in submit_calls for block in
                                        ((event.get('data') or {}).get('message') or {}).get('content', []))):
                            from server.recon_contract import submitted_plan
                            accepted_plan = submitted_plan(seen_events)
                # A completed request can arrive after a sibling stops.
                # Count its actual usage before honoring cancellation.
                if ((cancel_event is not None and cancel_event.is_set())
                        or (branch_stop_event is not None and branch_stop_event.is_set())):
                    raise ReconCancelled("侦察已被调用方停止（超时）")
                if gate_stop is not None:
                    raise gate_stop
                if accepted_plan is not None:
                    raise _ReconSubmissionComplete(accepted_plan)
            run_kwargs["on_notification"] = on_notification
            return harness.run(prompt, **run_kwargs)
        except _ReconSubmissionComplete as completed:
            # The context manager closes this phase's runtime. A later request
            # may already be in flight; retain received usage without claiming
            # that the interrupted session's full bill is known.
            if budget is not None:
                budget.mark_usage_unknown()
            return SimpleNamespace(final_response=json.dumps(completed.plan, ensure_ascii=False),
                                   finish_reason='completed', events=seen_events.copy(),
                                   completion_reason='accepted_submission', usage_unknown=True)
        except ReconGateStopped as exc:
            # The stopping reply came after every model request of this session,
            # all of which the budget above already counted.
            exc.phase_events = seen_events
            exc.diagnostics = {"session_id": session_id, **run_metrics(seen_events)}
            exc.usage_unknown = False
            raise
        except (ReconBudgetExceeded, ReconPhaseLimitExceeded, ReconCancelled) as exc:
            if isinstance(exc, ReconCancelled) and budget is not None:
                budget.mark_usage_unknown()
            exc.diagnostics = {"model_calls": phase_usage.calls,
                               "token_usage": phase_usage.usage.copy(),
                               "budget_total": budget.snapshot() if budget else phase_usage.snapshot()}
            exc.usage_unknown = False
            raise
        except TimeoutError as exc:
            error = ReconError("Harness 初始化或请求超时；未完成有效侦察")
            error.usage_unknown = True
            raise error from exc


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
        # Optional per-phase reasoning effort (off, low, high, max); unset phases use DSH_EFFORT.
        "phase_efforts": {phase: value for phase, value in (
            ("discovery", os.environ.get("DSH_EFFORT_DISCOVERY")),
            ("branch", os.environ.get("DSH_EFFORT_BRANCH"))) if value},
        "api_key": api_key,
        "patches": (str(here / "recon_runtime.cordis.yml"),),
        "parallel_branches": os.environ.get("FACULTY_RECON_PARALLEL", "1") != "0",
        "template_reuse": os.environ.get("FACULTY_RECON_TEMPLATE_REUSE", "1") != "0",
        "require_submission": True,
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
    provided_urls: list[str] | None = None,
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
    if budget is not None:
        budget.provider = cfg.get("provider", "deepseek-official")
        budget.model = cfg.get("model", "deepseek-flash")
        raw_price = os.environ.get("FACULTY_RECON_PRICE_USD_PER_MILLION")
        if raw_price:
            from server.recon_pricing import rates
            budget.price_override = json.loads(raw_price)
            rates(budget.provider, budget.model, time.time(), budget.price_override)
    cfg["phase_call_limits"] = phase_call_limits or {}
    # Supplied links are mandatory leads, not the upper limit of discovery or
    # automatic evidence of faculty membership. Each needs a branch/disposition.
    cfg["provided_urls"] = list(provided_urls or [])
    cancel_event = threading.Event()
    cfg["cancel_event"] = cancel_event
    cfg["gate_rejections"] = {}  # shared by every phase session of this call
    cfg['gate_rejection_contexts'] = {}  # execution-owned provenance, never inferred from a session name
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
        error.diagnostics = {"session_id": sid}
        if budget is not None:
            budget.mark_usage_unknown()
            error.diagnostics.update(budget.delta(budget_before))
            error.usage_unknown = True
        if rejected := last_gate_rejections(cfg):
            error.diagnostics["last_gate_rejections"] = rejected
        raise error from exc
    except asyncio.CancelledError:
        cancel_event.set()  # the whole task timed out or the worker is stopping
        raise
    except (ReconBudgetExceeded, ReconPhaseLimitExceeded) as exc:
        if budget is not None:
            exc.diagnostics = {**getattr(exc, "diagnostics", {}), **budget.delta(budget_before)}
        exc.usage_unknown = False
        raise
    except ReconError as error:
        if budget is not None:
            if getattr(error, "usage_unknown", False):
                budget.mark_usage_unknown()
            error.diagnostics = {**getattr(error, "diagnostics", {}), **budget.delta(budget_before)}
        if rejected := last_gate_rejections(cfg):
            # Ending without an accepted plan must not erase the actual refusal.
            error.diagnostics = {**getattr(error, "diagnostics", {}), "last_gate_rejections": rejected}
            if isinstance(error, ReconPlanError):
                feedback = dict(getattr(error, 'safe_feedback', {}))
                existing = [item for item in feedback.get('issues', [])
                            if item != {'status': 'plan_contract_rejected'}]
                if not existing:
                    actual = [issue for entry in rejected for issue in entry['issues']]
                    feedback['issues'] = [{'status': 'plan_contract_rejected', 'gate_issues': actual}]
                records = list(feedback.get('phase_rejections', []))
                attributed = {record.get('session_id') for record in records}
                records.extend(_phase_rejection(entry['phase'], entry['session_id'], entry['target_url'],
                    [{'status': 'plan_contract_rejected', 'gate_issues': entry['issues']}])
                    for entry in rejected if entry.get('phase') in {'discovery', 'branch'}
                    and entry['session_id'] not in attributed)
                feedback['phase_rejections'] = records
                error.safe_feedback = feedback
        if isinstance(error, ReconPlanError):
            safe = dict(getattr(error, 'safe_feedback', {}))
            safe.setdefault('phase_rejections', [])
            error.safe_feedback = safe
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
    if omitted := _required_source_issues(plan, _required_source_urls(cfg), events):
        error = ReconPlanError('最终计划遗漏给定或先前来源')
        error.diagnostics = diagnostics
        error.safe_feedback = {'previous_plan': plan, 'known_urls': _required_source_urls(cfg),
                               'issues': omitted}
        raise error
    if str(Path(__file__).with_name("recon_runtime.cordis.yml")) in cfg.get("patches", ()):
        from server.recon_contract import disposition_event_issues
        issues = [*disposition_event_issues(plan, events), *selector_evidence_issues(plan, events)]
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
        diagnostics={**run_metrics(events), **{key: value for key, value in
                     (getattr(result, 'partial_metrics', None) or {}).items()
                     if key in {'model_calls', 'token_usage', 'budget_total'}},
                     **({"cost": budget.delta(budget_before)["cost"]} if budget else {}),
                     "usage_unknown": bool(getattr(result, 'usage_unknown', False) or
                         (budget and budget.delta(budget_before)["cost"]["unknown_calls"])),
                     **({'stop_reason': result.stop_reason} if getattr(result, 'stop_reason', None) else {}),
                     "phases": getattr(result, "phase_metrics", []),
                     "discovery_pages": discovery_evidence(
                         getattr(result, "discovery_session_id", sid).removesuffix("-discover"))},
        discovery_checkpoint=getattr(result, "discovery_checkpoint", None),
    )
