"""Structure-only, live image proof shared by reconnaissance and capture."""
async def verify_table_images(page, selector):
    images = page.locator(selector)
    count = await images.count()
    if not count:
        return {"valid": False, "image_count": 0}
    for index in range(count):
        await images.nth(index).scroll_into_view_if_needed()
    await page.wait_for_function(
        "selector => [...document.querySelectorAll(selector)].every(n => n.tagName === 'IMG' && n.complete && n.naturalWidth > 0 && n.naturalHeight > 0)",
        arg=selector, timeout=5000)
    data = await images.evaluate_all("""nodes => nodes.map(n => ({
        url: n.currentSrc || n.src, width: n.naturalWidth, height: n.naturalHeight,
        loaded: n.tagName === 'IMG' && n.complete && n.naturalWidth > 0 && n.naturalHeight > 0
    }))""")
    return {"valid": all(item["loaded"] and item["url"].startswith(("http://", "https://")) for item in data),
            "image_count": len(data), "image_urls": [item["url"] for item in data],
            "dimensions": [[item["width"], item["height"]] for item in data]}
