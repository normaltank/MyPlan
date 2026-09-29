import json
from pathlib import Path

import pytest

from server.browser_poster_tool import capture_poster


class PosterPage:
    def __init__(self, context):
        self.context = context
        self.url = ""

    async def goto(self, url, **_kwargs):
        self.url = url
        self.context.visits.append(url)
        return None

    async def screenshot(self, **_kwargs):
        return b"rendered browser screenshot"

    async def wait_for_load_state(self, *_args, **_kwargs):
        self.context.load_waited = True

    async def evaluate(self, expression, *args):
        self.context.evaluations.append((expression, args))
        if "scrollHeight" in expression:
            return {"height": 800, "viewport": 800}
        if "naturalWidth" in expression:
            return {"image_count": 1, "unloaded_image_count": 0}
        return None

    async def wait_for_timeout(self, *_args):
        pass

    async def close(self):
        pass


class PosterContext:
    def __init__(self):
        self.visits = []
        self.evaluations = []
        self.load_waited = False

    async def new_page(self):
        return PosterPage(self)


@pytest.mark.asyncio
async def test_poster_ocr_uses_browser_screenshot_and_keeps_raw_lines_for_review(tmp_path):
    context = PosterContext()

    def ocr(image_bytes):
        assert image_bytes == b"rendered browser screenshot"
        return [[[0, 0], "张富有 刘圣洁", 0.91], [[0, 1], "教授", 0.88]], None

    result = await capture_poster("https://faculty.example.edu/poster", context, tmp_path, ocr_engine=ocr)

    assert context.visits == ["https://faculty.example.edu/poster"]
    assert result["status"] == "agent_review_required"
    assert result["line_count"] == 2
    assert context.load_waited is True
    assert result["unloaded_image_count"] == 0
    assert Path(result["screenshot_path"]).read_bytes() == b"rendered browser screenshot"
    lines = json.loads(Path(result["ocr_path"]).read_text(encoding="utf-8"))
    assert lines[0]["text"] == "张富有 刘圣洁"
