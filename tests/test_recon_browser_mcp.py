import asyncio
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.recon_browser_mcp import BrowserScout, MAX_INLINE_BYTES
from server.agent_recon import selector_evidence_issues


def test_oversized_ranges_keep_explicit_remaining_range_and_complete_local_evidence(tmp_path):
    scout = BrowserScout(None, tmp_path)
    value = {"total": 12, "offset": 0, "end": 12, "nextOffset": None,
             "items": [{"url": "https://example.edu/" + "x" * 1500 + str(i)} for i in range(12)]}
    reply = scout.text_result(value)
    text = reply["content"][0]["text"]
    assert len(text.encode("utf-8")) <= MAX_INLINE_BYTES
    compact = json.loads(text)
    assert compact["nextOffset"] == len(compact["items"]) < 12
    assert len(json.loads(next(tmp_path.glob("*.json")).read_text())["items"]) == 12
    reply = scout.text_result({"html": "x" * 20000})
    assert json.loads(reply["content"][0]["text"])["complete"] is False


@pytest.mark.asyncio
async def test_mcp_protocol_drives_real_browser_navigation_and_verification(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = ('<nav>' + ''.join(f'<a href="/branch/{i}">分支{i}</a>' for i in range(25)) +
                    '</nav><main><ul>' + ''.join(f'<li><a href="/person/{i}">教师{i}</a></li>' for i in range(37)) +
                    '</ul><a href="/" id="next" class="p_next" onclick="event.preventDefault();document.querySelectorAll(\'main li a\').forEach((n,i)=>n.textContent=\'Page 2 \'+i)">next</a></main>').encode("utf-8")
            if self.path.startswith('/person/'):
                body = '<article><p>完整正文与学术资料留在本地。</p></article>'.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    process = subprocess.Popen([sys.executable, "-m", "server.recon_browser_mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})

    async def request(index, method, params=None):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": index, "method": method,
                                       "params": params or {}}) + "\n")
        process.stdin.flush()
        reply = json.loads(await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 30))
        assert reply["id"] == index and "error" not in reply
        return reply["result"]

    try:
        assert (await request(1, "initialize"))["capabilities"] == {"tools": {}}
        tools = (await request(2, "tools/list"))["tools"]
        assert len(tools) == 8
        reply = await request(3, "tools/call", {"name": "browser_navigate",
                                              "arguments": {"url": f"http://127.0.0.1:{http.server_port}/"}})
        assert not reply["isError"]
        assert json.loads(reply["content"][0]["text"])["navigation"]["total"] == 63
        reply = await request(4, "tools/call", {"name": "browser_verify",
                                              "arguments": {"card_selector": "main ul > li"}})
        assert json.loads(reply["content"][0]["text"])["cardCount"] == 37
        assert json.loads(reply["content"][0]["text"])["pagination"]["total"] == 1
        assert "教师" not in reply["content"][0]["text"]
        url = f"http://127.0.0.1:{http.server_port}/"
        plan = {"branches": [{"url": url, "card_selector": "main ul > li", "next_selector": "#next",
                              "profile_content_selector": "article"}]}
        events = []
        for index, name, arguments in [
            (5, "browser_verify", {"card_selector": "main ul > li", "next_selector": "#next"}),
            (6, "browser_click", {"selector": "#next"}),
            (7, "browser_verify", {"card_selector": "main ul > li"}),
        ]:
            result = await request(index, "tools/call", {"name": name, "arguments": arguments})
            assert not result["isError"]
            events.extend([
                {"type": "tool/call", "data": {"callId": str(index), "name": "mcp__playwright__" + name,
                                                  "arguments": json.dumps(arguments)}},
                {"type": "tool/result", "data": {"message": {"content": [{"type": "tool-result",
                    "toolCallId": str(index), **result}]}}}])
        assert selector_evidence_issues(plan, events[:2])[0]["status"] == "pagination_not_verified"
        assert selector_evidence_issues(plan, events)[0]['status'] == 'profile_content_not_verified'
        for offset in range(2):
            await request(8 + offset * 2, 'tools/call', {'name': 'browser_navigate',
                          'arguments': {'url': url + f'person/{offset}'}})
            args = {'profile_content_selector': 'article'}
            result = await request(9 + offset * 2, 'tools/call', {'name': 'browser_verify_profile', 'arguments': args})
            cid = f'profile-{offset}'
            events.extend([
                {'type':'tool/call', 'data':{'callId':cid, 'name':'mcp__playwright__browser_verify_profile',
                                            'arguments':json.dumps(args)}},
                {'type':'tool/result', 'data':{'message':{'content':[{'toolCallId':cid, **result}]}}}])
        assert selector_evidence_issues(plan, events) == []
    finally:
        process.stdin.close()
        await asyncio.wait_for(asyncio.to_thread(process.wait), 15)
        process.stdout.close()
        process.stderr.close()
        http.shutdown()
        http.server_close()
