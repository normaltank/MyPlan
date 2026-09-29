"""职称词表与职称识别：全项目唯一的一份。

词表只用来识别“职称”这一列，不删改页面内容（见 README 红线 2）。
"""
from __future__ import annotations

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
    'assistant professor': '讲师',
    'lecturer': '讲师',
    'professor': '教授'
}


def resolve_title(header_text: str) -> str:
    """Earliest non-overlapping title keyword in a header/bio snippet; '教师' when none."""
    matches = []
    for kw in TITLE_KEYWORDS:
        pos = 0
        while True:
            idx = header_text.find(kw, pos)
            if idx == -1:
                break
            matches.append((idx, len(kw), kw))
            pos = idx + 1

    if not matches:
        low_header = header_text.lower()
        for en_kw, zh_t in _ENGLISH_TITLES.items():
            pos = 0
            while True:
                idx = low_header.find(en_kw, pos)
                if idx == -1:
                    break
                matches.append((idx, len(en_kw), zh_t))
                pos = idx + 1

    if matches:
        matches.sort(key=lambda x: (x[0], -x[1]))
        last_end = -1
        non_overlapping = []
        for pos, length, kw in matches:
            if pos >= last_end:
                non_overlapping.append(kw)
                last_end = pos + length
        if non_overlapping:
            return non_overlapping[0]

    return "教师"
