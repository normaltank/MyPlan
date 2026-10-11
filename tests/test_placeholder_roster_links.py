from html import escape

from bs4 import BeautifulSoup
import pytest

from server.faculty_extractor import FacultyDirectoryExtractor, FacultyHarvestPipeline
from iso_mining import first_href


@pytest.mark.parametrize('placeholder', [
    "<span style='color:red;font-size:9pt'>转换链接错误</span>",
    '<strong>link unavailable</strong>',
    '/list/%3CSPAN%3Eerror%3C/span%3E',
    '/list/%3Cspan%3Eerror%3C/span%20%3E',
    'https://faculty.example.edu/list/%3Cspan style=%27color:red;font-size:9pt%27%3E'
    '%E8%BD%AC%E6%8D%A2%E9%93%BE%E6%8E%A5%E9%94%99%E8%AF%AF%3C/span%3E',
])
def test_markup_href_keeps_the_person_without_fetching_a_fake_profile(placeholder):
    url = 'https://faculty.example.edu/mentors.htm'
    html = ('<ul><li><a href="' + escape(placeholder, quote=True)
            + '">罗凯</a><span>副教授</span></li></ul>')
    candidates, pending = FacultyDirectoryExtractor._verified_card_candidates(html, url, 'li')
    assert pending == []
    assert len(candidates) == 1 and candidates[0].name == '罗凯'
    assert candidates[0].url == url
    fetched = []
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates, lambda target: fetched.append(target), base_url=url,
        school='样本大学', college='样本学院')
    assert fetched == []
    assert result['total_count'] == 1
    assert result['sheet2_no_email'][0]['姓名'] == '罗凯'
    assert result['unresolved'] == []


@pytest.mark.parametrize('href', ['../info/1032/1242.htm',
                                 '/files/%3Cspan%3E.htm',
                                 'https://other.example.edu/profile?id=42&name=%3Cspan%3E'])
def test_regular_and_encoded_profile_urls_remain_links(href):
    from urllib.parse import urljoin
    node = BeautifulSoup('<a href="' + escape(href, quote=True) + '">教师</a>', 'html.parser').a
    assert first_href([node], 'https://faculty.example.edu/mentors.htm') == urljoin(
        'https://faculty.example.edu/mentors.htm', href)
