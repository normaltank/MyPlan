"""Inspect original biography media with local OCR, without model data extraction.

The caller supplies HTML already bounded to a verified content container. URLs
found here are leads to browser-observed resources, never generated guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup

_OCR_CACHE_VERSION = 1
# A photo's scene text (a sign, a blackboard, a banner) stays short; a written
# biography image on a coloured photo background is rare and, when it is the
# only biography, the image-only profile check still blocks the record.
SCENE_TEXT_CHARS = 600


def page_appearance(data: bytes) -> dict:
    """Whether an image looks like paper (a scanned or photographed page).

    Paper is light and nearly colourless; a portrait photo has skin, hair and
    clothing colours. Measured on a small copy: the share of light pixels and
    the share of clearly coloured pixels. Anything unreadable stays
    document-like, so the conservative review path is kept.
    """
    try:
        from PIL import Image
        with Image.open(BytesIO(data)) as source:
            image = source.convert("RGB")
        image.thumbnail((256, 256))
        pixels = image.size[0] * image.size[1]
        light = sum(value >= 170 for value in image.convert("L").getdata()) / pixels
        hsv = image.convert("HSV")
        coloured = sum(s >= 60 and v >= 60 for s, v in zip(hsv.getchannel("S").getdata(),
                                                             hsv.getchannel("V").getdata())) / pixels
    except Exception:
        return {"document_like": True, "reason": "unmeasurable"}
    return {"document_like": light >= 0.4 and coloured <= 0.10,
            "light_share": round(light, 3), "coloured_share": round(coloured, 3)}

# Every resume image found in the case library is under 3.5 MB; artwork
# originals on art-school pages run to tens of MB. An image the server
# declares larger than this is recorded and not downloaded.
MAX_PROFILE_IMAGE_BYTES = 10_000_000

# A biography page has several lines of text; a portrait has none. Below this
# many unread lines a page-shaped image is a picture, as with read lines.
UNREAD_PAGE_LINES = 3


def unread_text_lines(data: bytes, read_lines: list[dict], ocr_engine) -> int | None:
    """Text lines the OCR detector outlines on an image but recognition did not read.

    The detector finds printed lines even where the page is too blurred or too
    small to transcribe, while a portrait's face, clothing and backdrop give no
    line-shaped regions: one side at least three times the other, for
    horizontal or vertical writing. None when the image cannot be measured.
    """
    def span(box):
        xs, ys = [point[0] for point in box], [point[1] for point in box]
        return min(xs), min(ys), max(xs), max(ys)
    try:
        boxes, _ = ocr_engine(data, use_det=True, use_cls=False, use_rec=False)
        boxes = boxes.tolist() if hasattr(boxes, "tolist") else boxes or []
        read = [span(line["box"]) for line in read_lines]
        count = 0
        for box in boxes:
            left, top, right, bottom = span(box)
            width, height = right - left, bottom - top
            if max(width, height) < 3 * min(width, height):
                continue
            x, y = (left + right) / 2, (top + bottom) / 2
            count += not any(a <= x <= c and b <= y <= d for a, b, c, d in read)
        return count
    except Exception:
        return None


@dataclass(frozen=True)
class MediaReference:
    kind: str
    url: str
    position: int
    linked: bool = False


def _query_names_pdf(raw_url: str) -> bool:
    """An attachment handler names the file type in its query, not its path.

    VSB sites serve ``/virtual_attach_file.vsb?afc=...&e=.pdf``; download
    scripts take ``?file=roster.pdf``.  This is only a lead: the reader still
    requires the fetched bytes to carry the PDF signature.
    """
    try:
        values = parse_qs(urlparse(raw_url).query).values()
    except ValueError:
        return False
    return any(value.strip().lower().endswith(".pdf") for group in values for value in group)


def media_references(html: str, base_url: str) -> list[MediaReference]:
    """Return image and PDF references in DOM order from a scoped page."""
    soup = BeautifulSoup(html, "html.parser")
    result: list[MediaReference] = []
    seen: set[str] = set()
    for element in soup.find_all(("img", "source", "a", "object", "embed", "iframe")):
        if element.name == "source" and element.parent.name != "picture":
            continue
        if element.name == "img" and element.parent.name == "picture" and element.parent.find("source"):
            continue
        if element.name in {"img", "source"}:
            srcset = element.get("srcset") or ""
            raw_url = (element.get("data-src") or element.get("src")
                       or (srcset.split(",", 1)[0].strip().split(" ", 1)[0] if srcset else "")).strip()
            if not raw_url:
                # A template placeholder (<img src="">) shows nothing; resolved,
                # it would name the page itself as the picture.
                continue
            kind = "image"
        else:
            raw_url = element.get("href") or element.get("data") or element.get("src") or ""
            path = urlparse(raw_url).path.lower()
            if element.name == "iframe" and path.rsplit("/", 1)[-1] == "viewer.html":
                viewer_url = urljoin(base_url, raw_url)
                viewer = urlparse(viewer_url)
                if viewer.scheme not in {"http", "https"} or not viewer.netloc:
                    continue
                file_url = parse_qs(viewer.query).get("file", [""])[0]
                try:
                    file_path = urlparse(file_url).path.lower()
                except ValueError:
                    continue
                if not file_url or not (file_path.endswith(".pdf") or _query_names_pdf(file_url)):
                    continue
                # PDF.js resolves relative file parameters against its viewer,
                # not the containing article. Decode query escaping once.
                raw_url = quote(urljoin(viewer_url, file_url), safe=":/?#[]@!$&'()*+,;=%")
                path = urlparse(raw_url).path.lower()
            label = element.get_text(" ", strip=True).lower()
            if not (path.endswith(".pdf") or label.endswith(".pdf")
                    or (path.rsplit("/", 1)[-1] != "viewer.html" and _query_names_pdf(raw_url))
                    or element.get("type", "").lower() == "application/pdf"):
                continue
            kind = "pdf"
        url = urljoin(base_url, raw_url)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
            continue
        seen.add(url)
        result.append(MediaReference(kind=kind, url=url, position=len(result) + 1,
                                     linked=element.name == "a"))
    return result


def _read_ocr(raw_result: object) -> tuple[str, list[dict]]:
    def native(value):
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, (list, tuple)):
            return [native(part) for part in value]
        if hasattr(value, "item"):
            return value.item()
        return value

    raw_lines = raw_result[0] if isinstance(raw_result, tuple) else raw_result
    lines: list[dict] = []
    for item in raw_lines or []:
        if len(item) >= 3:
            lines.append({"box": native(item[0]), "text": str(item[1]),
                          "confidence": float(item[2])})
    return "\n".join(item["text"] for item in lines), lines


def scan_media(data: bytes, kind: str, *, ocr_engine=None) -> dict:
    """Read text layers first; OCR only raster content and preserve page order."""
    if kind not in {"image", "pdf"}:
        raise ValueError("media kind must be image or pdf")
    if not data:
        raise ValueError("empty media")
    if kind == "pdf" and not data[:1024].lstrip().startswith(b"%PDF-"):
        raise ValueError("PDF signature missing")

    def ocr(image_bytes: bytes) -> tuple[str, list[dict]]:
        nonlocal ocr_engine
        if ocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            ocr_engine = RapidOCR()
        return _read_ocr(ocr_engine(image_bytes))

    if kind == "image":
        from PIL import Image
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            image_format = (image.format or "").lower()
            image.verify()
        text, lines = ocr(data)
        return {"kind": kind, "page_count": 1, "pages_with_text_layer": 0,
                "pages_ocrd": 1, "text": text, "pages": [{"page": 1, "text": text,
                "lines": lines, "source": "local_ocr"}], "image_size": [width, height],
                "image_format": image_format}

    import pymupdf
    document = pymupdf.open(stream=data, filetype="pdf")
    if document.page_count > 100:
        raise ValueError("PDF page count exceeds review limit")
    pages = []
    text_layer_count = 0
    ocr_count = 0
    for index, page in enumerate(document, 1):
        text = page.get_text().strip()
        page_area = page.rect.width * page.rect.height
        full_page_raster = any(
            (info["bbox"][2] - info["bbox"][0])
            * (info["bbox"][3] - info["bbox"][1]) >= page_area * 0.8
            for info in page.get_image_info()
        ) if page_area else False
        # A page number or watermark is not a searchable biography text layer.
        if text and not (full_page_raster and len(text) <= 30):
            text_layer_count += 1
            pages.append({"page": index, "text": text, "lines": [], "source": "pdf_text_layer"})
            continue
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        text, lines = ocr(pixmap.tobytes("png"))
        ocr_count += 1
        pages.append({"page": index, "text": text, "lines": lines, "source": "local_ocr"})
    return {"kind": kind, "page_count": len(pages),
            "pages_with_text_layer": text_layer_count, "pages_ocrd": ocr_count,
            "text": "\n\n".join(page["text"] for page in pages), "pages": pages}


async def fetch_browser_asset(browser_context, url: str) -> bytes:
    """Read a DOM-observed resource with the browser session and bounded retry."""
    for attempt in range(3):
        try:
            response = await browser_context.request.get(url, timeout=45000)
            if not response.ok:
                status = response.status
                if status != 429 and status < 500:
                    raise ValueError(f"media HTTP {status}")
                raise RuntimeError(f"media HTTP {status}")
            return await response.body()
        except ValueError:
            raise
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.3 * (attempt + 1))
    raise AssertionError("unreachable")


async def browser_asset_size(browser_context, url: str) -> int | None:
    """The size a server declares for a resource, or None when it declares none.

    A HEAD request that fails or times out raises, so the caller can stop
    asking a host that does not answer.
    """
    response = await browser_context.request.head(url, timeout=10000)
    try:
        return int(response.headers.get("content-length", "")) if response.ok else None
    except ValueError:
        return None


async def audit_profile_media(
    profile_html: dict[str, str], owners: dict[str, set[str]], output_dir: str | Path,
    *, fetch_asset: Callable[[str], Awaitable[bytes]], ocr_engine=None,
    scoped_urls: set[str] | None = None, known_names: set[str] | None = None,
    review_decisions_path: str | Path | None = None,
    media_kind_decisions_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    unpublished_media: dict[str, set[str]] | None = None,
    asset_size: Callable[[str], Awaitable[int | None]] | None = None,
) -> dict:
    """Retain original assets, transcribe locally, and resolve only unique owners.

    The result contains data only in local files or deterministic worker memory;
    it must never be included in the model's reconnaissance plan.
    ``unpublished_media`` names, per profile, images the browser proof found
    gone on two opens with no room on the page; nothing was shown there to read.
    ``asset_size`` reads a size the server declares; an image declared over
    ``MAX_PROFILE_IMAGE_BYTES`` is listed in ``oversized_media`` unread. A
    profile whose only content is such an image still fails the image-only check.
    """
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    biographies: dict[str, str] = {}
    assigned_parts: dict[tuple[str, str], list[str]] = {}
    unresolved: list[dict] = []
    evidence: list[dict] = []
    skipped: list[dict] = []
    oversized: list[dict] = []
    shared_ocr_engine = ocr_engine

    def read_cache(path: Path, digest: str, kind: str) -> dict | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(value, dict):
            return None
        if (value.get("ocr_cache_version") == _OCR_CACHE_VERSION
                and value.get("source_sha256") == digest
                and value.get("kind") == kind
                and isinstance(value.get("pages"), list)):
            return value
        return None
    for profile_url, html in profile_html.items():
        if not html or profile_url not in owners:
            continue
        soup = BeautifulSoup(html, "html.parser")
        content = None
        if profile_url not in (scoped_urls or set()):
            content = next((soup.select_one(selector) for selector in
                            ("article", "#vsb_content", ".v_news_content",
                             ".wp_articlecontent", "main", ".article-content")
                            if soup.select_one(selector) is not None), None)
        scoped_html = str(content) if content is not None else html
        without_links = BeautifulSoup(scoped_html, "html.parser")
        for anchor in without_links.select("a"):
            anchor.decompose()
        has_substantial_dom_body = len(without_links.get_text(" ", strip=True)) >= 300
        for ref in media_references(scoped_html, profile_url):
            if ref.kind == "pdf" and ref.linked and has_substantial_dom_body:
                continue
            if ref.kind == "image" and ref.url in (unpublished_media or {}).get(profile_url, ()):
                skipped.append({"profile_url": profile_url, "media_url": ref.url,
                                "status": "image_gone_without_display_area"})
                continue
            if ref.kind == "image" and asset_size is not None:
                declared = await asset_size(ref.url)
                if declared is not None and declared > MAX_PROFILE_IMAGE_BYTES:
                    oversized.append({"profile_url": profile_url, "media_url": ref.url,
                                      "status": "image_over_size_cap", "declared_bytes": declared})
                    continue
            source = {"profile_url": profile_url, "media_url": ref.url,
                      "kind": ref.kind, "position": ref.position}
            try:
                original = await fetch_asset(ref.url)
                if len(original) > 50_000_000:
                    raise ValueError("media exceeds 50 MB review limit")
                digest = hashlib.sha256(original).hexdigest()
                stem = hashlib.sha256((profile_url + "|" + ref.url).encode("utf-8")).hexdigest()[:20]
                ocr_path = out / f"{stem}_ocr.json"
                cached_original = next((path for path in out.glob(f"{stem}.*")
                                        if hashlib.sha256(path.read_bytes()).hexdigest() == digest), None)
                local_scanned = (read_cache(ocr_path, digest, ref.kind)
                                 if cached_original is not None else None)
                scanned = local_scanned
                shared_path = (Path(cache_dir) / digest[:2]
                               / f"{digest}_{ref.kind}_v{_OCR_CACHE_VERSION}.json"
                               if cache_dir is not None else None)
                if scanned is None and shared_path is not None:
                    scanned = read_cache(shared_path, digest, ref.kind)
                reused = scanned is not None
                if scanned is None:
                    if shared_ocr_engine is None:
                        from rapidocr_onnxruntime import RapidOCR
                        shared_ocr_engine = RapidOCR()
                    scanned = await asyncio.to_thread(scan_media, original, ref.kind,
                                                      ocr_engine=shared_ocr_engine)
                    scanned["ocr_cache_version"] = _OCR_CACHE_VERSION
                    scanned["source_sha256"] = digest
                    if shared_path is not None:
                        shared_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = shared_path.with_name(
                            f".{shared_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                        try:
                            temporary.write_text(json.dumps(scanned, ensure_ascii=False), encoding="utf-8")
                            temporary.replace(shared_path)
                        finally:
                            temporary.unlink(missing_ok=True)
            except Exception as exc:
                unresolved.append({**source, "status": "image_bio_asset_failed",
                                   "error": f"{type(exc).__name__}: {exc}"})
                continue
            image_format = scanned.get("image_format") or Path(urlparse(ref.url).path).suffix.lstrip(".").lower()
            extension = "pdf" if ref.kind == "pdf" else {"jpeg": "jpg", "tiff": "tif"}.get(
                image_format, image_format if image_format in {"jpg", "png", "webp", "gif", "bmp", "tif"}
                else "img")
            original_path = cached_original or out / f"{stem}.{extension}"
            if cached_original is None:
                original_path.write_bytes(original)
            if local_scanned is None:
                ocr_path.write_text(json.dumps(scanned, ensure_ascii=False, indent=2), encoding="utf-8")
            record = {**source, "sha256": digest, "original_path": str(original_path),
                      "ocr_path": str(ocr_path), "page_count": scanned["page_count"],
                      "pages_ocrd": scanned["pages_ocrd"], "ocr_reused": reused}
            evidence.append(record)
            from server.media_kind_review import irrelevant_confirmed
            if ref.kind == "image" and irrelevant_confirmed(
                    Path(media_kind_decisions_path) if media_kind_decisions_path else None,
                    profile_url, ref.url, digest):
                # A person judged this exact original to carry nothing about the teacher.
                record["media_kind_review"] = "irrelevant"
                continue
            from server.image_bio_review import apply_review_decisions
            reviewed = set()
            if review_decisions_path is not None:
                scanned, reviewed = apply_review_decisions(
                    scanned, ref.url, digest, review_decisions_path)
            record["human_reviewed_lines"] = len(reviewed)
            if ref.kind == "pdf" and any(not page["text"].strip() for page in scanned["pages"]):
                unresolved.append({**source, "status": "image_bio_ocr_incomplete",
                                   "ocr_path": str(ocr_path)})
                continue
            text = scanned["text"].strip()
            lines = [line for page in scanned["pages"] for line in page["lines"]]
            compact = re.sub(r"\s+", "", text)
            if ref.kind == "image" and (len(lines) < 3 or len(compact) < 20):
                size = scanned.get("image_size", [0, 0])
                ratio = size[0] / size[1] if size[1] else 0
                if min(size) >= 700 and 0.58 <= ratio <= 0.78:
                    # A page-shaped image with almost no text may be a scanned
                    # resume OCR could not read, or simply a 2:3 portrait photo.
                    # Only the first still shows text lines to the detector.
                    if shared_ocr_engine is None:
                        from rapidocr_onnxruntime import RapidOCR
                        shared_ocr_engine = RapidOCR()
                    unread = await asyncio.to_thread(unread_text_lines, original, lines,
                                                     shared_ocr_engine)
                    record["unread_text_lines"] = unread
                    if unread is None or unread >= UNREAD_PAGE_LINES:
                        from server.media_kind_review import photo_confirmed
                        if photo_confirmed(Path(media_kind_decisions_path) if media_kind_decisions_path else None,
                                           profile_url, ref.url, digest):
                            record["media_kind_review"] = "photo"
                        else:
                            unresolved.append({**source, "status": "image_bio_ocr_incomplete",
                                               "ocr_path": str(ocr_path), "unread_text_lines": unread})
                continue
            if not text:
                continue
            headings = ([line["text"] for line in lines[:5]] if lines
                        else scanned["pages"][0]["text"].splitlines()[:5])
            matched = []
            for heading in headings:
                compressed_heading = re.sub(r"\s+", "", heading)
                matched = [name for name in owners[profile_url]
                           if re.sub(r"\s+", "", name) in compressed_heading]
                if matched:
                    break
            other_page_names = set()
            if ref.kind == "pdf" and known_names:
                for page in scanned["pages"][1:]:
                    page_lines = ([line["text"] for line in page["lines"][:5]]
                                  if page["lines"] else page["text"].splitlines()[:5])
                    for line in page_lines:
                        compressed_line = re.sub(r"\s+", "", line)
                        found = {name for name in known_names
                                 if re.sub(r"\s+", "", name) in compressed_line}
                        if found:
                            other_page_names.update(found)
                            break
            if ref.kind == "image" and not matched and len(compact) < SCENE_TEXT_CHARS:
                # A photo of the teacher in front of a sign or blackboard has a
                # few lines of scene text but is not a written biography.
                appearance = page_appearance(original)
                if not appearance["document_like"]:
                    record["page_appearance"] = appearance
                    record["classified_as"] = "photo_with_scene_text"
                    continue
            if (len(matched) != 1 or (ref.kind == "pdf" and len(owners[profile_url]) != 1)
                    or (matched and other_page_names - {matched[0]})):
                unresolved.append({**source, "status": "image_bio_owner_unresolved",
                                   "matched_owner_count": len(matched), "ocr_path": str(ocr_path)})
                continue
            if any(line["confidence"] < 0.8 and (page["page"], index) not in reviewed
                   for page in scanned["pages"] for index, line in enumerate(page["lines"])):
                unresolved.append({**source, "status": "image_bio_low_confidence",
                                   "ocr_path": str(ocr_path)})
                continue
            record["assigned_name"] = matched[0]
            assigned_parts.setdefault((profile_url, matched[0]), []).append(text)
        if len(owners[profile_url]) == 1:
            name = next(iter(owners[profile_url]))
            parts = assigned_parts.get((profile_url, name), [])
            if parts:
                biographies[profile_url] = "\n\n".join(parts)
    assigned = {key: "\n\n".join(parts) for key, parts in assigned_parts.items()}
    return {"biographies": biographies, "assigned_biographies": assigned,
            "unresolved": unresolved, "evidence": evidence, "unpublished_media": skipped,
            "oversized_media": oversized}
