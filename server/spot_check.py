"""Sample a finished college for a person to check against the source pages.

Automatic checks can only compare the harvest with other pages; a person is the
only independent judge of whether a record is right. This writes, next to the
task's audit (a local, ignored directory):

* ``spot-check.html``: a random sample of records, each with its fields and the
  captured page text around the name and e-mail, to tick off by hand;
* ``spot-check-oracle.json``: the reviewer's answers in the form
  ``server/harvest_benchmark.py`` reads, so checked colleges become a frozen
  accuracy benchmark.

With no error in n checked records, the error rate is below about 3/n at 95%
confidence (the rule of three): 30 clean records bound it near 10%.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import random
import re

from bs4 import BeautifulSoup

DEFAULT_SAMPLE = 30
CONTEXT_CHARS = 40


def _text(path: str | None) -> str:
    if not path or not Path(path).is_file():
        return ""
    soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"), "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _around(text: str, needle: str) -> str:
    if not needle:
        return ""
    at = text.lower().find(needle.lower())
    if at < 0:
        return "（页面原文中未找到）"
    return text[max(0, at - CONTEXT_CHARS):at + len(needle) + CONTEXT_CHARS]


def sample(records: list[dict], size: int, seed: int) -> list[dict]:
    """Half with e-mail, half without when both exist, so both sheets are checked."""
    rng = random.Random(seed)
    with_email = [row for row in records if row.get("邮箱")]
    without = [row for row in records if not row.get("邮箱")]
    half = min(len(with_email), size // 2)
    chosen = rng.sample(with_email, half)
    chosen += rng.sample(without, min(len(without), size - half))
    if len(chosen) < size:
        rest = [row for row in records if row not in chosen]
        chosen += rng.sample(rest, min(len(rest), size - len(chosen)))
    return chosen


def build(audit_path: Path, size: int = DEFAULT_SAMPLE, seed: int = 0) -> tuple[Path, Path]:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    records = json.loads(Path(audit["records_path"]).read_text(encoding="utf-8"))
    pages = {item["url"]: item["path"] for item in audit.get("snapshots", [])}
    chosen = sample(records, size, seed)
    rows = []
    for index, row in enumerate(chosen, 1):
        text = _text(pages.get(row.get("主页")))
        rows.append(
            "<tr><td>{i}</td><td>{name}<div class=ctx>{name_ctx}</div></td><td>{title}</td>"
            "<td>{email}<div class=ctx>{email_ctx}</div></td><td class=bio>{bio}</td>"
            "<td><a href='{url}' target=_blank>打开原页</a></td>"
            "<td>□姓名 □职称 □邮箱 □简介完整</td></tr>".format(
                i=index, name=escape(row.get("姓名", "")), title=escape(row.get("职称", "")),
                email=escape(row.get("邮箱", "") or "（无）"), url=escape(row.get("主页", ""), quote=True),
                name_ctx=escape(_around(text, row.get("姓名", ""))),
                email_ctx=escape(_around(text, row.get("邮箱", ""))) if row.get("邮箱") else "",
                bio=escape((row.get("简介") or "")[:200])))
    cross = audit.get("cross_check") or {}
    fields = audit.get("field_check") or {}
    head = (f"<h1>{escape(audit.get('school', ''))} {escape(audit.get('college', ''))} 抽检</h1>"
            f"<p>入库 {audit.get('total', len(records))} 人，有邮箱 {audit.get('with_email', 0)} 人；"
            f"本页随机抽 {len(chosen)} 条（种子 {seed}）。</p>"
            f"<p>人数核对：{escape(cross.get('summary', '未运行'))}；"
            f"字段核对：{fields.get('flagged_records', 0)} 条记录待看。</p>"
            "<p>逐条打开原页核对：姓名是否就是这位老师、职称是否一致、邮箱是否本人、简介是否完整。"
            f"抽查 {len(chosen)} 条全部正确时，错误率在 95% 把握下不超过约 "
            f"{(3 / len(chosen) * 100) if chosen else 0:.0f}%。人数是否多少，请对照名录页和上面的人数核对。</p>")
    style = ("<style>body{font:14px/1.5 sans-serif;margin:24px}table{border-collapse:collapse;width:100%}"
             "td,th{border:1px solid #ccc;padding:6px;vertical-align:top}.ctx{color:#666;font-size:12px}"
             ".bio{max-width:360px;font-size:12px}</style>")
    table = ("<table><tr><th>#</th><th>姓名（原文片段）</th><th>职称</th><th>邮箱（原文片段）</th>"
             "<th>简介开头</th><th>来源</th><th>核对</th></tr>" + "".join(rows) + "</table>")
    html_path = audit_path.parent / "spot-check.html"
    html_path.write_text("<!doctype html><meta charset=utf-8><title>抽检</title>" + style + head + table,
                         encoding="utf-8")
    oracle_path = audit_path.parent / "spot-check-oracle.json"
    oracle_path.write_text(json.dumps({
        "reviewed_independently": None, "complete_roster_review": None,
        "expected_observations": None, "identity_set_matches": None,
        "sampled_profile_count": len(chosen), "sampled_fields_match": None,
        "sampled_fulltext_matches": None, "sampled_clean_body_match": None,
        "evidence_paths": [str(html_path)], "sample_seed": seed,
        "sampled": [{"name": row.get("姓名"), "url": row.get("主页"), "fields_correct": None}
                    for row in chosen],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return html_path, oracle_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a random spot-check sheet for one harvested college")
    parser.add_argument("audit", type=Path, help="the college's *_audit.json")
    parser.add_argument("--sample", type=int, default=DEFAULT_SAMPLE)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    for path in build(args.audit, args.sample, args.seed):
        print(path)


if __name__ == "__main__":
    main()
