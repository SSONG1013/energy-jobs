#!/usr/bin/env python3
"""원자력·방사선 관련 국내외 기사 수집 (구글 뉴스 RSS, 표준 라이브러리만 사용).

- 제목·언론사·날짜·원문 링크만 저장 (본문은 저장하지 않음)
- 주제는 키워드 규칙으로 분류, 주차(월요일 시작, KST)별로 묶을 수 있게 week 필드 저장
- 기존 news.json 과 합쳐서 최근 KEEP_DAYS 일만 유지
"""
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
OUT = Path(__file__).resolve().parent.parent / "docs" / "data" / "news.json"
KEEP_DAYS = 60
MAX_ITEMS = 1500

KR_QUERIES = ["원자력", "원전", "방사선", "방사성폐기물", "소형모듈원전 SMR", "비파괴검사",
              "한국수력원자력", "원자력안전위원회", "핵융합", "사용후핵연료"]
EN_QUERIES = ["nuclear power", "nuclear reactor SMR", "radiation safety", "nuclear regulatory commission",
              "radioactive waste decommissioning", "IAEA nuclear", "nondestructive testing"]

KR_REL = re.compile(r"원자력|원전|방사선|방사능|방사성|피폭|핵융합|사용후핵연료|소형모듈|SMR|비파괴|한수원|원안위|핵연료|원자로|IAEA|핵발전")
EN_REL = re.compile(r"nuclear|reactor|radiation|radioactive|uranium|fusion|IAEA|decommission|nondestructive|spent fuel", re.I)

TOPICS = [
    ("해체·폐기물", re.compile(r"해체|폐기물|사용후핵연료|방폐|고준위|decommission|waste|spent fuel", re.I)),
    ("SMR·신기술", re.compile(r"SMR|소형모듈|소형 원자로|4세대|핵융합|마이크로|small modular|microreactor|advanced reactor|fusion", re.I)),
    ("방사선 안전·규제", re.compile(r"방사선|방사능|피폭|원안위|원자력안전|안전규제|규제|radiation|NRC|regulat|safety|licens", re.I)),
    ("비파괴검사·산업", re.compile(r"비파괴|NDT|검사|수출|수주|계약|공급망|nondestructive|export|contract|supply chain|inspection", re.I)),
]
DEFAULT_TOPIC = "원전 정책·운영"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; energy-jobs-board/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def feed_url(q, region):
    q = f"{q} when:14d"
    if region == "국내":
        return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def norm_title(t):
    return re.sub(r"[^0-9a-z가-힣]", "", t.lower())[:60]


def week_start(d):
    """KST 날짜 d(date)가 속한 주의 월요일."""
    return d - timedelta(days=d.weekday())


def classify(title):
    for name, rx in TOPICS:
        if rx.search(title):
            return name
    return DEFAULT_TOPIC


def parse_feed(xml_bytes, region):
    out = []
    root = ET.fromstring(xml_bytes)
    rel = KR_REL if region == "국내" else EN_REL
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        elif " - " in title and not source:
            title, source = title.rsplit(" - ", 1)
        if not title or not link or not pub or not rel.search(title):
            continue
        try:
            dt = parsedate_to_datetime(pub).astimezone(KST)
        except Exception:
            continue
        out.append({"title": title, "source": source, "url": link, "date": dt.date().isoformat(), "region": region})
    return out


def main():
    old = []
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
        except Exception:
            old = []
    fetched, errs = [], 0
    for region, qs in (("국내", KR_QUERIES), ("해외", EN_QUERIES)):
        for q in qs:
            try:
                fetched += parse_feed(get(feed_url(q, region)), region)
            except Exception as e:
                errs += 1
                print(f"! {region} '{q}' 실패: {e}", file=sys.stderr)
    print(f"수집 {len(fetched)}건, 실패 {errs}건")
    if not fetched:
        print("수집된 기사가 없어 기존 파일을 유지합니다.")
        return 1 if errs else 0

    cutoff = (NOW.date() - timedelta(days=KEEP_DAYS)).isoformat()
    items, seen = [], set()
    for a in sorted(fetched + old, key=lambda x: x["date"], reverse=True):
        key = norm_title(a["title"])
        if not key or key in seen or a["date"] < cutoff or a["date"] > NOW.date().isoformat():
            continue
        seen.add(key)
        d = datetime.strptime(a["date"], "%Y-%m-%d").date()
        items.append({
            "id": hashlib.md5(key.encode()).hexdigest()[:10],
            "title": a["title"], "source": a.get("source", ""), "url": a["url"],
            "date": a["date"], "week": week_start(d).isoformat(),
            "region": a["region"], "topic": classify(a["title"]),
        })
    items = items[:MAX_ITEMS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"updated": NOW.strftime("%Y-%m-%d %H:%M"), "items": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"저장 {len(items)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
