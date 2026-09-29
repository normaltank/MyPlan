"""Chromium DOM tools run in a separate realm from official page scripts.

Some legacy sites replace global Map with a non-native constructor. Playwright
evaluate/evaluate_all use the main world and their own serializer then breaks.
The public CDP isolated-world API preserves the site's globals and DOM while
keeping tool intrinsics independent. Probes see DOM/location, not site JS globals.
CSS locator count/click remain Playwright's utility-world implementations.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.async_api import Error

_WORLD = 'faculty-dom-tools'
_PROBE = Path(__file__).with_name('recon_dom_probe.js').read_text(encoding='utf-8')


class IsolatedPage:
    def __init__(self, page: Any, context: Any):
        self.raw_page = page
        self._context = context
        self._session = None
        self._seeded_context = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw_page, name)

    async def _world(self) -> tuple[Any, int]:
        if self._session is None:
            self._session = await self._context.new_cdp_session(self.raw_page)
        tree = await self._session.send('Page.getFrameTree')
        created = await self._session.send('Page.createIsolatedWorld', {
            'frameId': tree['frameTree']['frame']['id'], 'worldName': _WORLD})
        context_id = created['executionContextId']
        if self._seeded_context != context_id:
            await self._evaluate_expression(_PROBE, context_id)
            self._seeded_context = context_id
        return self._session, context_id

    async def _evaluate_expression(self, expression: str, context_id: int) -> Any:
        result = await self._session.send('Runtime.evaluate', {
            'expression': expression, 'contextId': context_id,
            'returnByValue': True, 'awaitPromise': True})
        if 'exceptionDetails' in result:
            details = result['exceptionDetails']
            raise Error(details.get('exception', {}).get('description') or details.get('text', 'DOM probe failed'))
        remote = result['result']
        if remote.get('type') == 'undefined':
            return None
        if 'value' not in remote:
            raise Error('DOM probe must return JSON-serializable data')
        return remote['value']

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        _, context_id = await self._world()
        payload = json.dumps(arg, ensure_ascii=True, allow_nan=False)
        # Same public page.evaluate expression/function behavior, with JSON arguments.
        source = expression.strip().rstrip(';')
        invocation = f'(() => {{const fn=({source});return typeof fn === "function" ? fn({payload}) : fn;}})()'
        return await self._evaluate_expression(invocation, context_id)

    def locator(self, selector: str, **kwargs: Any) -> 'IsolatedLocator':
        return IsolatedLocator(self, self.raw_page.locator(selector, **kwargs), selector, bool(kwargs))


class IsolatedLocator:
    def __init__(self, page: IsolatedPage, locator: Any, selector: str, filtered: bool = False):
        self._page, self._locator, self._selector = page, locator, selector
        self._filtered = filtered

    def _validate_probe(self) -> None:
        if self._filtered:
            raise Error('Isolated DOM probes require a plain CSS locator without Playwright filters')

    def __getattr__(self, name: str) -> Any:
        return getattr(self._locator, name)

    async def evaluate_all(self, expression: str, arg: Any = None) -> Any:
        self._validate_probe()
        return await self._page.evaluate('args => {const nodes=Array.from(document.querySelectorAll(args.selector));'
            'const fn=eval("("+args.expression+")");return fn(nodes,args.arg);}',
            {'selector': self._selector, 'expression': expression, 'arg': arg})

    async def evaluate(self, expression: str, arg: Any = None, **kwargs: Any) -> Any:
        self._validate_probe()
        return await self._page.evaluate('args => {const nodes=document.querySelectorAll(args.selector);'
            'if(nodes.length!==1)throw new Error("CSS probe must match exactly one element");'
            'const fn=eval("("+args.expression+")");return fn(nodes[0],args.arg);}',
            {'selector': self._selector, 'expression': expression, 'arg': arg})


class IsolatedBrowserContext:
    def __init__(self, context: Any):
        self.raw_context = context

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw_context, name)

    async def new_page(self) -> IsolatedPage:
        return IsolatedPage(await self.raw_context.new_page(), self.raw_context)


async def new_browser_context(browser: Any, **kwargs: Any) -> IsolatedBrowserContext:
    """Create the production Chromium context with JSON DOM probes isolated."""
    return IsolatedBrowserContext(await browser.new_context(**kwargs))
