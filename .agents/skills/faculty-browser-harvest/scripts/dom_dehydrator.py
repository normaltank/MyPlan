"""
DOM Dehydrator for Faculty Harvest (Token Optimization Engine)
Pre-cleanses bloated university web pages before passing to LLM context.
Compresses 30KB~100KB raw DOM into a ~500-token structured candidate payload.
Reduces Token consumption by 90%~95%.
"""

import re
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Comment
from urllib.parse import urljoin

TITLE_KEYWORDS = ['教授', '副教授', '讲师', '助理教授', '研究员', '副研究员', '助理研究员', '师资博士后', '名誉教授', '特聘教授', '讲座教授']
JUNK_TAGS = ['script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav']
EMAIL_RE = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)')
PUBLIC_EMAILS = {'office@', 'dean@', 'admin@', 'info@', 'contact@', 'yjs@', 'news@', 'admission@', 'webmaster@', 'szb@', 'jiaoxueke@'}

def extract_clean_full_profile(html: str) -> str:
    """
    Extracts the full clean text of the scholar profile without artificial truncation.
    Strips script, style, header, footer, nav, and preserves complete paragraphs.
    Directly copied into the '简介' column without AI summarization.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()
    for comment in soup.find_all(text=lambda text: isinstance(text, Comment)):
        comment.extract()
    
    # Priority containers for faculty detail pages
    main_el = soup.find(['article', '.content', '.article', '.detail', '.text', '.profile', '#content', '#article', 'main']) or soup.body or soup
    text = main_el.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def clean_html_to_compact_text(html: str, max_chars: Optional[int] = None) -> str:
    """
    Backward-compatible helper. If max_chars is None, returns the full profile text.
    """
    full_text = extract_clean_full_profile(html)
    if max_chars:
        return full_text[:max_chars]
    return full_text

def extract_faculty_candidates(html: str, base_url: str) -> Dict:
    """
    Extracts structured candidate cards (name, title, email, url) from directory list pages.
    """
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()
        
    candidates = []
    seen_urls = set()
    seen_names = set()
    
    # Strategy 1: Find link cards
    for a in soup.find_all('a'):
        href = a.get('href', '').strip()
        text = a.get_text(strip=True)
        
        # Candidate filter
        if not href or href.startswith(('javascript:', '#', 'mailto:')) or len(text) < 2 or len(text) > 25:
            continue
            
        full_url = urljoin(base_url, href)
        if full_url in seen_urls:
            continue
            
        # Check if text looks like a Chinese name (2-4 chars) or name with title
        is_name = bool(re.match(r'^[\u4e00-\u9fa5]{2,4}$', text))
        matched_title = next((t for t in TITLE_KEYWORDS if t in text), "")
        
        if is_name or matched_title:
            # Look nearby for email
            parent_text = a.parent.get_text(separator=' ', strip=True) if a.parent else ""
            emails = EMAIL_RE.findall(parent_text)
            clean_email = next((e for e in emails if not any(p in e.lower() for p in PUBLIC_EMAILS)), "")
            
            candidates.append({
                "name": text.replace(matched_title, "").strip() if matched_title else text,
                "title": matched_title,
                "email": clean_email,
                "url": full_url
            })
            seen_urls.add(full_url)
            seen_names.add(text)
            
    return {
        "candidate_count": len(candidates),
        "candidates": candidates
    }
