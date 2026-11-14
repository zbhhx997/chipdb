#!/usr/bin/env python3
# 生成热搜 App 的“正文镜像”：知乎日报 + 爱范儿 RSS
#
# 用法：python tools/fetch_news.py
# 产出：news/news.json（列表）  news/news_body.json（正文）
# 只用标准库，GitHub Actions 直接跑。

import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "news")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Encoding": "identity",
    "Accept": "application/json, text/plain, */*",
}

MAX_ZHIHU = 10
MAX_IFANR = 10
MAX_CHARS = 5000

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def strip_html(s):
    """HTML -> 纯文本，保留段落换行。"""
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(
        r"(?i)</(p|div|h[1-6]|li|blockquote|section|article|figure|figcaption)>",
        "\n",
        s,
    )
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = []
    for ln in s.splitlines():
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return "\n".join(lines)


def cut_body(text):
    text = (text or "").strip()
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS].rstrip() + "\n（正文过长，已截断）"
    return text


def fetch_zhihu():
    out = []
    latest = json.loads(get("https://news-at.zhihu.com/api/4/news/latest"))
    stories = (latest.get("stories") or [])[:MAX_ZHIHU]
    for s in stories:
        sid = s.get("id")
        title = (s.get("title") or "").strip()
        if not sid or not title:
            continue
        body = ""
        try:
            detail = json.loads(
                get("https://news-at.zhihu.com/api/4/news/%s" % sid, 20)
            )
            body = cut_body(strip_html(detail.get("body") or ""))
        except Exception as e:
            print("zhihu detail fail %s: %s" % (sid, e))
        if not body:
            continue
        out.append(
            {
                "id": "zhihu-%s" % sid,
                "title": title,
                "source": "知乎日报",
                "meta": (s.get("hint") or "").strip(),
                "url": s.get("url") or "",
                "body": body,
            }
        )
    return out


def fetch_ifanr():
    xml = get("https://www.ifanr.com/feed", 30)
    xml = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml)
    root = ET.fromstring(xml)
    ch = root.find("channel")
    if ch is None:
        return []
    out = []
    for it in ch.findall("item")[:MAX_IFANR]:
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        enc = it.find("{%s}encoded" % CONTENT_NS)
        raw = enc.text if enc is not None and enc.text else (it.findtext("description") or "")
        body = cut_body(strip_html(raw))
        if not title or not body:
            continue
        author = (it.findtext("{%s}creator" % DC_NS) or "").strip()
        out.append(
            {
                "id": "ifanr-" + hashlib.md5(link.encode("utf-8")).hexdigest()[:12],
                "title": title,
                "source": "爱范儿",
                "meta": author,
                "url": link,
                "body": body,
            }
        )
    return out


def main():
    items = []
    for fn in (fetch_zhihu, fetch_ifanr):
        try:
            items.extend(fn())
        except Exception as e:
            print("source fail %s: %s" % (fn.__name__, e))

    if not items:
        print("no news fetched")
        sys.exit(1)

    sig = hashlib.md5(
        json.dumps(
            [
                (x["id"], x["title"], hashlib.md5(x["body"].encode("utf-8")).hexdigest())
                for x in items
            ],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    os.makedirs(DOCS, exist_ok=True)
    list_path = os.path.join(DOCS, "news.json")
    if os.path.exists(list_path):
        try:
            old = json.load(open(list_path, encoding="utf-8"))
            if old.get("sig") == sig:
                print("no change")
                return
        except Exception:
            pass

    now = datetime.now(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    list_items = [{k: v for k, v in x.items() if k != "body"} for x in items]
    body_map = {x["id"]: x["body"] for x in items}

    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(
            {"updated": now, "sig": sig, "items": list_items},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    with open(os.path.join(DOCS, "news_body.json"), "w", encoding="utf-8") as f:
        json.dump(body_map, f, ensure_ascii=False, separators=(",", ":"))

    print(
        "news %d 篇 | 正文合计 %.0f KB | 列表 %.0f KB"
        % (
            len(items),
            sum(len(x["body"]) for x in items) / 1024,
            os.path.getsize(list_path) / 1024,
        )
    )


if __name__ == "__main__":
    main()
