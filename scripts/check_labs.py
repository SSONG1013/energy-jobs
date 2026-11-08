#!/usr/bin/env python3
"""연구실 링크 점검 (표준 라이브러리만 사용). 결과는 docs/data/labs-status.json 에 저장."""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "docs" / "data" / "labs.json"
OUT = ROOT / "docs" / "data" / "labs-status.json"
UA = "Mozilla/5.0 (compatible; energy-jobs-board/1.0; +https://github.com/SSONG1013/energy-jobs)"


def check(url):
    """ok / bad / unknown. 접속 거부(403 등)는 봇 차단일 수 있어 unknown 으로 둠."""
    last = "bad"
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=25) as r:
                return "ok" if r.status < 400 else "bad"
        except urllib.error.HTTPError as e:
            if e.code in (404, 410) or e.code >= 500:
                last = "bad"
            else:
                return "unknown"
        except Exception:
            last = "bad"
    return last


def main():
    data = json.loads(LABS.read_text(encoding="utf-8"))
    urls = set()
    for i in data["institutions"]:
        if i.get("url"):
            urls.add(i["url"])
    links = {u: check(u) for u in sorted(urls)}
    bad = [u for u, s in links.items() if s == "bad"]
    print(f"점검 {len(links)}건, 확인 필요 {len(bad)}건")
    for u in bad:
        print("  !", u)
    OUT.write_text(json.dumps({"checked": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "links": links}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
