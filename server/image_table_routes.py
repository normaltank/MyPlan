"""Owner-scoped human OCR edits, isolated from university identity decisions."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()


class TableReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    verified_text: str | None = Field(default=None, max_length=40000)
    expected_count: int | None = Field(default=None, ge=1)


@router.get("/review/task/{task_id}/table", response_class=HTMLResponse)
def table_page(task_id: str, request: Request):
    from server import app as web
    from server import image_table_review
    _, audit = web._review_task(task_id, request)
    try:
        return image_table_review.render(audit, task_id, request.cookies.get(web.CSRF_COOKIE, ""))
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, "表格证据已变化或不完整") from exc


@router.post("/api/task/{task_id}/review/table", status_code=204)
def table_decide(task_id: str, payload: TableReviewRequest, request: Request):
    from server import app as web
    from server import image_table_review
    _, audit = web._review_task(task_id, request)
    if (payload.verified_text is None) == (payload.expected_count is None):
        raise HTTPException(422, "只能提交格内文字或整图人数之一")
    try:
        image_table_review.save(audit, payload.id, verified_text=payload.verified_text,
                                expected_count=payload.expected_count, reviewed_by=web._user(request).id)
    except (ValueError, OSError, KeyError) as exc:
        raise HTTPException(409, "表格证据、格子或人数已变化，请刷新核对") from exc
