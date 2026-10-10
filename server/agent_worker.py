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
import math
import hashlib
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from server.agent_queue import AgentTaskQueue
from server.local_service_config import local_web_url
from server.recovery_policy import NON_STRUCTURAL, classify_issues
from server.agent_recon import (PlanHygieneError, ReconBudget, ReconBudgetExceeded,
                               ReconError, ReconPhaseLimitExceeded, ReconPlanError, ReconResult,
                               build_repair_feedback, reconnoiter, run_metrics, validate_model_plan)

__all__ = ["AgentWorker", "main"]

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
DEFAULT_OUTPUT_DIR = _HERE / "outputs"
DEFAULT_BASE_URL = local_web_url()

# 审计建议值：超过此时长即判定卡死（见 audits C3）
DEFAULT_MAX_TASK_SECONDS = 45 * 60
# Time kept after a recon for the deterministic harvest, and the shortest recon
# window worth starting. A repair that cannot fit is skipped with a clear
# status instead of being cut off by the task deadline midway.
DEFAULT_HARVEST_RESERVE_SECONDS = 600
MIN_RECON_SECONDS = 180
# Upper bound for the navigation census that cross-checks a finished harvest.
CROSS_CHECK_SECONDS = 240
IDLE_POLL_MIN_SECONDS = 0.2
IDLE_POLL_MAX_SECONDS = 2.0
DEFAULT_MAX_RECON_CALLS = 0
DEFAULT_MAX_RECON_TOKENS = 0
# Report (never stop) a college whose reconnaissance crosses these totals.
DEFAULT_RECON_ALERT_TOKENS = 0
DEFAULT_RECON_ALERT_CALLS = 0
DEFAULT_RECON_ALERT_COST_USD = 1.0
# Every queued college still starts at once; a new one waits only while the
# machine is short of memory and at least one college is already running.
DEFAULT_MIN_FREE_MEMORY_MB = 1024
# Optional operator-selected phase caps; legitimate complex sites have no
# default request-count ceiling. Task deadlines and audited progress remain.
DEFAULT_MAX_DISCOVERY_CALLS = 0
DEFAULT_MAX_BRANCH_CALLS = 0


def available_memory_mb() -> float | None:
    """Physical memory currently available, or None when it cannot be read."""
    if sys.platform == "win32":
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullAvailPhys / 2**20
        except (AttributeError, OSError):
            pass
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


# Audit items a new structure plan cannot change: they wait for a person on the
# review page (rare-surname names, OCR lines, image names, e-mail choices) or are
# properties of the images themselves. Sending them back to the scout only
# repeats the same harvest, so they never start a reconnaissance repair.
NOT_STRUCTURAL = NON_STRUCTURAL  # Compatibility for existing integrations.


def gate_rejection_summary(exc: BaseException) -> str:
    """What the submission gate last refused, as codes only, for a recon failure message."""
    rejected = (getattr(exc, "diagnostics", None) or {}).get("last_gate_rejections") or []
    codes = list(dict.fromkeys(f"{item.get('status')}/{item.get('failed_condition', '-')}"
                               for entry in rejected for item in entry.get("issues", [])))
    return f"；最后一次门禁打回：{', '.join(codes[:3])}" if codes else ""


class SleepGuard:
    """Keep Windows from sleeping while colleges are being harvested.

    A sleeping machine pauses every browser and model session; on waking the
    45-minute limit has already passed and the college starts over. Only system
    sleep is held off: the screen may still turn off or lock. Released as soon
    as no college is running. Other platforms: no effect.
    """

    _CONTINUOUS = 0x80000000
    _SYSTEM_REQUIRED = 0x00000001

    def __init__(self, platform: str | None = None) -> None:
        self.platform = platform or sys.platform
        self.held = False

    def update(self, busy: bool) -> None:
        if busy == self.held or self.platform != "win32":
            return
        try:
            import ctypes
            flags = self._CONTINUOUS | (self._SYSTEM_REQUIRED if busy else 0)
            if not ctypes.windll.kernel32.SetThreadExecutionState(flags):
                return
        except Exception:
            return
        self.held = busy
        print("[worker] 有学院在采集，已暂停系统睡眠" if busy else "[worker] 采集结束，恢复系统睡眠设置",
              flush=True)


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
        recon_alert_tokens: int = DEFAULT_RECON_ALERT_TOKENS,
        recon_alert_calls: int = DEFAULT_RECON_ALERT_CALLS,
        recon_alert_cost_usd: float = DEFAULT_RECON_ALERT_COST_USD,
        min_free_memory_mb: float = DEFAULT_MIN_FREE_MEMORY_MB,
        max_discovery_calls: int = DEFAULT_MAX_DISCOVERY_CALLS,
        max_branch_calls: int = DEFAULT_MAX_BRANCH_CALLS,
        harvest_reserve_seconds: float = DEFAULT_HARVEST_RESERVE_SECONDS,
    ) -> None:
        if harvest_reserve_seconds < 0:
            raise ValueError("harvest_reserve_seconds must not be negative")
        self.harvest_reserve_seconds = harvest_reserve_seconds
        if lease_renew_seconds <= 0:
            raise ValueError("lease_renew_seconds must be positive")
        if recon_alert_tokens < 0 or recon_alert_calls < 0 or min_free_memory_mb < 0:
            raise ValueError("alert thresholds and memory floor must not be negative")
        self.lease_renew_seconds = lease_renew_seconds
        self.recon_alert_tokens = recon_alert_tokens
        self.recon_alert_calls = recon_alert_calls
        if not math.isfinite(recon_alert_cost_usd) or recon_alert_cost_usd < 0:
            raise ValueError("recon cost alert must be finite and non-negative")
        self.recon_alert_cost_usd = recon_alert_cost_usd
        self.min_free_memory_mb = min_free_memory_mb
        if max_discovery_calls < 0 or max_branch_calls < 0:
            raise ValueError("phase call limits must not be negative")
        self.phase_call_limits = {"discovery": max_discovery_calls, "branch": max_branch_calls}
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
        if self.min_free_memory_mb:
            print(f"[worker] 内存保护：可用内存低于 {self.min_free_memory_mb:.0f}MB 时暂缓接新单"
                  "（已在运行的学院不受影响）", flush=True)
        active: set[asyncio.Task[None]] = set()
        running_ids: set[str] = set()  # this process's colleges; never reclaimed here
        presence_id = uuid.uuid4().hex
        last_heartbeat = float('-inf')
        memory_paused = False
        idle_delay = IDLE_POLL_MIN_SECONDS
        awake = SleepGuard()
        try:
            while True:
                awake.update(bool(active))
                if time.monotonic() - last_heartbeat >= 5:
                    self.queue.touch_worker(presence_id)
                    last_heartbeat = time.monotonic()
                if active and self.min_free_memory_mb:
                    free = available_memory_mb()
                    if free is not None and free < self.min_free_memory_mb:
                        if not memory_paused:
                            print(f"[worker] 可用内存 {free:.0f}MB 低于 {self.min_free_memory_mb:.0f}MB，"
                                  f"暂缓接新单（运行中 {len(active)} 个学院）", flush=True)
                            memory_paused = True
                        await asyncio.sleep(2)
                        continue
                if memory_paused:
                    print("[worker] 内存已恢复，继续接单", flush=True)
                    memory_paused = False
                task = self.queue.claim_task("server-worker", task_type="harvest",
                                             exclude_task_ids=running_ids)
                if task is None:
                    # Back off while the queue stays empty; a new ticket is still
                    # picked up within IDLE_POLL_MAX_SECONDS.
                    await asyncio.sleep(idle_delay)
                    idle_delay = min(idle_delay * 2, IDLE_POLL_MAX_SECONDS)
                    continue
                idle_delay = IDLE_POLL_MIN_SECONDS
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
            awake.update(False)
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

        deadline = started + self.max_task_seconds

        async def harvest() -> dict[str, Any]:
            return await self._harvest_with_repair(task, task_output_dir, deadline=deadline)

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
            await self._mark_failed(task, f"侦察失败：{exc}{gate_rejection_summary(exc)}")
            return

        elapsed = time.monotonic() - started
        ready = bool(report.get("delivery_ready"))
        print(f"[worker]   采集完成 {elapsed:.0f}s | delivery_ready={ready} | "
              f"total={report.get('total')} with_email={report.get('with_email')}", flush=True)

        if not ready or report.get("unresolved"):
            unresolved = report.get("unresolved") or []
            pdf_media = {item.get("media_url") for item in report.get("pdf_roster_evidence") or []}
            pdf_issues =[item for item in unresolved if str(item.get("status", "")).startswith("pdf_roster_")
                          or (item.get("media_url") in pdf_media and item.get("status") in {
                              "image_bio_low_confidence", "segment_name_needs_review",
                              "segment_email_ambiguous", "segment_duplicate_name"})]
            reason = ("审计未通过交付门禁：嵌入 PDF 名录尚未核验；需核对 PDF 人员与主名录的差额、"
                      "姓名冲突及采集范围。已保留本地证据，未导出。"
                      if any(item.get("status") == "embedded_pdf_roster_requires_review" for item in unresolved)
                      else f"审计未通过交付门禁：多人 PDF 名录已按页读取，仍有 {len(pdf_issues)} 项分段、"
                           "姓名或邮箱归属待复核；已保留原件、页码与行证据，未导出。"
                      if pdf_issues
                      else f"审计未通过交付门禁：unresolved={len(unresolved)} "
                           f"{[u.get('status') for u in unresolved[:3]]}")
            await self._mark_failed(
                task,
                reason,
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

    def _recon_window(self, deadline: float | None, attempt: int) -> float | None:
        """Seconds this recon may use, or None when a repair would not fit."""
        if deadline is None:
            return self.recon_timeout
        window = deadline - time.monotonic() - self.harvest_reserve_seconds
        if attempt > 0 and window < MIN_RECON_SECONDS:
            return None
        return max(MIN_RECON_SECONDS, min(self.recon_timeout, window))

    async def _harvest_with_repair(self, task: Any, output_dir: Path, *,
                                   deadline: float | None = None) -> dict[str, Any]:
        started = time.monotonic()
        browser_runs = []
        feedback = None
        report: dict[str, Any] = {"delivery_ready": False, "unresolved": []}
        output_dir.mkdir(parents=True, exist_ok=True)
        resumed = self._resume_plan(task, output_dir)
        attempts = resumed.diagnostics.pop("prior_attempts", []) if resumed else []
        budget = ReconBudget(
            max_calls=self.max_recon_calls, max_tokens=self.max_recon_tokens,
            alert_calls=self.recon_alert_calls, alert_tokens=self.recon_alert_tokens,
            alert_cost_usd=self.recon_alert_cost_usd,
            on_alert=lambda alert: self._record_usage_alert(task, output_dir, alert))
        budget.seed(attempts)

        async def capture(plan, directory, retry):
            capture_started = time.monotonic()
            result = await self._run_browser_plan(plan, directory)
            browser_runs.append({"attempt": int(directory.name.removeprefix("attempt-")), "retry": retry,
                "elapsed_seconds": round(time.monotonic() - capture_started, 3),
                "stage_seconds": result.get("stage_seconds", {}),
                "checkpoints": {key: value for key, value in (result.get("checkpoints") or {}).items()
                                if key not in {"plan_digest", "evidence_captured_at"}},
                "delivery_ready": bool(result.get("delivery_ready"))})
            return result

        def save_runtime(result):
            from server.harvest_runtime_report import build_runtime_report
            result["runtime_report"] = build_runtime_report(attempts, browser_runs,
                result.get("unresolved") or [], time.monotonic() - started, budget.snapshot())
            (output_dir / "runtime-report.json").write_text(json.dumps(
                result["runtime_report"], ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        verified_branches = None
        resume_discovery = None
        previous_contract_failure = None
        harvested_plan = None  # the plan whose capture produced the current report
        harvested_keys = set()  # every plan this task has already captured
        for attempt in range(self.max_recon_repairs + 1):
            attempt_dir = output_dir / f"attempt-{attempt + 1}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            from server.task_image_review import carry_review_decisions
            carry_review_decisions(output_dir, attempt_dir)
            recon = resumed if attempt == 0 else None
            window = self._recon_window(deadline, attempt)
            if window is None:
                remaining = max(0, int(deadline - time.monotonic()))
                print(f"[worker]   剩余 {remaining}s 不足一轮补缺加采集，停止补缺", flush=True)
                report = {**report, "unresolved": [*(report.get("unresolved") or []),
                          {"status": "repair_skipped_task_deadline", "remaining_seconds": remaining}]}
                break
            print(f"[worker]   侦察 {attempt + 1}… {task.url}", flush=True)
            try:
                provided = list(getattr(task, "roster_urls", None) or [])
                if attempt == 0 and recon is None and not provided:
                    # A cached plan was scoped by a site-wide discovery; a task
                    # with supplied roster links is scoped by those links instead.
                    recon = await self._cached_recon(task, output_dir)
                if recon is None:
                    kwargs = {"timeout_seconds": window,
                              "feedback": feedback, "budget": budget,
                              "phase_call_limits": self.phase_call_limits}
                    if verified_branches:
                        kwargs["verified_branches"] = verified_branches
                    if resume_discovery:
                        kwargs["resume_discovery"] = resume_discovery
                    if provided:
                        kwargs["provided_urls"] = provided
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
                if not classify_issues(feedback["issues"]).structure:
                    # A page fact the gate proved on two opens: another scout meets the same page.
                    print("[worker]   结构计划被页面本身挡住，换个时间再试", flush=True)
                    break
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
                status = ("recon_budget_exceeded" if isinstance(exc, ReconBudgetExceeded)
                          else "recon_phase_limit" if isinstance(exc, ReconPhaseLimitExceeded)
                          else "recon_failed")
                attempts.append({**getattr(exc, "diagnostics", {}), "attempt": attempt + 1,
                                 "status": status})
                self._save_recon_audit(output_dir, attempts, status,
                                      usage_unknown=getattr(exc, "usage_unknown", True))
                rejected = getattr(exc, "diagnostics", {}).get("last_gate_rejections")
                save_runtime({"unresolved": [{"status": status,
                                              **({"last_gate_rejections": rejected} if rejected else {})}]})
                raise
            if self._capture_plan_key(recon.plan) in harvested_keys:
                # Capture is deterministic for a plan: a plan this task already
                # captured, in any earlier round, would reproduce its report.
                print("[worker]   补缺后计划与已采集过的计划相同，不再重复采集", flush=True)
                attempts.append({**recon.summary(), "status": "repair_plan_unchanged"})
                self._save_recon_audit(output_dir, attempts, "repair_plan_unchanged")
                report["recon_attempts"] = attempts.copy()
                break
            (attempt_dir / "plan.json").write_text(
                json.dumps(recon.plan, ensure_ascii=False, indent=2), encoding="utf-8")
            attempts.append(recon.summary())
            self._save_recon_audit(output_dir, attempts, "recon_completed")
            self._save_active_plan(task, output_dir, recon, attempts)
            report = await capture(recon.plan, attempt_dir, 0)
            harvested_plan = recon.plan
            harvested_keys.add(self._capture_plan_key(recon.plan))
            local_attempts = []
            for retry in range(2):
                issues = report.get("unresolved") or []
                recovery = classify_issues(issues)
                if not recovery.access or recovery.stop:
                    break
                print("[worker]   访问失败，按页面断点补缺", flush=True)
                local_attempts.append({"retry": retry + 1, "issues": build_repair_feedback(recon.plan, report)["issues"],
                                       "model_calls": 0})
                report = await capture(recon.plan, attempt_dir, retry + 1)
            report["recon"] = attempts[-1]
            report["recon_attempts"] = attempts.copy()
            if budget.alerts:
                report["recon_usage_alerts"] = budget.alerts
            report["local_access_repairs"] = local_attempts
            report["recovery"] = classify_issues(report.get("unresolved") or []).summary()
            save_runtime(report)
            audit = report.get("audit_path")
            if audit and Path(audit).is_file():
                saved = json.loads(Path(audit).read_text(encoding="utf-8"))
                saved.update(recon=report["recon"], recon_attempts=report["recon_attempts"],
                             local_access_repairs=local_attempts, recovery=report["recovery"],
                             runtime_report=report["runtime_report"])
                if budget.alerts:
                    saved["recon_usage_alerts"] = budget.alerts
                Path(audit).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            if report.get("delivery_ready") and not report.get("unresolved"):
                from server.recon_plan_cache import ReconPlanCache
                try:
                    if not getattr(task, "roster_urls", None):
                        ReconPlanCache(self.output_dir / "plan-cache").put(
                            recon.plan, task.url, recon.diagnostics.get("discovery_pages", []))
                except (ValueError, OSError):
                    pass  # Unsupported interactions or missing evidence are researched again.
                await self._attach_cross_check(task, recon.plan, report, deadline)
                return report
            recovery = classify_issues(report.get("unresolved") or [])
            structural = recovery.structure
            if recovery.stop or not structural:
                break  # Only a person (review page) or the content itself can settle these.
            feedback = build_repair_feedback(recon.plan, {**report, "unresolved": self._with_branch_urls(
                structural, report.get("coverage_path"))})
            resume_discovery = None
            if not feedback["issues"]:
                break
            if attempt < self.max_recon_repairs:
                print(f"[worker]   审计未通过，返回浏览器补缺（{len(feedback['issues'])} 项）", flush=True)
                verified_branches = await self._unaffected_branches(recon.plan, feedback, attempt_dir)
                # Every issue names an accepted branch: the branch scouts repair
                # it, and a new discovery could only keep the same entries.
                if self._damaged_branches(recon.plan, feedback) is not None:
                    resume_discovery = recon.discovery_checkpoint
        if harvested_plan is not None:
            await self._attach_cross_check(task, harvested_plan, report, deadline)
        save_runtime(report)
        audit = report.get("audit_path")
        if audit and Path(audit).is_file():
            Path(audit).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    async def _census(self, entry_url: str, plan: dict, extra_urls: list[str] | None = None) -> dict:
        from server.browser_context import new_browser_context
        from server.headcount_check import census
        async with self._playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                return await census(await new_browser_context(browser), entry_url, plan,
                                    extra_urls=extra_urls or [])
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()

    async def _attach_cross_check(self, task: Any, plan: dict, report: dict, deadline: float | None) -> None:
        """Check a finished harvest against evidence the scout's plan did not choose.

        Field checks trace every record back to its captured page. A navigation
        census reads the site's own menu pages without the model; pager totals
        and stated staff counts come from the pages themselves. Differences are
        listed for a person and never change delivery.
        """
        if not report.get("coverage_path") or not Path(report["coverage_path"]).is_file():
            return
        from server.field_checks import check_records, page_text
        from server.headcount_check import compare, pager_totals, stated_counts
        coverage = json.loads(Path(report["coverage_path"]).read_text(encoding="utf-8"))
        snapshots = {item["url"]: Path(item["path"]) for item in coverage.get("snapshots", [])
                     if item.get("path") and Path(item["path"]).is_file()}
        records = (json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))
                   if report.get("records_path") and Path(report["records_path"]).is_file() else [])
        audit = report.get("audit_path")
        saved = json.loads(Path(audit).read_text(encoding="utf-8")) if audit and Path(audit).is_file() else None
        try:
            image_owned = {(item.get("assigned_name"), item.get("profile_url"))
                           for item in (saved or report).get("image_biography_evidence", [])}
            # A PDF roster record's fields come from the PDF, not the page text.
            image_owned |= {(record.get("name"), item.get("profile_url"))
                            for item in (saved or report).get("pdf_roster_evidence", [])
                            for record in item.get("records", [])}
            texts = {url: page_text(path.read_text(encoding="utf-8")) for url, path in snapshots.items()}
            report["field_check"] = check_records(
                [row for row in records if (row.get("姓名"), row.get("主页")) not in image_owned]
                + [{**row, "邮箱": ""} for row in records if (row.get("姓名"), row.get("主页")) in image_owned],
                texts)
        except Exception as exc:  # a check must never cost a delivery
            report["field_check"] = {"status": "unavailable", "error_type": type(exc).__name__}
        check = None
        if os.environ.get("HARVEST_CROSS_CHECK", "1") != "0":
            window = CROSS_CHECK_SECONDS if deadline is None else min(
                CROSS_CHECK_SECONDS, deadline - time.monotonic() - 30)
            try:
                if window < 30:
                    raise TimeoutError
                roster_pages = [(page["url"], snapshots[page["url"]].read_text(encoding="utf-8"), page["branch_url"])
                                for page in coverage.get("pages", []) if page.get("url") in snapshots]
                found = await asyncio.wait_for(
                    self._census(task.url, plan, list(getattr(task, "roster_urls", None) or [])), timeout=window)
                pages_html = found.pop("html", {})
                check = compare(found, [row.get("姓名", "") for row in records],
                                pager=pager_totals(roster_pages, coverage.get("roster_observations", [])),
                                stated=stated_counts(pages_html))
                detail = Path(report["coverage_path"]).with_name("cross-check.json")
                detail.write_text(json.dumps({**check, "census_pages": found.get("pages", [])},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
                check["detail_path"] = str(detail)
            except (asyncio.TimeoutError, TimeoutError):
                check = {"status": "unavailable", "summary": "人数核对超时或剩余时间不足，未完成"}
            except Exception as exc:
                check = {"status": "unavailable", "summary": "人数核对未完成", "error_type": type(exc).__name__}
            report["cross_check"] = check
            print(f"[worker]   人数核对：{check['summary']}", flush=True)
        if saved is not None:
            saved["field_check"] = report["field_check"]
            if check is not None:
                saved["cross_check"] = check
            Path(audit).write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _record_usage_alert(task: Any, root: Path, alert: dict[str, Any]) -> None:
        """Report unusual reconnaissance spend without stopping the college.

        Called from the Harness notification thread; it only prints and writes
        a small file, so it is safe to run there.
        """
        limits = "、".join(part for part in (
            f"${alert['alert_cost_usd']:.2f}" if alert.get("alert_cost_usd") else "",
            f"{alert['alert_tokens']:,} Token" if alert.get("alert_tokens") else "",
            f"{alert['alert_calls']} 次请求" if alert.get("alert_calls") else "") if part)
        known_usd = (alert.get("cost") or {}).get("known_usd")
        cost_text = f"、已核算约 ${known_usd:.4f}" if known_usd is not None else ""
        print(f"[worker]   ⚠ 侦察用量告警 {task.school}-{task.college}：已 {alert['calls']} 次请求{cost_text}、"
              f"{alert['total_tokens']:,} Token（告警线 {limits}）；未中断，"
              "详见任务目录 recon-usage-alert.json", flush=True)
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f"recon-usage-alert.{uuid.uuid4().hex}.tmp"
        staging.write_text(json.dumps({"task_id": getattr(task, "task_id", None), **alert},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
        staging.replace(root / "recon-usage-alert.json")

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
        recovery = classify_issues(issues)
        return bool(recovery.access and not (recovery.structure or recovery.review or recovery.stop))

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

    @staticmethod
    def _with_branch_urls(issues: list[dict], coverage_path: str | None) -> list[dict]:
        """Name the branch each issue was captured for.

        A later roster page ('.../fjs/1.htm') names its own branch; a profile
        page names every branch whose roster pages linked it, one issue each.
        """
        try:
            coverage = json.loads(Path(coverage_path).read_text(encoding="utf-8"))
        except (TypeError, OSError, ValueError):
            return issues
        owner = {page.get("url"): page.get("branch_url") for page in coverage.get("pages", [])
                 if page.get("branch_url")}
        linked = {url: sorted({owner[roster] for roster in rosters if roster in owner})
                  for url, rosters in (coverage.get("profile_sources") or {}).items()}
        mapped = []
        for item in issues:
            if item.get("branch_url"):
                mapped.append(item)
            elif item.get("url") in owner:
                mapped.append({**item, "branch_url": owner[item["url"]]})
            elif linked.get(item.get("url")):
                mapped.extend({**item, "branch_url": branch} for branch in linked[item["url"]])
            else:
                mapped.append(item)
        return mapped

    @staticmethod
    def _capture_plan_key(plan: dict) -> str:
        """What capture reads from a plan; spelling out a default changes nothing."""
        default_source = plan.get("source_kind") or "university_faculty_roster"
        branches = [{key: value for key, value in branch.items()
                     if not (key == "source_kind" and (value or default_source) == default_source)}
                    if isinstance(branch, dict) else branch
                    for branch in plan.get("branches") or []]
        return json.dumps({**plan, "source_kind": default_source, "branches": branches},
                          sort_keys=True, ensure_ascii=False)

    @staticmethod
    def _damaged_branches(plan: dict, feedback: dict) -> set | None:
        """Branch URLs the issues name, or None when any issue names no accepted branch."""
        branches = {branch["url"] for branch in plan["branches"]}
        damaged = {item.get("branch_url") or item.get("directory_url") or item.get("url")
                   for item in feedback.get("issues", [])}
        if not damaged or None in damaged or not damaged.issubset(branches):
            return None
        return damaged

    async def _unaffected_branches(self, plan: dict, feedback: dict, root: Path) -> dict | None:
        branches = {branch["url"]: branch for branch in plan["branches"]}
        # Unknown or unattributed profile failures cannot justify skipping a roster scout.
        damaged = self._damaged_branches(plan, feedback)
        if damaged is None:
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
                return await run_plan(plan, ctx, output_dir, state=HarvestState(output_dir.parent, plan),
                                      task_id=output_dir.parent.name)
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
            alert_path = path.with_name("recon-usage-alert.json")
            if alert_path.is_file():
                with contextlib.suppress(OSError, ValueError):
                    stats["recon_usage_alerts"] = [json.loads(alert_path.read_text(encoding="utf-8"))]
            runtime_path = path.with_name("runtime-report.json")
            if runtime_path.is_file():
                with contextlib.suppress(OSError, ValueError):
                    stats["runtime_report"] = json.loads(runtime_path.read_text(encoding="utf-8"))
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
    p.add_argument("--recon-alert-tokens", type=int, default=DEFAULT_RECON_ALERT_TOKENS,
                   help="单学院侦察累计 Token 超过此值时告警（不中断）；0 关闭")
    p.add_argument("--recon-alert-calls", type=int, default=DEFAULT_RECON_ALERT_CALLS,
                   help="单学院侦察累计模型请求超过此值时告警（不中断）；0 关闭")
    p.add_argument("--recon-alert-cost-usd", type=float, default=DEFAULT_RECON_ALERT_COST_USD,
                   help="按缓存/非缓存输入及输出核算的美元费用告警线（只告警，0 关闭）")
    p.add_argument("--max-discovery-calls", type=int, default=DEFAULT_MAX_DISCOVERY_CALLS,
                   help="入口发现阶段单次会话的模型请求上限，超过即停止该学院；0 关闭")
    p.add_argument("--max-branch-calls", type=int, default=DEFAULT_MAX_BRANCH_CALLS,
                   help="单个名录分支验证会话的模型请求上限，超过即停止该学院；0 关闭")
    p.add_argument("--min-free-memory-mb", type=float, default=DEFAULT_MIN_FREE_MEMORY_MB,
                   help="可用内存低于此值时暂缓接新单（已有学院在跑时才生效）；0 关闭")
    p.add_argument("--harvest-reserve-seconds", type=float, default=DEFAULT_HARVEST_RESERVE_SECONDS,
                   help="补缺侦察须为之后的确定性采集预留的秒数；不够时不再开始新一轮补缺")
    p.add_argument("--once", action="store_true", help="处理一个工单后退出")
    p.add_argument("--skip-self-check", action="store_true",
                   help="跳过启动自检（仅排障用；缺依赖时采集质量会静默下降）")
    return p


def main() -> None:
    if sys.platform == "win32" and sys.stdout is not None:
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8")

    args = _build_parser().parse_args()
    if args.skip_self_check:
        print("[worker] self-check skipped (--skip-self-check)", flush=True)
    else:
        from server.worker_selfcheck import run_self_check
        problems = run_self_check()
        try:  # shown on the Web page; a broken database must not hide the real reason
            AgentTaskQueue(args.db).record_self_check(problems)
        except Exception as exc:
            print(f"[worker] 自检结果未能写入队列数据库：{type(exc).__name__}: {exc}",
                  file=sys.stderr, flush=True)
        if problems:
            print("[worker] 启动自检未通过，未领取任何工单：", file=sys.stderr, flush=True)
            for problem in problems:
                print(f"[worker]   {problem}", file=sys.stderr, flush=True)
            sys.exit(2)
        # ASCII marker read by scripts/start_local_services.ps1.
        print("[worker] self-check passed", flush=True)
    worker = AgentWorker(
        output_dir=args.output_dir,
        base_url=args.base_url,
        db_path=args.db,
        max_task_seconds=args.max_task_seconds,
        recon_timeout=args.recon_timeout,
        max_recon_repairs=args.max_recon_repairs,
        max_recon_calls=args.max_recon_calls,
        max_recon_tokens=args.max_recon_tokens,
        recon_alert_tokens=args.recon_alert_tokens,
        recon_alert_calls=args.recon_alert_calls,
        recon_alert_cost_usd=args.recon_alert_cost_usd,
        min_free_memory_mb=args.min_free_memory_mb,
        harvest_reserve_seconds=args.harvest_reserve_seconds,
        max_discovery_calls=args.max_discovery_calls,
        max_branch_calls=args.max_branch_calls,
    )
    try:
        asyncio.run(worker.run_forever(once=args.once))
    except KeyboardInterrupt:
        print("\n[worker] 已停止", flush=True)


if __name__ == "__main__":
    main()
