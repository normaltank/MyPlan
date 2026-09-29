"""
Opus Algorithm Suite for University Faculty Harvesting.
Exports core L2 mining, bipartite matching, text-list parsing, template inference, and L3 synthesis.
"""

import os
import sys

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from .iso_mining import (
    VirtualNode,
    first_href,
    mine_records,
    primary_tokens,
    RecordGroup,
    MiningResult,
)
from .email_matching import (
    DomainConventionModel,
    match_flow,
    match_1to1,
)
from .text_lists import (
    email_spans,
    lex,
    text_stream,
)

__all__ = [
    "VirtualNode",
    "first_href",
    "mine_records",
    "primary_tokens",
    "RecordGroup",
    "MiningResult",
    "DomainConventionModel",
    "match_flow",
    "match_1to1",
    "email_spans",
    "lex",
    "text_stream",
]
