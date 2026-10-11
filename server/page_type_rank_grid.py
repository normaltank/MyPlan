"""Explicit HTML rank groups with multi-person name cells, not one card/person.

Each container has one exact declared rank heading and structurally selected
name cells. Every token is admitted, reviewed, or unresolved. This parser never
infers a rank from unrelated prose and never assigns a cell's first link to all
its people. Source qualification and downstream identity/export remain shared.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


def applicable(branch: dict) -> bool:
    return ((branch.get('kind') or 'roster') == 'roster'
            and all(isinstance(branch.get(key), str) and branch[key].strip()
                    for key in ('container_selector', 'record_start_selector', 'card_selector')))


def reconcile(_old_parser, captured, plan, name_decisions=None):
    from bs4 import BeautifulSoup
    from server.faculty_extractor import FacultyCandidate
    from server.harvest_workflow import RosterReconciliation, _apply_name_decisions
    from server.name_list import _item, _list_items, _person_name, _rare_name
    from server.source_qualification import captured_source_evidence
    from server.titles import TITLE_KEYWORDS

    result = RosterReconciliation([], {}, [], [])
    identity_positions = {}
    for page in captured:
        branch = page.branch
        if not applicable(branch):
            result.unresolved.append({'url': page.url, 'status': 'rank_grid_structure_invalid'})
            continue
        soup = BeautifulSoup(page.html, 'html.parser')
        groups = soup.select(branch['container_selector'])
        source = captured_source_evidence(page.html, page.url, branch, plan, page.source_role_evidence)
        if not groups:
            result.unresolved.append({'url': page.url, 'status': 'rank_grid_groups_missing'})
        group_ids = {id(group) for group in groups}
        if any(not any(id(ancestor) in group_ids for ancestor in cell.parents)
               for cell in soup.select(branch['card_selector'])):
            result.unresolved.append({'url': page.url, 'status': 'rank_grid_cell_outside_group'})
        total_tokens, admitted, awaiting_count, excluded_count = 0, 0, 0, 0
        item_observations = []
        for index, group in enumerate(groups):
            if any(id(ancestor) in group_ids for ancestor in group.parents):
                result.unresolved.append({'url': page.url, 'status': 'rank_grid_groups_overlap', 'group': index})
                continue
            headings = group.select(branch['record_start_selector'])
            rank = re.sub(r'\s+', '', headings[0].get_text()) if len(headings) == 1 else ''
            if rank not in TITLE_KEYWORDS:
                result.unresolved.append({'url': page.url, 'status': 'rank_grid_rank_unverified', 'group': index})
                continue
            cells = group.select(branch['card_selector'])
            if not cells:
                result.unresolved.append({'url': page.url, 'status': 'rank_grid_cells_missing', 'group': index})
            for cell_index, cell in enumerate(cells):
                text = cell.get_text(' ', strip=True)
                tokens = [token for part in re.split(r'[、,，;；\n]', text)
                          for token in _list_items(part) if token]
                if not tokens:
                    result.unresolved.append({'url': page.url, 'status': 'rank_grid_cell_empty',
                                              'group': index, 'cell': cell_index})
                total_tokens += len(tokens)
                links = {}
                for anchor in cell.select('a[href]'):
                    label = _item(anchor.get_text(' ', strip=True))
                    name = _person_name(label) or _rare_name(label)
                    if name:
                        address = urljoin(page.url, anchor['href'])
                        parsed = urlparse(address)
                        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username:
                            links.setdefault(name, set()).add(address)
                        else:
                            result.unresolved.append({'url': page.url, 'status': 'rank_grid_profile_invalid',
                                                      'group': index, 'cell': cell_index, 'name': name})
                found, review = [], []
                for token_index, token in enumerate(tokens):
                    position = {'group': index, 'cell': cell_index, 'item': token_index, 'rank': rank}
                    name = _person_name(token)
                    rare = _rare_name(token) if name is None else None
                    if not name and not rare:
                        result.unresolved.append({'url': page.url, 'status': 'rank_grid_name_unrecognized',
                                                  'group': index, 'cell': cell_index, 'token': token})
                        item_observations.append({**position, 'token': token, 'disposition': 'unresolved'})
                        continue
                    name = name or rare
                    addresses = links.get(name, set())
                    if len(addresses) > 1:
                        result.unresolved.append({'url': page.url, 'status': 'rank_grid_profile_ambiguous',
                                                  'group': index, 'cell': cell_index, 'name': name})
                    profile = next(iter(addresses)) if len(addresses) == 1 else page.url
                    key = (page.url, name, profile)
                    if key in identity_positions:
                        result.unresolved.append({'url': page.url, 'status': 'rank_grid_identity_ambiguous',
                            'name': name, 'profile_url': profile,
                            'positions': [identity_positions[key], position]})
                    else:
                        identity_positions[key] = position
                    item_observations.append({**position, 'name': name, 'profile_url': profile,
                                              'disposition': 'review' if rare else 'accepted'})
                    person = FacultyCandidate(name=name, url=profile, title=rank, card_context=str(cell),
                        name_review_reason='rare_surname' if rare else '')
                    (review if rare else found).append(person)
                excluded = []
                awaiting = _apply_name_decisions(page.url, found, review, name_decisions or {},
                                                result.unresolved, excluded)
                admitted += len(found)
                awaiting_count += len(awaiting)
                excluded_count += len(excluded)
                for person in found:
                    result.candidates.append(person)
                    result.candidate_sources.setdefault((person.name, person.url), set()).add(source)
                    if person.url != page.url and branch.get('profile_content_selector'):
                        selector = branch['profile_content_selector']
                        start = branch.get('profile_content_start_selector')
                        result.profile_content_selectors.setdefault(person.url, set()).add((selector, start))
                        result.profile_content_sources.setdefault(person.url, []).append({
                            'roster_url': page.url, 'profile_content_selector': selector,
                            'profile_content_start_selector': start})
        result.observations.append({'url': page.url, 'kind': 'rank_grid', 'groups': len(groups),
            'listed_items': total_tokens, 'extracted_candidates': admitted,
            'awaiting_review': awaiting_count, 'reviewed_exclusions': excluded_count,
            'items': item_observations})
    return result


def register():
    from server.page_types import RosterParams, TypeDefinition, register_type
    register_type(TypeDefinition('roster.rank_grid', 1, 'roster', RosterParams, applicable, reconcile,
        'captured pages + source-qualified groups with exact rank heading and name cells',
        'RosterReconciliation, preserving every name-cell token disposition and source evidence',
        ('rank_grid_structure_invalid', 'rank_grid_groups_missing', 'rank_grid_groups_overlap',
         'rank_grid_rank_unverified', 'rank_grid_cells_missing', 'rank_grid_cell_empty',
         'rank_grid_name_unrecognized', 'rank_grid_profile_ambiguous', 'rank_grid_profile_invalid',
         'rank_grid_cell_outside_group', 'rank_grid_identity_ambiguous', 'rare_surname_needs_review'),
        'HTML roster with container_selector rank groups, exactly one record_start_selector rank heading per '
        'group, and card_selector name cells inside each group; a cell may contain several people. '
        'Ranks must be exact declared titles; unknown tokens, outside cells and ambiguous identities block delivery.'))
