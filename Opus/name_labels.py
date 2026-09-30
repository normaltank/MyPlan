"""Separate explicit academic degree annotations from a Chinese name slot.

This only normalizes a candidate label, never profile prose. A degree is not
a job title. Require a complete name before the suffix so a name such as
李博士 is preserved rather than shortened to a one-character string.
"""

import re

_DEGREE_LABEL = re.compile(r"^([\u4e00-\u9fff]{2,4})(?:博士|硕士)$")


def without_degree_annotation(label: str) -> str:
    match = _DEGREE_LABEL.fullmatch(label)
    return match.group(1) if match else label
