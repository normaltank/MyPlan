"""WebPlus-style rosters put the row ordinal inside the link text.

山东中医药大学 卫生管理学院 卫生管理系 renders::

    <li><a href="../info/1081/2374.htm">2王颖</a><div class="sj">2026-05-22</div></li>
    <li><a href="../info/1081/2376.htm">4 丁保扬</a><div class="sj">2026-05-22</div></li>
    <li><a href="...">1.宫春博（劳动与社会保障教研室）</a>...</li>

primary_tokens() fed those strings into the token stream unchanged, so
faculty_extractor's `toks[0]` name became the serial number ("4", "3", "2") and
the unspaced forms ("2王颖") produced no name token at all — the card was dropped,
the roster audit reported roster_card_mismatch (3 visible / 2 extracted), and two
real teachers (王颖 / 宫春博) were missing from the delivery.

A name token never contains a digit, so the index is stripped and any remaining
digit-bearing token (row dates, ids) is discarded.
"""
from bs4 import BeautifulSoup

from Opus.iso_mining import primary_tokens
from server.faculty_extractor import FacultyDirectoryExtractor

ROSTER_HTML = """<html><body><div class="listcon"><ul class="listul">
<li><a href="/info/2376.htm">4 丁保扬</a><div class="sj">2026-05-22</div></li>
<li><a href="/info/2375.htm">3 沈郁淇</a><div class="sj">2026-05-22</div></li>
<li><a href="/info/2374.htm">2王颖</a><div class="sj">2026-05-22</div></li>
<li><a href="/info/2394.htm">1.宫春博（劳动与社会保障教研室）</a><div class="sj">2026-05-22</div></li>
<li><a href="/info/2390.htm">张迪</a><div class="sj">2025-08-08</div></li>
</ul></div></body></html>"""


def test_tokens_drop_serials_and_row_dates():
    soup = BeautifulSoup(ROSTER_HTML, "html.parser")
    li = soup.select("ul.listul > li")[2]          # '2王颖'
    toks = primary_tokens([li])
    assert "王颖" in toks
    assert not any(any(ch.isdigit() for ch in t) for t in toks)


def test_extractor_returns_people_not_serials():
    candidates = FacultyDirectoryExtractor.extract(ROSTER_HTML, "http://glxy.example.edu.cn/szdw/wsglx.htm")
    names = [c.name for c in candidates]
    assert len(candidates) == 5, names
    assert set(names) == {"丁保扬", "沈郁淇", "王颖", "宫春博", "张迪"}, names
    assert not any(name.isdigit() for name in names)
