"""Person-section evidence for a PDF linked from a single person's profile.

Publication author mentions are body text. They never become person boundaries
merely because a page break puts them near the top. This reader only blocks an
assignment; it neither splits a document nor removes any original text.
"""

from __future__ import annotations

import hashlib
import re
import statistics

# Complete, explicit resume column labels; an arbitrary short heading still
# cannot be declared a section merely because it resembles an owner's title.
_RESUME_SECTIONS = {'个人简况', '工作和学习经历', '研究方向及兴趣',
                    '主持的项目或课题', '代表性论文'}
_RESUME_TITLE = re.compile(r'^([一-鿿·•]{2,8}?)(?:个人)?(?:简历|简介)'
                           r'(?:[（(](?:word|docx?|pdf)[）)])?$', re.I)


def inspect_owner_scope(
    original: bytes,
    scanned: dict,
    owner: str,
    known_names: set[str],
    reviewed: set[tuple[int, int]],
) -> dict:
    from server.detail_mecc import _TITLE_FIELD_LINE
    from server.name_list import _HAN_NAME, _MINORITY_NAME
    from server.pdf_roster import EMPHASIS_RATIO, layout_lines
    from server.segmented_roster import _HEAD_WORDS, _LABELS, _SPLIT, heading_shape
    from server.titles import TITLE_KEYWORDS

    try:
        import pymupdf

        with pymupdf.open(stream=original, filetype="pdf") as document:
            page_count = document.page_count
            page_widths = {
                index: page.rect.width for index, page in enumerate(document, 1)
            }
        pages = scanned["pages"]
        if sorted(page["page"] for page in pages) != list(
            range(1, page_count + 1)
        ) or any(
            page.get("source") not in {"pdf_text_layer", "local_ocr"} for page in pages
        ):
            return {
                "method": "person_heading_structure",
                "status": "unknown",
                "reason": "pdf_page_evidence_incomplete",
            }
        lines, issues = layout_lines(original, scanned, reviewed)
    except Exception as exc:
        return {
            "method": "person_heading_structure",
            "status": "unknown",
            "reason": "pdf_layout_unavailable",
            "error_type": type(exc).__name__,
        }
    if issues or not lines:
        return {
            "method": "person_heading_structure",
            "status": "unknown",
            "reason": "pdf_layout_incomplete",
            "layout_issues": issues,
        }
    if any(
        line.source == "local_ocr"
        and (line.bbox[2] <= line.bbox[0] or line.bbox[3] <= line.bbox[1])
        for line in lines
    ):
        return {
            "method": "person_heading_structure",
            "status": "unknown",
            "reason": "pdf_ocr_layout_unavailable",
        }

    names = known_names | {owner}
    name_headings = {
        re.sub(r"\s+", "", name) + suffix: name
        for name in names
        for suffix in {"", *_LABELS}
    }
    body_sizes = {
        page: statistics.median(
            line.size for line in lines if line.page == page and line.size > 0
        )
        for page in {line.page for line in lines}
        if any(line.page == page and line.size > 0 for line in lines)
    }

    def emphasized_line(line):
        return line.bold or line.size >= EMPHASIS_RATIO * body_sizes.get(
            line.page, float("inf")
        )

    def same_heading_slot(line, anchor):
        # Page-normalized alignment survives different glyph counts and OCR
        # scaling; a left-aligned section is not a centered person's title.
        width = page_widths[line.page] * (2 if line.source == "local_ocr" else 1)
        anchor_width = page_widths[anchor.page] * (
            2 if anchor.source == "local_ocr" else 1
        )
        center = (line.bbox[0] + line.bbox[2]) / (2 * width)
        anchor_center = (anchor.bbox[0] + anchor.bbox[2]) / (2 * anchor_width)
        tolerance = max(line.size / width, anchor.size / anchor_width)
        centered = (
            abs(center - 0.5) <= tolerance and abs(anchor_center - 0.5) <= tolerance
        )
        same_left = (
            abs(line.bbox[0] / width - anchor.bbox[0] / anchor_width) <= tolerance
        )
        return line.bold == anchor.bold and (centered or same_left)

    person_evidence = []
    person_words = set(TITLE_KEYWORDS) | _HEAD_WORDS
    intro_labels = {label for label in _LABELS if label.endswith(("简介", "简历"))}
    for index, line in enumerate(lines):
        analysis_text = line.text
        if person_evidence:
            previous, previous_name, previous_role = person_evidence[-1]
            nearby = (previous.page == line.page and previous_name == owner and previous_role
                      and abs(previous.bbox[0] - line.bbox[0]) <= max(previous.size, line.size)
                      and 0 <= line.bbox[1] - previous.bbox[1]
                      and line.bbox[1] - previous.bbox[3] <= max(previous.size, line.size) * 1.5)
            # A known owner's introduction can wrap within a role word. Join
            # it for grammar only; a following person's name is never a suffix.
            if nearby and any(previous.text.rstrip().endswith(word[:cut])
                              and re.match(re.escape(word[cut:]) + r'(?=$|[\s,，;；:：。、（(）)])', line.text.lstrip())
                              for word in person_words for cut in range(1, len(word))):
                analysis_text = previous.text + line.text.lstrip()
        compact = re.sub(r"\s+", "", analysis_text)
        tokens = [token for token in _SPLIT.split(analysis_text.strip()) if token]
        introduced = bool(len(tokens) > 1 and tokens[0] in intro_labels)
        person_line = analysis_text
        if introduced:
            person_line = analysis_text[analysis_text.index(tokens[0]) + len(tokens[0]) :]
            separator = _SPLIT.match(person_line)
            if separator:
                person_line = person_line[separator.end() :]
        shape = heading_shape(analysis_text)
        if not shape or shape[0] not in names:
            # Verified source names need no second surname-frequency test.
            # The trailing role still has to follow the shared person grammar.
            for candidate in names:
                prefix = re.match(
                    r"^\s*"
                    + "".join(re.escape(char) + r"\s*" for char in candidate)
                    + r"(.*)$",
                    person_line,
                )
                tail = (
                    [token for token in _SPLIT.split(prefix[1]) if token]
                    if prefix
                    else []
                )
                if tail and tail[0] in person_words:
                    shape = (
                        candidate,
                        False,
                        "gender" if tail[0] in {"男", "女"} else "title",
                    )
                    break
        name = shape[0] if shape else None
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        own_title_field = (
            next_line is not None
            and next_line.page == line.page
            and bool(_TITLE_FIELD_LINE.fullmatch(next_line.text.strip()))
        )
        emphasized = emphasized_line(line)
        starts_with_name = bool(name and compact.startswith(re.sub(r"\s+", "", name)))
        role = None
        # A field explicitly naming a person is stronger than name vocabulary;
        # an unreadable or unfamiliar value is not permission to merge it.
        label = re.match(r"^\s*姓\s*名\s*[:：]?\s*(.+)$", line.text)
        resume = _RESUME_TITLE.fullmatch(compact) if compact not in _LABELS else None
        if resume:
            name = resume[1] if resume[1] in names else None
            role = 'person_resume_title'
        elif label:
            if re.sub(r"\s+", "", label[1]) == re.sub(r"\s+", "", owner):
                name = owner
            role = "name_field"
        elif introduced and name_headings.get(re.sub(r"\s+", "", person_line)):
            name = name_headings[re.sub(r"\s+", "", person_line)]
            role = "person_introduction"
        elif (
            shape
            and name not in _LABELS
            and (starts_with_name or introduced)
            and shape[2] in {"gender", "title"}
        ):
            role = "person_heading"
        elif shape and name not in _LABELS and not starts_with_name and emphasized:
            role = "prefixed_person_heading"
        else:
            # A supplied faculty name standing alone (or as a profile heading)
            # is an unresolved section lead, regardless of its page position.
            standalone = name_headings.get(compact)
            if standalone:
                name = standalone
                role = (
                    "person_heading_with_title_field"
                    if own_title_field
                    else "standalone_person_heading"
                )
            elif (
                shape and name not in _LABELS and shape[2] == "none" and own_title_field
            ):
                role = "person_heading_with_title_field"
            elif not _TITLE_FIELD_LINE.fullmatch(line.text.strip()):
                if introduced:
                    tokens = tokens[1:]
                possible_name = bool(
                    tokens
                    and re.fullmatch(r"[一-鿿·•]{2,8}", tokens[0])
                    and tokens[0] not in _LABELS
                    and tokens[0] not in person_words
                )
                # Use the shared grammar's actual role token, not an arbitrary
                # word suffix: an institution post or education value can end
                # in that same word. A normal labelled metadata field is not a
                # person introduction. Glyph-spaced names retain their tokens.
                role_index = next(
                    (i for i, token in enumerate(tokens) if token in person_words), None
                )
                prefix_tokens = tokens[:role_index] if role_index else []
                prefix = "".join(prefix_tokens)
                # Reuse name syntax without surname confidence. Multiple full
                # tokens are not one person's name; only glyph-spaced runs can
                # stand for a single name. Body locations and field values must
                # not be invented into an identity because a later token is a role.
                one_name = len(prefix_tokens) == 1 or (
                    prefix_tokens and all(len(token) == 1 for token in prefix_tokens)
                )
                person_role = bool(
                    one_name
                    and (
                        _HAN_NAME.fullmatch(prefix) or _MINORITY_NAME.fullmatch(prefix)
                    )
                    and prefix not in _LABELS
                    and (introduced or not re.match(r"^\s*[^:：]+[:：]", person_line))
                )
                possible_name = possible_name or person_role
                if possible_name and (
                    (len(tokens) == 1 and (own_title_field or introduced))
                    or person_role
                ):
                    if person_role:
                        name = None
                    role = "unreadable_person_heading"
        person_evidence.append((line, name, role))

    # First establish actual person evidence anywhere on the complete first
    # page. Every accepted owner field/introduction/title supplies its observed
    # layout; a short initial window or a bare-name-only lookup would lose it.
    # An unstyled standalone author name, unlike a person field or role heading,
    # supplies no proof that this document is that person's biography.
    owner_headings = [
        line
        for line, name, role in person_evidence
        if line.page == 1
        and name == owner
        and role
        and (role != "standalone_person_heading" or emphasized_line(line))
    ]
    blockers = []
    for line, name, role in person_evidence:
        compact = re.sub(r"\s+", "", line.text)
        if (
            role is None
            and emphasized_line(line)
            and compact not in _LABELS
            and compact not in _RESUME_SECTIONS
            and re.fullmatch(r"[一-鿿·•]{2,8}", compact)
            and any(same_heading_slot(line, anchor) for anchor in owner_headings)
        ):
            # Layout alone cannot distinguish an unfamiliar person from a short
            # section title. Preserve this as unknown, never whitelist prose.
            role = "unknown_person_heading_template"
        if role and name != owner:
            blockers.append(
                {
                    "page": line.page,
                    "bbox": [round(v, 2) for v in line.bbox],
                    "heading_sha256": hashlib.sha256(line.text.encode()).hexdigest(),
                    "role": role,
                    "name_recognized": name is not None,
                }
            )
    if not owner_headings and not blockers:
        return {
            "method": "person_heading_structure",
            "status": "unknown",
            "reason": "pdf_owner_heading_unverified",
            "inspected_pages": len(pages),
        }
    return {
        "method": "person_heading_structure",
        "status": "multiple_or_unknown" if blockers else "single_owner",
        "inspected_pages": len(scanned["pages"]),
        "boundaries": blockers,
    }
