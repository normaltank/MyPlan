"""学院官方师资范围的人工确认入口。

判定本身在 ``agent_queue.mark_no_faculty``：按 0 人完成，记下判定人和原状态。
这里只组织复核页中的操作；结论由实际核实官网的人提交：

- 已结束的学院任务均有师资范围复核入口，已有字段/来源材料时继续保留那些复核操作。
- 大学交付复核页：每个已结束的学院列出状态、人数和前几位姓名。学院按“完成”收尾但采错了人
  （集美继续教育学院采到的是行政办公室），程序发现不了，只有人在交付前看名单时能发现。
"""
from __future__ import annotations

from html import escape
import json

from server.faculty_identity import field_value

NAME_PREVIEW = 8
VERDICT_GUIDANCE = ('请核对官网正式列入本学院的全部师资，包括兼职、外聘。'
                   '访问失败、自动零结果或没有单独师资栏目，不能单独证明无师资。')


def can_judge(status: str, stats: dict | None) -> bool:
    """Same rule as ``mark_no_faculty``: a finished college not judged before."""
    return (status in ("failed", "completed") and not (stats or {}).get("no_faculty")
            and not (stats or {}).get('official_site_issue'))


def script(csrf_token: str) -> str:
    data = json.dumps({"csrf": csrf_token}, ensure_ascii=False).replace("<", "\\u003c")
    return ("<script>const noFaculty=" + data + ";"
            "document.querySelectorAll('button.no-faculty').forEach(button=>button.onclick=async()=>{"
            "if(!confirm('已核实「'+button.dataset.college+'」全部官方师资范围（含兼职、外聘），确认无师资？\\n"
            "访问失败或没找到栏目不等于无师资。判定后按 0 人人工结案，之前采到的人不进大学表。'))return;"
            "const status=button.closest('.no-faculty-row').querySelector('.status');button.disabled=true;"
            "try{const response=await fetch('/api/task/'+encodeURIComponent(button.dataset.task)+'/no-faculty',"
            "{method:'POST',headers:{'X-CSRF-Token':noFaculty.csrf}});"
            "if(!response.ok){const body=await response.json().catch(()=>({}));"
            "throw new Error(body.detail||('HTTP '+response.status));}"
            "button.remove();status.textContent='已判为无师资，按 0 人完成';}"
            "catch(error){button.disabled=false;status.textContent='保存失败：'+error.message;}});"
            "</script>")


def _button(task_id: str, college: str) -> str:
    return ('<button type="button" class="secondary no-faculty" data-task="' + escape(task_id, quote=True)
            + '" data-college="' + escape(college, quote=True) + '">确认无师资</button>')


def _judged(stats: dict) -> str:
    return "已判为无师资（人工判定），按 0 人完成" if stats.get("no_faculty") else ""


def render_college_page(task, *, csrf_token: str, return_url: str = "/") -> str:
    """A college whose scout found no roster: the reviewer checks the official site."""
    from server.image_bio_review import _REVIEW_CSS, _base_css, _page_link
    links = "".join("<li>" + _page_link(url) + "</li>" for url in [task.url, *task.roster_urls] if url)
    action = "" if not can_judge(task.status, task.stats) else (
        '<div class="actions">' + _button(task.task_id, task.college) + "</div>")
    return ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>学院复核 · 高校师资采集平台</title><style>" + _base_css() + _REVIEW_CSS + "</style>"
            '<p class="back"><a href="' + escape(return_url, quote=True) + '">← 返回采集平台</a></p>'
            "<h1>" + escape(task.school) + " · " + escape(task.college) + "</h1>"
            '<p class="intro">' + VERDICT_GUIDANCE + '</p>'
            '<section class="card no-faculty-row"><h2>官网</h2><ul>' + (links or "<li>（没有入口网址）</li>")
            + '</ul><p class="meta">侦察结果：' + escape(task.error or "（无）") + "</p>" + action
            + '<p class="status" role="status">' + _judged(task.stats) + "</p></section>"
            + script(csrf_token) + "</html>")


def render_action_section(task, *, csrf_token: str) -> str:
    """Keep the college verdict available beside field/source review materials."""
    if not can_judge(task.status, task.stats):
        return ''
    return ('<section class="card no-faculty-row"><h2>学院师资范围确认</h2><p>' + VERDICT_GUIDANCE
            + '</p><div class="actions">' + _button(task.task_id, task.college)
            + '</div><p class="status" role="status"></p></section>' + script(csrf_token))


def colleges_section(tasks: list[dict]) -> str:
    """Every college of the delivery, with what it would deliver, and the verdict."""
    rows = []
    for task in tasks:
        stats = task.get("stats") or {}
        names = [field_value(record, "姓名") for record in task.get("records") or []]
        preview = "、".join(name for name in names[:NAME_PREVIEW] if name)
        state = {"completed": "采集完成", "failed": "未完成"}.get(task["status"], "采集中")
        summary = (state + " · " + str(len(names)) + " 人"
                   + ("：" + preview + ("等" if len(names) > NAME_PREVIEW else "") if preview else ""))
        action = _button(task["task_id"], task["college"]) if can_judge(task["status"], stats) else ""
        rows.append('<div class="no-faculty-row observation"><p><strong>' + escape(task["college"])
                    + "</strong></p><p class=\"meta\">" + escape(summary) + "</p>" + action
                    + '<p class="status" role="status">' + _judged(stats) + "</p></div>")
    return ('<h2>各学院</h2><p>' + VERDICT_GUIDANCE + '</p>'
            + '<section class="card">' + "".join(rows) + "</section>")
