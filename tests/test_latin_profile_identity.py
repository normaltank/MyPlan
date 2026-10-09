"""Profile identity treats whitespace consistently without accepting another person."""
import pytest

from server.browser_batch_tool import run_plan
from test_browser_batch_tool import FakeContext, roster, profile


@pytest.mark.asyncio
@pytest.mark.parametrize('same_person', [True, False])
async def test_latin_profile_identity_with_spaces_is_compared_symmetrically(tmp_path, same_person):
    name = "John William Smith"
    url = "https://faculty.example.edu/list"
    context = FakeContext({url: [roster([(1, name)])],
        "https://faculty.example.edu/p/1.htm": [profile(name if same_person else 'Mary Brown', "john@example.edu")]})
    result = await run_plan({"school": "测试大学", "college": "测试学院", "source_kind": "university_faculty_roster",
        "branches": [{"url": url, "card_selector": "main ul > li", "single_page": True}]}, context, tmp_path)
    identity_failed = any(item['status'] == 'profile_identity_not_found' for item in result['unresolved'])
    assert identity_failed is not same_person
    if same_person:
        assert result['delivery_ready']
