"""Foreign full names must survive tokenisation of photo cards and name links."""
import pytest

from server.faculty_extractor import FacultyDirectoryExtractor

URL = 'https://eim.pdsu.edu.cn/szdw/dqgcjqzdh.htm'
NAMES = ['Salvador Gómez Jiménez', 'María del Carmen López', "Jean-Pierre D’Arcy", 'John A. Smith']


@pytest.mark.parametrize('linked', [True, False])
def test_verified_photo_cards_keep_full_latin_name(linked):
    cards = ''.join(f'<td><a href="/photo/{i}.jpg"><img src="/photo/{i}.jpg"></a>'
                    + (f'<a href="/person/{i}">{name}</a>' if linked else f'<p>{name} 电气工程博士 副教授</p>')
                    + '</td>' for i, name in enumerate(NAMES))
    found, review = FacultyDirectoryExtractor.extract_with_review(
        f'<table><tr>{cards}</tr></table>', URL, card_selector='td')
    assert not review
    assert [p.name for p in found] == NAMES


def test_spanish_names_with_degree_metadata_match_reported_cards():
    names = ['Salvador Pineda', 'Mario Javier Durán Martínez', 'José Antonio Aguado Sánchez',
             'Juan Bernabé García González', 'Alejandro Rodríguez Gómez']
    cards = ''.join(f'<td><a href="/images/{i}.jpg"><img src="/images/{i}.jpg"></a>'
                    f'<p>{name} 电气工程博士 教授</p></td>' for i, name in enumerate(names))
    found, review = FacultyDirectoryExtractor.extract_with_review(
        f'<table><tr>{cards}</tr></table>', URL, card_selector='td')
    assert not review and [p.name for p in found] == names


def test_unverified_linked_cards_preserve_full_latin_name():
    cards = ''.join(f'<li><a href="/person/{i}">{name}</a></li>' for i, name in enumerate(NAMES))
    found, review = FacultyDirectoryExtractor.extract_with_review(f'<ul>{cards}</ul>', URL)
    assert not review
    assert [p.name for p in found] == NAMES
