"""
Regression tests for server.detail_mecc (Minimum Enclosing Content Container).
"""
import pytest
from server.detail_mecc import DetailMECCExtractor, ProfileDetail


HEADER = """
<div class="site-header">
    <h1>计算机科学与技术学院</h1>
    <ul class="nav">
        <li><a href="/index.htm">首页</a></li>
        <li><a href="/szdw.htm">师资队伍</a></li>
        <li><a href="/kxyj.htm">科学研究</a></li>
    </ul>
</div>
"""

FOOTER = """
<div class="site-footer">
    <p>地址：北京市海淀区学院路1号 邮编：100083</p>
    <p>办公室邮箱：office@cs.univ.edu.cn 咨询电话：010-62330000</p>
    <p>版权所有 © 计算机学院 保留所有权利</p>
</div>
"""


def _make_page(name: str, title: str, email: str, bio_lines: list[str]) -> str:
    bio_html = "".join(f"<p>{line}</p>" for line in bio_lines)
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>{name} - 师资队伍</title></head>
    <body>
        {HEADER}
        <div class="main-wrapper">
            <div class="sidebar">
                <h3>系所导航</h3>
                <ul><li>软件工程</li><li>人工智能</li></ul>
            </div>
            <div class="teacher-detail-content">
                <div class="profile-header">
                    <h2>{name}</h2>
                    <span class="title">{title}</span>
                </div>
                <div class="contact-box">
                    <p>电子邮箱：{email}</p>
                    <p>办公地点：计算机楼402室</p>
                </div>
                <div class="resume-body">
                    <h3>个人履历与研究方向</h3>
                    {bio_html}
                </div>
            </div>
        </div>
        {FOOTER}
    </body>
    </html>
    """


def test_single_page_mecc_extraction():
    html = _make_page(
        name="张三",
        title="教授",
        email="zhangsan@cs.univ.edu.cn",
        bio_lines=["2010年获得清华大学博士学位。", "主要研究方向为分布式系统与编译器优化。"]
    )
    detail = DetailMECCExtractor.extract_single(html, "https://cs.univ.edu.cn/t/1.htm", fallback_name="张三")
    assert detail.status == "ok"
    assert detail.name == "张三"
    assert detail.title == "教授"
    assert detail.email == "zhangsan@cs.univ.edu.cn"
    assert "分布式系统与编译器优化" in detail.full_bio
    # Ensure footer is not in the extracted bio
    assert "版权所有" not in detail.full_bio


def test_cluster_mecc_and_detail_df_elimination():
    """Verify that cross-page template inference isolates MECC and detail-df rejects footer emails."""
    p1 = _make_page(
        name="张伟",
        title="教授",
        email="zhangwei@cs.univ.edu.cn",
        bio_lines=["国家杰出青年科学基金获得者。", "主要从事数据库理论与海量数据挖掘研究。"]
    )
    p2 = _make_page(
        name="李娜",
        title="副教授",
        email="lina_lab@163.com",
        bio_lines=["毕业于复旦大学计算机学院。", "主要研究方向为人机交互与智能前端系统。"]
    )
    p3 = _make_page(
        name="王强",
        title="讲师",
        email="",  # Scholar without personal email on page, only footer email exists
        bio_lines=["2022年博士毕业于浙江大学。", "主要从事知识图谱与自然语言处理研究。"]
    )

    cluster_pages = [
        ("https://cs.univ.edu.cn/t/1.htm", p1, "张伟"),
        ("https://cs.univ.edu.cn/t/2.htm", p2, "李娜"),
        ("https://cs.univ.edu.cn/t/3.htm", p3, "王强"),
    ]

    results = DetailMECCExtractor.extract_cluster(cluster_pages)
    assert len(results) == 3

    r1, r2, r3 = results
    assert r1.name == "张伟" and r1.email == "zhangwei@cs.univ.edu.cn" and r1.title == "教授"
    assert r2.name == "李娜" and r2.email == "lina_lab@163.com" and r2.title == "副教授"

    # Crucial assertion: 王强 does not have a personal email.
    # The footer email office@cs.univ.edu.cn is present on all 3 pages, so detail-df >= 2 rejects it!
    assert r3.name == "王强"
    assert r3.email == "", f"Footer email should be rejected by detail-df, but got {r3.email}"
    assert "知识图谱与自然语言处理" in r3.full_bio


def test_obfuscated_email_in_detail():
    html = _make_page(
        name="赵敏",
        title="副研究员",
        email="zhaomin(at)cs.univ.edu.cn",
        bio_lines=["研究方向：密码学与网络空间安全。"]
    )
    detail = DetailMECCExtractor.extract_single(html, "https://cs.univ.edu.cn/t/4.htm", fallback_name="赵敏")
    assert detail.email == "zhaomin@cs.univ.edu.cn"


def test_empty_html_fails_gracefully():
    detail = DetailMECCExtractor.extract_single("", "https://cs.univ.edu.cn/t/empty.htm", fallback_name="刘洋")
    assert detail.status == "no_profile"
    assert detail.name == "刘洋"
    assert detail.email == ""
