import os
import sys
import re
import uuid
import json
import sqlite3
from html import escape
from datetime import datetime
from urllib.parse import urlsplit
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

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel
import pandas as pd

if __package__:
    from .agent_queue import agent_queue, AgentTask, LeaseConflictError
    from .access_control import AccountStore, Account
    from .autonomous_agent import AutonomousBrowserAgent
    from .task_image_review import (task_review_audit, has_image_review, review_items,
                                    review_complete, prepare_review_resume,
                                    legacy_review_audits)
    from .image_bio_review import (render_review_page, save_review_decision,
                                   save_email_decision)
else:
    from agent_queue import agent_queue, AgentTask, LeaseConflictError
    from access_control import AccountStore, Account
    from autonomous_agent import AutonomousBrowserAgent
    from task_image_review import (task_review_audit, has_image_review, review_items,
                                   review_complete, prepare_review_resume,
                                   legacy_review_audits)
    from image_bio_review import (render_review_page, save_review_decision,
                                  save_email_decision)

app = FastAPI(title="高校师资智能全流程采集平台 (Local Agent Dispatch Center)", version="3.0.0")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
LEGACY_REVIEW_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scratch')
os.makedirs(OUTPUT_DIR, exist_ok=True)

agent = AutonomousBrowserAgent()
account_store = AccountStore(agent_queue.db_path)

SESSION_COOKIE = 'faculty_session'
CSRF_COOKIE = 'faculty_csrf'


def _local_request(request: Request) -> bool:
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
    items_text: str  # Multi-line text

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
    lease_seconds: int = 300
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
    lines = [l.strip() for l in req.items_text.split('\n') if l.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="请输入院校名单")
    
    batch_id = str(uuid.uuid4())[:8]
    items = []
    for number, line in enumerate(lines, 1):
        parts = line.split()
        if len(parts) != 3:
            raise HTTPException(status_code=400, detail=f'第 {number} 行需填写高校、学院和官网入口')
        try:
            entry = _verified_entry_url(parts[2])
        except HTTPException:
            raise HTTPException(status_code=400, detail=f'第 {number} 行需有效的学院官网入口') from None
        items.append((parts[0], parts[1], entry))

    agent_queue.submit_batch(items, batch_id, owner_user_id=_user(request).id)
    return {"batch_id": batch_id, "total_queued": len(items)}

def present_task(data, scope):
    data["delivery_scope"] = scope
    if (data.get("status") == "running" and
            data.get("current_step") == "工单已进入队列，等待智能体接单..."):
        data["current_step"] = "后台已接单，正在核实官网结构"
    identity_review = any(item.get("requires_review") for item in scope.get("identity_conflicts", []))
    if identity_review:
        data["counts_basis"] = "profile_observations_not_verified_people"
        data["current_step"] = f"已保留 {data.get('stats', {}).get('total', 0)} 条页面观察；身份与人数待复核"
    if identity_review or (scope["state"] != "ready"
            and str(data.get("download_url", "")).startswith("/download/versions/")):
        data["download_url"] = ""
    if data.get('status') == 'failed':
        task = agent_queue.get_task(data['task_id'])
        audit = task_review_audit(OUTPUT_DIR, task) if task else None
        if audit is not None and has_image_review(audit):
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
        raise HTTPException(status_code=409, detail='该工单当前不在图片复核状态')
    audit = task_review_audit(OUTPUT_DIR, task)
    if audit is None or not has_image_review(audit):
        raise HTTPException(status_code=404, detail='没有该工单的图片复核材料')
    return task, audit


@app.get('/review/task/{task_id}', response_class=HTMLResponse)
async def task_image_review_page(task_id: str, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        ocr_items, email_items = review_items(audit)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据不完整，请管理员检查审计文件') from exc
    return render_review_page(ocr_items, email_items,
        decision_url=f'/api/task/{task_id}/review/ocr',
        email_decision_url=f'/api/task/{task_id}/review/email',
        retry_url=f'/api/task/{task_id}/review/retry',
        csrf_token=request.cookies.get(CSRF_COOKIE, ''), return_url='/')


@app.post('/api/task/{task_id}/review/ocr', status_code=204)
async def save_task_ocr_review(task_id: str, payload: OcrReviewRequest, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        item = next((item for item in review_items(audit)[0] if item['id'] == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail='复核证据不存在')
        save_review_decision(audit.parent / 'image_bio_reviews.json', item, payload.verified_text)
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
        save_email_decision(audit.parent / 'image_bio_email_reviews.json', item,
                            payload.selected_email)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='邮箱选择无效') from exc
    return Response(status_code=204)


@app.post('/api/task/{task_id}/review/retry')
async def retry_task_after_image_review(task_id: str, request: Request):
    _, audit = _review_task(task_id, request)
    try:
        ocr_items, email_items = review_items(audit)
        if not review_complete(audit, ocr_items, email_items):
            raise HTTPException(status_code=409, detail='请先完成全部图片文字和邮箱复核')
        prepare_review_resume(audit, task_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=409, detail='复核证据无效') from exc
    if not agent_queue.retry_failed_review_task(task_id):
        raise HTTPException(status_code=409, detail='工单状态已变化，请刷新页面')
    return {'status': 'queued', 'task_id': task_id}


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
                    + '</a> · ' + ('待复核（历史试跑）' if has_image_review(path) else '已复核')
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
        save_review_decision(audit.parent / 'image_bio_reviews.json', item, payload.verified_text)
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
        save_email_decision(audit.parent / 'image_bio_email_reviews.json', item,
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
    scopes = {scope["school"]: scope for scope in status["delivery_scopes"]}
    status["subtasks"] = [present_task(_public_task(task), scopes[task["school"]])
                          for task in status["subtasks"]]
    running = next((task for task in status["subtasks"] if task["status"] == "running"), None)
    if running:
        status["current_step"] = running["current_step"]
    univ_files = export_batch_by_university(batch_id)
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
    try:
        task = agent_queue.sync_task_result(
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
    delivery = publish_delivery(agent_queue, task.batch_id, task.school, OUTPUT_DIR)
    if task.mode == "single" and delivery.get("download_url"):
        with agent_queue._connect() as con:
            con.execute("UPDATE tasks SET download_url=? WHERE task_id=?", (delivery["download_url"], task_id))
        task = agent_queue.get_task(task_id)
    return {"status": "synced", "task": task.to_dict(),
            "delivery_scope": delivery["delivery_scope"],
            "university_excel": delivery.get("university_excel"),
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

def _bootstrap_page() -> str:
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>首次设置 · 高校师资采集平台</title><style>body{font:16px system-ui,sans-serif;background:#f4f6fb;color:#1e293b;margin:0;display:grid;min-height:100vh;place-items:center}main{background:white;box-shadow:0 12px 40px #1e293b14;border-radius:18px;padding:32px;width:min(380px,calc(100vw - 48px))}h1{font-size:22px;margin:0 0 8px}p{color:#64748b;font-size:14px}label{display:block;font-size:14px;font-weight:600;margin:16px 0 6px}input{box-sizing:border-box;width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:9px;font-size:16px}button{width:100%;padding:12px;border:0;border-radius:9px;background:#4f46e5;color:white;font-size:15px;font-weight:700;margin-top:20px;cursor:pointer}#message{min-height:22px;color:#b91c1c}</style></head>
<body><main><h1>首次创建管理员</h1><p>此页面仅能从服务器本机打开。请设置管理员账号；之后可在平台内创建员工账号。</p>
<form id="setup"><label for="username">登录名（小写英文）</label><input id="username" autocomplete="username" required>
<label for="displayName">显示名称</label><input id="displayName" required>
<label for="password">密码（至少 12 位）</label><input id="password" type="password" autocomplete="new-password" required>
<label for="confirm">确认密码</label><input id="confirm" type="password" autocomplete="new-password" required>
<button type="submit">创建并登录</button><p id="message" role="alert"></p></form></main>
<script>document.getElementById('setup').addEventListener('submit',async event=>{event.preventDefault();const password=document.getElementById('password').value;if(password!==document.getElementById('confirm').value){document.getElementById('message').textContent='两次密码不一致';return}const button=event.target.querySelector('button');button.disabled=true;const response=await fetch('/api/auth/bootstrap',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value,display_name:document.getElementById('displayName').value,password})});if(response.ok){location.reload();return}const data=await response.json();document.getElementById('message').textContent=data.detail||'创建失败';button.disabled=false});</script></body></html>"""


def _login_page() -> str:
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · 高校师资采集平台</title>
<style>body{font:16px system-ui,sans-serif;background:#f4f6fb;color:#1e293b;margin:0;display:grid;min-height:100vh;place-items:center}main{background:white;box-shadow:0 12px 40px #1e293b14;border-radius:18px;padding:32px;width:min(360px,calc(100vw - 48px))}h1{font-size:22px;margin:0 0 8px}p{color:#64748b;font-size:14px}label{display:block;font-size:14px;font-weight:600;margin:18px 0 6px}input{box-sizing:border-box;width:100%;padding:11px;border:1px solid #cbd5e1;border-radius:9px;font-size:16px}button{width:100%;padding:12px;border:0;border-radius:9px;background:#4f46e5;color:white;font-size:15px;font-weight:700;margin-top:20px;cursor:pointer}#message{min-height:22px;color:#b91c1c}</style>
</head><body><main><h1>高校师资采集平台</h1><p>请使用管理员分配的账号登录。每位员工只会看到自己的工单。</p>
<form id="login"><label for="username">用户名</label><input id="username" autocomplete="username" required>
<label for="password">密码</label><input id="password" type="password" autocomplete="current-password" required>
<button type="submit">登录</button><p id="message" role="alert"></p></form></main>
<script>document.getElementById('login').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:document.getElementById('username').value,password:document.getElementById('password').value})});if(response.ok){location.reload();return}const data=await response.json();document.getElementById('message').textContent=data.detail||'登录失败';button.disabled=false});</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
async def serve_ui(request: Request = None):
    if request is not None and getattr(request.state, 'user', None) is None:
        if not account_store.has_admin() and _direct_local_http(request):
            return _bootstrap_page()
        return _login_page()
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>高校师资采集平台 · 自动采集与大学交付</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .hidden { display: none !important; }
    </style>
</head>
<body class="bg-slate-50 min-h-screen text-slate-800">
    <!-- Header -->
    <header class="bg-white border-b border-slate-200 sticky top-0 z-30 shadow-sm">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 py-3.5 flex items-center justify-between">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-600 via-purple-600 to-pink-600 flex items-center justify-center text-white shadow-md">
                    <i class="fa-solid fa-robot text-xl"></i>
                </div>
                <div>
                    <div class="flex items-center space-x-2">
                        <h1 class="text-xl font-bold bg-gradient-to-r from-slate-900 to-slate-700 bg-clip-text text-transparent">高校师资采集平台</h1>
                        <span class="px-2 py-0.5 text-[11px] font-bold bg-indigo-100 text-indigo-700 rounded-md">自动采集</span>
                    </div>
                    <p class="text-xs text-slate-500">按学院采集 · 完整正文保留 · 按大学审核交付</p>
                </div>
            </div>
            <div class="flex items-center space-x-3">
                <span id="queueStatsPill" class="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-slate-100 text-slate-700 border border-slate-200">
                    <i class="fa-solid fa-layer-group text-slate-500 mr-1.5"></i>
                    <span id="queueStatsText">正在读取队列</span>
                </span>
                <span id="agentPill" class="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                    <span id="workerStatusText">正在检查后台</span>
                </span>
                <span id="accountLabel" class="text-xs text-slate-600">正在读取账号</span>
                <button onclick="logoutAccount()" class="text-xs font-semibold text-indigo-700">退出</button>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        <details class="bg-white rounded-2xl p-4 border border-slate-200/80 shadow-sm mb-5">
            <summary class="text-sm font-semibold cursor-pointer">修改我的密码</summary>
            <div class="flex flex-wrap gap-2 mt-3">
                <input id="oldPassword" type="password" autocomplete="current-password" placeholder="当前密码" class="border rounded-lg px-2 py-1.5 text-sm">
                <input id="changedPassword" type="password" autocomplete="new-password" placeholder="新密码（至少12位）" class="border rounded-lg px-2 py-1.5 text-sm">
                <button onclick="changeMyPassword()" class="bg-indigo-600 text-white rounded-lg px-3 py-1.5 text-sm">保存新密码</button>
            </div>
            <p id="passwordStatus" class="text-xs mt-2 text-slate-500"></p>
        </details>
        <section class="bg-white rounded-2xl p-4 border border-slate-200/80 shadow-sm mb-5">
            <div class="flex flex-wrap items-center gap-3">
                <span class="font-semibold text-sm">每日工作统计</span>
                <input id="reportDay" type="date" class="border border-slate-300 rounded-lg px-2 py-1 text-sm">
                <button onclick="loadDailyReport()" class="text-sm font-semibold text-indigo-700">查看</button>
                <span id="dailySummary" class="text-xs text-slate-600">正在读取</span>
            </div>
            <div id="employeeDailyRows" class="hidden mt-3 text-xs text-slate-700 space-y-1"></div>
        </section>
        <section id="adminUsers" class="hidden bg-white rounded-2xl p-4 border border-slate-200/80 shadow-sm mb-5">
            <div class="font-semibold text-sm mb-2">管理员 · 创建员工账号</div>
            <a href="/review/legacy" class="text-xs font-semibold text-indigo-700">查看历史图片复核材料</a>
            <div class="flex flex-wrap gap-2">
                <input id="newUsername" autocomplete="off" placeholder="登录名（小写英文）" class="border rounded-lg px-2 py-1.5 text-sm">
                <input id="newDisplayName" autocomplete="off" placeholder="员工姓名" class="border rounded-lg px-2 py-1.5 text-sm">
                <input id="newPassword" type="password" autocomplete="new-password" placeholder="初始密码（至少12位）" class="border rounded-lg px-2 py-1.5 text-sm">
                <select id="newRole" class="border rounded-lg px-2 py-1.5 text-sm"><option value="employee">员工</option><option value="admin">管理员</option></select>
                <button onclick="createAccount()" class="bg-indigo-600 text-white rounded-lg px-3 py-1.5 text-sm">创建</button>
            </div>
            <p id="createAccountStatus" class="text-xs mt-2 text-slate-500"></p>
        </section>
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
            
            <!-- Left Form Area (7 Cols) -->
            <div class="lg:col-span-7 space-y-6">
                <!-- Info Banner -->
                <div class="bg-gradient-to-r from-indigo-600 via-purple-600 to-pink-600 rounded-2xl p-6 text-white shadow-lg shadow-indigo-500/15">
                    <div class="flex items-center justify-between mb-2">
                        <h2 class="text-lg font-bold flex items-center">
                            <i class="fa-solid fa-microchip mr-2"></i> 提交学院，后台自动接单
                        </h2>
                        <span class="px-2 py-0.5 bg-white/20 text-xs font-medium rounded-full backdrop-blur-sm">真实浏览器</span>
                    </div>
                    <p class="text-indigo-100 text-sm leading-relaxed">
                        每个学院独立采集：后台通过 dsh 与 DeepSeek 核实名录、分页和正文范围，使用真实浏览器访问页面，由确定性程序提取教师资料与完整简介。已验证的方案可复用；访问失败或信息冲突会明确显示，全部目标学院通过审核后提供大学 Excel。
                    </p>
                </div>

                <!-- Mode Switch Tabs -->
                <div class="flex bg-slate-200/80 p-1.5 rounded-xl text-sm font-semibold">
                    <button id="tabSingle" onclick="switchTab('single')" class="flex-1 py-2 rounded-lg bg-white text-indigo-700 shadow-sm transition-all flex items-center justify-center space-x-2">
                        <i class="fa-solid fa-crosshairs"></i>
                        <span>单学院极速派发</span>
                    </button>
                    <button id="tabBatch" onclick="switchTab('batch')" class="flex-1 py-2 rounded-lg text-slate-600 hover:text-slate-900 transition-all flex items-center justify-center space-x-2">
                        <i class="fa-solid fa-network-wired"></i>
                        <span>批量提交学院任务</span>
                    </button>
                </div>

                <!-- Single Mode Card -->
                <div id="singleCard" class="bg-white rounded-2xl p-6 border border-slate-200/80 shadow-sm space-y-5">
                    <div class="border-b border-slate-100 pb-3 flex items-center justify-between">
                        <h3 class="font-bold text-slate-800 text-sm flex items-center">
                            <i class="fa-solid fa-file-signature text-indigo-500 mr-2"></i> 单学院任务工单
                        </h3>
                        <span class="text-xs text-slate-400">可填写学院官网首页，由后台查找师资名录</span>
                    </div>

                    <div class="space-y-4">
                        <div>
                            <label class="block text-sm font-semibold text-slate-700 mb-1">高校全称 <span class="text-red-500">*</span></label>
                            <input type="text" id="schoolInput" placeholder="例如：华东师范大学" 
                                class="w-full px-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm">
                        </div>

                        <div>
                            <label class="block text-sm font-semibold text-slate-700 mb-1">二级学院全称 <span class="text-red-500">*</span></label>
                            <input type="text" id="collegeInput" placeholder="例如：地理科学学院" 
                                class="w-full px-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm">
                        </div>

                        <div>
                            <label class="block text-sm font-semibold text-slate-700 mb-1">学院官网或师资列表 URL <span class="text-red-500">*</span></label>
                            <input type="url" id="urlInput" placeholder="例如：https://geo.ecnu.edu.cn/" 
                                class="w-full px-4 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent text-sm">
                        </div>
                    </div>

                    <div class="pt-2">
                        <button id="startSingleBtn" onclick="startSingleCollect()" 
                            class="w-full py-3.5 px-6 rounded-xl bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white font-bold text-sm shadow-md shadow-indigo-600/20 transition-all flex items-center justify-center space-x-2">
                            <i class="fa-solid fa-paper-plane"></i>
                            <span>提交采集请求</span>
                        </button>
                    </div>
                </div>

                <!-- Batch Mode Card -->
                <div id="batchCard" class="bg-white rounded-2xl p-6 border border-slate-200/80 shadow-sm space-y-5 hidden">
                    <div class="border-b border-slate-100 pb-3 flex items-center justify-between">
                        <h3 class="font-bold text-slate-800 text-sm flex items-center">
                            <i class="fa-solid fa-list-check text-indigo-500 mr-2"></i> 批量并发工单池
                        </h3>
                        <button onclick="insertSixCollegesExample()" class="text-xs bg-indigo-50 text-indigo-600 hover:bg-indigo-100 px-3 py-1.5 rounded-lg font-semibold transition-all">
                            <i class="fa-solid fa-bolt mr-1 text-amber-500"></i>填入 6 所示范学院 (测试并发)
                        </button>
                    </div>

                    <div>
                        <label class="block text-sm font-semibold text-slate-700 mb-1">每行一所学院 (格式：高校 学院 官网入口)；三项均必填：</label>
                        <textarea id="batchInput" rows="7" placeholder="复旦大学 哲学学院 https://philosophy.fudan.edu.cn/&#10;同济大学 环境科学与工程学院 https://sese.tongji.edu.cn/&#10;华东师范大学 地理科学学院 https://geo.ecnu.edu.cn/&#10;上海外国语大学 国际金融贸易学院 https://sft.shisu.edu.cn/&#10;上海大学 计算机工程与科学学院 https://cs.shu.edu.cn/&#10;上海师范大学 环境与地理科学学院 https://segs.shnu.edu.cn/25639/list.htm" 
                            class="w-full p-4 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent font-mono text-xs leading-relaxed"></textarea>
                    </div>

                    <div class="pt-2">
                        <button id="startBatchBtn" onclick="startBatchCollect()" 
                            class="w-full py-3.5 px-6 rounded-xl bg-gradient-to-r from-indigo-600 via-purple-600 to-pink-600 hover:opacity-95 active:opacity-90 text-white font-bold text-sm shadow-md shadow-indigo-600/20 transition-all flex items-center justify-center space-x-2">
                            <i class="fa-solid fa-rocket"></i>
                            <span>提交批量采集请求</span>
                        </button>
                    </div>
                </div>

                <!-- Feature Badges -->
                <div class="grid grid-cols-3 gap-3">
                    <div class="bg-white p-4 rounded-xl border border-slate-200/60 text-center shadow-xs">
                        <i class="fa-solid fa-users-gear text-indigo-500 text-lg mb-1"></i>
                        <div class="text-xs font-bold text-slate-700">学院独立采集</div>
                        <div class="text-[11px] text-slate-400">每个学院独立采集</div>
                    </div>
                    <div class="bg-white p-4 rounded-xl border border-slate-200/60 text-center shadow-xs">
                        <i class="fa-solid fa-file-pdf text-purple-500 text-lg mb-1"></i>
                        <div class="text-xs font-bold text-slate-700">断点与方案复用</div>
                        <div class="text-[11px] text-slate-400">页面保存 · 本轮重新核验</div>
                    </div>
                    <div class="bg-white p-4 rounded-xl border border-slate-200/60 text-center shadow-xs">
                        <i class="fa-solid fa-table text-emerald-500 text-lg mb-1"></i>
                        <div class="text-xs font-bold text-slate-700">双 Sheet 守恒交付</div>
                        <div class="text-[11px] text-slate-400">自动合并跨院全量总表</div>
                    </div>
                </div>
            </div>

            <!-- Right Status Area (5 Cols) -->
            <div class="lg:col-span-5 space-y-6">
                <!-- Monitor Card -->
                <div class="bg-white rounded-2xl p-6 border border-slate-200/80 shadow-sm flex flex-col h-full">
                    <div class="border-b border-slate-100 pb-3 flex items-center justify-between">
                        <h3 class="font-bold text-slate-800 text-base flex items-center">
                            <i class="fa-solid fa-terminal text-indigo-500 mr-2"></i> 采集进度
                        </h3>
                        <span id="taskBadge" class="text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-500 font-medium">待提交</span>
                    </div>

                    <div class="pt-3 text-xs text-slate-500 flex items-center justify-between gap-2">
                        <span id="requestContext">本次采集 · 尚未提交</span>
                        <div class="flex gap-3 shrink-0">
                            <button id="historyToggle" onclick="toggleHistory()" class="text-indigo-700">历史工单</button>
                            <button id="newRequestBtn" onclick="resetCurrentView()" class="text-indigo-700">新建采集</button>
                        </div>
                    </div>
                    <div id="historyPanel" class="hidden mt-3 p-3 border border-slate-200 rounded-xl">
                        <p class="text-xs text-slate-500 mb-2">最近工单；点击查看不会重新采集。</p>
                        <div id="historyList" class="space-y-2 max-h-48 overflow-y-auto"></div>
                    </div>
                    <div class="py-4 space-y-3">
                        <div class="flex justify-between text-xs text-slate-500 font-medium">
                            <span id="stepText">填写左侧学院信息后提交采集请求</span>
                            <span id="percentText">0%</span>
                        </div>
                        <div class="w-full bg-slate-100 rounded-full h-2.5 overflow-hidden">
                            <div id="progressBar" class="bg-indigo-600 h-2.5 rounded-full transition-all duration-500" style="width: 0%"></div>
                        </div>
                    </div>

                    <!-- Subtask Status Grid (for Batch Mode) -->
                    <div id="subtaskCardBox" class="hidden mb-3 space-y-2 max-h-48 overflow-y-auto pr-1">
                        <div class="text-xs font-semibold text-slate-500">并发学院进度卡片</div>
                        <div id="subtaskList" class="space-y-1.5"></div>
                    </div>

                    <!-- Agent Thoughts Stream -->
                    <div class="flex-1 flex flex-col min-h-[260px]">
                        <div class="text-xs font-semibold text-slate-400 mb-1 flex items-center justify-between">
                            <span>执行记录与审计反馈</span>
                            <i class="fa-solid fa-brain text-indigo-400"></i>
                        </div>
                        <div id="thoughtBox" class="bg-slate-900 text-slate-200 p-3.5 rounded-xl font-mono text-xs overflow-y-auto max-h-64 flex-1 space-y-2">
                            <div class="text-slate-500">提交后显示本次任务进度。已结束的旧任务可从“历史工单”查看。</div>
                        </div>
                    </div>

                    <!-- Result Action Area -->
                    <div id="resultArea" class="mt-5 pt-4 border-t border-slate-100 hidden space-y-3">
                        <div id="resultStatusCard" class="bg-emerald-50 border border-emerald-200 rounded-xl p-3 flex items-center justify-between">
                            <div>
                                <div class="text-xs font-bold text-slate-800">任务结果与交付状态</div>
                                <div id="statSummary" class="text-[11px] text-emerald-600">全库入库 0 位 | 有邮箱 0 位</div>
                            </div>
                            <i id="resultStatusIcon" class="fa-solid fa-circle-check text-emerald-500 text-2xl"></i>
                        </div>
                        <div id="univDownloadBox" class="space-y-2"></div>
                        <a id="reviewBtn" href="#" class="hidden w-full py-3 px-4 rounded-xl bg-amber-600 hover:bg-amber-700 text-white font-bold text-sm text-center">核对图片文字与邮箱</a>
                        <a id="downloadBtn" href="#" 
                            class="w-full py-3 px-4 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-sm shadow-md shadow-emerald-600/20 flex items-center justify-center space-x-2 transition-all">
                            <i class="fa-solid fa-file-excel"></i>
                            <span id="downloadBtnText">下载交付标准表</span>
                        </a>
                    </div>
                </div>
            </div>

        </div>
    </main>

    <script>
        let currentUser = null;
        let csrfToken = '';
        let currentTaskId = null;
        let currentBatchId = null;
        let pollTimer = null;
        let requestEpoch = 0;
        let viewingHistory = false;
        let historyOffset = 0;

        function escapeHtml(value) {
            return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
        }

        function requestKey() {
            return currentUser ? `facultyActiveRequest:${currentUser.id}` : '';
        }

        async function logoutAccount() {
            await fetch('/api/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': csrfToken}});
            location.reload();
        }

        async function changeMyPassword() {
            const response = await fetch('/api/auth/change-password', {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
                body: JSON.stringify({old_password: document.getElementById('oldPassword').value,
                    new_password: document.getElementById('changedPassword').value})});
            document.getElementById('oldPassword').value = '';
            document.getElementById('changedPassword').value = '';
            if (response.ok) { location.reload(); return; }
            const result = await response.json();
            document.getElementById('passwordStatus').innerText = result.detail || '修改失败';
        }

        async function loadDailyReport() {
            const day = document.getElementById('reportDay').value;
            const response = await fetch(`/api/reports/daily?day=${encodeURIComponent(day)}`);
            if (!response.ok) return;
            const report = await response.json();
            document.getElementById('dailySummary').innerText = `提交 ${report.submitted_colleges} 个学院 · 完成 ${report.completed_colleges} 个学院 · 交付 ${report.delivered_record_rows} 条教师记录 · 失败 ${report.failed_colleges} 个学院`;
            const rows = document.getElementById('employeeDailyRows');
            if (Array.isArray(report.employees)) {
                rows.classList.remove('hidden');
                rows.innerHTML = report.employees.map(item => `<div>${escapeHtml(item.display_name)}：提交 ${item.submitted_colleges} 院，完成 ${item.completed_colleges} 院，交付 ${item.delivered_record_rows} 条记录，失败 ${item.failed_colleges} 院</div>`).join('') || '今天还没有员工工单';
            } else {
                rows.classList.add('hidden');
                rows.replaceChildren();
            }
        }

        async function createAccount() {
            const payload = {
                username: document.getElementById('newUsername').value.trim(),
                display_name: document.getElementById('newDisplayName').value.trim(),
                password: document.getElementById('newPassword').value,
                role: document.getElementById('newRole').value
            };
            const response = await fetch('/api/admin/users', {method: 'POST',
                headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken},
                body: JSON.stringify(payload)});
            const result = await response.json();
            document.getElementById('newPassword').value = '';
            document.getElementById('createAccountStatus').innerText = response.ok
                ? `已创建 ${result.display_name}（${result.username}）`
                : (typeof result.detail === 'string' ? result.detail : '创建失败');
            if (response.ok) loadDailyReport();
        }

        function setResultTone(ready) {
            document.getElementById('resultStatusCard').className = `${ready ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'} border rounded-xl p-3 flex items-center justify-between`;
            document.getElementById('statSummary').className = `text-[11px] ${ready ? 'text-emerald-700' : 'text-amber-800'}`;
            document.getElementById('resultStatusIcon').className = `fa-solid ${ready ? 'fa-circle-check text-emerald-500' : 'fa-circle-exclamation text-amber-500'} text-2xl`;
        }

        function readSavedRequest() {
            try {
                const saved = JSON.parse(localStorage.getItem(requestKey()) || 'null');
                if (saved && ['task', 'batch'].includes(saved.kind) && typeof saved.id === 'string') return saved;
                return null;
            } catch (_) { return null; }
        }

        function rememberRequest(kind, id) {
            try {
                localStorage.setItem(requestKey(), JSON.stringify({kind, id}));
            } catch (_) {}
        }

        function forgetRequest(kind, id) {
            const saved = readSavedRequest();
            if (saved?.kind === kind && saved?.id === id) {
                try { localStorage.removeItem(requestKey()); } catch (_) {}
            }
        }

        function resetCurrentView(clearPointer = true) {
            requestEpoch++;
            if (pollTimer) clearInterval(pollTimer);
            pollTimer = null;
            currentTaskId = currentBatchId = null;
            viewingHistory = false;
            document.getElementById('historyPanel').classList.add('hidden');
            if (clearPointer) {
                try { localStorage.removeItem(requestKey()); } catch (_) {}
            }
            document.getElementById('taskBadge').innerText = '待提交';
            document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-500 font-medium';
            document.getElementById('requestContext').innerText = '本次采集 · 尚未提交';
            document.getElementById('stepText').innerText = '填写左侧学院信息后提交采集请求';
            document.getElementById('progressBar').style.width = '0%';
            document.getElementById('percentText').innerText = '0%';
            document.getElementById('subtaskList').replaceChildren();
            document.getElementById('subtaskCardBox').classList.add('hidden');
            document.getElementById('resultArea').classList.add('hidden');
            setResultTone(true);
            document.getElementById('univDownloadBox').replaceChildren();
            document.getElementById('downloadBtn').classList.add('hidden');
            document.getElementById('downloadBtn').removeAttribute('href');
            document.getElementById('reviewBtn').classList.add('hidden');
            document.getElementById('reviewBtn').removeAttribute('href');
            document.getElementById('thoughtBox').innerText = '提交后显示本次任务进度。历史工单仍保留；新建采集不会取消已提交的任务。';
            unlockUI();
        }

        function openRequest(request, history = false, remember = false) {
            resetCurrentView(false);
            viewingHistory = history;
            document.getElementById('historyPanel').classList.add('hidden');
            document.getElementById('requestContext').innerText = `${history ? '历史工单' : '本次采集'} · ${request.id}`;
            if (remember) rememberRequest(request.kind, request.id);
            if (request.kind === 'batch') {
                currentBatchId = request.id;
                document.getElementById('subtaskCardBox').classList.remove('hidden');
                pollBatchStatus();
            } else {
                currentTaskId = request.id;
                pollTaskStatus();
            }
        }

        async function toggleHistory() {
            const panel = document.getElementById('historyPanel');
            panel.classList.toggle('hidden');
            if (panel.classList.contains('hidden')) return;
            historyOffset = 0;
            document.getElementById('historyList').replaceChildren();
            await loadHistoryPage();
        }

        async function loadHistoryPage() {
            const panel = document.getElementById('historyPanel');
            const epoch = requestEpoch;
            const list = document.getElementById('historyList');
            const more = list.querySelector('[data-history-more]');
            if (more) more.remove();
            if (!historyOffset) list.innerText = '正在读取历史工单…';
            try {
                const response = await fetch(`/api/history?limit=20&offset=${historyOffset}`);
                if (!response.ok) throw new Error('历史接口不可用');
                const data = await response.json();
                if (epoch !== requestEpoch || panel.classList.contains('hidden')) return;
                if (!historyOffset) list.replaceChildren();
                for (const request of data.requests) {
                    const button = document.createElement('button');
                    button.className = 'block w-full text-left text-xs p-2 rounded-lg bg-slate-50 hover:bg-indigo-50 text-slate-700';
                    const date = new Date(request.created_at * 1000).toLocaleString();
                    button.textContent = `${request.schools.join('、')} · ${request.total_colleges} 个学院 · ${request.active ? '尚未结束' : '已结束，点击查看结果'} · ${date}`;
                    button.onclick = () => openRequest(request, true);
                    list.appendChild(button);
                }
                historyOffset += data.requests.length;
                if (!historyOffset) list.innerText = '暂无历史工单';
                if (data.requests.length === 20) {
                    const button = document.createElement('button');
                    button.dataset.historyMore = 'true';
                    button.className = 'text-xs text-indigo-700 font-semibold p-2';
                    button.textContent = '加载更多工单';
                    button.onclick = loadHistoryPage;
                    list.appendChild(button);
                }
            } catch (_) {
                if (epoch === requestEpoch && !panel.classList.contains('hidden')) {
                    const message = document.createElement('div');
                    message.className = 'text-xs text-amber-700 p-2';
                    message.textContent = '暂时无法读取更多工单，请稍后重试';
                    if (!historyOffset) list.replaceChildren();
                    list.appendChild(message);
                }
            }
        }

        // Auto poll queue stats
        async function fetchQueueStats() {
            try {
                const res = await fetch('/api/queue/stats');
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById('queueStatsText').innerText = `排队: ${data.queued} | 运行: ${data.running} | 历史结束: ${data.completed + data.failed}`;
                }
            } catch (e) {}
            try {
                const response = await fetch('/api/worker/status');
                if (!response.ok) throw new Error('后台状态不可用');
                const status = await response.json();
                document.getElementById('workerStatusText').innerText = status.online ? '后台自动接单在线' : '后台接单离线 · 请求将排队';
                document.getElementById('agentPill').className = `inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold border ${status.online ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`;
            } catch (_) {
                document.getElementById('workerStatusText').innerText = '后台状态未知';
                document.getElementById('agentPill').className = 'inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold border bg-slate-100 text-slate-600';
            }
        }
        setInterval(fetchQueueStats, 3000);
        fetchQueueStats();

        function switchTab(mode) {
            const singleTab = document.getElementById('tabSingle');
            const batchTab = document.getElementById('tabBatch');
            const singleCard = document.getElementById('singleCard');
            const batchCard = document.getElementById('batchCard');

            if (mode === 'single') {
                singleTab.className = 'flex-1 py-2 rounded-lg bg-white text-indigo-700 shadow-sm transition-all flex items-center justify-center space-x-2';
                batchTab.className = 'flex-1 py-2 rounded-lg text-slate-600 hover:text-slate-900 transition-all flex items-center justify-center space-x-2';
                singleCard.classList.remove('hidden');
                batchCard.classList.add('hidden');
            } else {
                batchTab.className = 'flex-1 py-2 rounded-lg bg-white text-indigo-700 shadow-sm transition-all flex items-center justify-center space-x-2';
                singleTab.className = 'flex-1 py-2 rounded-lg text-slate-600 hover:text-slate-900 transition-all flex items-center justify-center space-x-2';
                batchCard.classList.remove('hidden');
                singleCard.classList.add('hidden');
            }
        }

        function insertSixCollegesExample() {
            document.getElementById('batchInput').value = 
`复旦大学 哲学学院 https://philosophy.fudan.edu.cn/
同济大学 环境科学与工程学院 https://sese.tongji.edu.cn/
华东师范大学 地理科学学院 https://geo.ecnu.edu.cn/
上海外国语大学 国际金融贸易学院 https://sft.shisu.edu.cn/
上海大学 计算机工程与科学学院 https://cs.shu.edu.cn/
上海师范大学 环境与地理科学学院 https://segs.shnu.edu.cn/25639/list.htm`;
        }

        async function startSingleCollect() {
            const school = document.getElementById('schoolInput').value.trim();
            const college = document.getElementById('collegeInput').value.trim();
            const url = document.getElementById('urlInput').value.trim();

            let validEntry = false;
            try {
                const parsed = new URL(url);
                validEntry = ['http:', 'https:'].includes(parsed.protocol) && Boolean(parsed.hostname) && !parsed.username && !parsed.password;
            } catch (_) {}
            if (!school || !college || !validEntry) {
                alert('请填写学校、学院及有效的学院官网入口 URL（http/https）。');
                return;
            }

            resetCurrentView();
            lockUI();
            const epoch = requestEpoch;
            try {
                const resp = await fetch('/api/collect', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                    body: JSON.stringify({ school, college, url: url || null })
                });
                if (!resp.ok) throw new Error((await resp.json()).detail || '服务未接受请求');
                const data = await resp.json();
                if (epoch !== requestEpoch) return;
                openRequest({kind: 'task', id: data.task_id}, false, true);
            } catch (err) {
                if (epoch !== requestEpoch) return;
                alert('工单派发失败：' + err);
                unlockUI();
            }
        }

        async function startBatchCollect() {
            const text = document.getElementById('batchInput').value.trim();
            if (!text) {
                alert('请输入至少一所高校和学院！');
                return;
            }

            resetCurrentView();
            lockUI();
            const epoch = requestEpoch;
            try {
                const resp = await fetch('/api/collect_batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken },
                    body: JSON.stringify({ items_text: text })
                });
                if (!resp.ok) throw new Error((await resp.json()).detail || '服务未接受请求');
                const data = await resp.json();
                if (epoch !== requestEpoch) return;
                openRequest({kind: 'batch', id: data.batch_id}, false, true);
            } catch (err) {
                if (epoch !== requestEpoch) return;
                alert('批量工单派发失败：' + err);
                unlockUI();
            }
        }

        function lockUI() {
            document.getElementById('startSingleBtn').disabled = true;
            document.getElementById('startSingleBtn').classList.add('opacity-50', 'cursor-not-allowed');
            document.getElementById('startBatchBtn').disabled = true;
            document.getElementById('startBatchBtn').classList.add('opacity-50', 'cursor-not-allowed');

            document.getElementById('taskBadge').innerText = '正在提交';
            document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium animate-pulse';
            document.getElementById('resultArea').classList.add('hidden');
            document.getElementById('thoughtBox').innerText = '正在提交到队列，后台将自动认领。';
        }

        function unlockUI() {
            document.getElementById('startSingleBtn').disabled = false;
            document.getElementById('startSingleBtn').classList.remove('opacity-50', 'cursor-not-allowed');
            document.getElementById('startBatchBtn').disabled = false;
            document.getElementById('startBatchBtn').classList.remove('opacity-50', 'cursor-not-allowed');
        }

        function pollTaskStatus() {
            if (pollTimer) clearInterval(pollTimer);
            const targetId = currentTaskId;
            const epoch = requestEpoch;
            const tick = async () => {
                if (!targetId || epoch !== requestEpoch) return;
                try {
                    const resp = await fetch(`/api/task/${encodeURIComponent(targetId)}`);
                    if (epoch !== requestEpoch) return;
                    if (!resp.ok) {
                        if (resp.status === 404) {
                            clearInterval(pollTimer);
                            forgetRequest('task', targetId);
                            unlockUI();
                            document.getElementById('taskBadge').innerText = '待命中';
                            document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-500 font-medium';
                            document.getElementById('thoughtBox').innerHTML = '<div class="text-amber-400">> 提示：工单不存在或服务已重置，请刷新页面重新提交！</div>';
                            return;
                        }
                        return;
                    }
                    const task = await resp.json();
                    if (epoch !== requestEpoch) return;

                    document.getElementById('progressBar').style.width = `${task.progress}%`;
                    document.getElementById('percentText').innerText = task.status === 'running' && task.progress === 0 ? '侦察中' : `${task.progress}%`;
                    const taskScope = task.delivery_scope;
                    if (task.status === 'queued' || task.status === 'running' || task.status === 'compiling') {
                        document.getElementById('taskBadge').innerText = task.status === 'queued' ? '排队待接单' : task.status === 'compiling' ? '交付审计中' : '自动采集中';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium';
                    }
                    document.getElementById('stepText').innerText = taskScope ? `本次范围：${taskScope.target_colleges.join('、')} · ${taskScope.state === 'ready' ? '可交付' : taskScope.state === 'blocked' ? '待复核' : '采集中'}；${task.current_step || ''}` : task.current_step || '';

                    if (task.agent_thoughts && task.agent_thoughts.length > 0) {
                        const box = document.getElementById('thoughtBox');
                        box.innerHTML = task.agent_thoughts.map(th => `
                            <div class="border-l-2 border-indigo-500 pl-2.5 py-1">
                                <div class="text-[11px] text-indigo-400 font-bold">[${escapeHtml(th.phase)}] ${escapeHtml(th.time)}</div>
                                <div class="text-slate-300">${escapeHtml(th.thought)}</div>
                                <div class="text-emerald-400 text-[11px]">↳ ${escapeHtml(th.action)}</div>
                            </div>
                        `).join('');
                        box.scrollTop = box.scrollHeight;
                    }

                    if (task.status === 'completed') {
                        clearInterval(pollTimer);
                        if (!viewingHistory) forgetRequest('task', targetId);
                        setResultTone(!task.counts_basis && taskScope?.state === 'ready');
                        document.getElementById('taskBadge').innerText = task.counts_basis ? '身份与人数待复核' : taskScope?.state === 'ready' ? '本次范围已完成' : '学院已完成，大学范围待处理';
                        document.getElementById('taskBadge').className = `text-xs px-2.5 py-0.5 rounded-full font-medium ${task.counts_basis || taskScope?.state === 'blocked' ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`;
                        document.getElementById('statSummary').innerText = task.counts_basis ? `${task.college} 保留 ${task.stats.total || 0} 条页面观察，人数待核算` : `${task.college} 入库 ${task.stats.total || 0} 位 | 有邮箱 ${task.stats.with_email || 0} 位 | 无邮箱 ${task.stats.no_email || 0} 位`;
                        document.getElementById('univDownloadBox').innerHTML = '';
                        document.getElementById('downloadBtn').classList.toggle('hidden', !task.download_url);
                        if (task.download_url) {
                            document.getElementById('downloadBtn').href = task.download_url;
                            document.getElementById('downloadBtn').setAttribute('download', task.download_url.split('/').pop());
                            document.getElementById('downloadBtnText').innerText = `${taskScope?.state === 'ready' ? '下载本次范围交付' : '下载学院中间成果'}【${task.download_url.split('/').pop()}】`;
                        }
                        document.getElementById('resultArea').classList.remove('hidden');
                        unlockUI();
                    } else if (task.status === 'failed') {
                        clearInterval(pollTimer);
                        if (!viewingHistory && !task.review_url) forgetRequest('task', targetId);
                        document.getElementById('taskBadge').innerText = '失败';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-red-100 text-red-700 font-medium';
                        document.getElementById('thoughtBox').innerText = task.error || task.current_step || '任务失败，请查看审计记录';
                        if (task.review_url) {
                            const review = document.getElementById('reviewBtn');
                            review.href = task.review_url;
                            review.classList.remove('hidden');
                            document.getElementById('downloadBtn').classList.add('hidden');
                            document.getElementById('statSummary').innerText = '图片证据需要人工核对；完成后可重新采集本工单';
                            setResultTone(false);
                            document.getElementById('resultArea').classList.remove('hidden');
                        }
                        unlockUI();
                    }
                } catch (e) {
                    console.error('Polling error:', e);
                }
            };
            tick();
            pollTimer = setInterval(tick, 1000);
        }

        function pollBatchStatus() {
            if (pollTimer) clearInterval(pollTimer);
            const targetId = currentBatchId;
            const epoch = requestEpoch;
            const tick = async () => {
                if (!targetId || epoch !== requestEpoch) return;
                try {
                    const resp = await fetch(`/api/batch/${encodeURIComponent(targetId)}`);
                    if (epoch !== requestEpoch) return;
                    if (!resp.ok) {
                        if (resp.status === 404) {
                            clearInterval(pollTimer);
                            forgetRequest('batch', targetId);
                            unlockUI();
                            document.getElementById('taskBadge').innerText = '待命中';
                            document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-500 font-medium';
                            document.getElementById('thoughtBox').innerHTML = '<div class="text-amber-400">> 提示：该批次不存在或服务已重置，请刷新页面后重新派发！</div>';
                            return;
                        }
                        return;
                    }
                    const batch = await resp.json();
                    if (epoch !== requestEpoch) return;
                    if (['completed', 'failed', 'partially_failed'].includes(batch.status) && !viewingHistory
                            && !(batch.subtasks || []).some(st => st.review_url)) forgetRequest('batch', targetId);
                    if (batch.status === 'queued' || batch.status === 'running') {
                        document.getElementById('taskBadge').innerText = batch.status === 'queued' ? '排队待接单' : '自动采集中';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium';
                    }

                    document.getElementById('progressBar').style.width = `${batch.progress}%`;
                    document.getElementById('percentText').innerText = batch.status === 'running' && batch.progress === 0 ? '侦察中' : `${batch.progress}%`;
                    const scopeText = (batch.delivery_scopes || []).map(s => `${s.school}：本次 ${s.target_colleges.join('、')}，${s.state === 'ready' ? '可交付' : s.state === 'blocked' ? '待复核' : '采集中'}`).join('；');
                    document.getElementById('stepText').innerText = `[${batch.completed_colleges}/${batch.total_colleges} 院系完成] ${batch.current_step}${scopeText ? '；' + scopeText : ''}`;

                    // Render subtasks list
                    if (batch.subtasks) {
                        const sList = document.getElementById('subtaskList');
                        sList.innerHTML = batch.subtasks.map(st => {
                            let badgeCls = 'bg-slate-100 text-slate-500';
                            let stText = '排队中';
                            if (st.status === 'running') { badgeCls = 'bg-amber-100 text-amber-700 animate-pulse'; stText = '采集中'; }
                            if (st.status === 'completed') { badgeCls = st.counts_basis ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'; stText = st.counts_basis ? `待复核 (${st.stats?.total || 0}条观察)` : `完成 (${st.stats?.total || 0}人)`; }
                            if (st.status === 'failed') { badgeCls = 'bg-red-100 text-red-700'; stText = '失败'; }
                            const dlBtn = st.download_url ? `<a href="${escapeHtml(st.download_url)}" download class="px-2 py-0.5 rounded bg-emerald-50 hover:bg-emerald-100 text-emerald-700 border border-emerald-200 text-[11px] font-bold flex items-center space-x-1" title="下载该学院双Sheet Excel"><i class="fa-solid fa-download"></i><span>表格</span></a>` : '';
                            const reviewBtn = st.review_url ? `<a href="${escapeHtml(st.review_url)}" class="px-2 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-200 text-[11px] font-bold">图片复核</a>` : '';
                            return `
                                <div class="flex items-center justify-between p-2 rounded-lg bg-slate-50 border border-slate-200/60 text-xs">
                                    <span class="font-medium text-slate-700 truncate max-w-[190px]">${escapeHtml(st.school)} - ${escapeHtml(st.college)}</span>
                                    <div class="flex items-center space-x-1.5">
                                        <span class="px-2 py-0.5 rounded text-[11px] font-semibold ${badgeCls}">${stText}</span>
                                        ${dlBtn}
                                        ${reviewBtn}
                                    </div>
                                </div>
                            `;
                        }).join('');
                    }

                    // Extract thoughts from all subtasks
                    let allThoughts = [];
                    batch.subtasks.forEach(st => {
                        if (st.agent_thoughts) {
                            st.agent_thoughts.forEach(th => {
                                allThoughts.push({ college: st.college, ...th });
                            });
                        }
                    });

                    if (allThoughts.length > 0) {
                        const box = document.getElementById('thoughtBox');
                        box.innerHTML = allThoughts.map(th => `
                            <div class="border-l-2 border-indigo-500 pl-2.5 py-1">
                                <div class="text-[11px] text-indigo-400 font-bold">[${escapeHtml(th.college || 'Agent')} - ${escapeHtml(th.phase)}] ${escapeHtml(th.time)}</div>
                                <div class="text-slate-300">${escapeHtml(th.thought)}</div>
                                <div class="text-emerald-400 text-[11px]">↳ ${escapeHtml(th.action)}</div>
                            </div>
                        `).join('');
                        box.scrollTop = box.scrollHeight;
                    }

                    if (batch.status === 'completed' && batch.delivery_state === 'blocked') {
                        clearInterval(pollTimer);
                        setResultTone(false);
                        document.getElementById('taskBadge').innerText = '大学交付待复核';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium';
                        document.getElementById('statSummary').innerText = '原始页面观察已保留，身份与人数待核算';
                        document.getElementById('downloadBtn').classList.add('hidden');
                        document.getElementById('univDownloadBox').innerText = '请先核实身份与来源，再生成大学交付。';
                        document.getElementById('resultArea').classList.remove('hidden');
                        unlockUI();
                    } else if (batch.status === 'completed' && batch.delivery_state === 'ready') {
                        clearInterval(pollTimer);
                        setResultTone(true);
                        document.getElementById('taskBadge').innerText = '批量全部完成';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium';
                        document.getElementById('statSummary').innerText = `本次请求 ${batch.total_colleges} 个学院全部完成；学院记录 ${batch.stats.total} 位 | 有邮箱 ${batch.stats.with_email} 位；大学表按来源身份合并`;
                        
                        const univBox = document.getElementById('univDownloadBox');
                        if (batch.university_files && batch.university_files.length > 0) {
                            univBox.innerHTML = `
                                <div class="text-xs font-bold text-slate-700 mb-1.5 flex items-center">
                                    <i class="fa-solid fa-building-columns text-indigo-600 mr-1.5"></i>按大学交付成果（双Sheet守恒）：
                                </div>
                                <div class="space-y-2">
                                    ${batch.university_files.map(uf => `
                                        <a href="${escapeHtml(uf.download_url)}" download="${escapeHtml(uf.filename)}"
                                            class="w-full py-2.5 px-3.5 rounded-xl bg-gradient-to-r from-indigo-50 to-purple-50 hover:from-indigo-100 hover:to-purple-100 border border-indigo-200/80 text-indigo-950 font-bold text-xs flex items-center justify-between shadow-sm transition-all group">
                                            <div class="flex items-center space-x-2">
                                                <i class="fa-solid fa-file-excel text-emerald-600 text-sm group-hover:scale-110 transition-transform"></i>
                                                <span class="text-sm text-indigo-900">${escapeHtml(uf.filename)}</span>
                                            </div>
                                            <div class="flex items-center space-x-2">
                                                <span class="text-[11px] text-slate-600 font-normal">本次 ${escapeHtml(uf.delivery_scope.target_colleges.join('、'))} · 版本 ${escapeHtml(uf.version)}${uf.current_snapshot_state === 'blocked' ? ' · 历史跨学院汇总待复核' : ''}</span>
                                                <span class="text-[11px] font-semibold text-emerald-700 bg-emerald-100/70 px-2 py-0.5 rounded-md">
                                                    共 ${uf.total} 位 (邮箱 ${uf.with_email})
                                                </span>
                                                <i class="fa-solid fa-download text-indigo-500 text-xs ml-1"></i>
                                            </div>
                                        </a>
                                    `).join('')}
                                </div>
                            `;
                            document.getElementById('downloadBtn').classList.add('hidden');
                        } else if (batch.download_url) {
                            univBox.innerHTML = '';
                            document.getElementById('downloadBtn').classList.remove('hidden');
                            document.getElementById('downloadBtn').href = batch.download_url;
                            document.getElementById('downloadBtn').setAttribute('download', batch.download_url.split('/').pop());
                            document.getElementById('downloadBtnText').innerText = `下载交付表格【${batch.download_url.split('/').pop()}】`;
                        }
                        document.getElementById('resultArea').classList.remove('hidden');
                        unlockUI();
                    } else if (batch.status === 'failed' || batch.status === 'partially_failed') {
                        clearInterval(pollTimer);
                        document.getElementById('taskBadge').innerText = '任务异常';
                        document.getElementById('taskBadge').className = 'text-xs px-2.5 py-0.5 rounded-full bg-red-100 text-red-700 font-medium';
                        unlockUI();
                    }
                } catch (e) {
                    console.error('Polling error:', e);
                }
            };
            tick();
            pollTimer = setInterval(tick, 1000);
        }

        window.addEventListener('DOMContentLoaded', async () => {
            const identity = await fetch('/api/auth/me');
            if (!identity.ok) { location.reload(); return; }
            currentUser = await identity.json();
            csrfToken = currentUser.csrf_token;
            document.getElementById('accountLabel').innerText = `${currentUser.display_name}（${currentUser.role === 'admin' ? '管理员' : '员工'}）`;
            if (currentUser.role === 'admin') document.getElementById('adminUsers').classList.remove('hidden');
            document.getElementById('reportDay').value = new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10);
            loadDailyReport();
            fetchQueueStats();
            const saved = readSavedRequest();
            if (!saved) return;
            const epoch = requestEpoch;
            try {
                const resp = await fetch(`/api/${saved.kind}/${encodeURIComponent(saved.id)}`);
                if (epoch !== requestEpoch) return;
                if (resp.status === 404) { forgetRequest(saved.kind, saved.id); return; }
                if (resp.ok) {
                    const request = await resp.json();
                    if (epoch !== requestEpoch) return;
                    if (['queued', 'running', 'compiling'].includes(request.status)) openRequest(saved, false, true);
                    else forgetRequest(saved.kind, saved.id);
                }
            } catch (e) {
                console.error('Auto restore batch error:', e);
            }
        });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    host = cfg.get("host", "127.0.0.1")
    port = cfg.get("port", 8000)
    print(f"Starting Agent Dispatch Center on http://{host}:{port} ...")
    uvicorn.run(app, host=host, port=port)

