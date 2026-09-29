"""Local live-request evidence shared by roster and pagination readiness checks."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestTracker:
    pending: set[Any] = field(default_factory=set)
    activity: int = 0
    available: bool = False
    navigation_observed: bool = False
    failures: int = 0
    failed_requests: list[tuple[str, str, int | None]] = field(default_factory=list)
    document_response: Any = None  # latest main-frame navigation response


def install_request_tracking(page: Any) -> RequestTracker:
    """Install before goto; repeated calls share the same tracker and listeners.

    A previously reached Playwright networkidle state is not evidence that no
    later request is pending. This records local objects and counters only.
    """
    existing = getattr(page, "_faculty_request_tracking", None)
    if isinstance(existing, RequestTracker):
        return existing
    tracker = RequestTracker()
    if page is None or not callable(getattr(page, "on", None)):
        return tracker

    def started(request):
        tracker.activity += 1
        tracker.pending.add(request)
        try:
            if request.is_navigation_request() and request.frame == page.main_frame:
                tracker.navigation_observed = True
                tracker.failures = 0
                tracker.failed_requests.clear()
        except Exception:
            # Service-worker requests may have no frame; they still count.
            pass

    def finished(request):
        if request in tracker.pending:
            tracker.activity += 1
        tracker.pending.discard(request)

    answered = set()  # requests already recorded with their HTTP error status

    def failed(request):
        # Chromium aborts a 404 subresource after its response arrives; that is
        # one failure, already recorded with its status, not a second one.
        if request not in answered:
            tracker.failures += 1
            tracker.failed_requests.append((getattr(request, "url", ""),
                                            getattr(request, "resource_type", ""), None))
        finished(request)

    def response_received(response):
        try:
            if response.request.is_navigation_request() and response.frame == page.main_frame:
                tracker.document_response = response
        except Exception:
            pass
        if response.status >= 400:
            answered.add(response.request)
            tracker.failures += 1
            tracker.failed_requests.append((response.url, response.request.resource_type, response.status))

    try:
        page.on("request", started)
        page.on("requestfinished", finished)
        page.on("requestfailed", failed)
        page.on("response", response_received)
        setattr(page, "_faculty_request_tracking", tracker)
        tracker.available = True
    except Exception:
        tracker.available = False
    return tracker
