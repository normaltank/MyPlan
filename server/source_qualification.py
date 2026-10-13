"""Server-owned page-purpose witnesses, separate from names and employment type.

Role claims in plans never qualify a person. Current rendered scope, title and
navigation witnesses must establish a positive faculty relationship. Unknown
and organizational observations remain evidence; this module excludes nobody.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Literal
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

SourceRole = Literal['faculty_roster', 'organizational_membership', 'unverified_people_source']
SOURCE_EVIDENCE_VERSION = 2
ROLES = frozenset({'faculty_roster', 'organizational_membership', 'unverified_people_source'})
SOURCE_FIELDS = frozenset({'source_role', 'source_container_selector', 'role_evidence_selector',
                           'source_context_selector'})
# Positive relationship vocabulary, not a people/college blacklist. A context
# menu anywhere on the page is insufficient: the witness must name this page.
_FACULTY_PURPOSE = re.compile(
    r'(?:师资(?:队伍|名录|力量|介绍|简介)|教师(?:名单|名录|队伍|介绍|简介|风采)|'
    r'教职工(?:名单|名录|队伍)|教授博士介绍|导师(?:名单|名录|介绍|信息|队伍|风采)|'
    r'(?:博士生|硕士生|研究生)导师|教学(?:队伍|名师)|'
    r'(?:专任|兼职|外聘|客座)教师|客座教授|faculty(?:\s+members)?|academic\s+staff)', re.I)
_ORGANIZATIONAL_PURPOSE = re.compile(
    r'(?:党(?:委|总支|支部)(?:成员|委员|组成|领导)?|'
    r'[\u4e00-\u9fff]{2,30}委员会(?:成员|名单|组成)?|'
    r'行政(?:机构|人员|领导)|现任领导|学院领导|组织机构)', re.I)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _purpose_label(text: str, target: dict) -> str:
    """Remove only observed target institution branding around a purpose label."""
    text = text.strip()
    school, college = (str(target.get(field) or '').strip() for field in ('school', 'college'))
    affixes = [value for value in (school + college, college + school, school, college) if value]
    previous = None
    while text != previous:
        previous = text
        for affix in affixes:
            if text.startswith(affix):
                text = text[len(affix):].strip(' ：:-|—_')
            if text.endswith(affix):
                text = text[:-len(affix)].strip(' ：:-|—_')
    return text


def faculty_purpose(text: str, target: dict) -> bool:
    """A complete affirmative label, including ordinary target-branded titles."""
    return bool(_FACULTY_PURPOSE.fullmatch(_purpose_label(text, target)))


def _faculty_group_path(heading, title: str, url: str, target: dict) -> dict | None:
    """Read an explicit current group path; a continuation still needs a link proof."""
    from server.titles import TITLE_KEYWORDS

    # A general menu or an unrelated sidebar cannot supply this relationship.
    # The selected witness must identify itself as the current-position path.
    if not re.search(r'当前位置\s*[:：]', heading.get_text(' ', strip=True)):
        return None
    links = heading.select('a[href]')
    if len(links) < 2:
        return None
    parent, current = links[-2:]
    for node in (parent, current):
        destination = urlsplit(urljoin(url, node['href']))
        if (destination.scheme not in {'http', 'https'} or destination.username
                or destination.password is not None
                or destination.hostname != urlsplit(url).hostname):
            return None
    label = current.get_text(' ', strip=True)
    group = (label in TITLE_KEYWORDS or bool(re.fullmatch(
        r'[\u4e00-\u9fffA-Za-z·（）()]+(?:系|教研室|教学部)', label)))
    if (faculty_purpose(parent.get_text(' ', strip=True), target)
            and group and _purpose_label(title, target) == label):
        return {'root_url': urljoin(url, current['href']),
                'faculty_parent_url': urljoin(url, parent['href']), 'group_label': label}
    return None


def _document_branding(title: str, target: dict) -> dict[str, bool]:
    """Exact institution signature segments in the current browser document title."""
    segments = [part.strip() for part in re.split(r'[-—_|]', title)]
    institutions = {field: str(target.get(field) or '').strip() for field in ('school', 'college')}
    complete = bool(all(institutions.values()) and institutions['school'] + institutions['college'] in segments)
    return {field: bool(name and (name in segments or complete)) for field, name in institutions.items()}


def organizational_purpose(text: str, scope, target: dict) -> bool:
    """Recognize the source's organization type, not its members' occupations."""
    label = _purpose_label(text, target)
    if _ORGANIZATIONAL_PURPOSE.fullmatch(label):
        return True
    if label != '组织建设':
        return False
    office = re.compile(r'(?:党委书记|党委副书记(?:、纪委书记)?|纪委书记|'
                        r'(?:教工|学生(?:第[一二三四五六七八九十]+)?)党支部)[:：]([\u4e00-\u9fff]{2,4})')
    # A standalone office/value row is directory structure. A mention inside
    # a policy sentence or a negated office is not. Values remain raw evidence,
    # not an affirmative identification or teacher-employment judgement.
    return any(office.fullmatch(re.sub(r'\s+', '', node.get_text(' ', strip=True)))
               for node in scope.select('p,td,li,dd') if not node.find(['p', 'td', 'li', 'dd']))


def source_claim_valid(branch: dict) -> bool:
    return (branch.get('source_role') in ROLES and all(
        isinstance(branch.get(key), str) and branch[key].strip()
        for key in SOURCE_FIELDS - {'source_role'}))


def source_unit_selector(branch: dict) -> str | None:
    if branch.get('unit_selector'):
        return branch['unit_selector']
    if branch.get('kind') == 'navigation':
        return branch.get('child_link_selector')
    if branch.get('kind') == 'segmented':
        return branch.get('container_selector')
    return branch.get('card_selector')


def _institution_context(node) -> bool:
    """Site identity in a header/navigation/logo/footer, not a person's prose."""
    lineage = [node, *node.parents]
    if any(parent.name == 'article' for parent in lineage):
        return False
    for parent in lineage:
        if parent.name in {'html', 'body', '[document]'}:
            break
        if parent.name in {'header', 'footer', 'nav'}:
            return True
        tokens = ' '.join([str(parent.get('id') or ''), *parent.get('class', [])])
        # Identifier word boundaries also appear in camel/Pascal case. Keep
        # the same institution vocabulary rather than matching arbitrary prefixes.
        tokens = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', tokens)
        tokens = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', tokens)
        if re.search(r'(?:^|[-_\s])(?:head(?:er)?|foot(?:er)?|top|bottom|copy|logo|nav|brand|school|college|institution|affiliation)(?:$|[-_\s])', tokens, re.I):
            return True
    return bool(re.search(r'版权所有|copyright', node.get_text(' ', strip=True), re.I))


def _branding_text(context, url: str, field: str) -> str:
    """Read this site's branding rather than a link to another institution."""
    fragment = BeautifulSoup(str(context), 'html.parser')
    current = urlsplit(url)
    host = (current.hostname or '').removeprefix('www.')
    for anchor in list(fragment.select('a[href]')):
        href = str(anchor['href']).strip()
        destination = urlsplit(urljoin(url, href))
        linked_host = (destination.hostname or '').removeprefix('www.')
        institution_home = (bool(linked_host) and len(linked_host.split('.')) >= 2
                            and (host == linked_host or host.endswith('.' + linked_host)))
        same_site = destination.hostname == current.hostname
        page_path = destination.path.rstrip('/')
        local_section = (same_site and (not page_path or current.path == destination.path
                                        or current.path.startswith(page_path + '/')))
        navigation = any(parent.name == 'nav' or parent.get('role') == 'navigation'
                         or re.search(r'(?:^|[-_\s])(?:nav(?:igation|bar)?|menu)(?:$|[-_\s])',
                                      ' '.join([str(parent.get('id') or ''), *parent.get('class', [])]), re.I)
                         for parent in anchor.parents)
        logo = same_site and anchor.find('img') is not None and not navigation
        allowed = institution_home if field == 'school' else local_section or logo
        if (destination.scheme not in {'http', 'https'} or not href or href.startswith('#')
                or not allowed):
            anchor.decompose()
    return fragment.get_text(' ', strip=True) + ' ' + ' '.join(
        str(image.get('alt') or image.get('title') or '') for image in fragment.find_all('img'))


def witness(html: str, url: str, branch: dict, target: dict, *, pagination_relationship=None) -> dict:
    """Read complete local witnesses; a model supplies only role and CSS."""
    result = {'url': url, 'source_evidence_version': SOURCE_EVIDENCE_VERSION,
              **{key: branch.get(key) for key in SOURCE_FIELDS},
              'source_html_sha256': digest(html), 'role_supported': False}
    if not source_claim_valid(branch):
        return {**result, 'reason': 'source_claim_missing'}
    soup = BeautifulSoup(html, 'html.parser')
    try:
        scopes = soup.select(branch['source_container_selector'])
        headings = soup.select(branch['role_evidence_selector'])
        contexts = soup.select(branch['source_context_selector'])
    except Exception:
        return {**result, 'reason': 'source_selector_invalid'}
    result.update(scope_count=len(scopes), role_count=len(headings), context_count=len(contexts))
    if len(scopes) != 1 or len(headings) != 1 or not contexts:
        return {**result, 'reason': 'source_witness_not_unique'}
    scope, heading = scopes[0], headings[0]
    unit_selector = source_unit_selector(branch)
    units_within = True
    if unit_selector:
        try:
            units = soup.select(unit_selector)
            units_within = all(node is scope or scope in node.parents for node in units)
        except Exception:
            units_within = False
    result.update(unit_selector=unit_selector, units_within_scope=units_within)
    result.update(selected_scope_sha256=digest(str(scope)), role_scope_sha256=digest(str(heading)),
                  context_scope_sha256=digest(''.join(str(context) for context in contexts)))
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    heading_text = heading.get_text(' ', strip=True)
    # A document heading matching its own title or an explicit current-page
    # navigation link binds purpose to this source, rather than a sibling menu.
    terminal_heading = (heading.select('a[href]')[-1].get_text(' ', strip=True)
                        if heading.select('a[href]') else heading_text)
    title_bound = bool(title and any(
        re.sub(r'\s+', '', _purpose_label(text, target)) == re.sub(r'\s+', '', _purpose_label(title, target))
        for text in (heading_text, terminal_heading)))
    own_links = [a.get_text(' ', strip=True) for a in heading.select('a[href]')
                 if urljoin(url, a['href']) == url]
    if heading.name == 'a' and heading.get('href') and urljoin(url, heading['href']) == url:
        own_links.append(heading_text)
    branding = {field: ' '.join(_branding_text(context, url, field) for context in contexts)
                for field in ('school', 'college')}
    college = str(target.get('college') or '').strip()
    school = str(target.get('school') or '').strip()
    # Schools often put university and college branding in separate page
    # regions. Read and bind all selected institution regions from this page;
    # an entire body or a person's biography is not institutional branding.
    separate_context = all(scope is not context and scope not in context.descendants
                           and context not in scope.descendants for context in contexts)
    institutional_context = all(_institution_context(context) for context in contexts)
    result['non_institutional_context_count'] = sum(not _institution_context(context) for context in contexts)
    result['source_context_issue'] = ('source_context_overlaps_content' if not separate_context else
                                      'source_context_not_institutional' if not institutional_context else None)
    document_branding = _document_branding(title, target)
    result['document_title_branding'] = document_branding
    college_bound = bool(college and (college in branding['college'] or document_branding['college'])
                         and school and (school in branding['school'] or document_branding['school'])
                         and separate_context and institutional_context)
    result.update(title_bound=title_bound, current_link_bound=bool(own_links), college_bound=college_bound)
    # A positive, source-bound organizational purpose can retire an old roster
    # claim. Merely failing the faculty vocabulary is never negative evidence.
    faculty_page_purpose = (faculty_purpose(title, target)
                            or any(faculty_purpose(node.get_text(' ', strip=True), target)
                                   for node in soup.find_all('h1')))
    result['non_faculty_purpose'] = bool(
        branch['source_role'] == 'organizational_membership' and college_bound
        and not faculty_page_purpose
        and ((title_bound and organizational_purpose(title, scope, target))
             or any(organizational_purpose(text, scope, target) for text in own_links)))
    if branch['source_role'] == 'faculty_roster':
        group_path = _faculty_group_path(heading, title, url, target)
        result['faculty_group_path'] = group_path
        from server.source_navigation import relationship_matches
        paginated_group = bool(group_path and pagination_relationship and relationship_matches(
            html, url, branch, group_path, pagination_relationship))
        purpose = bool((title_bound and faculty_purpose(title, target))
                       or any(faculty_purpose(text, target) for text in own_links)
                       or (group_path and group_path['root_url'] == url) or paginated_group)
        result['role_supported'] = bool(purpose and college_bound and units_within)
        if not result['role_supported']:
            result['reason'] = 'faculty_relationship_unverified'
    else:
        result['role_supported'] = True  # It remains auxiliary, never faculty permission.
    return result


@dataclass(frozen=True)
class SourceEvidence:
    source_url: str
    role: str
    status: str
    source_html_sha256: str
    selected_scope_sha256: str = ''
    role_scope_sha256: str = ''
    context_scope_sha256: str = ''
    reason: str = ''
    source_kind: str = ''
    owner_name: str = ''

    def to_dict(self) -> dict:
        return asdict(self)


def captured_source_evidence(html: str, url: str, branch: dict, target: dict,
                             proof: dict | None = None) -> SourceEvidence:
    proof = read_source_packet(proof)
    current = witness(html, url, branch, target,
                      pagination_relationship=(proof if isinstance(proof, dict) else {}).get('pagination_relationship'))
    keys = ('source_evidence_version', 'url', *SOURCE_FIELDS, 'unit_selector', 'units_within_scope', 'source_html_sha256', 'selected_scope_sha256',
            'role_scope_sha256', 'context_scope_sha256')
    proven = (isinstance(proof, dict) and proof.get('confirmed') is True
              and proof.get('dom_stable') is True and proof.get('network_idle') is True
              and proof.get('witness_visible') is True
              and all(proof.get(key) == current.get(key) for key in keys)
              and current['role_supported'])
    return SourceEvidence(url, branch.get('source_role', 'unverified_people_source'),
                          'verified' if proven else 'unknown', current['source_html_sha256'],
                          current.get('selected_scope_sha256', ''), current.get('role_scope_sha256', ''),
                          current.get('context_scope_sha256', ''),
                          '' if proven else current.get('reason', 'source_browser_proof_missing'),
                          branch.get('source_kind') or target.get('source_kind') or '')


def owned_profile_relationship(html: str | None, url: str, name: str, target: dict,
                               scope_evidence: dict | None, *, unique_owner: bool) -> SourceEvidence | None:
    """Independent current affiliation from an actually proved own profile.

    This conservative reader accepts an explicit current work-unit field and
    owned teaching job/identity field only. Education, rank alone and arbitrary
    university mentions are not relationship evidence. No model/plan provides
    names, quotes, titles or permission.
    """
    from server.profile_content import scope_profile_html
    from server.segmented_roster import heading_shape
    if not html or not unique_owner or not isinstance(scope_evidence, dict):
        return None
    proof = scope_evidence.get('profile_content_browser_evidence')
    if not isinstance(proof, dict) or any(proof.get(key) is not True for key in
            ('confirmed', 'container_visible', 'dom_stable', 'network_idle')):
        return None
    if proof.get('html_sha256') != digest(html):
        return None
    scoped, current, issue = scope_profile_html(html, scope_evidence.get('profile_content_selector'), url,
                                               start_selector=scope_evidence.get('profile_content_start_selector'))
    if issue or not current or proof.get('selected_html_sha256') != current['selected_html_sha256']:
        return None
    soup = BeautifulSoup(scoped, 'html.parser')
    compact_name = re.sub(r'\s+', '', name)
    owners = [node for node in soup.select('h1,h2,h3,h4,h5,h6')
              if re.sub(r'\s+', '', node.get_text()) == compact_name]
    if not owners:
        return None
    for node in soup.select('h1,h2,h3,h4,h5,h6,p,td,dd'):
        text = node.get_text(' ', strip=True)
        shape = heading_shape(text)
        label = re.fullmatch(r'姓\s*名\s*[:：]\s*(.+)', text)
        if ((label and re.sub(r'\s+', '', label[1]) != compact_name)
                or (node.name.startswith('h') and shape and shape[0] != name
                    and shape[2] in {'none', 'title', 'gender'})):
            return None
    academic_roles = {'教授', '副教授', '讲师', '高级讲师', '助教', '助理教授', '研究员',
                      '副研究员', '助理研究员', '师资博士后', '名誉教授', '特聘教授', '讲座教授'}
    role_pattern = re.compile(r'\s*(?:身份|岗位|职务|现任职务|现任岗位)\s*[:：]\s*'
                              r'(?:(?:兼职|外聘|专任|客座|特聘)\s*)?(?:教师|'
                              + '|'.join(re.escape(role) for role in sorted(academic_roles, key=len, reverse=True)) + r')\s*')
    jobs = [node for node in soup.select('p,td,dd,div')
            if not node.find(['p', 'td', 'dd', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            and role_pattern.fullmatch(node.get_text(' ', strip=True))]
    # An academic rank plus a university mention is insufficient. The owned
    # current job/identity must explicitly establish the teaching relationship.
    if not jobs:
        return None
    school = re.sub(r'\s+', '', str(target.get('school') or ''))
    college = re.sub(r'\s+', '', str(target.get('college') or ''))
    if not school or not college:
        return None
    units = []
    for node in soup.select('p,td,dd,div'):
        if node.find(['p', 'td', 'dd', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            continue
        match = re.fullmatch(r'\s*(?:现工作单位|工作单位|任职单位|所在单位)\s*[:：]\s*(.+)',
                             node.get_text(' ', strip=True))
        if match and re.sub(r'\s+', '', match[1]) == school + college:
            units.append(node)
    if len(units) != 1:
        return None
    return SourceEvidence(url, 'faculty_profile_appointment', 'verified', digest(html),
                          current['selected_html_sha256'], digest(str(jobs[0])), digest(str(units[0])),
                          owner_name=name)


async def _verify_source_here(page, branch: dict, target: dict, *, local_capture: dict | None = None,
                              extra_witness_selectors: tuple[str, ...] = ()) -> dict:
    """Current browser proof shares the existing visibility/network/DOM contract."""
    from server.profile_structure import _FACTS, verify_profile_container
    from server.browser_readiness import install_request_tracking
    tracker = install_request_tracking(page)
    key = (page.url, branch['source_container_selector'])
    previous = getattr(page, '_faculty_source_read', None)
    recurring = None
    if (previous and previous['key'] == key and tracker.document_response is not None
            and previous['navigation_response'] is not tracker.document_response):
        recurring = previous['failed_requests']
    captured = {}
    scope = await verify_profile_container(page, branch['source_container_selector'], local_capture=captured,
                                           recurring_failures=recurring)
    page._faculty_source_read = {'key': key, 'navigation_response': tracker.document_response,
                                'failed_requests': captured.get('failed_requests', [])}
    read_activity = tracker.activity - tracker.image_activity
    html = captured.get('html') or await page.content()
    value = witness(html, page.url, branch, target)
    first_witness = value.copy()
    visible = True
    rendered_context = ''
    for key in ('role_evidence_selector', 'source_context_selector'):
        selected = page.locator(branch[key])
        count = await selected.count()
        visible = visible and (count == 1 if key == 'role_evidence_selector' else count > 0)
        for index in range(count):
            node = selected.nth(index)
            facts = await page.evaluate(_FACTS, {'selector': branch[key], 'index': index,
                                                'start': None, 'trivialChars': 0})
            visible = (visible and await node.is_visible() and facts.get('container_visible') is True
                       and not facts.get('hidden_content_count') and not facts.get('active_animation_count'))
            if key == 'source_context_selector' and visible:
                # Read the actual visible branding from every selected region.
                # Hidden child text is still rejected by the shared facts above.
                rendered_context += ' ' + await node.inner_text()
                rendered_context += ' ' + ' '.join(await node.locator('img').evaluate_all(
                    "nodes => nodes.filter(n => n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden')"
                    ".map(n => n.alt || n.title || '')"))
    document_branding = _document_branding(await page.title(), target)
    rendered_affiliation = all(str(target.get(field) or '').strip()
                               and (str(target[field]).strip() in rendered_context or document_branding[field])
                               for field in ('school', 'college'))
    # Capture final HTML and all computed witness visibility in one browser
    # task. A theme stylesheet outside the selected nodes can still hide them.
    selectors = [branch[key] for key in ('source_container_selector', 'role_evidence_selector',
                                        'source_context_selector')]
    selectors.extend(extra_witness_selectors)
    from server.profile_content import TRIVIAL_HIDDEN_CHARS
    from server.word_toc import live_decorations
    snapshot = await page.evaluate('args => { const facts = (' + _FACTS + ''');
        const rows=args.selectors.map((selector,role)=>[...document.querySelectorAll(selector)].map((_,index)=>
          facts({selector,index,start:null,trivialChars:role===0?args.trivialChars:0,
            wordToc:role===0?args.wordToc:[],allowEmpty:false})));
        const doctype=document.doctype?new XMLSerializer().serializeToString(document.doctype):'';
        return {url:location.href,html:doctype+document.documentElement.outerHTML,rows}; }''',
        {'selectors': selectors, 'trivialChars': TRIVIAL_HIDDEN_CHARS,
         'wordToc': live_decorations(html, selectors[0])})
    final_html = snapshot['html']
    final_value = witness(final_html, snapshot['url'], branch, target)
    # Compare the complete semantic witness, including non_faculty_purpose and
    # any future decision dependencies; the unrelated original SHA may differ.
    stable = scope.get('dom_stable') is True and all(first_witness.get(key) == final_value.get(key)
        for key in set(first_witness) | set(final_value) if key != 'source_html_sha256')
    final_visible = all((bool(rows) if index == 2 else len(rows) == 1) and all(
        facts['container_visible'] and not facts['hidden_content_count'] and not facts['active_animation_count']
        for facts in rows) for index, rows in enumerate(snapshot['rows']))
    visible = visible and final_visible
    network_idle = (scope.get('network_idle') is True
                    and read_activity == tracker.activity - tracker.image_activity)
    value = {**final_value, 'rendered_context_sha256': digest(rendered_context)}
    if branch['source_role'] == 'faculty_roster' and not rendered_affiliation:
        value.update(role_supported=False, college_bound=False, reason='source_context_not_visible')
    if not rendered_affiliation:
        value['non_faculty_purpose'] = False
    value.update(source_html_sha256=digest(final_html), dom_stable=stable, network_idle=network_idle,
                 witness_visible=visible, confirmed=bool(scope.get('confirmed') and stable and visible and network_idle
                                                       and value['role_supported']))
    value['final_witness_facts_sha256'] = digest(json.dumps(snapshot['rows'], sort_keys=True))
    if extra_witness_selectors:
        value['extra_witness_selectors'] = list(extra_witness_selectors)
        value['extra_witness_facts'] = snapshot['rows'][3:]
    value['source_request_evidence'] = {'current_failures': list(tracker.failed_requests),
        'previous_navigation_failures': recurring, 'server_rendered_content': scope.get('server_rendered_content'),
        'scope_blocking_failed_requests': scope.get('blocking_failed_request_count'),
        'scope_blocking_pending_requests': scope.get('blocking_pending_request_count'),
        'scope_network_idle': scope.get('network_idle'), 'final_network_idle': network_idle,
        'final_non_image_activity_delta': tracker.activity - tracker.image_activity - read_activity,
        'final_pending_requests': [(getattr(request, 'url', ''), getattr(request, 'resource_type', ''))
                                   for request in tracker.pending],
        'main_document_url': getattr(tracker.document_response, 'url', None),
        'main_document_status': getattr(tracker.document_response, 'status', None)}
    if not scope.get('confirmed'):
        value['source_scope_issue'] = scope.get('reason') or scope.get('scope_issue')
        value['source_scope_blocking_requests'] = scope.get('blocking_requests', [])
    elif not network_idle:
        value['source_scope_issue'] = 'network_activity_changed'
    value['proof_sha256'] = digest(json.dumps(value, sort_keys=True, ensure_ascii=False))
    if local_capture is not None:
        local_capture.update(html=final_html)
    return value


async def verify_source(page, branch: dict, target: dict, *, local_capture: dict | None = None) -> dict:
    """Prove this source; at most one canonical faculty-root read can bind pagination."""
    capture = {}
    value = await _verify_source_here(page, branch, target, local_capture=capture)
    path = value.get('faculty_group_path')
    ready = all(value.get(key) is True for key in (
        'college_bound', 'units_within_scope', 'witness_visible', 'dom_stable', 'network_idle'))
    if (not value['confirmed'] and ready and branch['source_role'] == 'faculty_roster'
            and path and path['root_url'] != page.url):
        import asyncio
        from server.source_navigation import read_pagination_parent
        related = {'related_sources': [], 'root_observation': {'url': path['root_url']}}
        try:
            related = await asyncio.wait_for(read_pagination_parent(
                page, branch, target, path, local_capture=related), timeout=30)
        except TimeoutError:
            related['reason'] = 'pagination_parent_timeout'
        if related.get('relationship'):
            value = related['current_proof']
            capture['html'] = related['html']
            value.update(pagination_relationship=related['relationship'], role_supported=True, confirmed=True)
            value.pop('reason', None)
        else:
            value['pagination_relationship_issue'] = related.get('reason', 'pagination_relationship_unverified')
            value['pagination_parent_observation'] = related.get('root_observation', {})
        capture['related_sources'] = related.get('related_sources', [])
        value['proof_sha256'] = digest(json.dumps(value, sort_keys=True, ensure_ascii=False))
    if local_capture is not None:
        local_capture.update(capture)
    return value


def read_source_packet(packet: dict | None) -> dict | None:
    """Expand the browser-owned hashed source packet, preserving all proof fields."""
    if not isinstance(packet, dict):
        return None
    if 'full_source_result' not in packet:
        return packet
    reference = packet['full_source_result']
    if (not isinstance(reference, dict) or set(reference) != {'sha256', 'evidence_path'}
            or not re.fullmatch(r'[a-f0-9]{64}', str(reference.get('sha256', '')))
            or not isinstance(reference.get('evidence_path'), str)):
        return None
    from pathlib import Path
    path = Path(reference['evidence_path'])
    try:
        if path.suffix != '.json' or path.stat().st_size > 1024 * 1024:
            return None
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference['sha256']:
            return None
        value = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) and 'full_source_result' not in value else None


def _source_observations(branch: dict, events: list[dict]):
    """Yield calls in initiation order, retaining failed/incomplete latest calls."""
    if not source_claim_valid(branch):
        return
    calls, ordered, current_url = {}, [], None
    for event in events:
        data = event.get('data') or {}
        if event.get('type') == 'tool/call':
            try:
                args = json.loads(data.get('arguments') or '{}')
            except (ValueError, KeyError):
                continue
            if not isinstance(args, dict):
                continue
            name = data.get('name')
            if name == 'mcp__playwright__browser_navigate':
                current_url = args.get('url')
            entry = {'args': args, 'name': name, 'url': data.get('source_url') or current_url,
                     'proof': None, 'observation': None, 'error': False, 'complete': False}
            calls[data.get('callId')] = entry
            if name == 'mcp__playwright__browser_verify_source' and all(
                    args.get(key) == branch[key] for key in SOURCE_FIELDS):
                ordered.append(entry)
        elif event.get('type') == 'tool/result':
            for block in (data.get('message') or {}).get('content', []):
                entry = calls.get(block.get('toolCallId'))
                if entry is None:
                    continue
                entry.update(complete=True, error=block.get('isError') is True)
                for item in block.get('content', []):
                    if item.get('type') != 'text':
                        continue
                    try:
                        value = json.loads(item['text'])
                    except (ValueError, KeyError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    if entry['name'] == 'mcp__playwright__browser_survey':
                        visits = value.get('pages') or []
                        current_url = (visits[-1].get('url') if isinstance(visits, list) and visits
                                       and isinstance(visits[-1], dict) else None)
                    if entry['name'] == 'mcp__playwright__browser_verify_source':
                        entry['url'] = value.get('source_call_url') or entry['url'] or value.get('url')
                        expanded = read_source_packet(value)
                        if expanded is None:
                            entry['observation'] = value
                            continue
                        value = expanded
                    if value.get('url'):
                        current_url = value['url']
                    if entry['name'] == 'mcp__playwright__browser_verify_source':
                        entry['url'] = value.get('source_call_url') or entry['url'] or value.get('url')
                        entry['observation'] = value
                        if not entry['error']:
                            entry['proof'] = value
    for entry in ordered:
        if entry['url'] == branch['url']:
            yield entry['args'], entry['proof'], entry


def source_event_proof(branch: dict, events: list[dict]) -> dict | None:
    """Accept only the latest dedicated browser call matching this exact claim."""
    candidate = None
    unit = source_unit_selector(branch)
    for args, proof, _ in _source_observations(branch, events):
        candidate = None
        if not isinstance(proof, dict) or proof.get('url') != branch.get('url'):
            continue
        if (proof.get('confirmed') is True and proof.get('source_evidence_version') == SOURCE_EVIDENCE_VERSION
                and all(branch[key] == proof.get(key) for key in SOURCE_FIELDS)
                and (not unit or (args.get('unit_selector') == unit
                                  and proof.get('unit_selector') == unit and proof.get('units_within_scope') is True))
                and all(proof.get(key) is True for key in ('role_supported', 'witness_visible', 'dom_stable', 'network_idle'))
                and all(re.fullmatch(r'[0-9a-f]{64}', str(proof.get(key, ''))) for key in (
                    'source_html_sha256', 'selected_scope_sha256', 'role_scope_sha256', 'context_scope_sha256'))):
            candidate = proof
    return candidate


def source_event_issues(branch: dict, events: list[dict]) -> list[dict]:
    if source_event_proof(branch, events) is not None:
        return []
    observations = list(_source_observations(branch, events))
    issue = {'url': branch.get('url'), 'status': 'source_role_evidence_unverified',
             'failed_condition': 'current_source_role_proof_missing',
             'need': 'Use browser_verify_source with this exact role, witness selectors and unit_selector. '
                     'Use the failed conditions to correct CSS, affiliation or the current purpose/navigation scope; '
                     'retry access only when the actual request or DOM facts changed. '
                     'An unrecognized purpose is still unknown, not proof of a non-faculty page. '
                     'Keep its original and unresolved scope while processing other supported sources.'}
    if observations:
        args, proof, call = observations[-1]
        if not isinstance(proof, dict) or proof.get('url') != branch.get('url'):
            condition = ('source_verification_tool_failed' if call['error'] else
                         'source_verification_reply_invalid' if call['complete'] else 'source_verification_incomplete')
            issue.update(failed_condition=condition, failed_conditions=[condition])
            if isinstance(call['observation'], dict):
                issue['closest_observation'] = {key: call['observation'][key] for key in (
                    'url', 'source_call_url', *SOURCE_FIELDS, 'unit_selector', 'status', 'error_type',
                    'source_html_sha256', 'original_path', 'evidence_path') if key in call['observation']}
            return [issue]
        failures = []
        if proof.get('source_evidence_version') != SOURCE_EVIDENCE_VERSION:
            failures.append('source_evidence_version_mismatch')
        if not all(proof.get(key) == branch[key] for key in SOURCE_FIELDS):
            failures.append('current_source_parameter_binding_missing')
        unit = source_unit_selector(branch)
        if unit and (args.get('unit_selector') != unit or proof.get('unit_selector') != unit):
            failures.append('current_source_unit_binding_missing')
        if proof.get('units_within_scope') is False:
            failures.append('source_units_outside_scope')
        for fact, condition in (
                ('witness_visible', 'source_witness_not_visible'),
                ('college_bound', 'target_institution_unverified'),
                ('role_supported', 'faculty_relationship_unverified'),
                ('dom_stable', 'source_dom_unstable'),
                ('network_idle', 'source_network_not_idle')):
            if proof.get(fact) is not True:
                failures.append(condition)
        if not all(re.fullmatch(r'[0-9a-f]{64}', str(proof.get(key, ''))) for key in (
                'source_html_sha256', 'selected_scope_sha256', 'role_scope_sha256', 'context_scope_sha256')):
            failures.append('source_snapshot_binding_missing')
        failures = failures or [proof.get('source_scope_issue') or 'source_scope_unverified']
        issue.update(failed_condition=failures[0], failed_conditions=failures,
            closest_observation={key: proof[key] for key in (
                'url', 'source_evidence_version', *SOURCE_FIELDS, 'unit_selector', 'scope_count', 'role_count', 'context_count',
                'units_within_scope', 'college_bound', 'title_bound', 'current_link_bound', 'role_supported',
                'witness_visible', 'dom_stable', 'network_idle', 'confirmed', 'reason', 'source_context_issue',
                'source_scope_issue', 'pagination_relationship_issue', 'faculty_group_path', 'source_html_sha256',
                'selected_scope_sha256', 'role_scope_sha256', 'context_scope_sha256',
                'original_path', 'evidence_path') if key in proof})
    return [issue]


async def capture_source_dispositions(context, plan: dict, output_dir) -> tuple[list[dict], list[dict]]:
    """Fresh originals for inactive/pending sources, independent of scout claims."""
    from datetime import datetime, timezone
    from pathlib import Path
    from server.recon_browser_mcp import BrowserScout
    from server.recon_contract import disposition_event_issues, navigation_observations
    issues, evidence = [], []
    items = plan.get('source_dispositions', [])
    if not plan.get('branches'):
        issues.append({'status': 'no_active_roster_sources'})
    if not items:
        return issues, evidence
    root = Path(output_dir) / 'source-dispositions'
    page = await context.new_page()
    await page.add_init_script(Path(__file__).with_name('recon_dom_probe.js').read_text(encoding='utf-8'))
    scout = BrowserScout(page, root, 'discovery', plan)
    try:
        for item in items:
            await scout.call('browser_navigate', {'url': item['url']})
            visit = navigation_observations(scout.events).get(item['url'], {})
            if source_claim_valid(item) and not visit.get('error'):
                try:
                    await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS})
                except Exception as exc:
                    visit['error'] = type(exc).__name__
            valid = not disposition_event_issues({'source_dispositions': [item]}, scout.events)
            proof = source_event_proof(item, scout.events) if source_claim_valid(item) else None
            status = ('inactive' if valid and item['reason'] == 'not_faculty_roster' else
                      'unavailable' if valid and item['reason'] == 'source_unreadable' else
                      'pending' if valid else 'unverified')
            evidence.append({**item, 'status': status, 'method': 'browser_navigate',
                             'observed_at': datetime.now(timezone.utc).isoformat(),
                             'http_status': visit.get('status'), 'error_kind': visit.get('error'),
                             'source_html_sha256': (proof or visit).get('source_html_sha256'),
                             'original_path': (proof or visit).get('original_path'),
                             'browser_proof': proof})
            if status != 'inactive':
                issues.append({'url': item['url'], 'status': item['reason'] if valid else 'source_disposition_unverified'})
        event_path = root / 'events.json'
        event_path.write_bytes(json.dumps(scout.events, ensure_ascii=False, indent=2).encode('utf-8'))
        event_sha = hashlib.sha256(event_path.read_bytes()).hexdigest()
        for item in evidence:
            item.update(navigation_evidence_path=str(event_path), navigation_evidence_sha256=event_sha)
    finally:
        await page.close()
    return issues, evidence
