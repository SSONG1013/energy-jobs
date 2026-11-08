# -*- coding: utf-8 -*-
"""국내외 원자력·방사선 관련 기사 수집 (Google News RSS, 표준 라이브러리만 사용, Claude 토큰 불필요).
제목·링크·언론사·날짜만 저장합니다 (본문은 저장하지 않음)."""
import json, re, html, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

KST = timezone(timedelta(hours=9))
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "news.json"
KEEP_DAYS = 60
MAX_ITEMS = 700

QUERIES = [
    ("국내", "원자력 OR 원전", "ko", "KR", "KR:ko"),
    ("국내", "방사선 OR 방사능 OR 방사성", "ko", "KR", "KR:ko"),
    ("국내", "비파괴검사", "ko", "KR", "KR:ko"),
    ("국내", "소형모듈원전 OR SMR OR 핵융합", "ko", "KR", "KR:ko"),
    ("국내", "한국수력원자력 OR 한수원 OR 원자력안전위원회", "ko", "KR", "KR:ko"),
    ("해외", "nuclear power plant", "en-US", "US", "US:en"),
    ("해외", "nuclear energy SMR", "en-US", "US", "US:en"),
    ("해외", "radiation safety", "en-US", "US", "US:en"),
    ("해외", "nondestructive testing", "en-US", "US", "US:en"),
    ("해외", "IAEA nuclear regulator", "en-US", "US", "US:en"),
]
TOPICS = [
    ("비파괴검사", re.compile(r"비파괴|nondestructive|non-destructive|\bNDT\b", re.I)),
    ("SMR·신기술", re.compile(r"SMR|소형모듈|핵융합|fusion|small modular|microreactor", re.I)),
    ("방사선", re.compile(r"방사선|방사능|방사성|radiation|radioactive|radiological", re.I)),
    ("원자력", re.compile(r"원자력|원전|원자로|핵발전|nuclear|reactor", re.I)),
]
JUNK = re.compile(r"주가|급등|급락|특징주|목표가|증권|ETF|stock|shares|price target|NYSE|NASDAQ", re.I)


def fetch(q, hl, gl, ceid):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q + " when:7d", "hl": hl, "gl": gl, "ceid": ceid})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; energy-jobs-board/1.0; +https://github.com/SSONG1013/energy-jobs)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return ET.fromstring(r.read())


def topic_of(title):
    for name, rx in TOPICS:
        if rx.search(title):
            return name
    return "기타"


def norm(t):
    return re.sub(r"[^0-9a-z가-힣]", "", t.lower())[:50]


def main():
    now = datetime.now(KST)
    old = {"items": []}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    items, seen_link, seen_title = [], set(), set()
    for it in old.get("items", []):
        items.append(it); seen_link.add(it["url"]); seen_title.add(norm(it["title"]))
    got = 0
    for region, q, hl, gl, ceid in QUERIES:
        try:
            root = fetch(q, hl, gl, ceid)
        except Exception as e:
            print("실패:", q, e)
            continue
        for e in root.iter("item"):
            title = html.unescape((e.findtext("title") or "").strip())
            link = (e.findtext("link") or "").strip()
            src = html.unescape((e.findtext("source") or "").strip())
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3].strip()
            try:
                dt = parsedate_to_datetime(e.findtext("pubDate")).astimezone(KST)
            except Exception:
                continue
            if not title or not link or JUNK.search(title):
                continue
            if (now - dt).days > KEEP_DAYS or dt > now + timedelta(days=1):
                continue
            nt = norm(title)
            if link in seen_link or nt in seen_title:
                continue
            seen_link.add(link); seen_title.add(nt)
            items.append({"title": title, "url": link, "source": src, "date": dt.strftime("%Y-%m-%d"),
                          "time": dt.strftime("%H:%M"), "region": region, "topic": topic_of(title)})
            got += 1
        print(region, q, "->", got)
    if got == 0 and old.get("items"):
        print("새 기사 없음/수집 실패 - 기존 유지")
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    items = [i for i in items if i["date"] >= cutoff]
    items.sort(key=lambda i: (i["date"], i["time"]), reverse=True)
    items = items[:MAX_ITEMS]
    if not items:
        raise SystemExit("기사 0건 - 중단")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": now.strftime("%Y-%m-%d %H:%M"), "items": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("저장:", len(items), "건, 신규", got)


if __name__ == "__main__":
    main()
