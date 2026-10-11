"""Trusted, versioned page mechanisms; bindings contain structure, never people.

This registry selects implementations, not sources. Source qualification,
browser evidence, plan hygiene and delivery gates remain the callers' duties.
No implementation is imported, compiled or deserialized from a plan/database.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import re
from types import CodeType
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal['entrance', 'roster', 'pagination', 'detail']
ROLES = ('entrance', 'roster', 'pagination', 'detail')
_ID = re.compile(r'^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$')
_SELECTORS = frozenset({'child_link_selector', 'card_selector', 'container_selector',
    'record_start_selector', 'empty_state_selector', 'roster_container_selector',
    'next_selector', 'initial_click_selector', 'profile_content_selector',
    'profile_content_start_selector', 'profile_ready_selector'})
_COUNTS = frozenset({'max_children', 'max_pages'})
_FLAGS = frozenset({'single_page'})
ROLE_PARAMETERS = {
    'entrance': frozenset({'child_link_selector', 'max_children'}),
    'roster': frozenset({'card_selector', 'container_selector', 'record_start_selector',
                         'empty_state_selector', 'roster_container_selector'}),
    'pagination': frozenset({'single_page', 'next_selector', 'max_pages', 'initial_click_selector'}),
    'detail': frozenset({'profile_content_selector', 'profile_content_start_selector',
                         'profile_ready_selector'}),
}


class StructureParams(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class EntranceParams(StructureParams):
    child_link_selector: str | None = None
    max_children: int | None = None


class RosterParams(StructureParams):
    card_selector: str | None = None
    container_selector: str | None = None
    record_start_selector: str | None = None
    empty_state_selector: str | None = None
    roster_container_selector: str | None = None


class PaginationParams(StructureParams):
    single_page: bool | None = None
    next_selector: str | None = None
    max_pages: int | None = None
    initial_click_selector: str | None = None


class DetailParams(StructureParams):
    profile_content_selector: str | None = None
    profile_content_start_selector: str | None = None
    profile_ready_selector: str | None = None


class TypeBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type_id: str = Field(pattern=_ID.pattern)
    implementation_version: int = Field(ge=1)
    params: dict[str, str | int | bool] = Field(default_factory=dict)


class PageTypeBindings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    entrance: TypeBinding | None = None
    roster: TypeBinding | None = None
    pagination: TypeBinding | None = None
    detail: TypeBinding | None = None

    @model_validator(mode='after')
    def registered_bindings(self):
        for role in ROLES:
            binding = getattr(self, role)
            if binding is not None:
                resolve_type(binding, role)
        return self


@dataclass(frozen=True)
class EntranceCapture:
    html: str | None
    source_urls: list[str]
    errors: list[dict]
    evidence: dict


@dataclass(frozen=True)
class ProfileCapture:
    html: str | None
    proof: dict | None
    errors: list[dict]
    meta: dict


@dataclass(frozen=True)
class StageEvidenceContext:
    role: Role
    binding: TypeBinding
    branch: dict
    requested_url: str
    final_url: str
    source_html_sha256: str
    source_html: str | None = None


@dataclass(frozen=True)
class TypeDefinition:
    type_id: str
    implementation_version: int
    role: Role
    params_model: type[BaseModel]
    applicability: Callable[[dict[str, Any]], bool]
    implementation: Callable[..., Any]
    input_contract: str
    output_contract: str
    failure_contract: tuple[str, ...]
    applicability_contract: str = 'See input_contract; applicability is checked against the branch at runtime.'
    stage_probe: Callable[..., Any] | None = None
    evidence_validator: Callable[..., bool] | None = None
    evidence_contract: str = ''


_REGISTRY: dict[tuple[str, int], TypeDefinition] = {}
_PRODUCTION_COMPATIBLE = {
    'entrance': frozenset({('entrance.direct', 1), ('entrance.navigation', 1)}),
    'pagination': frozenset({('pagination.single_page', 1), ('pagination.next_control', 1)}),
    'detail': frozenset({('detail.profile', 1)}),
}
_LEGACY_KEYS = frozenset({('entrance.direct', 1), ('entrance.navigation', 1),
    ('roster.cards', 1), ('roster.segmented', 1), ('roster.image_table', 1),
    ('pagination.single_page', 1), ('pagination.next_control', 1), ('detail.profile', 1)})


def _has_stage_evidence(definition: TypeDefinition) -> bool:
    return (callable(definition.stage_probe) and callable(definition.evidence_validator)
            and isinstance(definition.evidence_contract, str) and bool(definition.evidence_contract.strip()))


def register_type(definition: TypeDefinition) -> None:
    """Register reviewed Python code once; a plan cannot register an implementation."""
    if (not isinstance(definition, TypeDefinition) or not _ID.fullmatch(definition.type_id)
            or type(definition.implementation_version) is not int
            or definition.implementation_version < 1 or definition.role not in ROLES):
        raise ValueError('invalid page type definition')
    model = definition.params_model
    if (not isinstance(model, type) or not issubclass(model, BaseModel)
            or model.model_config.get('extra') != 'forbid'
            or model.model_config.get('strict') is not True
            or set(model.model_fields) - ROLE_PARAMETERS[definition.role]
            or any(field.alias or field.validation_alias or field.serialization_alias
                   for field in model.model_fields.values())):
        raise ValueError('page type parameters must use strict structure-only fields')
    if (not callable(definition.applicability) or not callable(definition.implementation)
            or not definition.input_contract or not definition.output_contract
            or not definition.failure_contract
            or any(not isinstance(code, str) or not code for code in definition.failure_contract)):
        raise ValueError('page type requires implementation and input/output/failure contracts')
    if any((definition.stage_probe, definition.evidence_validator, definition.evidence_contract)) and not _has_stage_evidence(definition):
        raise ValueError('page type stage evidence requires probe, validator and contract')
    if inspect.iscoroutinefunction(definition.evidence_validator):
        raise ValueError('page type evidence validator must be synchronous')
    key = (definition.type_id, definition.implementation_version)
    if key in _REGISTRY:
        raise ValueError('page type version is already registered')
    _REGISTRY[key] = definition


def catalog() -> list[dict[str, Any]]:
    """Publish selectable contracts only, without executable/importable objects.

    Future entrance/pagination/detail mechanisms require a version-bound scout
    evidence protocol before appearing here. Roster selection retains its own
    typed browser-proof gate in the recon caller.
    """
    return [{
        'role': definition.role,
        'type_id': definition.type_id,
        'implementation_version': definition.implementation_version,
        'params_schema': definition.params_model.model_json_schema(),
        'input_contract': definition.input_contract,
        'output_contract': definition.output_contract + (
            '; stage evidence: ' + definition.evidence_contract if definition.evidence_contract else ''),
        'failure_contract': list(definition.failure_contract),
        'applicability_contract': definition.applicability_contract,
    } for definition in sorted(_REGISTRY.values(),
                               key=lambda item: (item.role, item.type_id, item.implementation_version))
        if definition.role == 'roster' or (definition.type_id, definition.implementation_version)
        in _PRODUCTION_COMPATIBLE[definition.role] or _has_stage_evidence(definition)]


def _checked_parameters(definition: TypeDefinition, params: dict) -> dict:
    if set(params) - ROLE_PARAMETERS[definition.role]:
        raise ValueError('page type parameter is not a structural field for this role')
    for key, value in params.items():
        if key in _SELECTORS:
            if (not isinstance(value, str) or not value.strip()
                    or re.search(r'[\u4e00-\u9fff]', value)):
                raise ValueError('page type selector must be nonempty structural CSS')
        elif key in _COUNTS:
            if type(value) is not int or value < 1:
                raise ValueError('page type count must be a positive integer')
        elif key in _FLAGS and type(value) is not bool:
            raise ValueError('page type flag must be boolean')
    validated = definition.params_model.model_validate(params, strict=True)
    return validated.model_dump(exclude_none=True)


def resolve_type(binding: TypeBinding | dict, role: Role) -> TypeDefinition:
    if role not in ROLES:
        raise ValueError('unknown page type role')
    ref = binding if isinstance(binding, TypeBinding) else TypeBinding.model_validate(binding)
    definition = _REGISTRY.get((ref.type_id, ref.implementation_version))
    if definition is None:
        raise ValueError('unknown page type or implementation version')
    if definition.role != role:
        raise ValueError('page type role mismatch')
    _checked_parameters(definition, ref.params)
    return definition


def _legacy_ids(branch: dict) -> dict[str, str]:
    kind = branch.get('kind') or 'roster'
    if kind not in {'roster', 'navigation', 'segmented', 'image_table'}:
        raise ValueError('unknown legacy page kind')
    return {'entrance': 'entrance.navigation' if kind == 'navigation' else 'entrance.direct',
            'roster': {'segmented': 'roster.segmented', 'image_table': 'roster.image_table'}.get(
                kind, 'roster.cards'),
            'pagination': 'pagination.next_control' if branch.get('next_selector') else 'pagination.single_page',
            'detail': 'detail.profile'}


def normalize_branch(branch: dict) -> dict:
    """Materialize bindings/structure without mutating or qualifying their source.

    Partial component inputs are supported; the outer Plan/batch/evidence gates
    still validate required URLs, targets, source claims and selector proof.
    Explicit parameters may fill missing structure, never contradict its proof.
    """
    if not isinstance(branch, dict):
        raise ValueError('page branch must be an object')
    result = deepcopy(branch)
    raw = result.get('page_types', {})
    if raw is None:
        raw = {}
    refs = PageTypeBindings.model_validate(raw).model_dump(exclude_none=True)
    for role, ref in refs.items():
        definition = resolve_type(ref, role)
        for key, value in _checked_parameters(definition, ref['params']).items():
            if key in result and result[key] != value:
                raise ValueError('page type parameter conflicts with branch structure')
            result[key] = value
    defaults = _legacy_ids(result)
    for role in ROLES:
        refs.setdefault(role, {'type_id': defaults[role], 'implementation_version': 1, 'params': {}})
        definition = resolve_type(refs[role], role)
        effective = {key: result[key] for key in definition.params_model.model_fields
                     if key in result and result[key] is not None}
        refs[role]['params'] = _checked_parameters(definition, effective)
        if definition.applicability(result) is not True:
            raise ValueError('page type is not applicable to this branch')
    result['page_types'] = refs
    if 'child_branch' in result and result['child_branch'] is not None:
        result['child_branch'] = normalize_branch(result['child_branch'])
    if 'child_branches' in result and result['child_branches'] is not None:
        if not isinstance(result['child_branches'], list):
            raise ValueError('child_branches must be a list')
        if result.get('child_branch') is not None:
            raise ValueError('child_branch and child_branches are mutually exclusive')
        result['child_branches'] = [normalize_branch(child) for child in result['child_branches']]
    return result


def binding_for(branch: dict, role: Role) -> TypeBinding:
    if role not in ROLES:
        raise ValueError('unknown page type role')
    return TypeBinding.model_validate(normalize_branch(branch)['page_types'][role])


def requires_stage_evidence(role: Role, branch: dict) -> bool:
    binding = binding_for(branch, role)
    if (binding.type_id, binding.implementation_version) in _LEGACY_KEYS:
        return False
    definition = resolve_type(binding, role)
    if role == 'roster':
        return False  # Roster uses its existing selected-parser proof contract.
    if not _has_stage_evidence(definition):
        raise ValueError(f'unsupported {role} page type evidence contract')
    return True


def validate_production_bindings(branch: dict) -> None:
    """Fail closed for role mechanisms whose browser evidence is not version-bound.

    Register/normalize/invoke remain low-level extension interfaces. Production
    recon and batch callers additionally call this guard; registration alone
    cannot approve a future entrance, pagination or detail mechanism.
    """
    canonical = normalize_branch(branch)

    def validate(node: dict) -> None:
        for role, supported in _PRODUCTION_COMPATIBLE.items():
            ref = node['page_types'][role]
            if ((ref['type_id'], ref['implementation_version']) not in supported
                    and not _has_stage_evidence(resolve_type(ref, role))):
                raise ValueError(f'unsupported {role} page type evidence contract')
        expected_roster = {'segmented': 'roster.segmented',
                           'image_table': 'roster.image_table'}.get(node.get('kind'))
        if expected_roster is None and ('empty_state_selector' in node or 'roster_container_selector' in node):
            expected_roster = 'roster.cards'
        if expected_roster is not None:
            roster = node['page_types']['roster']
            if (roster['type_id'], roster['implementation_version']) != (expected_roster, 1):
                raise ValueError('unsupported roster page type evidence contract')
        if node.get('child_branch') is not None:
            validate(node['child_branch'])
        for child in node.get('child_branches') or []:
            validate(child)

    validate(canonical)


def invoke(role: Role, branch: dict, fallback_callable: Callable, *args, **kwargs):
    """Dispatch reviewed implementation using the existing operation signature.

    Implementations receive (fallback_callable, *args, **kwargs). The fallback
    encloses existing browser/proof/checkpoint work; custom mechanisms retain
    responsibility for its evidence contracts. Async results are left intact.
    """
    if not callable(fallback_callable):
        raise ValueError('page type operation must be callable')
    binding = binding_for(branch, role)
    definition = resolve_type(binding, role)
    result = definition.implementation(fallback_callable, *args, **kwargs)
    if inspect.isawaitable(result):
        if role == 'roster':
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError('roster page type must return a synchronous reconciliation')

        async def checked():
            value = await result
            _validate_output(role, value, navigation=branch.get('kind') == 'navigation',
                             legacy=(binding.type_id, binding.implementation_version) in _LEGACY_KEYS)
            return value

        return checked()
    _validate_output(role, result, navigation=branch.get('kind') == 'navigation',
                     legacy=(binding.type_id, binding.implementation_version) in _LEGACY_KEYS)
    return result


async def invoke_async(role: Role, branch: dict, fallback_callable: Callable, *args, **kwargs):
    result = invoke(role, branch, fallback_callable, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _validate_output(role: Role, result: Any, *, navigation: bool = False, legacy: bool = False) -> None:
    """Keep stage shape stable; callers additionally bind/prove source evidence."""
    valid = False
    if role == 'detail':
        valid = (isinstance(result, ProfileCapture)
                 and (result.html is None or isinstance(result.html, str))
                 and (result.proof is None or isinstance(result.proof, dict))
                 and isinstance(result.errors, list) and all(isinstance(item, dict) for item in result.errors)
                 and isinstance(result.meta, dict)) or (legacy and result is None)
    elif role == 'roster':
        valid = (all(isinstance(getattr(result, key, None), list)
                     for key in ('candidates', 'observations', 'unresolved'))
                 and isinstance(getattr(result, 'candidate_sources', None), dict))
    elif role == 'entrance' and isinstance(result, EntranceCapture):
        valid = ((result.html is None or isinstance(result.html, str))
                 and isinstance(result.source_urls, list) and all(isinstance(url, str) for url in result.source_urls)
                 and isinstance(result.errors, list) and all(isinstance(item, dict) for item in result.errors)
                 and isinstance(result.evidence, dict))
    elif role == 'entrance' and legacy and navigation and isinstance(result, tuple) and len(result) == 4:
        html, urls, errors, evidence = result
        valid = ((html is None or isinstance(html, str)) and isinstance(urls, list)
                 and all(isinstance(url, str) for url in urls)
                 and isinstance(errors, list) and all(isinstance(item, dict) for item in errors)
                 and isinstance(evidence, dict))
    elif (role == 'pagination' or (role == 'entrance' and legacy and not navigation)) and isinstance(result, tuple) and len(result) == 3:
        pages, errors, evidence = result
        valid = (isinstance(pages, list)
                 and all(isinstance(page, tuple) and len(page) == 2
                         and all(isinstance(value, str) for value in page) for page in pages)
                 and isinstance(errors, list) and all(isinstance(item, dict) for item in errors)
                 and isinstance(evidence, dict))
    if not valid:
        raise ValueError('page type implementation violated stage output contract')


def _legacy_adapter(operation, *args, **kwargs):
    return operation(*args, **kwargs)


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROOF_KEYS = frozenset({'role', 'type_id', 'implementation_version', 'params',
    'requested_url', 'final_url', 'source_html_sha256', 'implementation_digest', 'payload'})


def _definition_sources(definition: TypeDefinition) -> dict[str, Path]:
    paths = {}
    for item in (definition.implementation, definition.stage_probe,
                 definition.evidence_validator, definition.applicability, definition.params_model):
        if item is None:
            continue
        filename = inspect.getsourcefile(item)
        if not filename:
            raise ValueError('registered page type code requires an auditable source file')
        path = Path(filename).resolve()
        try:
            key = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            # Absolute keys prevent unrelated external modules colliding by basename.
            key = str(path)
        paths[key] = path
    return paths


def _source_manifest(definitions, root: Path | None = None) -> dict[str, str]:
    paths = {}
    for definition in definitions:
        paths.update(_definition_sources(definition))
    manifest = {}
    for key, original in sorted(paths.items()):
        path = original
        if root is not None:
            if Path(key).is_absolute():
                raise ValueError('external registered source cannot use a repository override')
            path = Path(root).resolve().parent / key
        # Missing overrides fail closed; never read the original file instead.
        manifest[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def registered_implementation_manifest(root: Path | None = None) -> dict[str, str]:
    """Actual registered source files, keyed by repo path (external paths absolute).

    root, when supplied, is a replacement server directory. Every source file,
    including tests defining temporary registrations, must exist in that tree.
    """
    return _source_manifest(_REGISTRY.values(), root)


def _constant_fingerprint(value):
    """Deterministic code data, independent of marshal's reference/intern tables."""
    if isinstance(value, CodeType):
        return ['code', _code_fingerprint(value)]
    if value is None or type(value) in {bool, int, str}:
        return [type(value).__name__, value]
    if type(value) is bytes:
        return ['bytes', value.hex()]
    if type(value) is float:
        return ['float', value.hex()]
    if type(value) is complex:
        return ['complex', value.real.hex(), value.imag.hex()]
    if type(value) is tuple:
        return ['tuple', [_constant_fingerprint(item) for item in value]]
    if type(value) is frozenset:
        items = [_constant_fingerprint(item) for item in value]
        return ['frozenset', sorted(items, key=lambda item: json.dumps(item, sort_keys=True))]
    if value is Ellipsis:
        return ['ellipsis']
    raise ValueError('unsupported registered code constant')


def _code_fingerprint(code: CodeType) -> dict:
    return {'bytecode': code.co_code.hex(),
            'constants': [_constant_fingerprint(item) for item in code.co_consts],
            'names': code.co_names, 'varnames': code.co_varnames,
            'freevars': code.co_freevars, 'cellvars': code.co_cellvars,
            'argcount': code.co_argcount, 'posonlyargcount': code.co_posonlyargcount,
            'kwonlyargcount': code.co_kwonlyargcount, 'flags': code.co_flags,
            'nlocals': code.co_nlocals, 'stacksize': code.co_stacksize,
            'name': code.co_name, 'qualname': code.co_qualname,
            'firstlineno': code.co_firstlineno,
            'linetable': code.co_linetable.hex(), 'exceptiontable': code.co_exceptiontable.hex()}


def _callable_digest(function) -> str | None:
    if function is None:
        return None
    code = getattr(function, '__code__', None)
    if code is None:
        return None
    content = json.dumps(_code_fingerprint(code), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(content.encode()).hexdigest()


def _definitions_digest(definitions, root=None) -> str:
    definitions = sorted(definitions, key=lambda d: (d.role, d.type_id, d.implementation_version))
    content = [{'role': d.role, 'type_id': d.type_id, 'version': d.implementation_version,
                'params': d.params_model.model_json_schema(), 'input': d.input_contract,
                'output': d.output_contract, 'failure': d.failure_contract,
                'applicability': d.applicability_contract, 'evidence': d.evidence_contract,
                'callables': [_callable_digest(item) for item in
                    (d.implementation, d.stage_probe, d.evidence_validator, d.applicability)]}
               for d in definitions]
    serialized = json.dumps({'definitions': content, 'sources': _source_manifest(definitions, root)},
                            ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode()).hexdigest()


def implementation_digest(binding: TypeBinding | dict, role: Role, *, root: Path | None = None) -> str:
    return _definitions_digest([resolve_type(binding, role)], root)


def selected_implementation_digest(plan: dict, *, root: Path | None = None,
                                   only_nonlegacy: bool = False) -> str:
    """Hash only effective selected definitions, including heterogeneous children."""
    selected = {}

    def visit(branch):
        node = normalize_branch(branch)
        for role in ROLES:
            ref = node['page_types'][role]
            key = (ref['type_id'], ref['implementation_version'])
            if not only_nonlegacy or key not in _LEGACY_KEYS:
                selected[key] = resolve_type(ref, role)
        if node.get('child_branch') is not None:
            visit(node['child_branch'])
        for child in node.get('child_branches') or []:
            visit(child)

    for branch in plan.get('branches', []):
        visit(branch)
    return _definitions_digest(selected.values(), root) if selected else ''


def _stage_context(role, branch, result, *, requested_url, final_url=None, source_html=None):
    canonical = normalize_branch(branch)
    binding = TypeBinding.model_validate(canonical['page_types'][role])
    _validate_output(role, result)
    if role == 'entrance':
        actual_html = result.html
        actual_url = result.evidence.get('final_url') or result.evidence.get('url') or requested_url
        errors = result.errors
    elif role == 'detail':
        actual_html = result.html
        actual_url = result.meta.get('final_url') or result.meta.get('url') or requested_url
        errors = result.errors
    elif role == 'pagination':
        pages, errors, evidence = result
        if not pages:
            raise ValueError('stage evidence requires captured source HTML')
        actual_url, actual_html = pages[0]
        actual_url = evidence.get('source_final_url') or actual_url
    else:
        raise ValueError('roster evidence uses the typed roster contract')
    if errors or not isinstance(actual_html, str) or not actual_html:
        raise ValueError('stage evidence requires successful captured source HTML')
    if (not isinstance(requested_url, str) or not requested_url
            or not isinstance(actual_url, str) or not actual_url
            or (source_html is not None and source_html != actual_html)
            or (final_url is not None and final_url != actual_url)):
        raise ValueError('stage evidence source does not match the actual capture')
    return StageEvidenceContext(role, binding, canonical, requested_url, actual_url,
                                hashlib.sha256(actual_html.encode()).hexdigest(), actual_html)


async def stage_evidence_for_result(role: Role, branch: dict, result, *, requested_url: str,
                                    final_url: str | None = None, source_html: str | None = None) -> dict:
    """Prove a completed selected-handler result without executing it again."""
    context = _stage_context(role, branch, result, requested_url=requested_url,
                             final_url=final_url, source_html=source_html)
    definition = resolve_type(context.binding, role)
    if not _has_stage_evidence(definition):
        raise ValueError(f'unsupported {role} page type evidence contract')
    payload = definition.stage_probe(context, result)
    if inspect.isawaitable(payload):
        payload = await payload
    if not isinstance(payload, dict) or definition.evidence_validator(context, result, payload) is not True:
        raise ValueError('page type stage evidence validation failed')
    # Restrict proof payloads to JSON data; raw captures remain in the local sink.
    payload = json.loads(json.dumps(payload, allow_nan=False))
    return {'role': role, 'type_id': context.binding.type_id,
            'implementation_version': context.binding.implementation_version,
            'params': deepcopy(context.binding.params), 'requested_url': context.requested_url,
            'final_url': context.final_url, 'source_html_sha256': context.source_html_sha256,
            'implementation_digest': implementation_digest(context.binding, role), 'payload': payload}


async def run_stage_probe(role: Role, branch: dict, fallback_callable: Callable, *args,
                          requested_url: str, final_url: str | None = None,
                          source_html: str | None = None, local_capture: dict | None = None, **kwargs) -> dict:
    result = await invoke_async(role, branch, fallback_callable, *args, **kwargs)
    if local_capture is not None:
        local_capture['result'] = result
    return await stage_evidence_for_result(role, branch, result, requested_url=requested_url,
                                           final_url=final_url, source_html=source_html)


def validate_stage_evidence(role: Role, branch: dict, proof: dict, *, requested_url: str,
                            final_url: str, source_html_sha256: str,
                            source_html: str | None = None, result=None) -> bool:
    """Bind trusted probe payload to current capture and exact registered mechanism."""
    try:
        canonical = normalize_branch(branch)
        binding = TypeBinding.model_validate(canonical['page_types'][role])
        definition = resolve_type(binding, role)
        if not _has_stage_evidence(definition) or not isinstance(proof, dict) or set(proof) != _PROOF_KEYS:
            return False
        if not re.fullmatch(r'[0-9a-f]{64}', source_html_sha256):
            return False
        expected = {'role': role, 'type_id': binding.type_id,
                    'implementation_version': binding.implementation_version, 'params': binding.params,
                    'requested_url': requested_url, 'final_url': final_url,
                    'source_html_sha256': source_html_sha256,
                    'implementation_digest': implementation_digest(binding, role)}
        # JSON identity also distinguishes bool from int (Python True == 1).
        if (json.dumps({key: proof.get(key) for key in expected}, sort_keys=True, allow_nan=False)
                != json.dumps(expected, sort_keys=True, allow_nan=False)):
            return False
        if source_html is not None and hashlib.sha256(source_html.encode()).hexdigest() != source_html_sha256:
            return False
        context = StageEvidenceContext(role, binding, canonical, requested_url, final_url,
                                       source_html_sha256, source_html)
        if result is not None:
            actual = _stage_context(role, branch, result, requested_url=requested_url,
                                    final_url=final_url, source_html=source_html)
            if actual.source_html_sha256 != source_html_sha256:
                return False
        return (isinstance(proof.get('payload'), dict)
                and definition.evidence_validator(context, result, proof['payload']) is True)
    except (ValueError, TypeError, KeyError, OSError):
        return False


def _install_legacy_types():
    definitions = (
        ('entrance.direct', 'entrance', EntranceParams, lambda b: b.get('kind') != 'navigation'),
        ('entrance.navigation', 'entrance', EntranceParams, lambda b: b.get('kind') == 'navigation'),
        ('roster.cards', 'roster', RosterParams, lambda b: (b.get('kind') or 'roster') in {'roster', 'navigation'}),
        ('roster.segmented', 'roster', RosterParams, lambda b: b.get('kind') == 'segmented'),
        ('roster.image_table', 'roster', RosterParams, lambda b: b.get('kind') == 'image_table'),
        ('pagination.single_page', 'pagination', PaginationParams, lambda b: not b.get('next_selector')),
        ('pagination.next_control', 'pagination', PaginationParams, lambda b: bool(b.get('next_selector'))),
        ('detail.profile', 'detail', DetailParams, lambda b: True),
    )
    contracts = {
        'entrance': ('branch + browser context', 'captured navigation HTML, child URLs, errors, evidence'),
        'roster': ('captured page + source-qualified branch', 'roster reconciliation with observations and unresolved items'),
        'pagination': ('branch + browser context + checkpoint callbacks', 'captured pages, errors and pagination evidence'),
        'detail': ('person page URL + browser fetch operation', 'HTML and browser evidence; access failures remain unresolved'),
    }
    for type_id, role, params, applicability in definitions:
        inputs, outputs = contracts[role]
        if type_id == 'entrance.direct':
            outputs = 'captured roster pages, errors and capture evidence'
        conditions = {
            'entrance.direct': 'Non-navigation branch; existing direct source capture.',
            'entrance.navigation': 'Navigation branch; discovered child URLs remain bounded and source-checked.',
            'roster.cards': 'Ordinary roster branch; compatibility child rosters use their existing card/name-list mechanisms.',
            'roster.segmented': 'Segmented branch with its verified container and person-start selectors.',
            'roster.image_table': 'Image-table branch with verified single-page image selector.',
            'pagination.single_page': 'No next_selector; browser single-page/termination evidence is still required.',
            'pagination.next_control': 'Nonempty next_selector; actual page change and termination must be proved.',
            'detail.profile': 'Existing personal-page capture; content selectors and browser proof remain required when scoped.',
        }
        register_type(TypeDefinition(type_id, 1, role, params, applicability, _legacy_adapter,
            inputs, outputs, ('page_type_not_applicable', 'page_type_parameter_invalid',
                              'source_or_browser_evidence_unresolved'), conditions[type_id]))


_install_legacy_types()

# Import only reviewed Python modules. Plans carry IDs, never import paths.
from server.page_type_rank_grid import register as _register_rank_grid  # noqa: E402

_register_rank_grid()

from server.page_stage_types import register as _register_page_stages  # noqa: E402

_register_page_stages()
