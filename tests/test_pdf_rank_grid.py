"""A rank-grouped name grid is many independent cells, not biography headings."""
import pytest
import pymupdf

from server.pdf_roster import read_pdf_rosters


def original_grid(*, unknown=False, many=False, wrapped=False, raster=False):
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=500)
    for y, rank, names in [(80, '教授', ['张伟', '李明', '王奔', '赵强']),
                            (200, '讲师/实验师/工程师', ['刘文伟', '孙小明', 'TBD' if unknown else '陈静', '王芳'])]:
        width = pymupdf.get_text_length(rank, fontname='china-s', fontsize=15)
        page.insert_text((300-width/2, y), rank, fontname='china-s', fontsize=15)
        page.draw_rect(pymupdf.Rect(60,y-20,540,y+10), color=(0,0,0))
        for index, name in enumerate(names):
            if index == 0 and rank == '教授' and many:
                name = '张伟、李明'
            x = 180 + (index % 2) * 240
            width = pymupdf.get_text_length(name, fontname='china-s', fontsize=13)
            if raster and index == 0 and rank == '教授':
                import io
                from PIL import Image
                image = io.BytesIO()
                Image.new('RGB', (40, 16), 'black').save(image, format='PNG')
                page.insert_image(pymupdf.Rect(x-20, y+15, x+20, y+31), stream=image.getvalue())
            else:
                page.insert_text((x-width/2, y+35+(index//2)*30), name, fontname='china-s', fontsize=13)
            top = y+10+(index//2)*30
            left = 60+(index%2)*240
            page.draw_rect(pymupdf.Rect(left,top,left+240,top+30), color=(0,0,0))
            if index == 2 and rank != '教授' and wrapped:
                # A second full token in the same native box is not a new row.
                page.insert_text((x-15,top+25), '王强', fontname='china-s', fontsize=10)
    return doc.tobytes()


@pytest.mark.asyncio
async def test_read_every_grid_cell_and_preserve_ambiguous_rank_group(tmp_path):
    raw = original_grid()
    async def fetch(_url):
        return raw
    result = await read_pdf_rosters([{'url': 'https://faculty.example.edu/list.htm',
                                     'media_urls': ['https://faculty.example.edu/list.pdf'],
                                     'source_kind': 'university_faculty_roster'}],
                                   fetch_asset=fetch, evidence_dir=tmp_path, ocr_engine=object())
    assert len(result['records']) == 8
    assert result['unresolved'] == []
    assert {r.title for r in result['records'][:4]} == {'教授'}
    assert {r.title for r in result['records'][4:]} == {'教师'}
    assert all(not r.segment.text for r in result['records'])


@pytest.mark.asyncio
async def test_unknown_grid_cell_blocks_and_has_bound_original_evidence(tmp_path):
    raw = original_grid(unknown=True)
    async def fetch(_url):
        return raw
    result = await read_pdf_rosters([{'url': 'https://faculty.example.edu/list.htm',
                                     'media_urls': ['https://faculty.example.edu/list.pdf'],
                                     'source_kind': 'university_faculty_roster'}],
                                   fetch_asset=fetch, evidence_dir=tmp_path, ocr_engine=object())
    assert len(result['records']) == 7
    [issue] = result['unresolved']
    assert issue['status'] == 'rare_surname_needs_review'
    assert issue['reason'] == 'pdf_rank_grid_name_cell'
    assert 'pdf_sha256' in issue['card_text'] and 'cell' in issue['card_text']


@pytest.mark.parametrize('option', ['many','wrapped'])
@pytest.mark.asyncio
async def test_many_or_ambiguous_full_tokens_in_one_cell_cannot_silently_pass(tmp_path, option):
    raw = original_grid(**{option: True})
    async def fetch(_url):
        return raw
    result = await read_pdf_rosters([{'url': 'https://faculty.example.edu/list.htm',
                                     'media_urls': ['https://faculty.example.edu/list.pdf'],
                                     'source_kind': 'university_faculty_roster'}],
                                   fetch_asset=fetch, evidence_dir=tmp_path, ocr_engine=object())
    assert result['unresolved']
    assert len(result['records']) < 8


@pytest.mark.asyncio
async def test_small_image_cell_stays_unread_even_on_text_layer_page(tmp_path):
    async def fetch(_url):
        return original_grid(raster=True)
    result = await read_pdf_rosters([{'url': 'https://faculty.example.edu/list.htm',
                                     'media_urls': ['https://faculty.example.edu/list.pdf'],
                                     'source_kind': 'university_faculty_roster'}],
                                   fetch_asset=fetch, evidence_dir=tmp_path, ocr_engine=object())
    assert len(result['records']) == 7
    assert any(item['status'] == 'pdf_roster_raster_unread' for item in result['unresolved'])


@pytest.mark.parametrize('cell', ['张伟、李明', '张伟，李明', '张伟,李明', '张伟 李明',
                                '张伟 哔甲', '张伟 哔', '张 伟 李 明'])
def test_single_name_decision_cannot_clear_multi_member_cell(cell):
    from server.pdf_roster import TableLayout, TableRow, reconcile_table
    table = TableLayout([TableRow(1,1,{'姓名':cell},0,[],cell_bbox=(0,0,30,20))],
                        [{'heading':'教授', 'header':['姓名'], 'page':1}],[],{},kind='rank_grid')
    result = reconcile_table(table, page_url='https://faculty.example.edu/list.htm',
                             media_url='https://faculty.example.edu/list.pdf', sha256='0'*64,
                             title_default='教师', name_decisions={})
    assert result['records'] == []
    [issue] = result['unresolved']
    assert issue['status'] == 'pdf_roster_table_row_unassigned'
    assert issue['reason'] == 'ambiguous_members_in_grid_cell'
    assert 'id' not in issue  # No single-name decision can bypass structure repair.


@pytest.mark.parametrize('cell,name', [('张 伟','张伟'),('刘 文 伟','刘文伟')])
def test_unambiguous_padded_name_uses_shared_list_rule(cell,name):
    from server.pdf_roster import TableLayout, TableRow, reconcile_table
    table = TableLayout([TableRow(1,1,{'姓名':cell},0,[],cell_bbox=(0,0,30,20))],
                        [{'heading':'教授', 'header':['姓名'], 'page':1}],[],{},kind='rank_grid')
    result = reconcile_table(table, page_url='https://faculty.example.edu/list.htm',
                             media_url='https://faculty.example.edu/list.pdf', sha256='0'*64,
                             title_default='教师', name_decisions={})
    assert result['unresolved'] == []
    assert [record.name for record in result['records']] == [name]
