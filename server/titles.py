"""职称词表与职称识别：全项目唯一的一份。

词表只用来识别“职称”这一列，不删改页面内容（见 README 红线 2）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# 长的在前，'副教授' 先于 '教授'、'教授级高级工程师' 先于 '教授' 命中。
TITLE_KEYWORDS = sorted(
    ['教授', '副教授', '讲师', '高级讲师', '助理教授', '研究员', '副研究员', '助理研究员',
     '师资博士后', '名誉教授', '特聘教授', '讲座教授', '高级实验师', '实验师',
     '正高级工程师', '教授级高级工程师', '高级工程师',
     '主任医师', '副主任医师', '主治医师', '主任护师', '副主任护师', '主任药师', '副主任药师'],
    key=len,
    reverse=True
)

_ENGLISH_TITLES = {
    'associate professor': '副教授',
    'assistant professor': '助理教授',
    'lecturer': '讲师',
    'professor': '教授'
}


def title_mentions(text: str) -> tuple[str, ...]:
    """Canonical, non-overlapping mentions in original Chinese/English order."""
    matches = []
    for kw in TITLE_KEYWORDS:
        pos = 0
        while True:
            idx = text.find(kw, pos)
            if idx == -1:
                break
            matches.append((idx, len(kw), kw))
            pos = idx + 1

    for en_kw, zh_t in _ENGLISH_TITLES.items():
        for match in re.finditer(r'(?<![A-Za-z])' + re.escape(en_kw) + r'(?![A-Za-z])', text, re.I):
            matches.append((match.start(), len(en_kw), zh_t))

    if matches:
        matches.sort(key=lambda x: (x[0], -x[1]))
        last_end = -1
        non_overlapping = []
        for pos, length, kw in matches:
            if pos >= last_end:
                non_overlapping.append(kw)
                last_end = pos + length
        return tuple(non_overlapping)

    return ()


def resolve_title(header_text: str) -> str:
    """Earliest non-overlapping title keyword; '教师' when none."""
    mentions = title_mentions(header_text)
    return mentions[0] if mentions else '教师'


@dataclass(frozen=True)
class TitleSelection:
    title: str
    conflicts: tuple[str, ...] = ()


def select_title(*, explicit: Iterable[str] = (), card_title: str = '教师',
                 body_title: str = '教师') -> TitleSelection:
    """Explicit own-profile fields > specific roster title > first body mention.

    Multiple distinct explicit values require review; never pick one silently.
    Inputs are canonical titles extracted by the deterministic reader.
    """
    fields = tuple(dict.fromkeys(value for value in explicit if value and value != '教师'))
    if len(fields) > 1:
        return TitleSelection('教师', fields)
    if fields:
        return TitleSelection(fields[0])
    if card_title and card_title != '教师':
        return TitleSelection(card_title)
    return TitleSelection(body_title or '教师')
