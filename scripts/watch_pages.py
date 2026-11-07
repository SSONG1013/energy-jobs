#!/usr/bin/env python3
"""관심 기업 채용 페이지를 매일 열어 '내용이 바뀌었는지'만 감지합니다 (Claude 토큰 사용 없음).

결과는 watch/state.json 에 저장됩니다. needsReview=true 인 페이지는
Claude 예약 작업이 열어서 이미지 공고까지 분석한 뒤 false 로 되돌립니다.
- 처음 보는 페이지(기준선)와 내용이 바뀐 페이지는 needsReview=true
- 접속 실패는 status="error" 로만 기록하고 기존 기록은 지우지 않습니다.
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "watch" / "targets.json"
STATE = ROOT / "watch" / "state.json"
KST = timezone(timedelta(hours=9))
KW = re.compile(r"원자력|원전|방사선|방사능|방사성|비파괴|NDT|핵융합|SMR")

JS = """() => {
  const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
  const links = [...document.querySelectorAll('a[href]')]
    .map(a => (a.innerText||'').trim().replace(/\\s+/g,' ') + '|' + abs(a.getAttribute('href')))
    .filter(x => x.length > 3 && !x.includes('javascript:') );
  const imgs = [...document.querySelectorAll('img[src]')].map(i => abs(i.getAttribute('src'))).filter(Boolean);
  const text = (document.body.innerText || '').replace(/\\s+/g,' ');
  return {links, imgs, text};
}"""


def now():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    ok = err = changed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (compatible; energy-jobs-board/1.0; +https://github.com/SSONG1013/energy-jobs)")
        for t in targets:
            name, url = t["name"], t["url"]
            rec = state.get(url, {"name": name})
            rec["name"] = name
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2000)
                d = page.evaluate(JS)
                fp = "\n".join(sorted(set(d["links"]))) + "\n##\n" + "\n".join(sorted(set(d["imgs"])))
                h = hashlib.sha256(fp.encode("utf-8")).hexdigest()[:16]
                rec.update(status="ok", error="", checkedAt=now(), kwHit=bool(KW.search(d["text"])),
                           links=len(set(d["links"])), imgs=len(set(d["imgs"])))
                if rec.get("hash") != h:
                    rec["changedAt"] = now()
                    rec["needsReview"] = True
                    rec["baseline"] = "hash" not in rec
                    changed += 1
                rec["hash"] = h
                ok += 1
                print(f"[OK] {name}: 링크 {rec['links']}, 이미지 {rec['imgs']}, 변경={'예' if rec.get('needsReview') else '아니오'}")
            except Exception as e:  # 접속 차단·시간 초과 등
                rec.update(status="error", error=str(e)[:200], checkedAt=now())
                err += 1
                print(f"[실패] {name}: {str(e)[:120]}")
            finally:
                page.close()
            state[url] = rec
        browser.close()
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"완료: 정상 {ok}, 실패 {err}, 변경/신규 {changed}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
