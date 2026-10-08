"""A banner's slide controls cannot stand in for a faculty roster pager."""

import pytest
from bs4 import BeautifulSoup

from server.browser_batch_tool import _pagination_candidates, without_banner_arrows

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


# Desensitized shapes of two real banners: a script-only arrow next to image
# slides, outside the roster, with no pager container.
SLIDE_BOX = ('<div class="slideBox" id="slideBox"><div class="bd"><ul>'
             '<li><img src="/b1.png"><h3>学院活动</h3></li><li><img src="/b2.png"></li></ul></div>'
             '<a class="prev" href="javascript:void(0)"></a><a class="next" href="javascript:void(0)"></a></div>')
JQUERY_CAROUSEL = ('<div class="slide" id="lun2"><div class="carouse clearfix ser-carouse">'
                   '<div class="slideItem"><a href="javascript:void(0)"><img src="/c1.jpg"></a></div>'
                   '<div class="slideItem"><a href="javascript:void(0)"><img src="/c2.jpg"></a></div>'
                   '<a class="carousel-control left Next"></a><a class="carousel-control right Previous"></a>'
                   '</div></div>')


@pytest.mark.parametrize('banner', [SLIDE_BOX, JQUERY_CAROUSEL])
def test_script_only_banner_arrows_are_not_roster_pagination_leads(banner):
    assert _pagination_candidates(banner + ROSTER, URL, '.roster > li') == []


@pytest.mark.parametrize('html', [
    # A script-driven pager inside a pager container is still a lead.
    SLIDE_BOX + ROSTER + '<div class="pagination"><a class="next" href="javascript:void(0)">下一页</a></div>',
    # A banner-looking arrow with a real target is probed, not trusted.
    SLIDE_BOX.replace('class="next" href="javascript:void(0)"', 'class="next" href="/list/2"') + ROSTER,
    # Teacher cards inside the slide box still need pagination proof.
    ('<div class="slideBox"><ul class="roster"><li><img src="/a.png"><a href="/person/1">张三</a></li>'
     '<li><img src="/b.png"><a href="/person/2">李四</a></li></ul>'
     '<a class="next" href="javascript:void(0)"></a></div>'),
    # A slide box without slide images explains nothing.
    '<div class="slideBox"><a class="next" href="javascript:void(0)"></a></div>' + ROSTER,
    # A bare script-only next control near the roster is still unexplained.
    ROSTER + '<div class="tools"><a class="next" href="javascript:void(0)"></a></div>',
])
def test_script_only_controls_without_banner_proof_still_require_pagination_proof(html):
    assert _pagination_candidates(html, URL, '.roster > li')


@pytest.mark.parametrize('html, kept', [
    (SLIDE_BOX + ROSTER, 0),
    (SLIDE_BOX + ROSTER + '<div class="pagination"><a class="next" href="javascript:void(0)">下一页</a></div>', 1),
    (('<div class="slideBox"><ul class="roster"><li><img src="/a.png"><a href="/person/1">张三</a></li>'
      '<li><img src="/b.png"><a href="/person/2">李四</a></li></ul>'
      '<a class="next" href="javascript:void(0)"></a></div>'), 1),
])
def test_recon_gate_and_harvest_agree_on_banner_arrows(html, kept):
    # Selectors as the recon probe reports them: one per candidate control.
    selectors = ['#slideBox > a.next', 'div.pagination > a.next', 'div.slideBox > a.next']
    soup = BeautifulSoup(html, 'html.parser')
    found = [s for s in selectors if len(soup.select(s)) == 1]
    pagination = {'total': len(found), 'items': [{'selector': s, 'url': 'javascript:void(0)', 'label': ''}
                                                  for s in found]}
    assert without_banner_arrows(html, '.roster > li', pagination)['total'] == kept
    assert len(_pagination_candidates(html, URL, '.roster > li')) == kept
