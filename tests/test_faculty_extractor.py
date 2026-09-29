"""
TDD Tests for FacultyExtractor: Slice 1 - FacultyDirectoryExtractor
Verifies DOM Isomorphic Clustering without keyword blacklists.
"""

import os

import pytest
from server.faculty_extractor import FacultyDirectoryExtractor, FacultyCandidate

SAMPLE_DIRECTORY_HTML = """
<!DOCTYPE html>
<html>
<head><title>土木与交通学院 - 师资队伍</title></head>
<body>
    <!-- Top Global Nav: Non-teacher links -->
    <div class="header">
        <ul class="nav-menu">
            <li><a href="/index.htm">网站首页</a></li>
            <li><a href="/xygk.htm">学院概况</a></li>
            <li><a href="/xydw.htm">学院党委</a></li>
            <li><a href="/xgdt.htm">学工动态</a></li>
            <li><a href="/fmzl.htm">发明专利</a></li>
            <li><a href="/kyfx.htm">科研方向</a></li>
        </ul>
    </div>

    <div class="main-container">
        <!-- Sidebar Menu: Non-teacher category links -->
        <div class="sidebar">
            <ul class="sub-nav">
                <li><a href="/szdw/list.htm">师资概况</a></li>
                <li><a href="/szdw/msfc.htm">名师引航</a></li>
                <li><a href="/szdw/rczp.htm">人才招聘</a></li>
            </ul>
        </div>

        <!-- Dominant Faculty List Container (Isomorphic Subtrees) -->
        <div class="content-box">
            <ul class="faculty-grid">
                <li class="teacher-item">
                    <div class="avatar"><img src="/pic/1.jpg" /></div>
                    <div class="info">
                        <a href="info/1001.htm">张富有</a>
                        <span class="title">教授</span>
                    </div>
                </li>
                <li class="teacher-item">
                    <div class="avatar"><img src="/pic/2.jpg" /></div>
                    <div class="info">
                        <a href="info/1002.htm">刘圣洁</a>
                        <span class="title">副教授</span>
                    </div>
                </li>
                <li class="teacher-item">
                    <div class="avatar"><img src="/pic/3.jpg" /></div>
                    <div class="info">
                        <!-- Space inside name: 孙 洁 -->
                        <a href="info/1003.htm">孙 洁</a>
                        <span class="title">讲师</span>
                    </div>
                </li>
                <li class="teacher-item">
                    <div class="avatar"><img src="/pic/4.jpg" /></div>
                    <div class="info">
                        <!-- Name containing '党' (proves no blacklisting of '党') -->
                        <a href="info/1004.htm">党建国</a>
                        <span class="title">教授</span>
                    </div>
                </li>
                <li class="teacher-item">
                    <div class="avatar"><img src="/pic/5.jpg" /></div>
                    <div class="info">
                        <a href="info/1005.htm">徐金霞</a>
                        <span class="title">副教授</span>
                    </div>
                </li>
            </ul>

            <!-- Pagination Bar: Non-teacher navigation items -->
            <div class="pagination">
                <a href="?page=1" class="page-link">首页</a>
                <a href="?page=1" class="page-link">上一页</a>
                <a href="?page=1" class="page-num current">1</a>
                <a href="?page=2" class="page-num">2</a>
                <a href="?page=2" class="page-link">下一页</a>
                <a href="?page=2" class="page-link">尾页</a>
            </div>
        </div>
    </div>

    <!-- Institutional Footer -->
    <div class="footer">
        <p>版权所有：河海大学土木与交通学院 联系邮箱：sdy@hhu.edu.cn</p>
    </div>
</body>
</html>
"""

_LEGACY_PAGES = os.path.join(os.path.dirname(__file__), "fixtures", "legacy_pages")

def test_extract_directory_isomorphic_clustering():
    """
    Slice 1: Test that FacultyDirectoryExtractor extracts only the faculty members
    from the dominant isomorphic cluster, without using keyword blacklists.
    """
    base_url = "https://ccte.hhu.edu.cn/jsxq/list.htm"
    candidates = FacultyDirectoryExtractor.extract(SAMPLE_DIRECTORY_HTML, base_url)

    # Assert exact count of real scholars (5 items)
    assert len(candidates) == 5, f"Expected 5 faculty members, got {len(candidates)}: {[c.name for c in candidates]}"

    names = [c.name for c in candidates]
    expected_names = ["张富有", "刘圣洁", "孙洁", "党建国", "徐金霞"]
    assert names == expected_names, f"Expected {expected_names}, got {names}"

    # Verify whitespace normalization ('孙 洁' -> '孙洁')
    assert "孙洁" in names
    assert "孙 洁" not in names

    # Verify no accidental blacklisting of scholar named '党建国'
    assert "党建国" in names

    # Verify zero contamination from nav/sidebar/pagination/footer
    forbidden = ["学院党委", "发明专利", "学工动态", "网站首页", "师资概况", "上一页", "下一页", "尾页"]
    for word in forbidden:
        assert word not in names, f"Contaminated with '{word}'!"

    # Verify resolved URLs
    assert candidates[0].url == "https://ccte.hhu.edu.cn/jsxq/info/1001.htm"


def test_extract_profile_layout_aware_and_employee_email():
    """
    Slice 2: Test FacultyProfileExtractor with layout-aware tokenization:
    - Adjoining phone and email in table cells do not concatenate.
    - Numeric employee ID email (e.g. 8843120002@jiangnan.edu.cn) is preserved intact.
    - Full bio retains complete resume paragraphs.
    """
    from server.faculty_extractor import FacultyProfileExtractor

    # Case A: Table cells with telephone adjacent to email
    sample_table_html = """
    <!DOCTYPE html>
    <html>
    <head><title>王文龙 - 教师主页</title></head>
    <body>
        <div class="header"><a href="/">首页</a></div>
        <div class="article-content">
            <h1>王文龙</h1>
            <p class="title-bar">副教授 / 博士生导师</p>
            <table class="info-table">
                <tr><th>办公电话</th><td>85918219</td></tr>
                <tr><th>电子邮箱</th><td>Wwenlong2011@163.com</td></tr>
                <tr><th>研究方向</th><td>发酵工程与合成生物学</td></tr>
            </table>
            <div class="bio-body">
                <h3>个人履历</h3>
                <p>2010年获江南大学发酵工程博士学位。长期从事工业生物技术研究，主持国家自然科学基金等项目多项，发表学术论文40余篇。</p>
            </div>
        </div>
        <div class="footer"><p>联系我们: office@jiangnan.edu.cn</p></div>
    </body>
    </html>
    """

    profile_a = FacultyProfileExtractor.extract(sample_table_html, "https://example.edu.cn/w.htm", fallback_name="王文龙")
    assert profile_a.name == "王文龙"
    assert profile_a.title == "副教授"
    # Crucial assertion: email must NOT contain the telephone prefix '85918219'
    assert profile_a.email == "Wwenlong2011@163.com"
    assert "85918219" not in profile_a.email
    assert "2010年获江南大学发酵工程博士学位" in profile_a.full_bio

    # Case B: Numeric Employee ID Email (e.g. 殷俊)
    sample_numeric_email_html = """
    <!DOCTYPE html>
    <html>
    <head><title>殷俊 - 教师介绍</title></head>
    <body>
        <div class="main-content">
            <h1>殷俊</h1>
            <p>教授、系主任</p>
            <p>办公电话：0510-85910000</p>
            <p>联系邮箱：8843120002@jiangnan.edu.cn</p>
            <div class="resume">
                <p>从事数字媒体艺术与交互设计研究，教育部艺术学指导委员会委员。</p>
            </div>
        </div>
    </body>
    </html>
    """

    profile_b = FacultyProfileExtractor.extract(sample_numeric_email_html, "https://example.edu.cn/y.htm", fallback_name="殷俊")
    assert profile_b.name == "殷俊"
    assert profile_b.title == "教授"
    # Crucial assertion: Employee ID email must be preserved 100% intact, not stripped by phone regex
    assert profile_b.email == "8843120002@jiangnan.edu.cn"


def test_mecc_scoping_and_split_span_email():
    """
    Slice 3: Test MECC Scoping:
    - Institutional footer email (e.g. sdy@hhu.edu.cn) is physically isolated; scholar without personal email gets email=''.
    - Anti-scraping split spans (<span>xujinxia</span><span>@</span><span>hhu.edu.cn</span>) are seamlessly joined inline.
    """
    from server.faculty_extractor import FacultyProfileExtractor

    # Case A: Scholar with NO personal email; footer contains college public contact email
    sample_footer_email_html = """
    <!DOCTYPE html>
    <html>
    <head><title>刘小艳 - 教师详情</title></head>
    <body>
        <div class="header"><h1>土木与交通学院</h1></div>
        <div class="main-content">
            <div class="article-content">
                <h2>刘小艳</h2>
                <p>副教授，硕士生导师</p>
                <div class="bio">
                    <p>主要从事结构工程防灾减灾与智能监测研究。</p>
                </div>
            </div>
        </div>
        <!-- Universal institutional footer containing college admin email -->
        <div class="footer">
            <p>通讯地址：南京市西康路1号 邮编：210098</p>
            <p>学院联系邮箱：sdy@hhu.edu.cn 电话：025-83786635</p>
        </div>
    </body>
    </html>
    """

    profile_a = FacultyProfileExtractor.extract(sample_footer_email_html, "https://ccte.hhu.edu.cn/lxy.htm", fallback_name="刘小艳")
    assert profile_a.name == "刘小艳"
    # Crucial assertion: Must NOT inherit sdy@hhu.edu.cn from the footer!
    assert profile_a.email == "", f"Public footer email leaked! Got: {profile_a.email}"

    # Case B: Email split across sibling inline spans (Boda CMS obfuscation)
    sample_split_span_html = """
    <!DOCTYPE html>
    <html>
    <head><title>徐金霞 - 教师主页</title></head>
    <body>
        <div class="article">
            <h1>徐金霞</h1>
            <p>职称：副教授</p>
            <p class="contact">
                电子邮箱：<span>xujinxia</span><span>@</span><span>hhu.edu.cn</span>
            </p>
            <div class="content">
                <p>主持国家自然科学基金青年基金、江苏省自然科学基金等。</p>
            </div>
        </div>
    </body>
    </html>
    """

    profile_b = FacultyProfileExtractor.extract(sample_split_span_html, "https://ccte.hhu.edu.cn/xjx.htm", fallback_name="徐金霞")
    assert profile_b.name == "徐金霞"
    # Crucial assertion: Must cleanly join inline spans to extract xujinxia@hhu.edu.cn
    assert profile_b.email == "xujinxia@hhu.edu.cn"


def test_dual_sheet_conservation_pipeline():
    """
    Slice 4: Test end-to-end FacultyHarvestPipeline:
    - Verifies Dual Sheet Conservation Axiom: len(Sheet 1) + len(Sheet 2) == Total.
    - Verifies mutual exclusivity: Intersection between Sheet 1 and Sheet 2 is empty.
    - Zero keyword blacklists used.
    """
    from server.faculty_extractor import FacultyHarvestPipeline

    # Mock profile fetcher returning realistic scholar HTMLs
    def mock_fetcher(url: str) -> str:
        if "1001" in url: # 张富有 (with email)
            return """<div class="article"><h1>张富有</h1><p>教授</p><p>邮箱：fyzhangsy@hhu.edu.cn</p><div class="bio">长期从事岩土工程研究。</div></div>"""
        elif "1002" in url: # 刘圣洁 (with email)
            return """<div class="article"><h1>刘圣洁</h1><p>副教授</p><p>邮箱：lsjwork@126.com</p><div class="bio">主要从事防灾减灾工程研究。</div></div>"""
        elif "1003" in url: # 孙洁 (with email)
            return """<div class="article"><h1>孙洁</h1><p>讲师</p><p>邮箱：sunjie@hhu.edu.cn</p><div class="bio">从事交通运输规划。</div></div>"""
        elif "1004" in url: # 党建国 (NO email in bio, footer has public email)
            return """<div class="article"><h1>党建国</h1><p>教授</p><div class="bio">长期从事桥梁工程研究。</div></div><div class="footer">联系邮箱：sdy@hhu.edu.cn</div>"""
        elif "1005" in url: # 徐金霞 (with split-span email)
            return """<div class="article"><h1>徐金霞</h1><p>副教授</p><p>邮箱：<span>xujinxia</span><span>@</span><span>hhu.edu.cn</span></p><div class="bio">从事智能监测研究。</div></div>"""
        return ""

    pipeline = FacultyHarvestPipeline()
    base_url = "https://ccte.hhu.edu.cn/jsxq/list.htm"
    result = pipeline.harvest_directory(
        directory_html=SAMPLE_DIRECTORY_HTML,
        base_url=base_url,
        profile_fetcher=mock_fetcher,
        school="河海大学",
        college="土木与交通学院"
    )

    sheet1_with_email = result["sheet1_with_email"]
    sheet2_no_email = result["sheet2_no_email"]
    total_scholars = result["total_count"]

    # 1. Conservation Axiom: Sheet 1 + Sheet 2 == Total
    assert len(sheet1_with_email) + len(sheet2_no_email) == total_scholars
    assert total_scholars == 5

    # 2. Count breakdown: 4 with email, 1 without email (党建国 has no personal email)
    assert len(sheet1_with_email) == 4
    assert len(sheet2_no_email) == 1

    # 3. Mutual Exclusivity: Names in Sheet 1 and Sheet 2 must be disjoint
    names_s1 = set(row["姓名"] for row in sheet1_with_email)
    names_s2 = set(row["姓名"] for row in sheet2_no_email)
    assert names_s1.isdisjoint(names_s2), f"Overlap found between sheets: {names_s1 & names_s2}"

    # 4. Verify no footer email leak in Sheet 2
    assert "党建国" in names_s2
    djg_row = next(r for r in sheet2_no_email if r["姓名"] == "党建国")
    assert djg_row["邮箱"] == ""
    assert djg_row["职称"] == "教授"

    # 5. Verify Sheet 1 emails are pure
    s1_emails = {r["姓名"]: r["邮箱"] for r in sheet1_with_email}
    assert s1_emails["张富有"] == "fyzhangsy@hhu.edu.cn"
    assert s1_emails["刘圣洁"] == "lsjwork@126.com"
    assert s1_emails["孙洁"] == "sunjie@hhu.edu.cn"
    assert s1_emails["徐金霞"] == "xujinxia@hhu.edu.cn"

    # 6. Verify 10-column canonical schema adherence
    expected_cols = ["序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页"]
    for row in sheet1_with_email + sheet2_no_email:
        for col in expected_cols:
            assert col in row, f"Missing required column '{col}' in row: {row}"


def test_closing_block_tag_newline():
    """
    Code Review Regression Test:
    Verifies that closing a block element (e.g. <div>...</div>) emits a physical newline
    so that sibling inline text outside the block does not concatenate with text inside.
    """
    from bs4 import BeautifulSoup
    from server.faculty_extractor import LayoutAwareTokenizer

    html_snippet = """
    <div class="card">
        <div><span>电话：0510-85918219</span></div><span>邮箱：Wwenlong2011@163.com</span>
    </div>
    """
    soup = BeautifulSoup(html_snippet, 'html.parser')
    text = LayoutAwareTokenizer.get_clean_text(soup)

    # Must contain newline separating phone from email
    lines = text.split('\n')
    assert any("85918219" in l for l in lines)
    assert any("Wwenlong2011@163.com" in l for l in lines)
    assert "85918219邮箱" not in text
    assert "85918219Wwenlong" not in text


def test_zebra_striping_clustering_resilience():
    """
    Code Review Regression Test:
    Verifies that alternating zebra classes (.odd / .even, .row-0 / .row-1)
    do NOT fracture the dominant isomorphic cluster.
    """
    from server.faculty_extractor import FacultyDirectoryExtractor

    zebra_directory_html = """
    <div class="list-container">
        <table class="teacher-table">
            <tr class="row-item odd"><td class="name"><a href="1.htm">张三</a></td><td>教授</td></tr>
            <tr class="row-item even"><td class="name"><a href="2.htm">李四</a></td><td>副教授</td></tr>
            <tr class="row-item odd"><td class="name"><a href="3.htm">王五</a></td><td>讲师</td></tr>
            <tr class="row-item even"><td class="name"><a href="4.htm">赵六</a></td><td>教授</td></tr>
        </table>
    </div>
    """
    candidates = FacultyDirectoryExtractor.extract(zebra_directory_html, "https://example.edu.cn/list.htm")
    names = [c.name for c in candidates]
    # All 4 alternating rows must be clustered into the same dominant group
    assert names == ["张三", "李四", "王五", "赵六"]


def test_real_world_boda_and_shufe_pages():
    """
    Empirical Regression Test on real-world HTML fixtures:
    1. scratch_law.html (SISU Law - Boda CMS with 2-hex hash directory buckets)
    2. scratch_shufe_zj.html (SHUFE Zhejiang - 44 scholars table)
    """
    import os
    from server.faculty_extractor import FacultyDirectoryExtractor

    # 1. SISU Law
    if os.path.exists(os.path.join(_LEGACY_PAGES, "scratch_law.html")):
        with open(os.path.join(_LEGACY_PAGES, "scratch_law.html"), "r", encoding="utf-8") as f:
            html_law = f.read()
        candidates_law = FacultyDirectoryExtractor.extract(html_law, "https://law.shisu.edu.cn/zrjs/list.htm")
        names_law = [c.name for c in candidates_law]
        assert len(candidates_law) == 10, f"Expected 10 law faculty members, got {len(candidates_law)}: {names_law}"
        assert "曹亦千" in names_law
        assert "蔡嘉伟" in names_law

    # 2. SHUFE Zhejiang
    if os.path.exists(os.path.join(_LEGACY_PAGES, "scratch_shufe_zj.html")):
        with open(os.path.join(_LEGACY_PAGES, "scratch_shufe_zj.html"), "r", encoding="utf-8") as f:
            html_shufe = f.read()
        candidates_shufe = FacultyDirectoryExtractor.extract(html_shufe, "https://zjxy.sufe.edu.cn/szdw/list.htm")
        assert len(candidates_shufe) == 44, f"Expected 44 scholars, got {len(candidates_shufe)}"





