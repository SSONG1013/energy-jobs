#!/usr/bin/env python3
"""사람인 검색 결과(원자력·방사선·비파괴검사)를 읽어 docs/data/manual.json 의 '사람인 출처 공고'를 갱신합니다.

- 사람인이 아닌 기존 수동 등록 공고(잡코리아 등)는 건드리지 않습니다.
- 사람인 공고는 누적 방식: 이번에 보이면 갱신, 새로 보이면 추가, 안 보이는데 마감일이 지났으면 삭제.
- 수집이 막히거나 결과가 너무 적으면 파일을 바꾸지 않고 오류로 끝냅니다(기존 데이터 보존).
- 표준 라이브러리만 사용합니다. Claude 토큰은 쓰지 않습니다.
"""
import difflib
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
MANUAL = Path(__file__).resolve().parent.parent / "docs" / "data" / "manual.json"
UA = "Mozilla/5.0 (compatible; energy-jobs-board/1.0; +https://github.com/SSONG1013/energy-jobs)"
BASE = "https://www.saramin.co.kr/zf_user/search/recruit"
QUERIES = [("원자력", 12), ("방사선", 6), ("비파괴검사", 6),
           ("RI면허", 4), ("방사성동위원소취급자", 4), ("방사성동위원소", 4),
           ("항공 비파괴검사", 4), ("항공기 NDT", 3), ("NAS410", 2), ("항공 방사선", 3)]  # (검색어, 최대 쪽수)
RI_QUERIES = {"RI면허", "방사성동위원소취급자", "방사성동위원소"}
AVI_QUERIES = {"항공 비파괴검사", "항공기 NDT", "NAS410", "항공 방사선"}
# 항공 분야: 직무·자격에 NDT/비파괴(NAS 410, 레벨2·3 등)가 보이는 공고
AVI_NDT = re.compile(r"비파괴|(?<![A-Za-z])NDT(?![A-Za-z])|NAS\s*-?\s*410|(?<![A-Za-z])(?:UT|RT|ET|PT|MT)\s*(?:Level|LV|레벨)|와전류|초음파\s*탐상|침투\s*탐상|자분\s*탐상|방사선\s*투과")
DELAY = 1.5  # 요청 사이 대기(초) — 사이트에 부담을 주지 않기 위해
MIN_RELEVANT = 15  # 이보다 적게 걸리면 수집 실패로 보고 기존 데이터를 그대로 둡니다

# RI면허·방사성동위원소취급자 (필수·우대 모두)
RI_RE = re.compile(
    r"(?<![A-Za-z])R\.?I(?![A-Za-z])\s*(?:\(\s*방사성\s*동위\s*원소\s*\)\s*)?(?:취급|면허|자격|감독)"
    r"|방사성\s*동위\s*원소|동위\s*원소\s*취급"
)
REL = re.compile(r"원자력|원전|방사선|방사능|방사성|핵융합|핵연료|비파괴|NDT|SMR|가동중검사|원자로|신한울|새울|신고리|월성|한울|한빛|고리")
REL_CORP = re.compile(r"오르비텍|삼영검사|에이젠코어|ZETEC|지텍코리아|뉴클리어|레이메드|우진엔텍|한국에스지에스|하나원자력|한일원자력|케이엔디이|대한검사|서울검사|와이에스검사|본테크|아거스|한전|한수원|한국수력")
EXCLUDE = re.compile(r"행정조교|교육생|수강생|대학원생|미화|조리원|환경미화|한울에스앤아이|두원전자|사업관리보조|웹개발|마케팅")
# 병원·의원의 임상 방사선사 공고는 제외 (방사선 안전관리·원자력·비파괴 직무는 유지)
CLINICAL = re.compile(r"방사선사|영상의학|C-?arm|X-?ray|엑스레이|초음파|방사선과|임상병리|물리치료")
CLINIC_ORG = re.compile(r"병원|의원|의료재단|의료법인|한의원|메디컬|건강검진|요양|치과|결핵협회|보건소|의학원")
KEEP_ROLE = re.compile(r"안전관리|SRI|취급감독|원자력|원전|비파괴|핵융합|방사성폐기물|방사화학|방사선량|선량")
EXCLUDE_AGENCY = re.compile(r"한울에스앤아이|두원전자|거산에이치알|휴:콥|한전산업개발")


def log(*a):
    print(*a, flush=True)


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                return res.read().decode("utf-8", "replace")
        except Exception as e:  # 일시적 오류는 재시도
            last = e
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"요청 실패: {last}")


def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_items(page_html: str) -> list[dict]:
    parts = re.split(r'<div class="item_recruit"', page_html)[1:]
    items = []
    for blk in parts:
        m_id = re.match(r'[^>]*?value="(\d+)"', blk)
        m_t = re.search(r'<h2 class="job_tit">.*?title="([^"]*)"', blk, re.S)
        if not (m_id and m_t):
            continue
        m_corp = re.search(r'<strong class="corp_name">.*?<a [^>]*>(.*?)</a>', blk, re.S)
        m_date = re.search(r'<span class="date">([^<]*)</span>', blk)
        m_cond = re.search(r'<div class="job_condition">(.*?)</div>', blk, re.S)
        m_sec = re.search(r'<div class="job_sector">(.*?)</div>', blk, re.S)
        cond = [strip_tags(x) for x in re.findall(r"<span>(.*?)</span>", m_cond.group(1), re.S)] if m_cond else []
        tags = [strip_tags(x) for x in re.findall(r"<a\b[^>]*>([^<]*)</a>", m_sec.group(1))] if m_sec else []
        items.append({
            "id": m_id.group(1),
            "corp": strip_tags(m_corp.group(1)) if m_corp else "",
            "title": strip_tags(m_t.group(1)),
            "date": strip_tags(m_date.group(1)) if m_date else "",
            "region": cond[0] if len(cond) > 0 else "",
            "career": cond[1] if len(cond) > 1 else "",
            "edu": cond[2] if len(cond) > 2 else "",
            "hire": cond[3] if len(cond) > 3 else "",
            "tags": list(dict.fromkeys(tags)),
        })
    return items


def collect() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for word, max_pages in QUERIES:
        for page in range(1, max_pages + 1):
            q = urllib.parse.urlencode({
                "search_area": "main", "search_done": "y", "search_optional_item": "n", "searchType": "search",
                "searchword": word, "recruitPage": page, "recruitSort": "relation", "recruitPageCount": 40,
            })
            try:
                page_html = http_get(f"{BASE}?{q}")
            except RuntimeError as e:
                log(f"[{word} {page}쪽] {e}")
                break
            items = parse_items(page_html)
            new = [i for i in items if i["id"] not in found]
            log(f"[{word} {page}쪽] 항목 {len(items)}개, 신규 {len(new)}개")
            for i in new:
                found[i["id"]] = i
            if word in AVI_QUERIES:  # 항공 검색어에 걸린 공고 표시
                for i in items:
                    found[i["id"]]["avi_hit"] = True
            if word in RI_QUERIES:  # RI 검색어에 걸린 공고 표시 (이미 수집된 공고 포함)
                for i in items:
                    found[i["id"]]["ri_hit"] = True
            if not items or not new:
                break
            time.sleep(DELAY)
    return found


# 해외 근무 공고 제외 (지역이 아시아·중동·유럽 등이거나, 제목에 해외 현장이 드러나는 경우)
OVERSEAS_REGION = re.compile(r"^(아시아|중동|유럽|북미|남미|중남미|아프리카|오세아니아|해외)")
OVERSEAS_TITLE = re.compile(r"UAE|사우디|아랍|두바이|바라카|해외\s*(근무|파견|현장|발령)")


def is_overseas(it: dict) -> bool:
    return bool(OVERSEAS_REGION.search(it.get("region", "")) or OVERSEAS_TITLE.search(it.get("title", "")))


def is_clinical(it: dict) -> bool:
    return bool((CLINICAL.search(it["title"]) or CLINIC_ORG.search(it["corp"])) and not KEEP_ROLE.search(it["title"]))


def has_ri(it: dict) -> bool:
    """제목·회사·직무태그·조건 어디에든 RI면허/방사성동위원소취급자 문구가 있으면 참.
    RI 검색어로 걸렸지만 카드에 문구가 안 보이는 공고는 검색 정확도를 믿을 수 없어 제외합니다."""
    s = " ".join([it["title"], it["corp"], *it.get("tags", []), it.get("career", ""), it.get("edu", "")])
    return bool(RI_RE.search(s))


def has_avi_ndt(it: dict) -> bool:
    """항공 검색어로 걸렸고, 제목·회사·직무태그·조건에 NDT/비파괴 문구가 보이는 공고."""
    if not it.get("avi_hit"):
        return False
    s = " ".join([it["title"], it["corp"], *it.get("tags", []), it.get("career", ""), it.get("edu", "")])
    return bool(AVI_NDT.search(s))


def is_relevant(it: dict) -> bool:
    text = it["title"] + " " + it["corp"]
    if EXCLUDE.search(it["title"]) or EXCLUDE_AGENCY.search(it["corp"]):
        return False
    if has_avi_ndt(it):
        return not is_overseas(it)
    if has_ri(it):  # RI면허·방사성동위원소취급자 문구가 보이는 공고는 병원 등 임상 공고라도 포함
        return not is_overseas(it)
    if is_clinical(it) or is_overseas(it):
        return False
    return bool(REL.search(text) or REL_CORP.search(it["corp"]))


DOW = "월화수목금토일"


def parse_end(date_text: str, today) -> tuple[str, str]:
    """('2026-12-05', '') 또는 ('', '채용시') 형태로 돌려줍니다."""
    t = date_text.strip()
    if "오늘마감" in t:
        return today.isoformat(), ""
    m = re.search(r"(\d{1,2})/(\d{1,2})\(([월화수목금토일])\)", t)
    if m:
        mo, da, dw = int(m[1]), int(m[2]), m[3]
        for y in (today.year, today.year + 1, today.year - 1):
            try:
                d = datetime(y, mo, da).date()
            except ValueError:
                continue
            if DOW[d.weekday()] == dw and d >= today - timedelta(days=1):
                return d.isoformat(), ""
        m2 = re.search(r"(\d{1,2})/(\d{1,2})", t)
        y = today.year if (int(m2[1]), int(m2[2])) >= (today.month, today.day) else today.year + 1
        return f"{y}-{int(m2[1]):02d}-{int(m2[2]):02d}", ""
    return "", (t.lstrip("~ ").strip() or "채용시")


def categorize(it: dict) -> list[str]:
    s = it["title"] + " " + it["corp"]
    out = set()
    rad = re.search(r"방사선|방사능|방사성의약품|방사화학|삼중수소|SRI", s) or has_ri(it)
    waste = re.search(r"방사성폐기물", s)
    nuc = re.search(r"원자력|원전|원자로|신한울|새울|핵융합|고리|한울|CGID|내진|EQ|월성", s)
    ndt = re.search(r"비파괴", s) or has_avi_ndt(it)
    if rad or waste:
        out.add("방사선 안전")
    if waste or nuc:
        out.add("원자력")
    if ndt:
        out.add("비파괴검사")
    if not out:
        if any("비파괴" in t for t in it["tags"]):
            out.add("비파괴검사")
        elif any("방사" in t for t in it["tags"]):
            out.add("방사선 안전")
        else:
            out.add("원자력")
    return [c for c in ("방사선 안전", "원자력", "비파괴검사") if c in out]


def norm_corp(name: str) -> str:
    return re.sub(r"[\s()㈜（）]|\(주\)|주식회사|\(사\)|\(재\)", "", name)


def to_job(it: dict, today, job_id: str) -> dict:
    end, label = parse_end(it["date"], today)
    corp = re.sub(r"^주\)", "(주)", it["corp"].strip())
    field = "·".join(it["tags"][:3]) + f" ({it['career']} / {it['edu']})" if it["tags"] else f"({it['career']} / {it['edu']})"
    job = {
        "agency": corp,
        "field": field,
        "title": it["title"],
        "end": end,
        "region": it["region"],
        "hireType": it["hire"],
        "categories": categorize(it),
        "url": f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={it['id']}",
        "checked": today.isoformat(),
        "id": job_id,
    }
    if not end:
        job["endLabel"] = label
    if re.search(r"삼성물산|두산에너빌리티", corp):
        job["size"] = "대기업"
    return job


def rec_id(job: dict) -> str:
    m = re.search(r"rec_idx=(\d+)", job.get("url", ""))
    return m.group(1) if m and "saramin.co.kr" in job.get("url", "") else ""


def main() -> int:
    today = datetime.now(KST).date()
    data = json.loads(MANUAL.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    others = [j for j in jobs if not rec_id(j)]
    old_sr = {rec_id(j): j for j in jobs if rec_id(j)}

    found = collect()
    rel = [it for it in found.values() if is_relevant(it)]
    log(f"수집 {len(found)}건 → 관련 {len(rel)}건")
    if len(rel) < MIN_RELEVANT:
        log("관련 공고가 너무 적습니다(접속 차단 또는 페이지 구조 변경 가능성). 기존 데이터를 그대로 둡니다.")
        return 1

    max_no = max((int(m.group(1)) for j in jobs if (m := re.match(r"manual-(\d+)$", j.get("id", "")))), default=0)
    other_keys = [(norm_corp(j["agency"]), j["title"]) for j in others]

    result_sr: dict[str, dict] = {}
    added = updated = 0
    for it in rel:
        # 사람인이 아닌 기존 등록과 같은 공고면 건너뜀
        c = norm_corp(it["corp"])
        if any(c and (c in oc or oc in c) and difflib.SequenceMatcher(None, it["title"], ot).ratio() > 0.8 for oc, ot in other_keys):
            continue
        if it["id"] in old_sr:
            job = to_job(it, today, old_sr[it["id"]]["id"])
            updated += 1
        else:
            max_no += 1
            job = to_job(it, today, f"manual-{max_no:03d}")
            added += 1
        result_sr[it["id"]] = job

    removed = kept = 0
    for rid, j in old_sr.items():
        if rid in result_sr:
            continue
        end = j.get("end", "")
        if (end and end < today.isoformat()) or is_clinical({"title": j.get("title", ""), "corp": j.get("agency", "")}) or is_overseas(j):
            removed += 1
        else:
            result_sr[rid] = j  # 검색 순위에서만 밀린 경우는 유지 (사이트가 마감/14일 경과 시 자동으로 숨김)
            kept += 1

    new_jobs = others + sorted(result_sr.values(), key=lambda j: int(re.search(r"(\d+)$", j["id"]).group(1)))
    if new_jobs == jobs:
        log("변경 없음")
        return 0
    data["jobs"] = new_jobs
    data["updatedAt"] = today.isoformat()
    MANUAL.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"저장 완료: 추가 {added}, 갱신 {updated}, 삭제 {removed}, 유지 {kept}, 전체 {len(new_jobs)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
