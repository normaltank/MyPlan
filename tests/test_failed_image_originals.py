"""Byte originals independently constrain the scoped-image and parser changes."""
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup

from server.image_reference_evidence import css_graph, dom_graph
from server.pager_evidence import scope_digest

ROOT = Path('tests/fixtures/pager_failed_images/sanming-basic')


def test_real_original_bytes_roster_pager_and_css_reference_scope():
    manifest = json.loads((ROOT / 'manifest.json').read_bytes())
    for row in manifest['responses']:
        assert hashlib.sha256((ROOT / row['file']).read_bytes()).hexdigest() == row['sha256']
    rendered = (ROOT / 'rendered.html').read_bytes()
    assert hashlib.sha256(rendered).hexdigest() == manifest['rendered_sha256']
    [document] = [row for row in manifest['responses'] if row['kind'] == 'document']
    raw = (ROOT / document['file']).read_bytes().decode('utf-8')
    html = rendered.decode('utf-8')
    expected = manifest['expected']
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select(expected['roster_selector'])) == 6
    assert soup.select_one(expected['pager'] + ' .all_pages').get_text(strip=True) == '1'
    assert scope_digest(raw, expected['roster_selector']) == scope_digest(html, expected['roster_selector'])
    assert scope_digest(raw, expected['pager']) != scope_digest(html, expected['pager'])
    for selector in (expected['roster_selector'], expected['pager']):
        assert scope_digest(raw, selector, _parser='lxml') == scope_digest(html, selector, _parser='lxml')
    css = [(row['url'], (ROOT / row['file']).read_bytes().decode('utf-8'))
           for row in manifest['responses'] if row['kind'] == 'stylesheet']
    failed = {expected['failed_asset']}
    refs = set(css_graph(css, failed, soup, document['url'])) | set(dom_graph(soup, failed, document['url']))
    assert refs
    [widget], [pager] = soup.select(expected['widget']), soup.select(expected['pager'])
    for _, kind, _, paths in refs:
        assert kind == 'css'
        assert paths
        for selected in paths:
            [node] = soup.select(selected)
            assert widget not in node.parents and pager not in node.parents
            assert node not in widget.parents and node not in pager.parents
