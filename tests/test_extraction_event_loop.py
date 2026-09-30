"""Lease heartbeats keep running during deterministic field extraction."""
import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path

import pytest

from server import browser_batch_tool as batch
from test_browser_batch_tool import FakeContext, profile, roster


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['candidates', 'enrichment'])
async def test_heartbeat_runs_while_field_extraction_is_busy(tmp_path, monkeypatch, stage):
    entered, finished = threading.Event(), threading.Event()
    original = (batch.FacultyHarvestPipeline.harvest_candidates if stage == 'candidates'
                else batch._enrich_extracted_records)

    def slow(*args, **kwargs):
        entered.set()
        try:
            time.sleep(2)
            return original(*args, **kwargs)
        finally:
            finished.set()

    if stage == 'candidates':
        monkeypatch.setattr(batch.FacultyHarvestPipeline, 'harvest_candidates', slow)
    else:
        monkeypatch.setattr(batch, '_enrich_extracted_records', slow)
    ticks = []

    async def heartbeat():
        while True:
            if entered.is_set() and not finished.is_set():
                ticks.append(time.monotonic())
            await asyncio.sleep(0.1)

    base = 'https://faculty.example.edu'
    context = FakeContext({base + '/roster': roster([(1, '张富有')]),
                           base + '/p/1.htm': profile('张富有', '1234567890@example.edu')})
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [
        {'url': base + '/roster', 'single_page': True, 'card_selector': 'main li'}]}
    ticker = asyncio.create_task(heartbeat())
    try:
        report = await batch.run_plan(plan, context, tmp_path)
    finally:
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
    assert len(ticks) >= 5, 'the worker event loop stalled during field extraction'
    assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.5
    assert report['delivery_ready'] is True
    rows = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
    assert len(rows) == 1
    assert rows[0]['邮箱'] == '1234567890@example.edu'
    assert rows[0]['简介'] == '张富有\n邮箱：1234567890@example.edu\n完整履历：张富有的学习经历、科研项目、论文甲。'
