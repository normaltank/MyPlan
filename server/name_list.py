"""补充名单页：一张卡片里用顿号列出多位教师（教研室成员、研究生导师分专业名录）。

主名录一张卡片一个人；这类页面的一张卡片是一串姓名，例如
``函数论教研室（徐丽、董白英、职占江、张永勇）``。通用的名录抽取按 DOM 结构
聚类，串在同一段文字里的名字切不开，会漏人，也会让“卡片数 = 人数”的对账失效。

这里按结构分隔符切分：先按括号、冒号、分号、句号等断成分句，再把分句按顿号
切成名单项。一个分句里至少两项像人名、且像人名的项占多数，才算“人员名单串”；
“三、教学系”“土木工程、水利工程”这类不是。名单串里的每一项：

* 姓名先验够高的直接收录；
* 生僻姓（Opus/rare_surnames.txt）送人工复核，与主名录同一套流程；
* 其余认不出的项列为未解决，不静默丢弃。

名单串以外的说明文字（系所名称、机构名称、括号里单独出现的名字）不是名单
成员，不从这类页面收集；这些人通常也在主名录里。整张卡片只有一个名字时
（导师名录里只列一位导师的单元格）按一个人收录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from bs4 import BeautifulSoup

from server.faculty_extractor import FacultyCandidate, FacultyDirectoryExtractor
from server.detail_mecc import mailto_address_conflicts
from server.titles import TITLE_KEYWORDS

from iso_mining import first_href, name_prob, rare_surname_name  # noqa: E402  (Opus on sys.path)

NAME_PRIOR_FLOOR = 0.5
# Clause boundaries: a list of names never runs across these.
_BOUNDARY = re.compile(r"[（）()\[\]【】<>《》：:；;。！？!?|/\n\r\t]+")
_PADDED_TWO = re.compile(r"^([一-鿿])[\s　]+([一-鿿])$")
_HAN_NAME = re.compile(r"^[一-鿿]{2,4}$")
_MINORITY_NAME = re.compile(r"^[一-鿿]{2,6}[·•][一-鿿]{2,8}$")


def _item(raw: str) -> str:
    """One list item, with a padded two-character name ('王 奔') collapsed."""
    text = raw.strip().strip("　")
    padded = _PADDED_TWO.match(text)
    if padded:
        return padded.group(1) + padded.group(2)
    return text


def _person_name(item: str) -> str | None:
    cleaned, _title = FacultyDirectoryExtractor._clean_name(item)
    words = item.split()
    if (len(words) > 1 and not _PADDED_TWO.match(item)
            and cleaned == "".join(words)):
        return None  # Independent full tokens must not become an invented combined name.
    if not (_HAN_NAME.match(cleaned) or _MINORITY_NAME.match(cleaned)):
        return None
    return cleaned if name_prob(cleaned) >= NAME_PRIOR_FLOOR else None


def _rare_name(item: str) -> str | None:
    cleaned, _title = FacultyDirectoryExtractor._clean_name(item)
    words = item.split()
    if len(words) > 1 and not _PADDED_TWO.match(item) and cleaned == "".join(words):
        return None
    return cleaned if _HAN_NAME.match(cleaned) and rare_surname_name(cleaned) else None


def _list_items(part: str) -> list[str]:
    item = _item(part)
    words = item.split()
    if len(words) <= 1 or _person_name(item) or _rare_name(item):
        return [item] if item else []
    # Complete names remain separate; runs of one Han character are the
    # typography of padded names. Unknown tokens remain auditable items.
    items = []
    index = 0
    while index < len(words):
        if re.fullmatch(r"[一-鿿]", words[index]):
            end = index + 1
            while end < len(words) and re.fullmatch(r"[一-鿿]", words[end]):
                end += 1
            joined = "".join(words[index:end])
            # Two/three isolated characters cannot form two valid Han names;
            # four or more can, so keep that entire run unresolved.
            items.append(joined if 2 <= len(joined) <= 3 and (_person_name(joined) or _rare_name(joined))
                         else " ".join(words[index:end]))
            index = end
        else:
            items.append(words[index])
            index += 1
    return items


@dataclass
class NameChain:
    """One 顿号-separated run of list items inside a single clause."""

    text: str
    items: list[str]
    names: list[str] = field(default_factory=list)
    rare: list[str] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)

    @property
    def is_people(self) -> bool:
        people = len(self.names) + len(self.rare)
        return len(self.names) >= 1 and people >= 2 and people * 3 >= len(self.items) * 2


def chains(text: str) -> list[NameChain]:
    """Every 顿号 run of at least two items in ``text``, classified."""
    found = []
    for clause in _BOUNDARY.split(text or ""):
        if "、" not in clause and len(clause.split()) < 2:
            continue
        items = [item for part in clause.split("、") for item in _list_items(part)
                 if item not in TITLE_KEYWORDS]
        if len(items) < 2:
            continue
        chain = NameChain(text=clause.strip(), items=items)
        for item in items:
            name = _person_name(item)
            if name:
                chain.names.append(name)
                continue
            rare = _rare_name(item)
            if rare:
                chain.rare.append(rare)
            else:
                chain.unrecognized.append(item)
        found.append(chain)
    return found


def people_chains(text: str) -> list[NameChain]:
    return [chain for chain in chains(text) if chain.is_people]


def _single_name(text: str) -> str | None:
    """The card is exactly one name (a one-person cell of a name-list table)."""
    if _BOUNDARY.search(text):
        return None  # A role/unit label with a named office-holder is not a name cell.
    compact = _item(text)
    if compact != text.strip() and not _PADDED_TWO.match(text.strip()):
        return None
    return _person_name(compact)


def _chain_text(card) -> str:
    """Card text for finding name chains: a table row's cells are separate clauses.

    A rank cell beside a names cell ('正高级 | 崔海波、陈庆文') must not run into
    the first name of the chain.
    """
    cells = card.find_all(["td", "th"], recursive=False) if card.name == "tr" else []
    if len(cells) > 1:
        return "\n".join(cell.get_text(" ", strip=True) for cell in cells)
    return card.get_text(" ", strip=True)


def is_name_list_page(html: str, card_selector: str) -> bool:
    """Cards are lists of people rather than one person each.

    True when some card holds a people chain and cards holding people chains
    are at least half of the cards that hold any name at all.
    """
    cards = BeautifulSoup(html, "html.parser").select(card_selector)
    # Named personal links have their own URL identity. Do not convert them
    # into profile-less names merely because get_text inserted whitespace.
    if any(_person_name(a.get_text(' ', strip=True)) and first_href([a], None)
           for card in cards for a in ([card] if card.name == 'a' else []) + card.find_all('a')):
        return False
    list_cards = named_cards = 0
    for card in cards:
        text = card.get_text(" ", strip=True)
        has_chain = bool(people_chains(_chain_text(card)))
        list_cards += has_chain
        if has_chain or _single_name(text) or any(
                _person_name(token) for token in re.split(r"[\s　,，、;；:：()（）]+", text) if token):
            named_cards += 1
    return list_cards > 0 and list_cards * 2 >= named_cards


@dataclass
class NameListResult:
    found: list[FacultyCandidate]
    review: list[FacultyCandidate]
    unresolved: list[dict[str, Any]]
    observation: dict[str, Any]


def reconcile_name_list(html: str, url: str, card_selector: str) -> NameListResult:
    """Candidates of a name-list page; every item of a people chain is accounted for."""
    cards = BeautifulSoup(html, "html.parser").select(card_selector)
    found: dict[str, FacultyCandidate] = {}
    review: dict[str, FacultyCandidate] = {}
    unresolved: list[dict[str, Any]] = []
    list_cards = single_cards = 0
    for index, card in enumerate(cards, 1):
        conflicts = mailto_address_conflicts(card)
        if conflicts:
            unresolved.append({'url': url, 'status': 'mailto_address_conflict', 'card': index,
                               'matches': list(dict.fromkeys(address for conflict in conflicts
                                                           for address in conflict))})
        text = card.get_text(" ", strip=True)
        runs = people_chains(_chain_text(card))
        if runs:
            list_cards += 1
        else:
            single = _single_name(text)
            if single:
                single_cards += 1
                found.setdefault(single, FacultyCandidate(name=single, url=url, title="教师",
                                                          card_context=text))
            continue
        for chain in runs:
            for name in chain.names:
                found.setdefault(name, FacultyCandidate(name=name, url=url, title="教师",
                                                        card_context=chain.text))
            for name in chain.rare:
                if name not in found:
                    review.setdefault(name, FacultyCandidate(name=name, url=url, title="教师",
                                                             card_context=chain.text))
            for item in chain.unrecognized:
                unresolved.append({"url": url, "status": "name_list_item_unrecognized",
                                   "card": index, "item": item})
    observation = {"url": url, "card_selector": card_selector, "kind": "name_list",
                   "visible_cards": len(cards), "list_cards": list_cards,
                   "single_name_cards": single_cards,
                   "extracted_candidates": len(found) + len(review),
                   "unaccounted_cards": []}
    return NameListResult(list(found.values()), list(review.values()), unresolved, observation)
