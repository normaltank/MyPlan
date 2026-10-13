"""A decorative image failure needs scope and a responsive terminal controller."""
import base64

import pytest

URL = 'https://faculty.example.edu/list.htm'
SELECTOR = '#wp_news_w7 li'
PAGE = '''<html><head><link rel="stylesheet" href="/theme.css"></head><body>
<aside><a class="selected">Navigation</a></aside>
<div id="wp_news_w7"><ul><li>Person A</li><li>Person B</li></ul></div>
<div id="wp_paging_w7"><ul class="wp_paging">
<li class="pages_count"><em class="per_count">14</em><em class="all_count">2</em></li>
<li class="page_nav"><a class="first" href="javascript:void(0)">first</a>
<a class="prev" href="javascript:void(0)">prev</a><a class="next" href="javascript:void(0)">next</a>
<a class="last" href="javascript:void(0)">last</a></li>
<li class="page_jump"><em class="curr_page">1</em><em class="all_pages">1</em>
<input class="pageNum"><a class="pagingJump" href="javascript:void(0)">jump</a></li>
</ul></div><script>
document.querySelector('.pagingJump').onclick=()=>{
 const value=document.querySelector('.pageNum').value;
 if(value===''){alert('empty');return;} if(Number(value)>1){alert('outside');return;}
};</script></body></html>'''


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('case,expected', [
    ('outside', True), ('inside', False), ('also_inside', False), ('pseudo_inside', False),
    ('hidden_inside', False), ('unresponsive', False), ('multiple_pages', False),
    ('script_error', False), ('xhr_failed', False), ('unknown_selector', False),
    ('hover_inside', False), ('pager_inside', False), ('unreadable_css', False),
    ('new_xhr_failed', False), ('preload', False), ('srcset_inside', False),
    ('input_inside', False), ('video_inside', False), ('source_inside', False),
    ('void_inputs', True), ('changed_input_value', False), ('changed_roster', False),
])
async def test_failed_css_image_requires_complete_component_scope_and_controller(case, expected):
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.failed_image_pager import install_stylesheet_capture, terminal_pagers
    from server.pager_evidence import scoped_terminal_pagers

    selector = 'aside a.selected'
    if case == 'inside':
        selector = '#wp_news_w7 li'
    elif case == 'also_inside':
        selector += ',#wp_news_w7 li'
    elif case == 'pseudo_inside':
        selector = '#wp_news_w7 li::before'
    elif case == 'unknown_selector':
        selector = 'aside :is(a.selected)'
    elif case == 'hover_inside':
        selector += ',#wp_news_w7 li:hover'
    elif case == 'pager_inside':
        selector += ',#wp_paging_w7 .next'
    html = PAGE
    if case == 'hidden_inside':
        html = html.replace('Person A', 'Person A<img hidden src="/icon.gif">')
    elif case == 'unresponsive':
        html = html.replace("if(value===''){alert('empty');return;}", '')
    elif case == 'multiple_pages':
        html = html.replace('class="all_pages">1', 'class="all_pages">2').replace(
            'class="all_count">2', 'class="all_count">28')
        html = html.replace('<ul><li>Person A</li><li>Person B</li></ul>',
                            '<ul>' + ''.join(f'<li>Person {i}</li>' for i in range(14)) + '</ul>')
    elif case == 'script_error':
        html = html.replace('</script>', "throw new Error('fixture failure');</script>")
    elif case == 'xhr_failed':
        html = html.replace('</script>', "fetch('/data');</script>")
    elif case == 'new_xhr_failed':
        html = html.replace('</script>', "document.querySelector('.next').onclick=()=>fetch('/data');</script>")
    elif case == 'unreadable_css':
        html = html.replace('/theme.css', 'https://cdn.example.edu/theme.css')
    elif case == 'preload':
        html = html.replace('</head>', '<link rel="preload" as="image" href="/icon.gif"></head>')
    elif case == 'srcset_inside':
        html = html.replace('Person A', 'Person A<img src="/other.gif" srcset="/icon.gif 2x">')
    elif case == 'input_inside':
        html = html.replace('Person A', 'Person A<input type="image" src="/icon.gif">')
    elif case == 'video_inside':
        html = html.replace('Person A', 'Person A<video poster="/icon.gif"></video>')
    elif case == 'source_inside':
        html = html.replace('Person A', 'Person A<picture><source media="(min-width:99999px)" srcset="/icon.gif">'
                            '<img src="/other.gif"></picture>')
    elif case == 'void_inputs':
        html = html.replace('<input class="pageNum">', '<input class="pageNum"><input type="hidden" value=""></input>')
    elif case == 'changed_input_value':
        html = html.replace('</script>', "document.querySelector('.pageNum').setAttribute('value','7');</script>")
    elif case == 'changed_roster':
        html = html.replace('</script>', "document.querySelector('#wp_news_w7 li').textContent='changed';</script>")
    css = selector + '{background-image:url(/icon.gif);content:"icon";}'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            async def serve(route):
                path = route.request.url.rsplit('/', 1)[-1]
                if path == 'theme.css':
                    await route.fulfill(body=css, content_type='text/css')
                elif path in {'icon.gif', 'data'}:
                    await route.fulfill(status=404, body='missing')
                elif path == 'other.gif':
                    await route.fulfill(body=base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'),
                                        content_type='image/gif')
                else:
                    await route.fulfill(body=html, content_type='text/html')
            await context.route('**/*', serve)
            page = await context.new_page()
            install_request_tracking(page)
            install_stylesheet_capture(page)
            await page.goto(URL, wait_until='networkidle')
            old = await scoped_terminal_pagers(page, SELECTOR)
            assert not any(proof['confirmed'] for proof in old)
            proofs, materials = await terminal_pagers(page, SELECTOR)
            assert any(proof['confirmed'] for proof in proofs) is expected
            if case in {'inside', 'also_inside', 'pseudo_inside', 'hidden_inside', 'hover_inside', 'pager_inside', 'srcset_inside',
                        'input_inside', 'video_inside', 'source_inside'}:
                assert proofs[0]['request_scope_evidence']['samples'][0]['failed_resource_scope']['reason'] == 'image_inside_widget'
            if expected:
                assert proofs[0]['controller_evidence']['probes'] == 2
                assert materials
                assert len(proofs[0]['request_scope_evidence']['samples']) == 3
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_v3_actual_source_stage_submission_and_reading(tmp_path):
    import json
    import copy
    import hashlib
    from pathlib import Path
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan
    from server.recon_browser_mcp import BrowserScout

    source = {'source_role': 'faculty_roster', 'source_container_selector': '#wp_news_w7',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    branch = {'url': URL, **source, 'card_selector': SELECTOR, 'single_page': True,
              'profile_content_selector': 'article', 'page_types': {'pagination': {
                  'type_id': 'pagination.single_page', 'implementation_version': 3}}}
    people = {'a': '张伟', 'b': '李明'}
    html = PAGE.replace('<head>', '<head><title>师资队伍</title>').replace(
        '<body>', '<body><h1>师资队伍</h1><header>样本大学 样本学院</header>')
    html = html.replace('Person A', '<a href="/p/a">张伟</a>').replace('Person B', '<a href="/p/b">李明</a>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            async def serve(route):
                url = route.request.url
                if url.endswith('/theme.css'):
                    await route.fulfill(body='aside a.selected{background-image:url(/icon.gif)}', content_type='text/css')
                elif url.endswith('/icon.gif'):
                    await route.fulfill(status=404, body='missing')
                elif '/p/' in url:
                    name = people[url.rsplit('/', 1)[-1]]
                    await route.fulfill(body=f'<article><h1>{name}</h1><p>职称：教授</p>'
                        '<p>工作单位：样本大学样本学院</p><p>从事完整的教学和学术研究工作，公开研究与任职经历。</p></article>',
                        content_type='text/html; charset=utf-8')
                else:
                    await route.fulfill(body=html, content_type='text/html; charset=utf-8')
            await context.route('**/*', serve)
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            scout = BrowserScout(await context.new_page(), tmp_path / 'scout', 'branch',
                                 {'school': '样本大学', 'college': '样本学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            verified = await scout.call('browser_verify_source', {**source, 'unit_selector': SELECTOR})
            assert json.loads(verified['content'][0]['text'])['confirmed']
            await scout.call('browser_verify', {'card_selector': SELECTOR})
            stage = json.loads((await scout.call('browser_verify_stage',
                {'role': 'pagination', 'branch': branch}))['content'][0]['text'])
            assert stage['results'][0].get('stage_evidence'), stage
            metadata = json.loads(Path(stage['results'][0]['capture_metadata']['evidence_path']).read_bytes())
            from server.agent_recon import selector_evidence_issues
            plan = {'school': '样本大学', 'college': '样本学院', 'branches': [branch]}
            for change in ('missing_scope', 'inside_selector', 'missing_materials', 'fake_dom', 'missing_reference', 'wrong_rule'):
                altered = copy.deepcopy(metadata)
                for sample in altered['evidence']['terminal_pagers'][0]['request_scope_evidence']['samples']:
                    if change == 'missing_scope':
                        sample.pop('failed_resource_scope')
                    elif change == 'inside_selector':
                        sample['failed_resource_scope']['references'][0]['items'][0]['structural_selector'] = '#wp_news_w7 li'
                    elif change == 'fake_dom':
                        sample['failed_resource_scope']['references'][0]['items'][0].update(kind='dom', tag='img', attribute='src')
                    elif change == 'missing_reference':
                        sample['failed_resource_scope']['references'][0]['items'] = []
                    elif change == 'wrong_rule':
                        sample['failed_resource_scope']['references'][0]['items'][0]['location']['rule'] = 999
                if change == 'missing_materials':
                    altered['evidence']['failed_image_materials'] = {}
                raw = json.dumps(altered, ensure_ascii=False).encode()
                path = tmp_path / (change + '.json')
                path.write_bytes(raw)
                packet = copy.deepcopy(stage)
                packet['results'][0]['capture_metadata'] = {'sha256': hashlib.sha256(raw).hexdigest(), 'evidence_path': str(path)}
                packet['results'][0]['stage_evidence']['payload']['terminal_pagers'] = altered['evidence']['terminal_pagers']
                if change == 'missing_materials':
                    from server.failed_image_pager import _digest
                    packet['results'][0]['stage_evidence']['payload']['failed_image_materials_sha256'] = _digest({})
                events = copy.deepcopy(scout.events)
                events[-1]['data']['message']['content'][0]['content'][0]['text'] = json.dumps(packet)
                assert any(issue['status'] == 'stage_type_evidence_unverified'
                           for issue in selector_evidence_issues(plan, events)), change
            await scout.call('browser_verify_profile', {'profile_content_selector': 'article',
                                                      'urls': [URL.rsplit('/', 1)[0] + '/p/' + key for key in people]})
            submitted = json.loads((await scout.call('submit_plan', {'plan': branch}))['content'][0]['text'])
            assert submitted['accepted'], submitted
            result = await run_plan({'school': '样本大学', 'college': '样本学院', 'branches': [branch]},
                                    context, tmp_path / 'capture')
            records = json.loads(Path(result['records_path']).read_text(encoding='utf-8'))
            assert {row['姓名'] for row in records} == set(people.values())
            assert len(records) == 2
        finally:
            await browser.close()
