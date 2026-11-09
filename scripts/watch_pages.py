#!/usr/bin/env python3
"""관심 기업 채용 페이지를 매일 열어 원자력·방사선·비파괴검사 공고를 찾아 docs/data/manual.json 에 등록합니다.

Claude 토큰·사용자 컴퓨터 없이 GitHub Actions 에서만 동작합니다.
  1) 페이지의 링크 문구에 '키워드 + 채용 관련 단어'가 있으면 그 링크를 공고로 등록 (글자 공고)
  2) 큰 이미지(포스터)는 무료 OCR(Tesseract 한글)로 읽어 같은 조건이면 등록 (이미지 공고)
  - 등록 항목에는 '자동 인식(원문 확인 필요)' 표시를 붙입니다.
  - 접속 실패는 해당 페이지만 건너뛰고, 기존 데이터는 지우지 않습니다.
  - watch/state.json 에 페이지별 상태와 이미 처리한 이미지 목록을 기록합니다.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_saramin import categorize, is_clinical, is_overseas  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "watch" / "targets.json"
STATE = ROOT / "watch" / "state.json"
REVIEW = ROOT / "watch" / "review.json"  # 글자 인식이 못 읽은 이미지 목록 (Claude 보완용)
MANUAL = ROOT / "docs" / "data" / "manual.json"
KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()

KW = re.compile(r"원자력|원전|방사선|방사능|방사성|비파괴|NDT|핵융합|SMR|가동중검사|동위원소|RI\s*면허|RI\s*취급")
POST = re.compile(r"채용|모집|공고|인턴|경력|신입|수시|접수|지원자격")
MAX_IMG_PER_PAGE = 10
MIN_W, MIN_H = 450, 300  # 로고·아이콘 제외

JS = """() => {
  const abs = u => { try { return new URL(u, location.href).href } catch(e) { return '' } };
  const anchors = [...document.querySelectorAll('a[href]')].map(a => ({
    text: (a.innerText||a.getAttribute('title')||'').trim().replace(/\\s+/g,' '), href: abs(a.getAttribute('href'))}))
    .filter(x => x.text && x.href && !x.href.startsWith('javascript'));
  const imgs = [...document.querySelectorAll('img[src]')].map(i => ({
    src: abs(i.currentSrc || i.getAttribute('src')), w: i.naturalWidth||0, h: i.naturalHeight||0})).filter(x => x.src);
  const text = (document.body.innerText || '').replace(/\\s+/g,' ');
  return {anchors, imgs, text};
}"""


def now():
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def nospace(s):
    return re.sub(r"\s+", "", s)


KW3 = re.compile(r"원자력|방사선|방사능|방사성|비파괴|핵융합|가동중검사|동위원소")


def kw_hit(text):
    """키워드 판정. 공백을 지운 비교는 긴 단어(원자력 등)에만 씁니다('경영지원 전략'이 '원전'으로 오인되는 것 방지)."""
    return bool(KW.search(text) or KW3.search(nospace(text)))


def parse_end(text):
    """OCR 문장 속 날짜 중 마지막 것을 마감일로 추정. 없으면 ''."""
    ds = re.findall(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", text)
    for y, m, d in reversed(ds):
        try:
            return datetime(int(y), int(m), int(d)).date().isoformat()
        except ValueError:
            continue
    return ""


def ocr_image(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        r = subprocess.run(["tesseract", path, "stdout", "-l", "kor+eng", "--psm", "6"],
                           capture_output=True, text=True, timeout=120)
        return r.stdout
    except Exception as e:
        print("   OCR 실패:", str(e)[:80])
        return ""
    finally:
        Path(path).unlink(missing_ok=True)


def good_item(title, name):
    it = {"title": title, "corp": name, "region": ""}
    return not (is_clinical(it) or is_overseas(it))


STALE_PATH = re.compile(r"archive|interview|/history/|/old/", re.I)
STALE_DAYS = 60


def stale_img(src):
    """이미 마감됐을 가능성이 높은 옛 이미지(주소에 오래된 날짜가 있거나 보관/면접 경로)는 건너뜀."""
    if STALE_PATH.search(src):
        return True
    for m in re.finditer(r"(20\d{2})[/_\-.]?(0[1-9]|1[0-2])[/_\-.]?(0[1-9]|[12]\d|3[01])", src):
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            continue
        if (TODAY - d).days > STALE_DAYS:
            return True
    return False


def main() -> int:
    targets = json.loads(TARGETS.read_text(encoding="utf-8"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    manual = json.loads(MANUAL.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8")) if REVIEW.exists() else {"items": []}
    jobs = manual["jobs"]
    by_id = {j["id"]: j for j in jobs}
    used_urls = {j["url"] for j in jobs}
    max_no = max((int(m.group(1)) for j in jobs if (m := re.match(r"manual-(\d+)$", j.get("id", "")))), default=0)
    added = refreshed = ok = err = 0

    def add_job(name, title, url, end, endlabel, text_for_cat):
        nonlocal max_no, added
        max_no += 1
        job = {
            "agency": name, "field": "자동 인식(원문 확인 필요)", "title": title, "end": end,
            "region": "", "hireType": "",
            "categories": categorize({"title": text_for_cat, "corp": "", "tags": []}),
            "url": url, "checked": TODAY.isoformat(), "id": f"manual-{max_no:03d}", "size": "대기업",
        }
        if not end:
            job["endLabel"] = endlabel
        jobs.append(job)
        by_id[job["id"]] = job
        used_urls.add(url)
        added += 1
        return job["id"]

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ko-KR", user_agent="Mozilla/5.0 (compatible; energy-jobs-board/1.0; +https://github.com/SSONG1013/energy-jobs)")
        for t in targets:
            name, url = t["name"], t["url"]
            rec = state.get(url, {"name": name})
            rec["name"] = name
            rec.pop("needsReview", None)
            auto = rec.setdefault("auto", {})      # 키(링크 주소 또는 이미지 주소) -> manual id
            done_img = rec.setdefault("imgDone", {})  # 이미지 주소 -> 처리 결과 요약
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(2500)
                d = page.evaluate(JS)
                rec.update(status="ok", error="", checkedAt=now(), links=len(d["anchors"]), imgs=len(d["imgs"]))

                # 1) 글자 링크 공고
                seen = set()
                for a in d["anchors"]:
                    txt = a["text"]
                    if len(txt) > 120 or not (kw_hit(txt) and POST.search(txt)):
                        continue
                    key = a["href"] + "#" + txt[:40]
                    if key in seen:
                        continue
                    seen.add(key)
                    if not good_item(txt, name):
                        continue
                    link_end = parse_end(txt)
                    if link_end and link_end < TODAY.isoformat():
                        continue
                    if key in auto and auto[key] in by_id:
                        by_id[auto[key]]["checked"] = TODAY.isoformat(); refreshed += 1
                    elif a["href"] not in used_urls:
                        auto[key] = add_job(name, f"[{name}] {txt}", a["href"], link_end, "원문 확인", txt)
                        print(f"   + 링크 공고: {txt[:60]}")

                # 2) 이미지(포스터) OCR
                cand = [i for i in d["imgs"] if i["w"] >= MIN_W and i["h"] >= MIN_H][:MAX_IMG_PER_PAGE]
                for im in cand:
                    src = im["src"]
                    if src not in done_img and stale_img(src):
                        done_img[src] = "closed"
                        continue
                    if src in done_img:
                        mid = done_img[src]
                        if isinstance(mid, str) and mid in by_id:
                            by_id[mid]["checked"] = TODAY.isoformat(); refreshed += 1
                        continue
                    try:
                        resp = ctx.request.get(src, timeout=30000)
                        body = resp.body() if resp.ok else b""
                    except Exception:
                        body = b""
                    if not body or len(body) > 8_000_000:
                        done_img[src] = "skip"
                        continue
                    text = ocr_image(body)
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    hits = [i for i, l in enumerate(lines) if kw_hit(l)]
                    good = [i for i in hits if POST.search(" ".join(lines[max(0, i - 1): i + 2])) and len(re.findall(r"[가-힣]", lines[i])) >= 8]
                    if not good:
                        # 키워드는 있는데 확신이 없거나 글자를 거의 못 읽은 큰 이미지는 Claude 보완 대상으로 기록 (하루 토큰 한도를 위해 개수 제한)
                        hangul = len(re.findall(r"[가-힣]", text))
                        pending = [x for x in review["items"] if x["status"] == "pending"]
                        mine = [x for x in pending if x["company"] == name]
                        if (hits or hangul < 30) and im["h"] >= 500 and len(pending) < 15 and len(mine) < 3 and not any(x["img"] == src for x in review["items"]):
                            review["items"].append({"company": name, "page": url, "img": src, "addedAt": now(), "status": "pending"})
                            done_img[src] = "review"
                            continue
                        done_img[src] = "no-match"
                        continue
                    line = lines[good[0]]
                    title_src = re.sub(r"\s+", " ", line)[:70] or "이미지 공고"
                    end = parse_end(text)
                    if end and end < TODAY.isoformat():
                        done_img[src] = "closed"
                        continue
                    if not good_item(title_src + " " + text[:400], name):
                        done_img[src] = "filtered"
                        continue
                    jid = add_job(name, f"[{name}] 이미지 공고 — {title_src}", url, end, "원문 확인", text[:600])
                    done_img[src] = jid
                    print(f"   + 이미지 공고: {title_src}")
                ok += 1
                print(f"[OK] {name}: 링크 {len(d['anchors'])}, 이미지 {len(d['imgs'])}(후보 {len(cand)})")
            except Exception as e:
                rec.update(status="error", error=str(e)[:200], checkedAt=now())
                err += 1
                print(f"[실패] {name}: {str(e)[:120]}")
            finally:
                page.close()
            state[url] = rec
        browser.close()

    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    REVIEW.write_text(json.dumps(review, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if added or refreshed:
        manual["updatedAt"] = TODAY.isoformat()
        MANUAL.write_text(json.dumps(manual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"완료: 정상 {ok}, 실패 {err}, 새 공고 {added}, 갱신 {refreshed}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
