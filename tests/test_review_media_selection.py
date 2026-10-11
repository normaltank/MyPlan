"""Human selection and reversible saves use the existing per-evidence endpoint."""
import json

import pytest
from playwright.async_api import async_playwright

from server.image_bio_review import render_review_page
from server import missing_media_review


def page_html():
    return render_review_page([], media_kind_items=[{
        'id': str(index), 'evidence_basis': 'missing_source_snapshot', 'http_status': 404,
        'profile_url': f'https://faculty.example.edu/{index}',
        'media_url': f'https://faculty.example.edu/{index}.jpg',
        'context_text': '保存的原始正文', 'saved_verdict': 'unreadable' if index == 0 else '',
    } for index in range(3)], media_kind_decision_url='/decision', csrf_token='fixture-csrf')


def test_saved_photo_can_be_changed_without_changing_its_source(tmp_path):
    html = '<article><p>教师简介完整原文。</p><img src="/photo.jpg"></article>'
    item = missing_media_review.prepare_item(html, 'https://faculty.example.edu/1',
        'article', None, 'https://faculty.example.edu/photo.jpg', 404)
    assert item is not None
    path = tmp_path / missing_media_review.DECISIONS_FILE
    missing_media_review.save_decision(path, item, 'photo', reviewed_by=1)
    assert missing_media_review.confirmed(path, item)
    missing_media_review.save_decision(path, item, 'unreadable', reviewed_by=1)
    assert not missing_media_review.confirmed(path, item)
    missing_media_review.save_decision(path, item, 'photo', reviewed_by=1)
    assert missing_media_review.confirmed(path, item)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_bulk_saves_only_selected_items_and_retries_failed_items(tmp_path):
    calls, stored = [], {}
    fail_second = True
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def serve(route):
            nonlocal fail_second
            if route.request.url.endswith('/decision'):
                payload = route.request.post_data_json
                calls.append(payload)
                assert route.request.headers['x-csrf-token'] == 'fixture-csrf'
                if payload['id'] == '1' and fail_second:
                    await route.fulfill(status=409, json={'detail': '证据已变化'})
                else:
                    stored[payload['id']] = payload['verdict']
                    await route.fulfill(status=204)
            else:
                await route.fulfill(body=page_html(), content_type='text/html; charset=utf-8')
        await context.route('**/*', serve)
        page = await context.new_page()
        await page.goto('https://fixture.example.edu/review')
        checks = page.locator('[data-media-select]')
        await checks.nth(0).check()
        await checks.nth(1).check()
        await page.get_by_role('button', name='所选仅为人物照片，不含简介或邮箱', exact=True).click()
        await page.locator('#media-batch-status').filter(has_text='已保存 1 项，失败 1 项').wait_for()
        assert stored == {'0': 'photo'}
        assert [call['id'] for call in calls] == ['0', '1']
        assert await checks.nth(0).is_checked() is False
        assert await checks.nth(1).is_checked() is True
        fail_second = False
        await page.locator('[data-media-batch="photo"]').click()
        await page.locator('#media-batch-status').filter(has_text='已保存 1 项，失败 0 项').wait_for()
        assert stored == {'0': 'photo', '1': 'photo'}
        # A saved choice can be changed; it is not locked by a successful batch.
        await page.locator('.media-kind-card[data-id="0"] [data-verdict="unreadable"]').click()
        await page.locator('.media-kind-card[data-id="0"] .status').filter(has_text='已保存：用途未知').wait_for()
        assert stored['0'] == 'unreadable'
        assert await page.locator('.media-kind-card[data-id="0"] [data-verdict="photo"]').is_enabled()
        assert '2' not in stored
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_select_all_clear_and_empty_save_do_not_submit_decisions(tmp_path):
    posts = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        async def serve(route):
            if route.request.method == 'POST':
                posts.append(json.loads(route.request.post_data))
                await route.fulfill(status=204)
            else:
                await route.fulfill(body=page_html(), content_type='text/html; charset=utf-8')
        await page.route('**/*', serve)
        await page.goto('https://fixture.example.edu/review')
        await page.get_by_role('button', name='全选图片', exact=True).click()
        assert await page.locator('[data-media-select]:checked').count() == 3
        await page.get_by_role('button', name='取消选择', exact=True).click()
        assert await page.locator('[data-media-select]:checked').count() == 0
        await page.locator('[data-media-batch="photo"]').click()
        assert await page.locator('#media-batch-status').inner_text() == '请先勾选要保存的图片'
        assert posts == []
        assert '已保存：用途未知或含文字' in await page.locator('.media-kind-card[data-id="0"] .status').inner_text()
        await browser.close()
