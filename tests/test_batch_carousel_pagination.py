"""A banner's slide controls cannot stand in for a faculty roster pager."""

import pytest

from server.browser_batch_tool import _pagination_candidates

SLIDES = '<div class="slick-slide"><img></div>' * 2
CAROUSEL = ('<div class="slick-slider slick-initialized"><div class="slick-list">'
            '<div class="slick-track">' + SLIDES + '</div></div>'
            '<button class="slick-next">Next</button></div>')
ROSTER = '<ul class="roster"><li><a href="/person/1">张三</a></li></ul>'
URL = 'https://college.example.edu/faculty.htm'


def test_initialized_banner_does_not_create_a_roster_pagination_lead():
    assert _pagination_candidates(CAROUSEL + ROSTER, URL, '.roster > li') == []


@pytest.mark.parametrize('html', [
    CAROUSEL + ROSTER + '<nav class="pagination"><a rel="next" href="/page2">Next</a></nav>',
    CAROUSEL + ROSTER + '<nav class="pagination"><button>2</button></nav>',
    '<div class="slick-slider slick-initialized"><button class="slick-next">Next</button></div>' + ROSTER,
    CAROUSEL.replace(SLIDES, '<div class="slick-slide">' + ROSTER + '</div>' + SLIDES),
])
def test_real_or_unexplained_roster_controls_still_require_pagination_proof(html):
    assert _pagination_candidates(html, URL, '.roster > li')
