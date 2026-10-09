"""Chromium DOM tools run in a separate realm from official page scripts.

Some legacy sites replace global Map with a non-native constructor. Playwright
evaluate/evaluate_all use the main world and their own serializer then breaks.
The public CDP isolated-world API preserves the site's globals and DOM while
keeping tool intrinsics independent. Probes see DOM/location, not site JS globals.
CSS locator count/click remain Playwright's utility-world implementations.

The same wrappers are where every production request to an official site
starts, so they also keep the per-site pace (``server.site_limits``): page
navigations and clicks wait for the host's turn, the context's direct asset
requests wait out a site freeze, and a rate-limit answer pauses the site
instead of being read as page content.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.async_api import Error

from server.site_limits import check_document, check_response, site_turn

_WORLD = 'faculty-dom-tools'
_PROBE = Path(__file__).with_name('recon_dom_probe.js').read_text(encoding='utf-8')


class IsolatedPage:
    def __init__(self, page: Any, context: Any, *, polite: bool = True):
        self.raw_page = page
        self._context = context
        self._session = None
        self._seeded_context = None
        self.polite = polite

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw_page, name)

    async def _navigate(self, url: str, action: Any) -> Any:
        if self.polite:
            await site_turn(url)
        response = await action()
        if self.polite and response is not None:
            check_response(getattr(response, "url", None) or url, response.status,
                           getattr(response, "headers", None))
        return response

    async def goto(self, url: str, **kwargs: Any) -> Any:
        return await self._navigate(url, lambda: self.raw_page.goto(url, **kwargs))

    async def reload(self, **kwargs: Any) -> Any:
        return await self._navigate(self.raw_page.url, lambda: self.raw_page.reload(**kwargs))

    async def go_back(self, **kwargs: Any) -> Any:
        return await self._navigate(self.raw_page.url, lambda: self.raw_page.go_back(**kwargs))

    async def go_forward(self, **kwargs: Any) -> Any:
        return await self._navigate(self.raw_page.url, lambda: self.raw_page.go_forward(**kwargs))

    async def content(self) -> str:
        html = await self.raw_page.content()
        if self.polite:
            check_document(self.raw_page.url, html)
        return html

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

    async def click(self, **kwargs: Any) -> Any:
        # A click may navigate or send the site a request (pagination, tabs).
        if self._page.polite:
            await site_turn(self._page.raw_page.url)
        return await self._locator.click(**kwargs)

    async def evaluate(self, expression: str, arg: Any = None, **kwargs: Any) -> Any:
        self._validate_probe()
        return await self._page.evaluate('args => {const nodes=document.querySelectorAll(args.selector);'
            'if(nodes.length!==1)throw new Error("CSS probe must match exactly one element");'
            'const fn=eval("("+args.expression+")");return fn(nodes[0],args.arg);}',
            {'selector': self._selector, 'expression': expression, 'arg': arg})


class PoliteRequest:
    """The context's APIRequestContext (media originals, HEAD size checks): held during a site freeze."""

    def __init__(self, request: Any):
        self._request = request

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)

    async def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        # Static originals are not paced like pages, but wait out a site freeze.
        await site_turn(url, paced=False)
        response = await getattr(self._request, method)(url, **kwargs)
        headers = getattr(response, "headers", None) or {}
        check_response(url, response.status, headers)
        if method != "head" and "html" in headers.get("content-type", ""):
            check_document(url, (await response.body()).decode("utf-8", "replace"))
        return response

    async def get(self, url: str, **kwargs: Any) -> Any:
        return await self._send("get", url, **kwargs)

    async def head(self, url: str, **kwargs: Any) -> Any:
        return await self._send("head", url, **kwargs)

    async def fetch(self, url: str, **kwargs: Any) -> Any:
        return await self._send("fetch", url, **kwargs)


class IsolatedBrowserContext:
    def __init__(self, context: Any, *, polite: bool = True):
        self.raw_context = context
        self.polite = polite

    def __getattr__(self, name: str) -> Any:
        return getattr(self.raw_context, name)

    @property
    def request(self) -> Any:
        return PoliteRequest(self.raw_context.request) if self.polite else self.raw_context.request

    async def new_page(self) -> IsolatedPage:
        return IsolatedPage(await self.raw_context.new_page(), self.raw_context, polite=self.polite)


async def new_browser_context(browser: Any, *, polite: bool = True, **kwargs: Any) -> IsolatedBrowserContext:
    """Create the production Chromium context with JSON DOM probes isolated.

    ``polite=False`` is only for offline replays whose requests never leave the
    machine.
    """
    return IsolatedBrowserContext(await browser.new_context(**kwargs), polite=polite)
