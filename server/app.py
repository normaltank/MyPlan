import asyncio
import os
import sys
import uuid
import json
import sqlite3
from html import escape
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo
from typing import Optional, List

if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel, Field

if __package__:
    from .agent_queue import agent_queue, LeaseConflictError, failure_disposition
    from .access_control import AccountStore, Account
    from .image_table_routes import router as image_table_router
    from .task_image_review import (task_review_audit, review_items, prepare_review_resume,
                                    legacy_review_audits)
    from . import html_email_review, name_review, identity_review, media_kind_review, review_registry, image_bio_review
    from .image_bio_review import (render_review_page, save_review_decision,
                                   save_email_decision)
else:
    from agent_queue import agent_queue, LeaseConflictError, failure_disposition
    from access_control import AccountStore, Account
    from image_table_routes import router as image_table_router
    from task_image_review import (task_review_audit, review_items, prepare_review_resume,
                                   legacy_review_audits)
    import html_email_review
    import image_bio_review
    import review_registry
    import name_review
    import identity_review
    import media_kind_review
    from image_bio_review import (render_review_page, save_review_decision,
                                  save_email_decision)

app = FastAPI(title="高校师资智能全流程采集平台 (Local Agent Dispatch Center)", version="3.0.0")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
LEGACY_REVIEW_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scratch')
os.makedirs(OUTPUT_DIR, exist_ok=True)

account_store = AccountStore(agent_queue.db_path)

DEFAULT_MAX_BATCH_COLLEGES = 30


def _max_batch_colleges() -> int:
    """Largest batch one submission may enqueue (server/config.json ``max_batch_colleges``).

    Every queued college starts its own browser and Harness session at once,
    so an accidental paste of hundreds of lines must not reach the queue.
    """
    try:
        with open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8") as handle:
            value = int(json.load(handle).get("max_batch_colleges", DEFAULT_MAX_BATCH_COLLEGES))
    except (OSError, ValueError, TypeError, AttributeError):
        return DEFAULT_MAX_BATCH_COLLEGES
    return value if value > 0 else DEFAULT_MAX_BATCH_COLLEGES


SESSION_COOKIE = 'faculty_session'
CSRF_COOKIE = 'faculty_csrf'


# A reverse proxy on this machine also connects from 127.0.0.1. Anything it
# forwarded carries one of these headers and is treated as a remote request,
# so the proxy must send them (see docs/operations.md, 反向代理部署).
_FORWARDING_HEADERS = ('forwarded', 'x-forwarded-for', 'x-forwarded-host',
                       'x-forwarded-proto', 'x-real-ip')


def _local_request(request: Request) -> bool:
    if any(header in request.headers for header in _FORWARDING_HEADERS):
        return False
    return bool(request.client and request.client.host in {'127.0.0.1', '::1', 'testclient'})


def _direct_local_http(request: Request) -> bool:
    return (request.url.scheme == 'http' and _local_request(request) and
            request.url.hostname in {'127.0.0.1', 'localhost', 'testserver'})


def _safe_transport(request: Request) -> bool:
    return request.url.scheme == 'https' or _direct_local_http(request)


def _user(request: Request | None) -> Account:
    user = getattr(getattr(request, 'state', None), 'user', None)
    if user is None:
        raise HTTPException(status_code=401, detail='请先登录')
    return user


def _owner_filter(user: Account) -> int | None:
    return None if user.role == 'admin' else user.id


def _internal_user(request: Request | None) -> Account:
    """Allow direct Python callers used by worker/unit tests, never HTTP calls."""
    return _user(request) if request is not None else Account(0, 'internal', 'Internal', 'admin')


def _can_view(user: Account, owner_user_id: int | None) -> bool:
    return user.role == 'admin' or (owner_user_id is not None and owner_user_id == user.id)


def _public_task(task: dict) -> dict:
    return {key: value for key, value in task.items()
            if key not in {'lease_token', 'lease_until', 'worker_id', 'prompt_text', 'cluster_key'}}


@app.middleware('http')
async def access_gate(request: Request, call_next):
    path = request.url.path
    if path == '/api/auth/login':
        if not _safe_transport(request):
            return JSONResponse({'detail': '登录需要本机访问或 HTTPS'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    if path == '/api/auth/bootstrap':
        if not _direct_local_http(request):
            return JSONResponse({'detail': '首次设置仅限服务器本机'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    # Only the local worker may exchange a valid lease token for renewal/sync.
    if (path.startswith('/api/queue/renew/') or
            (path.startswith('/api/task/') and path.endswith('/sync'))):
        if not _local_request(request):
            return JSONResponse({'detail': '后台接口仅限本机'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    token = request.cookies.get(SESSION_COOKIE)
    user = account_store.resolve_session(token)
    request.state.user = user
    if path == '/':
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response
    if path.startswith('/api/') or path.startswith('/download/') or path.startswith('/review/'):
        if user is None:
            return JSONResponse({'detail': '请先登录'}, status_code=401)
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            csrf = request.headers.get('X-CSRF-Token')
            cookie_csrf = request.cookies.get(CSRF_COOKIE)
            if not csrf or csrf != cookie_csrf or not account_store.verify_csrf(token, csrf):
                return JSONResponse({'detail': '页面已过期，请刷新后重试'}, status_code=403)
            origin = request.headers.get('origin')
            if origin:
                try:
                    parsed = urlsplit(origin)
                    same_origin = (parsed.scheme == request.url.scheme and
                                   parsed.hostname == request.url.hostname and
                                   parsed.port == request.url.port)
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return JSONResponse({'detail': '请求来源不符'}, status_code=403)
        if path.startswith('/api/queue/claim/') and (user.role != 'admin' or not _local_request(request)):
            return JSONResponse({'detail': '无权认领'}, status_code=403)
        if path in {'/api/queue/pending', '/api/queue/mode'} and user.role != 'admin':
            return JSONResponse({'detail': '仅管理员可查看'}, status_code=403)
    response = await call_next(request)
    if path.startswith('/api/') or path.startswith('/download/') or path.startswith('/review/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

DISPATCH_MODE = "server_worker"  # Dedicated Harness worker; retired script harvester stays disabled.

class CollectRequest(BaseModel):
    school: str
    college: str
    url: Optional[str] = None

class BatchCollectRequest(BaseModel):
    items_text: str  # Multi-line text: 大学 学院 学院主页 [师资链接]
    confirm_warnings: bool = False  # the person has seen the data warnings

class TaskSyncRequest(BaseModel):
    records: List[dict] = []
    stats: dict = {}
    download_url: str = ""
    error: Optional[str] = None
    progress: int = 100
    worker_id: Optional[str] = None
    lease_token: Optional[str] = None

class LeaseRenewRequest(BaseModel):
    worker_id: str
    lease_seconds: int = Field(300, ge=30, le=900)
    lease_token: str


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    password: str
    role: str = 'employee'


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class OcrReviewRequest(BaseModel):
    id: str
    verified_text: str


class EmailReviewRequest(BaseModel):
    id: str
    selected_email: str


class MediaKindReviewRequest(BaseModel):
    id: str
    verdict: str


class HtmlEmailReviewRequest(BaseModel):
    id: str
    verdict: str
    selected_email: str = ''


class NameReviewRequest(BaseModel):
    id: str
    verdict: str
    verified_name: str = ''


class UniversityReviewDecisionRequest(BaseModel):
    school: str
    group_id: str
    verdict: str
    reason: str = ''
    expected_revision: int = Field(0, ge=0)


class UniversityPublishRequest(BaseModel):
    school: str


class IdentityReviewRequest(BaseModel):
    id: str
    verdict: str
    primary_url: str = ''
    assignments: dict[str, str] | None = None


@app.post('/api/auth/login')
async def login(req: LoginRequest, request: Request, response: Response):
    user = account_store.authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail='用户名或密码错误')
    token, csrf = account_store.create_session(user.id)
    secure = not _direct_local_http(request)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=secure,
                        samesite='strict', max_age=12 * 60 * 60, path='/')
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure,
                        samesite='strict', max_age=12 * 60 * 60, path='/')
    return {'id': user.id, 'username': user.username, 'display_name': user.display_name,
            'role': user.role}


@app.post('/api/auth/bootstrap')
async def bootstrap_admin(req: CreateUserRequest, request: Request, response: Response):
    if not _direct_local_http(request):
        raise HTTPException(status_code=403, detail='首次设置仅限服务器本机')
    try:
        user = account_store.bootstrap_admin(req.username, req.display_name, req.password)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='用户名已存在') from None
    except ValueError as exc:
        raise HTTPException(status_code=409 if str(exc) == '管理员已存在' else 422,
                            detail=str(exc)) from None
    token, csrf = account_store.create_session(user.id)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=False,
                        samesite='strict', max_age=12 * 60 * 60, path='/')
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=False,
                        samesite='strict', max_age=12 * 60 * 60, path='/')
    return {'id': user.id, 'username': user.username,
            'display_name': user.display_name, 'role': user.role}


@app.get('/api/auth/me')
async def current_user(request: Request):
    user = _user(request)
    return {'id': user.id, 'username': user.username,
            'display_name': user.display_name, 'role': user.role,
            'csrf_token': request.cookies.get(CSRF_COOKIE)}


@app.post('/api/auth/logout')
async def logout(request: Request, response: Response):
    account_store.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return {'status': 'logged_out'}


@app.post('/api/auth/change-password')
async def change_password(req: ChangePasswordRequest, request: Request, response: Response):
    user = _user(request)
    try:
        changed = account_store.change_password(user.id, req.old_password, req.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not changed:
        raise HTTPException(status_code=403, detail='当前密码不正确')
    response.delete_cookie(SESSION_COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    return {'status': 'password_changed'}


@app.post('/api/admin/users')
async def create_account(req: CreateUserRequest, request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可创建账号')
    try:
        user = account_store.create_user(req.username, req.display_name,
                                         req.password, req.role)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail='用户名已存在') from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {'id': user.id, 'username': user.username,
            'display_name': user.display_name, 'role': user.role}


@app.get('/api/reports/daily')
async def daily_report(request: Request, day: str | None = None):
    user = _user(request)
    selected = day or datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    try:
        counts = agent_queue.get_daily_counts(selected, owner_user_id=_owner_filter(user))
        if user.role == 'admin':
            counts['employees'] = [
                {'id': person.id, 'username': person.username,
                 'display_name': person.display_name,
                 **agent_queue.get_daily_counts(selected, owner_user_id=person.id)}
                for person in account_store.list_users() if person.role == 'employee']
        return counts
    except ValueError:
        raise HTTPException(status_code=400, detail='日期格式须为 YYYY-MM-DD') from None


def _verified_entry_url(value: str | None) -> str:
    entry = value.strip() if isinstance(value, str) else ''
    try:
        parsed = urlsplit(entry)
        allowed = parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
        allowed = allowed and parsed.username is None and parsed.password is None
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(status_code=400, detail='请填写有效的学院官网入口（http/https）')
    return entry

def export_batch_by_university(batch_id: str) -> list[dict]:
    """Publish each university only when its requested college set is ready."""
    from server.university_delivery import publish_delivery
    status = agent_queue.get_batch_status(batch_id)
    exported = []
    for scope in status.get("delivery_scopes", []):
        delivery = publish_delivery(agent_queue, batch_id, scope["school"], OUTPUT_DIR)
        if delivery.get("university_excel"):
            exported.append(delivery)
    return exported
@app.post("/api/collect")
async def start_single_collection(req: CollectRequest, request: Request = None):
    school = req.school.strip()
    college = req.college.strip()
    if not school or not college:
        raise HTTPException(status_code=400, detail='请填写学校与学院名称')
    url = _verified_entry_url(req.url)
    task = agent_queue.submit_task(school, college, url, mode="single",
                                   owner_user_id=_user(request).id)
    return {"task_id": task.task_id}

@app.post("/api/collect_batch")
async def start_batch_collection(req: BatchCollectRequest, request: Request = None):
    from server.intake import parse_batch
    parsed = parse_batch(req.items_text)
    if parsed.errors:
        raise HTTPException(status_code=400, detail="；".join(parsed.errors[:5]))
    if not parsed.colleges:
        raise HTTPException(status_code=400, detail="请输入院校名单")
    limit = _max_batch_colleges()
    if len(parsed.colleges) > limit:
        raise HTTPException(status_code=400,
                            detail=f'单次最多提交 {limit} 个学院（本次 {len(parsed.colleges)} 个），请分批提交')
    if parsed.warnings and not req.confirm_warnings:
        # Nothing is queued until a person has looked at suspicious rows.
        return JSONResponse(status_code=409, content={
            "detail": "名单里有需要确认的行", "warnings": parsed.warnings[:20]})
    batch_id = str(uuid.uuid4())[:8]
    items = [(entry.school, entry.college, entry.url, entry.roster_urls) for entry in parsed.colleges]
    agent_queue.submit_batch(items, batch_id, owner_user_id=_user(request).id)
    return {"batch_id": batch_id, "total_queued": len(items),
            "with_roster_links": sum(bool(entry.roster_urls) for entry in parsed.colleges)}

def present_task(data, scope):
    data["delivery_scope"] = scope
    if (data.get("status") == "running" and
            data.get("current_step") == "工单已进入队列，等待智能体接单..."):
        data["current_step"] = "后台已接单，正在核实官网结构"
    identity_review = any(item.get("requires_review") for item in scope.get("identity_conflicts", []))
    if identity_review:
        data["counts_basis"] = "profile_observations_not_verified_people"
        data["current_step"] = f"已保留 {data.get('stats', {}).get('total', 0)} 条页面观察；身份与人数待复核"
    if identity_review and data.get("batch_id"):
        data["university_review_url"] = _university_review_url(data["batch_id"], data["school"])
    if identity_review or (scope["state"] != "ready"
            and str(data.get("download_url", "")).startswith("/download/versions/")):
        data["download_url"] = ""
    if data.get('status') == 'failed':
        data['failure_disposition'] = failure_disposition(data.get('error'), data.get('stats'))
        task = agent_queue.get_task(data['task_id'])
        audit = task_review_audit(OUTPUT_DIR, task) if task else None
        if audit is not None:
            saved = json.loads(audit.read_text(encoding='utf-8'))
            source_issues = [item for item in saved.get('unresolved', [])
                             if item.get('status') == 'embedded_pdf_roster_requires_review'
                             or str(item.get('status', '')).startswith('pdf_roster_')]
            if source_issues:
                data['source_review_required'] = True
                links = []
                for item in source_issues:
                    for url in [item.get('url'), *item.get('media_urls', []), item.get('media_url')]:
                        if isinstance(url, str) and url.startswith(('https://', 'http://')) and url not in links:
                            links.append(url)
                data['source_review_urls'] = links
        if audit is not None and review_registry.has_review(audit):
            data['review_url'] = f"/review/task/{data['task_id']}"
    return data


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str, request: Request = None):
    task = agent_queue.get_task(task_id)
    if not task or not _can_view(_internal_user(request), task.owner_user_id):
        raise HTTPException(status_code=404, detail="Task not found")
    data = present_task(_public_task(task.to_dict()), agent_queue.get_delivery_scope(task.batch_id, task.school))
    data["queue_stats"] = agent_queue.get_queue_stats(owner_user_id=_owner_filter(_internal_user(request)))
    return data


def _review_task(task_id: str, request: Request):
    task = agent_queue.get_task(task_id)
    if not task or not _can_view(_user(request), task.owner_user_id):
        raise HTTPException(status_code=404, detail='Task not found')
    if task.status != 'failed':
        raise HTTPException(status_code=409, detail='该工单当前不在人工复核状态')
    audit = task_review_audit(OUTPUT_DIR, task)
    if audit is None or not review_registry.has_review(audit):
        raise HTTPException(status_code=404, detail='没有该工单的人工复核材料')
    return task, audit


@app.get('/review/task/{task_id}', response_class=HTMLResponse)
async def task_image_review_page(task_id: str, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        items = review_registry.collect(audit, 'image_bio_text', 'image_bio_email', 'name', 'identity',
                                        'html_email', 'media_kind')
        saved = identity_review.load_decisions(audit.parent / identity_review.DECISIONS_FILE)
        for item in items['identity']:
            decision = saved.get(item['id'], {})
            item['saved_verdict'] = decision.get('verdict', '')
        _mark_html_email_decisions(audit, task_id, items['html_email'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据不完整，请管理员检查审计文件') from exc
    rendered = render_review_page(items['image_bio_text'], items['image_bio_email'],
        decision_url=f'/api/task/{task_id}/review/ocr',
        email_decision_url=f'/api/task/{task_id}/review/email',
        retry_url=f'/api/task/{task_id}/review/retry',
        csrf_token=request.cookies.get(CSRF_COOKIE, ''), return_url='/',
        name_items=items['name'], name_decision_url=f'/api/task/{task_id}/review/name',
        identity_items=items['identity'], identity_decision_url=f'/api/task/{task_id}/review/identity',
        can_review_identity=_user(request).role == 'admin',
        html_email_items=items['html_email'], html_email_decision_url=f'/api/task/{task_id}/review/html-email',
        media_kind_items=items['media_kind'], media_kind_decision_url=f'/api/task/{task_id}/review/media-kind')
    if json.loads(audit.read_text(encoding="utf-8")).get("image_table_evidence"):
        rendered = rendered.replace("</html>", '<p><a href="/review/task/' + escape(task_id)
                                    + '/table">图片表格逐格复核</a></p></html>')
    return rendered


def _mark_html_email_decisions(audit: Path, task_id: str, items: list[dict]) -> None:
    saved = html_email_review.load_decisions(audit.parent / html_email_review.DECISIONS_FILE)
    for item in items:
        decision = saved.get(item['id'], {})
        if html_email_review.valid_decision(item, decision, task_id) is not None:
            item['saved_verdict'], item['saved_email'] = decision['verdict'], decision['email']


@app.post('/api/task/{task_id}/review/ocr', status_code=204)
async def save_task_ocr_review(task_id: str, payload: OcrReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in review_items(audit)[0] if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        save_review_decision(audit.parent / image_bio_review.TEXT_DECISIONS_FILE, item, payload.verified_text)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据无效') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/email', status_code=204)
async def save_task_email_review(task_id: str, payload: EmailReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in review_items(audit)[1] if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        save_email_decision(audit.parent / image_bio_review.EMAIL_DECISIONS_FILE, item,
                            payload.selected_email)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='邮箱选择无效') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/media-kind', status_code=204)
async def save_task_media_kind_review(task_id: str, payload: MediaKindReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in media_kind_review.collect_items(audit) if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='原图证据不存在或已变化')
        media_kind_review.save_decision(audit.parent / media_kind_review.DECISIONS_FILE,
                                       item, payload.verdict, _user(request).id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='原图类型复核无效') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/html-email', status_code=204)
async def save_task_html_email_review(task_id: str, payload: HtmlEmailReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in html_email_review.collect_items(audit) if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='邮箱复核证据不存在或已变化')
        html_email_review.save_decision(audit.parent / html_email_review.DECISIONS_FILE, item,
            payload.verdict, payload.selected_email, task_id=task_id, reviewed_by=_user(request).id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409,
            detail='只能选择页面上出现且无他人归属证据的地址；无个人邮箱须有全部地址属于他人的证据') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/name', status_code=204)
async def save_task_name_review(task_id: str, payload: NameReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in name_review.collect_items(audit) if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        name_review.save_decision(audit.parent / name_review.DECISIONS_FILE, item,
                                  payload.verdict, payload.verified_name)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='姓名复核无效') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/identity', status_code=204)
async def save_task_identity_review(task_id: str, payload: IdentityReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    user = _user(request)
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='身份合并与区分须由管理员核对来源后确认')
    try:
        item = next((item for item in identity_review.collect_items(audit) if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='身份复核证据不存在或已变化')
        identity_review.save_decision(audit.parent / identity_review.DECISIONS_FILE, item,
            payload.verdict, payload.primary_url, assignments=payload.assignments, reviewed_by=user.id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='身份决定必须覆盖全部来源，主来源须来自当前观察') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/retry')
async def retry_task_after_image_review(task_id: str, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        if not review_registry.review_complete(audit):
            raise HTTPException(status_code=409, detail='请先完成全部原图类型、图片文字、邮箱、姓名和身份复核；不可读文字或证据不足仍保持阻断')
        prepare_review_resume(audit, task_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据无效') from exc
    if not agent_queue.retry_failed_review_task(task_id):
        raise HTTPException(status_code=409, detail='工单状态已变化，请刷新页面')
    return {'status': 'queued', 'task_id': task_id}


def _university_review_url(batch_id: str | None, school: str) -> str:
    return f"/review/university/{quote(batch_id, safe='')}?school={quote(school, safe='')}" if batch_id else ''


def _university_scope(batch_id: str, school: str, request: Request):
    """A university scope is visible only to someone who can view every task in it."""
    from server.university_delivery import scope_identity_review
    status = agent_queue.get_batch_status(batch_id)
    user = _user(request)
    if (not status or school not in {task['school'] for task in status['subtasks']}
            or any(not _can_view(user, task['owner_user_id']) for task in status['subtasks'])):
        raise HTTPException(status_code=404, detail='大学交付范围不存在')
    return scope_identity_review(agent_queue, batch_id, school)


def _public_group(group: dict) -> dict:
    decision = group.get('decision')
    return {'group_id': group['group_id'], 'name': group['name'], 'applied': group['applied'],
            'can_be_distinct': group['merge_keys_distinct'],
            'decision': {key: decision[key] for key in ('revision', 'verdict', 'reason', 'reviewed_at')}
                        if decision else None,
            'observations': [{'task_id': o['task_id'], 'seq': o['seq'], 'college': o['college'],
                              'audit_sha256': o['audit_sha256'],
                              'record': {field: identity_review.field_value(o['record'], field)
                                         for field in ('姓名', '主页', '职称', '邮箱', '简介')}}
                             for o in group['observations']]}


@app.get('/review/university/{batch_id}', response_class=HTMLResponse)
async def university_identity_review_page(batch_id: str, request: Request, school: str = Query(...)):
    from server import university_identity_review as university_review
    review = _university_scope(batch_id, school, request)
    base = f"/api/university-review/{quote(batch_id, safe='')}"
    return university_review.render_page(school, review['scope'], review['groups'],
        can_decide=_user(request).role == 'admin', csrf_token=request.cookies.get(CSRF_COOKIE, ''),
        decision_url=base + '/decisions', publish_url=base + '/publish')


@app.get('/api/university-review/{batch_id}')
async def get_university_identity_review(batch_id: str, request: Request, school: str = Query(...)):
    review = _university_scope(batch_id, school, request)
    return {'school': school, 'state': review['scope']['state'],
            'groups': [_public_group(group) for group in review['groups']]}


@app.post('/api/university-review/{batch_id}/decisions')
async def save_university_identity_review(batch_id: str, payload: UniversityReviewDecisionRequest, request: Request):
    from server import university_identity_review as university_review
    review = _university_scope(batch_id, payload.school, request)
    user = _user(request)
    if user.role != 'admin':
        raise HTTPException(status_code=403, detail='跨学院身份判定须由管理员核对来源后确认')
    group = next((g for g in review['groups'] if g['group_id'] == payload.group_id), None)
    if group is None:
        raise HTTPException(status_code=409, detail='复核证据已变化，请刷新页面后重新核对')
    try:
        with agent_queue._connect() as con:
            decision, created = university_review.save_decision(con, payload.school, group, payload.verdict,
                payload.reason, reviewed_by=user.id, expected_revision=payload.expected_revision)
    except university_review.RevisionConflict as exc:
        raise HTTPException(status_code=409, detail='已有其他决定保存，请刷新页面后重新核对') from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='请填写判定理由；同一个人主页上的观察不能判为不同教师') from exc
    return JSONResponse(status_code=201 if created else 200, content={
        'group_id': decision['group_id'], 'revision': decision['revision'], 'verdict': decision['verdict']})


@app.post('/api/university-review/{batch_id}/publish')
async def publish_university_after_review(batch_id: str, payload: UniversityPublishRequest, request: Request):
    from server.university_delivery import publish_delivery
    _university_scope(batch_id, payload.school, request)
    delivery = await asyncio.to_thread(publish_delivery, agent_queue, batch_id, payload.school, OUTPUT_DIR)
    return {'state': delivery['delivery_scope']['state'], 'version': delivery.get('version'),
            'download_url': delivery.get('download_url')}


def _legacy_review_audit(audit_key: str, request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可查看历史复核材料')
    audit = next((path for key, path, _ in legacy_review_audits(LEGACY_REVIEW_ROOT)
                  if key == audit_key), None)
    if audit is None:
        raise HTTPException(status_code=404, detail='历史复核材料不存在')
    return audit


@app.get('/review/legacy', response_class=HTMLResponse)
async def legacy_image_review_list(request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可查看历史复核材料')
    rows = legacy_review_audits(LEGACY_REVIEW_ROOT)
    links = ''.join('<li><a href="/review/legacy/' + key + '">'
                    + escape(str(audit.get('school') or '历史材料')) + ' · '
                    + escape(str(audit.get('college') or path.parent.name))
                    + '</a> · ' + ('待复核（历史试跑）' if review_registry.has_review(path) else '已复核')
                    + '</li>' for key, path, audit in rows)
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<title>历史图片复核</title><body><h1>历史图片复核</h1>'
            '<p>这些验收材料没有员工工单归属，仅管理员可查看。</p><ul>'
            + (links or '<li>暂无历史图片复核材料</li>')
            + '</ul><a href="/">返回采集平台</a></body></html>')


@app.get('/review/legacy/{audit_key}', response_class=HTMLResponse)
async def legacy_image_review_page(audit_key: str, request: Request):
    audit = _legacy_review_audit(audit_key, request)
    try:
        ocr_items, email_items = review_items(audit)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='历史复核证据不完整') from exc
    return render_review_page(ocr_items, email_items,
        decision_url=f'/api/review/legacy/{audit_key}/ocr',
        email_decision_url=f'/api/review/legacy/{audit_key}/email',
        csrf_token=request.cookies.get(CSRF_COOKIE, ''), return_url='/review/legacy')


@app.post('/api/review/legacy/{audit_key}/ocr', status_code=204)
async def save_legacy_ocr_review(audit_key: str, payload: OcrReviewRequest, request: Request):
    audit = _legacy_review_audit(audit_key, request)
    try:
        item = next((item for item in review_items(audit)[0] if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        save_review_decision(audit.parent / image_bio_review.TEXT_DECISIONS_FILE, item, payload.verified_text)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据无效') from exc
    return Response(status_code=204)


@app.post('/api/review/legacy/{audit_key}/email', status_code=204)
async def save_legacy_email_review(audit_key: str, payload: EmailReviewRequest, request: Request):
    audit = _legacy_review_audit(audit_key, request)
    try:
        item = next((item for item in review_items(audit)[1] if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        save_email_decision(audit.parent / image_bio_review.EMAIL_DECISIONS_FILE, item,
                            payload.selected_email)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='邮箱选择无效') from exc
    return Response(status_code=204)

@app.get("/api/batch/latest")
async def get_latest_batch(request: Request = None):
    user = _internal_user(request)
    batch_ids = [r['id'] for r in agent_queue.get_request_history(50, owner_user_id=_owner_filter(user))
                 if r['kind'] == 'batch']
    if not batch_ids:
        raise HTTPException(status_code=404, detail="No batches found")
    return await get_batch_status(batch_ids[0], request)

@app.get("/api/batch/{batch_id}")
async def get_batch_status(batch_id: str, request: Request = None):
    status = agent_queue.get_batch_status(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail="Batch not found")
    user = _internal_user(request)
    if any(not _can_view(user, task['owner_user_id']) for task in status['subtasks']):
        raise HTTPException(status_code=404, detail="Batch not found")
    for scope in status["delivery_scopes"]:
        if any(item.get("requires_review") for item in scope.get("identity_conflicts", [])):
            scope["review_url"] = _university_review_url(batch_id, scope["school"])
    scopes = {scope["school"]: scope for scope in status["delivery_scopes"]}
    status["subtasks"] = [present_task(_public_task(task), scopes[task["school"]])
                          for task in status["subtasks"]]
    running = next((task for task in status["subtasks"] if task["status"] == "running"), None)
    if running:
        status["current_step"] = running["current_step"]
    univ_files = await asyncio.to_thread(export_batch_by_university, batch_id)
    status["university_files"] = univ_files
    if univ_files and len(univ_files) == len(status["delivery_scopes"]):
        status["download_url"] = univ_files[0]["download_url"]
    status["queue_stats"] = agent_queue.get_queue_stats(owner_user_id=_owner_filter(user))
    return status

@app.get("/api/queue/stats")
async def get_queue_stats(request: Request = None):
    return agent_queue.get_queue_stats(owner_user_id=_owner_filter(_internal_user(request)))

@app.get("/api/admin/failure-types")
async def get_failure_types(request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可查看失败类型统计')
    return agent_queue.get_failure_type_stats()

@app.get("/api/worker/status")
async def get_worker_status():
    return agent_queue.get_worker_status()

@app.get("/api/history")
async def get_request_history(request: Request = None, limit: int = Query(default=20, ge=1, le=50),
                              offset: int = Query(default=0, ge=0)):
    return {"requests": agent_queue.get_request_history(limit, owner_user_id=_owner_filter(_internal_user(request)),
                                                        offset=offset)}

@app.get("/api/queue/pending")
async def get_pending_tasks(request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可查看')
    pending = agent_queue.get_pending_tasks()
    return {
        "dispatch_mode": DISPATCH_MODE,
        "count": len(pending),
        "tasks": [_public_task(t.to_dict()) for t in pending]
    }

@app.post("/api/queue/claim/{task_id}")
async def claim_task(task_id: str, request: Request):
    if _user(request).role != 'admin' or not _local_request(request):
        raise HTTPException(status_code=403, detail='仅本机管理员可认领')
    task = agent_queue.claim_task(task_id, claimant="Antigravity (IDE Agent)")
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or not in queued state")
    return {"status": "claimed", "task": task.to_dict()}

@app.post("/api/queue/renew/{task_id}")
async def renew_task_lease(task_id: str, req: LeaseRenewRequest):
    if not agent_queue.renew_lease(task_id, req.worker_id, req.lease_seconds, lease_token=req.lease_token):
        raise HTTPException(status_code=409, detail="Lease no longer owned by worker")
    return {"status": "renewed"}

@app.post("/api/task/{task_id}/sync")
async def sync_task(task_id: str, req: TaskSyncRequest):
    # Database writes and workbook generation run off the event loop so one
    # sync cannot stall the page, lease renewals or other syncs.
    try:
        task = await asyncio.to_thread(
            agent_queue.sync_task_result,
            task_id=task_id,
            records=req.records,
            stats=req.stats,
            download_url=req.download_url,
            error=req.error,
            worker_id=req.worker_id,
            lease_token=req.lease_token
        )
    except LeaseConflictError:
        raise HTTPException(status_code=409, detail="Lease no longer owned by worker")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    from server.university_delivery import publish_delivery
    delivery = await asyncio.to_thread(publish_delivery, agent_queue, task.batch_id, task.school, OUTPUT_DIR)
    if task.mode == "single" and delivery.get("download_url"):
        with agent_queue._connect() as con:
            con.execute("UPDATE tasks SET download_url=? WHERE task_id=?", (delivery["download_url"], task_id))
        task = agent_queue.get_task(task_id)
    return {"status": "synced", "task": task.to_dict(),
            "delivery_scope": delivery["delivery_scope"],
            "university_excel": delivery.get("university_excel"),
            "university_download_url": delivery.get("download_url"),
            "delivery_version": delivery.get("version"),
            "current_snapshot_state": delivery.get("current_snapshot_state"),
            "current_identity_conflicts": delivery.get("current_identity_conflicts", [])}

@app.get("/api/queue/mode")
async def get_mode(request: Request):
    if _user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可查看')
    return {"dispatch_mode": DISPATCH_MODE}

@app.post("/api/queue/mode")
async def set_mode(mode: str, request: Request = None):
    if _internal_user(request).role != 'admin':
        raise HTTPException(status_code=403, detail='仅管理员可操作')
    global DISPATCH_MODE
    if mode == "server_worker":
        return {"status": "ok", "dispatch_mode": DISPATCH_MODE}
    raise HTTPException(status_code=400, detail="Only dedicated server_worker dispatch is supported; script dispatch is retired")

@app.get("/download/{filename:path}")
async def download_file(filename: str, request: Request = None):
    from pathlib import Path
    root = Path(OUTPUT_DIR).resolve()
    filepath = (root / filename).resolve()
    if not filepath.is_relative_to(root) or not filepath.is_file() or filepath.suffix != ".xlsx":
        raise HTTPException(status_code=404, detail="File not found")
    relative = filepath.relative_to(root)
    manifest_path = (filepath.parent / "manifest.json" if relative.parts[0] == "versions"
                     else filepath.with_suffix(".manifest.json"))
    user = _internal_user(request)
    if user.role != 'admin' and relative.parts[0] != 'versions':
        raise HTTPException(status_code=404, detail='File not found')
    if relative.parts[0] == "versions" or manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if user.role != 'admin':
                sources = manifest['college_snapshots']
                source_tasks = [agent_queue.get_task(source['task_id']) for source in sources]
                if (not source_tasks or any(task is None or not _can_view(user, task.owner_user_id)
                                            for task in source_tasks)):
                    raise HTTPException(status_code=404, detail='File not found')
            school = manifest["school"]
            from server.university_delivery import manifest_snapshot_is_safe
            safe = manifest_snapshot_is_safe(agent_queue, manifest)
            if relative.parts[0] == "versions":
                safe = safe and agent_queue.get_delivery_scope(manifest["scope"]["batch_id"], school)["state"] == "ready"
            else:
                snapshot = agent_queue.get_university_snapshot(school)
                safe = safe and not any(c["requires_review"] for c in snapshot["identity_conflicts"])
        except (OSError, ValueError, KeyError, TypeError):
            safe = False
        if not safe:
            raise HTTPException(status_code=409, detail="University delivery requires review")
    return FileResponse(filepath, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=filepath.name)

_TEMPLATES = Path(__file__).resolve().parent / "templates"
app.include_router(image_table_router)


def _page(name: str) -> str:
    """Static page from server/templates; read per request so edits need no restart.

    The ``/*base.css*/`` marker inside a page's <style> becomes the shared stylesheet.
    """
    page = (_TEMPLATES / name).read_text(encoding="utf-8")
    return page.replace("/*base.css*/", (_TEMPLATES / "base.css").read_text(encoding="utf-8"), 1)


def _bootstrap_page() -> str:
    return _page("bootstrap.html")


def _login_page() -> str:
    return _page("login.html")


@app.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request = None):
    if request is not None and getattr(request.state, 'user', None) is None:
        if not account_store.has_admin() and _direct_local_http(request):
            return _bootstrap_page()
        return _login_page()
    return _page("index.html")

if __name__ == "__main__":
    import uvicorn
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)
    print(f"Starting Agent Dispatch Center on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)
