"""Cluster indexing is linear in page count and preserves the frozen output."""
import dataclasses
import hashlib
import json

from server.detail_mecc import DetailMECCExtractor
import template_infer


def cluster_pages(count):
    result = []
    for i in range(count):
        nav = '<nav>' + ''.join(f'<p><a href="/nav/{j}">栏目{j} 学院教学研究与公共服务</a></p>' for j in range(24)) + '</nav>'
        bio = ''.join(f'<p>研究经历{j}：本页编号{i}，完整保留教育经历、任职经历、研究方向、项目与论文。' + '学术资料内容' * 12 + '</p>' for j in range(12))
        html = '<html><body>' + nav + f'<article><h1>样本教师{i}</h1><p>职称：教授</p><p>邮箱：{i:010d}@example.edu</p>' + bio + '</article><footer>学校公共服务 public@example.edu</footer></body></html>'
        result.append((f'https://faculty.example.edu/p/{i}', html, f'样本教师{i}'))
    return result


def varied_cluster_pages():
    result = []
    for i, (url, html, name) in enumerate(cluster_pages(60)):
        if i % 5 == 0:
            html = ''
        else:
            if i % 3 == 0:
                html = html.replace(f'<p>邮箱：{i:010d}@example.edu</p>', '')
            html = html.replace('</article>', '<p>' + '补充完整任职经历。' * (i % 7) + '</p></article>')
        result.append((url, html, name))
    return result


def frozen_profile_values(result):
    # The declared DOM fields are new internal parsing evidence. Keep the
    # original frozen comparison for every pre-existing field unchanged.
    return [{key: value for key, value in dataclasses.asdict(row).items()
             if key != 'title_fields'} for row in result]


def test_cluster_indexes_each_page_once_without_changing_any_field(monkeypatch):
    builds = []
    original = template_infer._Page.__init__

    def counted(self, soup):
        builds.append(id(soup))
        original(self, soup)

    monkeypatch.setattr(template_infer._Page, '__init__', counted)
    result = DetailMECCExtractor.extract_cluster(cluster_pages(20))
    serialized = json.dumps(frozen_profile_values(result), ensure_ascii=False, sort_keys=True)
    # Frozen from the unmodified f2220a4 extractor on Windows, before caching.
    assert hashlib.sha256(serialized.encode()).hexdigest() == '69442686b981546cb0e2180d304018b613fb5912d2f9a3b973f53a00c511ccc1'
    assert len(builds) == len(set(builds)) == 20
    assert all(len(row.title_fields) == 1 and row.title_fields[0].raw_value == '教授' for row in result)


def test_cluster_preserves_all_fields_for_missing_pages_and_varied_biographies():
    result = DetailMECCExtractor.extract_cluster(varied_cluster_pages())
    serialized = json.dumps(frozen_profile_values(result), ensure_ascii=False, sort_keys=True)
    # Frozen with the complete original f2220a4 detail module, uncached.
    assert hashlib.sha256(serialized.encode()).hexdigest() == '7c5ebe12c53301f8c456d50ecc1006175d4ba327f4e8211705d83f67da4ac8ad'
    assert sum(row.status == 'fetch_failed' for row in result) == 12
    assert sum(not row.emails for row in result) == 28
