"""Trusted, versioned page mechanisms; bindings contain structure, never people.

This registry selects implementations, not sources. Source qualification,
browser evidence, plan hygiene and delivery gates remain the callers' duties.
No implementation is imported, compiled or deserialized from a plan/database.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import inspect
import re
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


_REGISTRY: dict[tuple[str, int], TypeDefinition] = {}
_PRODUCTION_COMPATIBLE = {
    'entrance': frozenset({('entrance.direct', 1), ('entrance.navigation', 1)}),
    'pagination': frozenset({('pagination.single_page', 1), ('pagination.next_control', 1)}),
    'detail': frozenset({('detail.profile', 1)}),
}


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
        'output_contract': definition.output_contract,
        'failure_contract': list(definition.failure_contract),
        'applicability_contract': definition.applicability_contract,
    } for definition in sorted(_REGISTRY.values(),
                               key=lambda item: (item.role, item.type_id, item.implementation_version))
        if definition.role == 'roster' or (definition.type_id, definition.implementation_version)
        in _PRODUCTION_COMPATIBLE[definition.role]]


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
            if (ref['type_id'], ref['implementation_version']) not in supported:
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
    result = resolve_type(binding, role).implementation(fallback_callable, *args, **kwargs)
    if inspect.isawaitable(result):
        if role == 'roster':
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError('roster page type must return a synchronous reconciliation')

        async def checked():
            value = await result
            _validate_output(role, value, navigation=branch.get('kind') == 'navigation')
            return value

        return checked()
    _validate_output(role, result, navigation=branch.get('kind') == 'navigation')
    return result


async def invoke_async(role: Role, branch: dict, fallback_callable: Callable, *args, **kwargs):
    result = invoke(role, branch, fallback_callable, *args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _validate_output(role: Role, result: Any, *, navigation: bool = False) -> None:
    """Keep stage shape stable; callers additionally bind/prove source evidence."""
    valid = False
    if role == 'detail':
        # capture_profiles owns HTML/proof/error maps and verifies their entries.
        valid = result is None
    elif role == 'roster':
        valid = (all(isinstance(getattr(result, key, None), list)
                     for key in ('candidates', 'observations', 'unresolved'))
                 and isinstance(getattr(result, 'candidate_sources', None), dict))
    elif role == 'entrance' and navigation and isinstance(result, tuple) and len(result) == 4:
        html, urls, errors, evidence = result
        valid = ((html is None or isinstance(html, str)) and isinstance(urls, list)
                 and all(isinstance(url, str) for url in urls)
                 and isinstance(errors, list) and all(isinstance(item, dict) for item in errors)
                 and isinstance(evidence, dict))
    elif (role == 'pagination' or (role == 'entrance' and not navigation)) and isinstance(result, tuple) and len(result) == 3:
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
