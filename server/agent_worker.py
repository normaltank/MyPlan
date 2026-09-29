"""常驻队列消费者：把「Web 提单」接到「Agent 采集」，实现无人值守履约。

编排（全部在**同一条租约覆盖**之下）::

    取单 → [租约心跳启动] → 侦察(Agent) → 采集(确定性内核) → 同步回后台 → [心跳停止]

设计要点（每一条都是踩过的坑）：

1. **租约只有 300 秒，而侦察实测 28.7s（简单站）～187s（复杂站）。**
   故心跳必须**在侦察开始前**就启动，并一直覆盖到同步回后台结束。
   worker 直接在 tasks.db 上续租（不经 Web 服务）：Web 重启或数据库短暂繁忙
   只会让心跳重试，不会中断；只有确认失去租约才停止。
   同一进程绝不重新认领自己仍在运行的工单。

2. **`harness.run()` 是同步阻塞调用**，若直接在事件循环里跑，60 秒心跳一次都不会触发，
   300 秒后租约过期 → 第二个 worker 合法认领同一学院 → 两台 Chromium 同时打同一站点。
   `agent_recon.reconnoiter()` 已在内部用独立工作线程卸走。

3. **失败必须显式落库。** 若不标记失败，任务会停在 `running`，
   租约过期后被反复重领，形成无限重试。`sync_task_result(error=...)`
   带三要素围栏（worker_id + lease_token + 未过期），是唯一正确的失败出口。

4. **结构失败可有限补缺。** 无效计划或未解决审计在原租约内最多补缺两轮；
   运行环境错误与超时不反复调用模型，预算耗尽即终态失败。

用法::

    python -m server.agent_worker                    # 常驻
    python -m server.agent_worker --once             # 处理一个工单后退出（排障用）
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import hashlib
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from server.agent_queue import AgentTaskQueue
from server.agent_recon import (PlanHygieneError, ReconBudget, ReconBudgetExceeded,
                               ReconError, ReconPlanError, ReconResult,
                               build_repair_feedback, reconnoiter, run_metrics, validate_model_plan)

__all__ = ["AgentWorker", "main"]

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
DEFAULT_OUTPUT_DIR = _HERE / "outputs"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# 审计建议值：超过此时长即判定卡死（见 audits C3）
DEFAULT_MAX_TASK_SECONDS = 45 * 60
DEFAULT_MAX_RECON_CALLS = 0
DEFAULT_MAX_RECON_TOKENS = 0


class AgentWorker:
    """消费工单队列，按「侦察 → 采集 → 同步」履约。"""

    def __init__(
        self,
        *,
        output_dir: str | Path = DEFAULT_OUTPUT_DIR,
        base_url: str = DEFAULT_BASE_URL,
        db_path: str | Path | None = None,
        max_task_seconds: float = DEFAULT_MAX_TASK_SECONDS,
        recon_timeout: float = 900.0,
        max_recon_repairs: int = 2,
        max_recon_calls: int = DEFAULT_MAX_RECON_CALLS,
        max_recon_tokens: int = DEFAULT_MAX_RECON_TOKENS,
        lease_renew_seconds: float = 60,
    ) -> None:
        if lease_renew_seconds <= 0:
            raise ValueError("lease_renew_seconds must be positive")
        self.lease_renew_seconds = lease_renew_seconds
        self.output_dir = Path(output_dir)
        self.base_url = base_url.rstrip("/")
        self.queue = AgentTaskQueue(db_path)
        self.max_task_seconds = max_task_seconds
        self.recon_timeout = recon_timeout
        if max_recon_repairs < 0:
            raise ValueError("max_recon_repairs must be non-negative")
        self.max_recon_repairs = max_recon_repairs
        self.max_recon_calls = max_recon_calls
        self.max_recon_tokens = max_recon_tokens
        if (max_recon_calls == 0) != (max_recon_tokens == 0):
            raise ValueError("recon budget needs both limits, or both zero to disable")
        if max_recon_calls or max_recon_tokens:
            ReconBudget(max_calls=max_recon_calls, max_tokens=max_recon_tokens)
        self._delivery_locks: dict[str, asyncio.Lock] = {}

    # ── 主循环 ────────────────────────────────────────────────────────────
    async def run_forever(self, *, once: bool = False) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[worker] 已就绪，监听队列 {self.queue.db_path}", flush=True)
        print(f"[worker] 输出目录 {self.output_dir} | 后台 {self.base_url}", flush=True)
        print("[worker] 全量并行派发：每学院独立 Harness 会话，无项目级并发上限", flush=True)
        active: set[asyncio.Task[None]] = set()
        running_ids: set[str] = set()  # this process's colleges; never reclaimed here
        presence_id = uuid.uuid4().hex
        last_heartbeat = float('-inf')
        try:
            while True:
                if time.monotonic() - last_heartbeat >= 5:
                    self.queue.touch_worker(presence_id)
                    last_heartbeat = time.monotonic()
                task = self.queue.claim_task("server-worker", task_type="harvest",
                                             exclude_task_ids=running_ids)
                if task is None:
                    await asyncio.sleep(0.2)
                    continue
                if once:
                    await self._execute_task(task)
                    return
                running_ids.add(task.task_id)
                job = asyncio.create_task(self._execute_task(task), name=f"college-{task.task_id[:8]}")
                active.add(job)

                def finished(done: asyncio.Task[None], task_id: str = task.task_id) -> None:
                    active.discard(done)
                    running_ids.discard(task_id)

                job.add_done_callback(finished)
                # Let each claimed college start immediately; no application-level cap.
                await asyncio.sleep(0)
        finally:
            for job in active:
                job.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            self.queue.remove_worker(presence_id)

    async def _execute_task(self, task: Any) -> None:
        try:
            await self._process(task)
        except Exception as exc:  # One college must not stop the other agents.
            print(f"[worker] 未捕获异常 task={task.task_id[:8]}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            await self._mark_failed(task, f"{type(exc).__name__}: {exc}")

    # ── 单条工单 ──────────────────────────────────────────────────────────
    async def _process(self, task: Any) -> None:
        tag = f"{task.school}-{task.college}"
        print(f"\n[worker] 接单 {task.task_id[:8]} {tag}", flush=True)
        started = time.monotonic()

        if not task.url:
            await self._mark_failed(task, "工单缺少入口 URL")
            return

        task_output_dir = self.output_dir / "tasks" / task.task_id
        task_output_dir.mkdir(parents=True, exist_ok=True)

        from server.browser_batch_tool import LEASE_SECONDS, lease_keepalive, run_with_lease

        renew = self._lease_renewer(task)

        async def harvest() -> dict[str, Any]:
            return await self._harvest_with_repair(task, task_output_dir)

        try:
            report = await asyncio.wait_for(
                run_with_lease(
                    harvest,
                    task_id=task.task_id,
                    worker_id=task.worker_id or "server-worker",
                    lease_token=task.lease_token or "",
                    renew=renew,
                    interval_seconds=self.lease_renew_seconds,
                ),
                timeout=self.max_task_seconds,
            )
        except asyncio.TimeoutError:
            self._save_recon_audit(task_output_dir, None, "task_timeout", usage_unknown=True)
            await self._mark_failed(
                task, f"超过最大运行时长 {self.max_task_seconds:.0f}s，判定卡死")
            return
        except (ReconError, PlanHygieneError) as exc:
            await self._mark_failed(task, f"侦察失败：{exc}")
            return

        elapsed = time.monotonic() - started
        ready = bool(report.get("delivery_ready"))
        print(f"[worker]   采集完成 {elapsed:.0f}s | delivery_ready={ready} | "
              f"total={report.get('total')} with_email={report.get('with_email')}", flush=True)

        if not ready or report.get("unresolved"):
            unresolved = report.get("unresolved") or []
            await self._mark_failed(
                task,
                f"审计未通过交付门禁：unresolved={len(unresolved)} "
                f"{[u.get('status') for u in unresolved[:3]]}",
                failure_codes=[u.get("status") for u in unresolved],
            )
            return

        # Same-university reports may finish together. Serialize workbook writes
        # while keeping every college's recon and harvest fully concurrent.
        # The lease stays renewed while waiting for the lock and during sync.
        lock = self._delivery_locks.setdefault(task.school, asyncio.Lock())
        async with lease_keepalive(renew, interval_seconds=self.lease_renew_seconds,
                                   lease_seconds=LEASE_SECONDS) as lease:
            async with lock:
                if lease["error"]:
                    print(f"[worker]   ⚠ {tag} 等待同步时失去租约（{lease['error']}），"
                          "不提交结果；租约到期后将按断点重新接单", flush=True)
                    return
                if await self._sync(task, report):
                    print(f"[worker]   学院成果已同步 {tag}（{elapsed:.0f}s）", flush=True)

    def _lease_renewer(self, task: Any):
        """Renew this task's lease in tasks.db without depending on the Web process."""
        from server.browser_batch_tool import LEASE_SECONDS

        async def renew() -> bool:
            return await asyncio.to_thread(
                self.queue.renew_lease, task.task_id, task.worker_id or "server-worker",
                LEASE_SECONDS, lease_token=task.lease_token)

        return renew

    async def _harvest_with_repair(self, task: Any, output_dir: Path) -> dict[str, Any]:
        feedback = None
        output_dir.mkdir(parents=True, exist_ok=True)
        resumed = self._resume_plan(task, output_dir)
        attempts = resumed.diagnostics.pop("prior_attempts", []) if resumed else []
        budget = (ReconBudget(max_calls=self.max_recon_calls, max_tokens=self.max_recon_tokens)
                  if self.max_recon_calls else None)
        if budget:
            budget.seed(attempts)
        verified_branches = None
        resume_discovery = None
        previous_contract_failure = None
        for attempt in range(self.max_recon_repairs + 1):
            attempt_dir = output_dir / f"attempt-{attempt + 1}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            from server.task_image_review import carry_review_decisions
            carry_review_decisions(output_dir, attempt_dir)
            print(f"[worker]   侦察 {attempt + 1}… {task.url}", flush=True)
            try:
                recon = resumed if attempt == 0 else None
                if attempt == 0 and recon is None:
                    recon = await self._cached_recon(task, output_dir)
                if recon is None:
                    kwargs = {"timeout_seconds": self.recon_timeout,
                              "feedback": feedback, "budget": budget}
                    if verified_branches:
                        kwargs["verified_branches"] = verified_branches
                    if resume_discovery:
                        kwargs["resume_discovery"] = resume_discovery
                    recon = await reconnoiter(task.school, task.college, task.url, **kwargs)
            except (ReconPlanError, PlanHygieneError) as exc:
                # Rejected output may contain teacher data: never replay it or
                # interpolate the exception into feedback, artifacts, or logs.
                feedback = getattr(exc, "safe_feedback", {"issues": [{"status": "plan_contract_rejected"}]})
                resume_discovery = getattr(exc, "discovery_checkpoint", None)
                verified_branches = getattr(exc, "verified_branches", verified_branches)
                attempts.append({**getattr(exc, "diagnostics", {}),
                                 "attempt": attempt + 1, "status": "plan_contract_rejected",
                                 "issues": feedback.get("issues", [])})
                self._save_recon_audit(output_dir, attempts, "plan_contract_rejected")
                report = {"delivery_ready": False, "unresolved": feedback["issues"],
                          "recon_attempts": attempts.copy()}
                failure_state = (
                    tuple(sorted(json.dumps(issue, sort_keys=True, ensure_ascii=False)
                                 for issue in feedback["issues"])),
                    tuple(sorted(verified_branches or {})),
                    tuple(branch.get("url") for branch in
                          (resume_discovery or {}).get("plan", {}).get("branches", [])
                          if isinstance(branch, dict)),
                )
                if failure_state == previous_contract_failure:
                    print("[worker]   同一结构问题补缺后仍无进展，停止重复侦察", flush=True)
                    break
                previous_contract_failure = failure_state
                if attempt < self.max_recon_repairs:
                    print("[worker]   计划门禁未通过，重新核实结构计划", flush=True)
                continue
            except ReconError as exc:
                attempts.append({**getattr(exc, "diagnostics", {}), "attempt": attempt + 1,
                                 "status": ("recon_budget_exceeded" if isinstance(exc, ReconBudgetExceeded)
                                            else "recon_failed")})
                self._save_recon_audit(output_dir, attempts,
                                      "recon_budget_exceeded" if isinstance(exc, ReconBudgetExceeded)
                                      else "recon_failed",
                                      usage_unknown=getattr(exc, "usage_unknown", True))
                raise
            (attempt_dir / "plan.json").write_text(
                json.dumps(recon.plan, ensure_ascii=False, indent=2), encoding="utf-8")
            attempts.append(recon.summary())
            self._save_recon_audit(output_dir, attempts, "recon_completed")
            self._save_active_plan(task, output_dir, recon, attempts)
            report = await self._run_browser_plan(recon.plan, attempt_dir)
            local_attempts = []
            for retry in range(2):
                issues = report.get("unresolved") or []
                if not self._access_failures_only(issues):
                    break
                print("[worker]   访问失败，按页面断点补缺", flush=True)
                local_attempts.append({"retry": retry + 1, "issues": build_repair_feedback(recon.plan, report)["issues"],
                                       "model_calls": 0})
                report = await self._run_browser_plan(recon.plan, attempt_dir)
            report["recon"] = attempts[-1]
            report["recon_attempts"] = attempts.copy()
            report["local_access_repairs"] = local_attempts
            audit = report.get("audit_path")
            if audit and Path(audit).is_file():
                saved = json.loads(Path(audit).read_text(encoding="utf-8"))
                saved.update(recon=report["recon"], recon_attempts=report["recon_attempts"],
                             local_access_repairs=local_attempts)
                Path(audit).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            if report.get("delivery_ready") and not report.get("unresolved"):
                from server.recon_plan_cache import ReconPlanCache
                try:
                    ReconPlanCache(self.output_dir / "plan-cache").put(
                        recon.plan, task.url, recon.diagnostics.get("discovery_pages", []))
                except (ValueError, OSError):
                    pass  # Unsupported interactions or missing evidence are researched again.
                return report
            if self._access_failures_only(report.get("unresolved") or []):
                break
            if any(item.get("status") in {"distinct_profile_identity_unverified", "zero_person_delivery_requires_review"}
                   for item in report.get("unresolved", [])):
                break  # Identity and zero-person delivery review are not structure repair.
            feedback = build_repair_feedback(recon.plan, report)
            resume_discovery = None
            if not feedback["issues"]:
                break
            if attempt < self.max_recon_repairs:
                print(f"[worker]   审计未通过，返回浏览器补缺（{len(feedback['issues'])} 项）", flush=True)
                verified_branches = await self._unaffected_branches(recon.plan, feedback, attempt_dir)
        return report

    @staticmethod
    def _save_recon_audit(root: Path, attempts: list | None, status: str, *, usage_unknown: bool = False) -> None:
        path = root / "recon-attempts.json"
        if attempts is None:
            try:
                attempts = json.loads(path.read_text(encoding="utf-8"))["attempts"]
            except (OSError, ValueError, KeyError):
                attempts = []
        value = {"status": status, "usage_unknown": usage_unknown, "attempts": attempts}
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f"recon-attempts.{uuid.uuid4().hex}.tmp"
        staging.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        staging.replace(path)

    @staticmethod
    def _access_failures_only(issues: list) -> bool:
        failures = {item.get("url") for item in issues if item.get("status") in {
            "profile_navigation_failed", "browser_navigation_failed"}}
        branch_failures = {item.get("url") for item in issues if item.get("status") == "browser_navigation_failed"}
        branch_failures.update(item.get("branch_url") for item in issues
                               if item.get("status") == "browser_navigation_failed" and item.get("branch_url"))
        if not failures:
            return False
        for item in issues:
            status, url = item.get("status", ""), item.get("url") or item.get("主页")
            if status in {"profile_navigation_failed", "browser_navigation_failed"}:
                continue
            if status.startswith("fetch_failed") and url in failures:
                continue
            if status in {"branch_termination_unverified", "planned_branch_not_captured"} and url in branch_failures:
                continue
            return False
        return True

    def _save_active_plan(self, task: Any, root: Path, recon: ReconResult, attempts: list) -> None:
        from server.recon_plan_cache import _contract
        value = {"entry_url": task.url, "saved_at": recon.diagnostics.get("original_saved_at", time.time()), "contract": _contract(),
                 "task_id": getattr(task, "task_id", str(root.resolve())),
                 "plan": recon.plan, "recon": recon.summary(), "recon_attempts": attempts}
        value["digest"] = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        staging = root / f"active-plan.{uuid.uuid4().hex}.tmp"
        staging.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        staging.replace(root / "active-plan.json")

    def _resume_plan(self, task: Any, root: Path) -> ReconResult | None:
        from server.browser_batch_tool import _validate_plan
        from server.recon_plan_cache import _contract
        try:
            value = json.loads((root / "active-plan.json").read_text(encoding="utf-8"))
            digest = value.pop("digest")
            if hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest() != digest:
                return None
            if (value["task_id"] != getattr(task, "task_id", str(root.resolve()))
                    or value["entry_url"] != task.url or value["contract"] != _contract()
                    or not 0 <= time.time() - value["saved_at"] <= 86400):
                return None
            _validate_plan(value["plan"])
            validate_model_plan(value["plan"], school=task.school, college=task.college)
            return ReconResult(value["plan"], 0, "completed", f"resume-{uuid.uuid4().hex[:12]}", diagnostics={
                **run_metrics([]), "model_calls": 0, "resumed": True,
                "discovery_pages": value["recon"].get("discovery_pages", []),
                "prior_attempts": value["recon_attempts"], "original_saved_at": value["saved_at"]})
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def _validate_structure(self, entry: dict, evidence_dir: Path, *, verify_discovery: bool = True) -> dict:
        from server.recon_plan_cache import validate_cached_plan
        from server.browser_context import new_browser_context
        async with self._playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await new_browser_context(browser)
            try:
                return await asyncio.wait_for(validate_cached_plan(
                    entry, ctx, evidence_dir, verify_discovery=verify_discovery), timeout=120)
            except asyncio.TimeoutError:
                return {"valid": False, "reason": "verification_timeout", "model_calls": 0}
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()

    async def _cached_recon(self, task: Any, root: Path) -> ReconResult | None:
        from server.recon_plan_cache import ReconPlanCache
        library = ReconPlanCache(self.output_dir / "plan-cache")
        entry = library.get(task.school, task.college, task.url)
        if entry is None:
            return None
        started = time.monotonic()
        proof = await self._validate_structure(entry, root / "cache-evidence")
        library.record_validation(entry, proof)
        if not proof.get("valid"):
            (root / "cache-miss.json").write_text(json.dumps({key: value for key, value in proof.items()
                if key not in {"events", "issues"}}, ensure_ascii=False), encoding="utf-8")
            return None
        return ReconResult(entry["plan"], time.monotonic() - started, "completed", f"cache-{uuid.uuid4().hex[:12]}",
                           diagnostics={**run_metrics([]), "model_calls": 0, "cache_hit": True,
                                        **{key: proof[key] for key in ("discovery_pages", "evidence_root", "browser_tool_calls")
                                           if key in proof}})

    async def _unaffected_branches(self, plan: dict, feedback: dict, root: Path) -> dict | None:
        branches = {branch["url"]: branch for branch in plan["branches"]}
        issues = feedback.get("issues", [])
        # Unknown or profile-level failures cannot justify skipping a roster scout.
        damaged = {item.get("branch_url") or item.get("directory_url") or item.get("url") for item in issues}
        if not damaged or None in damaged or not damaged.issubset(branches):
            return None
        verified = {}
        for index, (url, branch) in enumerate(branches.items()):
            if url in damaged:
                continue
            proof = await self._validate_structure({"plan": {**plan, "branches": [branch]}},
                                                   root / f"reuse-branch-{index}", verify_discovery=False)
            if proof.get("valid"):
                verified[url] = {"branch": branch, "events": proof["events"], "evidence_root": proof.get("evidence_root")}
        return verified or None

    async def _run_browser_plan(self, plan: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        from server.browser_batch_tool import run_plan
        from server.harvest_state import HarvestState
        from server.browser_context import new_browser_context

        async with self._playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await new_browser_context(browser)
            try:
                return await run_plan(plan, ctx, output_dir, state=HarvestState(output_dir.parent, plan))
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()

    # ── 交付 ──────────────────────────────────────────────────────────────
    async def _sync(self, task: Any, report: dict[str, Any]) -> bool:
        from server.browser_batch_sync import sync_report

        audit_path = report.get("audit_path")
        if not audit_path or not Path(audit_path).is_file():
            await self._mark_failed(task, "审计文件缺失，无法同步")
            return False
        async with httpx.AsyncClient(base_url=self.base_url, timeout=120) as client:
            try:
                result = await sync_report(
                    task.task_id, audit_path,
                    worker_id=task.worker_id or "server-worker",
                    lease_token=task.lease_token or "",
                    output_root=self.output_dir,
                    db_path=self.queue.db_path,
                    client=client,
                )
            except Exception as exc:
                await self._mark_failed(task, f"同步回后台失败：{type(exc).__name__}: {exc}")
                return False
        if result.get("university_excel"):
            print(f"[worker]   大学版本已就绪：{result['university_excel']}", flush=True)
        else:
            print(f"[worker]   大学交付范围状态：{result.get('delivery_scope', {}).get('state', 'collecting')}", flush=True)
        return True

    # ── 失败出口（三要素围栏）────────────────────────────────────────────
    async def _mark_failed(self, task: Any, reason: str,
                           failure_codes: list[str | None] | None = None) -> None:
        """把工单标记为终态失败。

        注意：**绝不修改内存里的 `task.status`。**
        `task_done()` 会检查该字段——若在此处改成 "failed"，它会再次调用
        `sync_task_result`；而本方法落库时已把 `lease_token` 清空，
        第二次调用必然撞上三要素围栏抛出 `LeaseConflictError`。
        保持它停留在 "running"，`task_done()` 即自然 no-op。
        """
        print(f"[worker]   ❌ {task.school}-{task.college}: {reason}", flush=True)
        try:
            stats = {"total": 0, "with_email": 0, "no_email": 0}
            if failure_codes:
                stats["failure_codes"] = sorted({code for code in failure_codes
                                                  if isinstance(code, str)})
            path = self.output_dir / "tasks" / task.task_id / "recon-attempts.json"
            if path.is_file():
                stats.update(recon_audit_path=str(path),
                             recon_audit_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.queue.sync_task_result(
                task.task_id, [], stats,
                "", reason,
                worker_id=task.worker_id, lease_token=task.lease_token,
            )
        except Exception as exc:
            # 租约已过期 / 被他人接管 —— 记录但不改状态，交给租约回收
            print(f"[worker]   （标记失败未生效：{type(exc).__name__}: {exc}）", flush=True)

    # ── 工具 ──────────────────────────────────────────────────────────────
    @staticmethod
    def _playwright() -> Any:
        from playwright.async_api import async_playwright

        return async_playwright()


# ── CLI ───────────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="dsh Agent 工单消费者")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--db", default=None, help="tasks.db 路径（默认与 agent_queue 一致）")
    p.add_argument("--max-task-seconds", type=float, default=DEFAULT_MAX_TASK_SECONDS)
    p.add_argument("--recon-timeout", type=float, default=900.0)
    p.add_argument("--max-recon-repairs", type=int, default=2, help="审计失败后的浏览器补缺轮数")
    p.add_argument("--max-recon-calls", type=int, default=DEFAULT_MAX_RECON_CALLS)
    p.add_argument("--max-recon-tokens", type=int, default=DEFAULT_MAX_RECON_TOKENS)
    p.add_argument("--once", action="store_true", help="处理一个工单后退出")
    return p


def main() -> None:
    if sys.platform == "win32" and sys.stdout is not None:
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args()
    worker = AgentWorker(
        output_dir=args.output_dir,
        base_url=args.base_url,
        db_path=args.db,
        max_task_seconds=args.max_task_seconds,
        recon_timeout=args.recon_timeout,
        max_recon_repairs=args.max_recon_repairs,
        max_recon_calls=args.max_recon_calls,
        max_recon_tokens=args.max_recon_tokens,
    )
    try:
        asyncio.run(worker.run_forever(once=args.once))
    except KeyboardInterrupt:
        print("\n[worker] 已停止", flush=True)


if __name__ == "__main__":
    main()
