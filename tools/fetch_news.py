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
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

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
MAX_IFANR = 15
MAX_TMT = 15
MAX_CHARS = 8000

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"

# 分类关键词：命中标题或正文前 500 字，按顺序优先
CATS = [
    ("体育", ["足球", "篮球", "NBA", "CBA", "世界杯", "奥运", "球队", "球员", "冠军", "教练", "比赛"]),
    ("娱乐", ["电影", "电视剧", "明星", "演员", "导演", "综艺", "演唱会", "票房", "影视"]),
    ("财经", ["股票", "A股", "基金", "经济", "财报", "营收", "融资", "上市", "市值", "美元", "人民币", "关税", "收购", "银行"]),
    ("国际", ["美国", "英国", "欧盟", "俄罗斯", "乌克兰", "日本", "韩国", "以色列", "伊朗", "总统", "外长", "联合国", "北约"]),
    ("社会", ["警方", "法院", "通报", "事故", "救援", "火灾", "涉嫌", "调查", "村民", "市民", "城管", "物业"]),
    ("科技", ["AI", "人工智能", "手机", "芯片", "英伟达", "苹果", "华为", "小米", "模型", "算法", "汽车", "新能源", "机器人", "iPhone", "微软", "谷歌", "OpenAI", "处理器", "电池", "互联网"]),
    ("知识", ["科学", "研究", "为什么", "如何", "历史", "物理", "化学", "生物", "数学", "宇宙", "健康", "医生", "睡眠", "教育", "心理"]),
]


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


def first_image(raw_html, base_url):
    """取正文第一张图，并补成绝对地址。"""
    if not raw_html:
        return ""
    m = re.search(r'(?is)<img[^>]+src=["\']([^"\']+)["\']', raw_html)
    if not m:
        return ""
    src = html.unescape(m.group(1)).strip()
    if not src or src.startswith("data:"):
        return ""
    return urljoin(base_url, src)


def categorize(title, body):
    text = (title or "") + "\n" + (body or "")[:500]
    low = text.lower()
    for name, words in CATS:
        for w in words:
            if w.lower() in low:
                return name
    return "其他"


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
        img = ""
        try:
            detail = json.loads(
                get("https://news-at.zhihu.com/api/4/news/%s" % sid, 20)
            )
            raw = detail.get("body") or ""
            body = cut_body(strip_html(raw))
            img = first_image(raw, "https://daily.zhihu.com/")
        except Exception as e:
            print("zhihu detail fail %s: %s" % (sid, e))
        if not body:
            continue
        imgs = s.get("images") or []
        if not img and imgs:
            img = imgs[0]
        out.append(
            {
                "id": "zhihu-%s" % sid,
                "title": title,
                "source": "知乎日报",
                "meta": (s.get("hint") or "").strip(),
                "url": s.get("url") or "",
                "category": categorize(title, body),
                "img": img,
                "body": body,
            }
        )
    return out


def parse_rss(url, source, max_items, id_prefix):
    xml = get(url, 30)
    xml = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml)
    root = ET.fromstring(xml)
    ch = root.find("channel")
    if ch is None:
        return []
    out = []
    for it in ch.findall("item")[:max_items]:
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
                "id": id_prefix + hash_id(link),
                "title": title,
                "source": source,
                "meta": author,
                "url": link,
                "category": categorize(title, body),
                "img": first_image(raw, link),
                "body": body,
            }
        )
    return out


def hash_id(s):
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:12]


def fetch_ifanr():
    return parse_rss("https://www.ifanr.com/feed", "爱范儿", MAX_IFANR, "ifanr-")


def fetch_tmt():
    return parse_rss("https://www.tmtpost.com/rss.xml", "钛媒体", MAX_TMT, "tmt-")


def main():
    items = []
    for fn in (fetch_zhihu, fetch_ifanr, fetch_tmt):
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
                (
                    x["id"],
                    x["title"],
                    x["category"],
                    x["img"],
                    hashlib.md5(x["body"].encode("utf-8")).hexdigest(),
                )
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

    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(
            {"updated": now, "sig": sig, "items": list_items},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # 每篇正文单独一个文件，手表点哪篇只拉哪篇
    body_dir = os.path.join(DOCS, "body")
    if os.path.isdir(body_dir):
        shutil.rmtree(body_dir)
    os.makedirs(body_dir)
    total_body = 0
    for x in items:
        p = os.path.join(body_dir, x["id"] + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"body": x["body"]}, f, ensure_ascii=False, separators=(",", ":"))
        total_body += os.path.getsize(p)

    # 清掉旧版合并正文文件
    old_body = os.path.join(DOCS, "news_body.json")
    if os.path.exists(old_body):
        os.remove(old_body)

    print(
        "news %d 篇 | 正文合计 %.0f KB | 列表 %.0f KB | 单篇最大 %.0f KB"
        % (
            len(items),
            total_body / 1024,
            os.path.getsize(list_path) / 1024,
            max(os.path.getsize(os.path.join(body_dir, x["id"] + ".json")) for x in items)
            / 1024,
        )
    )


if __name__ == "__main__":
    main()
