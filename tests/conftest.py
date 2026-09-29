"""Test-suite isolation.

The browser batch tool falls back to launching a real Chrome over CDP when a
page cannot be fetched through the supplied browser context. Fixture-backed
tests have no live pages, so that fallback used to open visible Chrome windows
and issue real requests to production sites (e.g. the 甘肃 hospital fixture
hitting www.lzsdyyy.cn doctor pages). Tests must stay hermetic and offline.
"""
import os

os.environ.setdefault("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")
