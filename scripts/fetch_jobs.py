#!/usr/bin/env python3
"""
방사선 · 원자력 · 비파괴검사 · 에너지 채용공고 수집기 (전국)

수집 출처
 1) 공공기관: 잡알리오 (재정경제부_공공기관 채용정보 조회서비스, 공공데이터포털 15125273)
    - 환경변수 ALIO_API_KEY
 2) 공무원·공공기관: 나라일터 (인사혁신처_공공취업정보 조회 서비스, 공공데이터포털 15156780)
    - 같은 공공데이터포털 키 사용 (ALIO_API_KEY)
 3) 민간기업: 사람인 채용공고 API (oapi.saramin.co.kr)
    - 환경변수 SARAMIN_ACCESS_KEY  (없으면 민간기업 수집은 건너뜀)

한 출처가 실패해도 다른 출처는 정상 반영하고, 실패한 출처는 전날 자료(마감 전 공고)를 그대로 유지합니다.
외부 패키지 없이 파이썬 표준 라이브러리만 사용합니다.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
OUT_FILE = ROOT / "docs" / "data" / "jobs.json"

ALIO_URL = os.environ.get("ALIO_API_URL", "https://apis.data.go.kr/1051000/recruitment/list")
SARAMIN_URL = os.environ.get("SARAMIN_API_URL", "https://oapi.saramin.co.kr/job-search")
NARA_URL = os.environ.get("NARA_API_URL", "https://apis.data.go.kr/1760000/PblJobService")

CATEGORY_ORDER = ["방사선", "원자력", "비파괴검사", "에너지"]

# ---------------------------------------------------------------------------
# 분류 기준 — 필요하면 여기 단어/기관만 고치면 됩니다.
# ---------------------------------------------------------------------------
# 공고 제목·직무분야·키워드에 이 단어가 들어가면 해당 분야로 분류
# (영문 약어는 다른 단어 속에 섞인 경우를 막기 위해 단어 단위로만 인정)
KEYWORDS = {
    "방사선": [
        "방사선", "방사능", "방사성동위원소", "동위원소", "영상의학", "핵의학",
        "방사선사", "선량", "가속기", "방사광",
    ],
    "원자력": [
        "원자력", "원전", "원자로", "핵연료", "핵융합", "핵물질", "핵안보", "비확산",
        "방폐", "방사성폐기물", "사용후핵연료", "SMR", "소형모듈원자로", "원안위",
    ],
    "비파괴검사": [
        "비파괴", "NDT", "NDE", "초음파탐상", "방사선투과", "자분탐상", "침투탐상",
        "와전류", "누설검사", "음향방출", "가동중검사", "ISI", "PAUT", "TOFD",
    ],
    "에너지": [
        "에너지", "전력", "발전소", "발전설비", "발전운영", "발전기술", "신재생",
        "재생에너지", "태양광", "풍력", "수소", "연료전지", "송전", "배전", "변전",
        "전기안전", "ESS", "지역난방", "천연가스", "LNG", "석유", "열병합",
    ],
}

# (공공기관) 기관명에 이 단어가 있으면 해당 분야로 분류 → 그 기관의 모든 공고 포함
AGENCY_KEYWORDS = {
    "원자력": ["원자력", "핵융합"],
    "비파괴검사": ["비파괴"],
    "에너지": [
        "에너지", "남동발전", "중부발전", "서부발전", "남부발전", "동서발전", "전력", "한전",
        "가스", "석유", "지역난방", "전기안전", "전기연구원", "전력거래소", "광해광업", "석탄공사",
    ],
}

# (공공기관) 정확한 기관명 지정
AGENCY_EXACT = {
    "한국원자력의학원": ["방사선", "원자력"],
    "한국원자력안전기술원": ["원자력", "방사선"],
    "한국원자력안전재단": ["원자력", "방사선"],
    "한국원자력환경공단": ["원자력", "방사선"],
    "한국원자력통제기술원": ["원자력"],
    "한국원자력연구원": ["원자력", "방사선"],
    "한국수력원자력": ["원자력", "에너지"],
    "한국전력기술": ["원자력", "에너지"],
    "한전원자력연료": ["원자력", "에너지"],
    "한국핵융합에너지연구원": ["원자력", "에너지"],
    "한국전력국제원자력대학원대학교": ["원자력"],
}

# 제목에 이 단어가 있으면 애매한 단어로 잘못 잡힌 것으로 보고 제목 키워드 분류에서 제외
FALSE_POSITIVE_TITLE = ["발전기금", "지역발전", "발전방안"]

# (민간기업) 사람인 검색어 — 분야별로 검색 (쉼표로 구분된 단어는 '또는' 조건)
SARAMIN_QUERIES = {
    "방사선": "방사선,방사선안전관리,방사선관리,동위원소,방사선사",
    "원자력": "원자력,원전,원자로,핵연료,방사성폐기물,SMR",
    "비파괴검사": "비파괴,비파괴검사,NDT,초음파탐상,방사선투과,자분탐상,침투탐상,PAUT,와전류탐상",
    "에너지": "발전소,신재생에너지,태양광발전,풍력발전,수소에너지,연료전지,ESS,송배전,전력설비",
}
SARAMIN_COUNT = 110   # 한 번에 최대 110건
SARAMIN_PAGES = 4     # 분야별 최대 4페이지 (440건)

# ---------------------------------------------------------------------------


def clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value if v)
    return re.sub(r"\s+", " ", html.unescape(str(value))).strip()


def fmt_date(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def ts_date(value) -> str:
    try:
        return datetime.fromtimestamp(int(value), KST).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "energy-jobs-board/1.0"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                text = res.read().decode("utf-8")
        except Exception as e:  # 일시적 네트워크 오류는 재시도
            last = e
            time.sleep(3 * (attempt + 1))
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise RuntimeError("JSON이 아닌 응답을 받았습니다(인증키·이용승인 확인 필요): " + text[:200])
    raise RuntimeError(f"연결 실패: {last}")


def data_go_kr_key() -> str:
    key = (os.environ.get("DATA_GO_KR_KEY") or os.environ.get("ALIO_API_KEY") or "").strip()
    return urllib.parse.unquote(key) if "%" in key else key


def http_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (energy-jobs-board)"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            m = re.search(r"<errMsg>([^<]+)|\"errMsg\"\s*:\s*\"([^\"]+)", body)
            raise RuntimeError(f"HTTP {e.code} {(m.group(1) or m.group(2)) if m else body[:150]}")
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"연결 실패: {last}")


def has_word(text: str, w: str) -> bool:
    if w.isascii():  # NDT, ISI 같은 영문 약어는 단어 단위로만 인정
        return re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", text, re.I) is not None
    return w in text


def classify(name: str, text: str, use_agency_rules: bool) -> list[str]:
    cats: set[str] = set()
    if use_agency_rules:
        cats.update(AGENCY_EXACT.get(name, []))
        for cat, words in AGENCY_KEYWORDS.items():
            if any(w in name for w in words):
                cats.add(cat)
    for cat, words in KEYWORDS.items():
        if any(has_word(text, w) for w in words):
            cats.add(cat)
    return [c for c in CATEGORY_ORDER if c in cats]


# ---------------------------------------------------------------------------
# 1) 공공기관 — 잡알리오
# ---------------------------------------------------------------------------
def fetch_alio() -> tuple[list[dict], int]:
    key = data_go_kr_key()
    if not key:
        raise LookupError("ALIO_API_KEY 가 없어 잡알리오 수집을 건너뜁니다.")

    def page(n: int) -> dict:
        q = urllib.parse.urlencode({"serviceKey": key, "pageNo": n, "numOfRows": 100, "ongoingYn": "Y"})
        data = json.loads(http_text(f"{ALIO_URL}?{q}"))
        code = str(data.get("resultCode", ""))
        if code not in ("0", "00", "0000", "200"):
            raise RuntimeError(f"잡알리오 API 오류 (resultCode={code}) {data.get('resultMsg', '')}")
        return data

    def rows(data: dict) -> list[dict]:
        r = data.get("result")
        if isinstance(r, dict):
            r = r.get("item", [])
        if isinstance(r, dict):
            r = [r]
        return [(x.get("item") if isinstance(x.get("item"), dict) else x) for x in (r or []) if isinstance(x, dict)]

    first = page(1)
    total = int(first.get("totalCount") or 0)
    raw = rows(first)
    for n in range(2, min(40, -(-total // 100)) + 1):
        raw.extend(rows(page(n)))
        time.sleep(0.3)

    jobs = []
    for r in raw:
        agency, title, field = clean(r.get("instNm")), clean(r.get("recrutPbancTtl")), clean(r.get("ncsCdNmLst"))
        title_ok = not any(fp in title for fp in FALSE_POSITIVE_TITLE)
        cats = classify(agency, f"{title if title_ok else ''} {field}", use_agency_rules=True)
        if not cats:
            continue
        sn = clean(r.get("recrutPblntSn"))
        jobs.append({
            "id": f"alio-{sn}" if sn else f"alio-{agency}|{title}",
            "sector": "공공기관",
            "agency": agency,
            "field": field or "직무분야 미기재",
            "title": title,
            "start": fmt_date(r.get("pbancBgngYmd")),
            "end": fmt_date(r.get("pbancEndYmd")),
            "endLabel": "",
            "region": clean(r.get("workRgnNmLst")),
            "hireType": clean(r.get("hireTypeNmLst")),
            "categories": cats,
            "source": "잡알리오",
            "url": f"https://job.alio.go.kr/recruitview.do?idx={sn}" if sn.isdigit() else "https://job.alio.go.kr/recruit.do",
        })
    return jobs, len(raw)


# ---------------------------------------------------------------------------
# 2) 공무원·공공기관 — 나라일터
# ---------------------------------------------------------------------------
NARA_TYPE = {"e01": "공개경쟁채용", "e02": "경력경쟁채용", "e03": "계약직", "e04": "행정지원인력",
             "e06": "공모직위", "e07": "전입공모", "g01": "국가공무원", "g02": "지방공무원",
             "g03": "공공기관", "g04": "교육청"}
NARA_SCAN_PAGES = 50       # 최신순으로 최대 2,500건 확인 (50건씩)
NARA_LOOKBACK_DAYS = 120   # 이보다 오래 전에 등록된 공고만 남으면 중단


def nara_call(op: str, **params) -> ET.Element:
    q = urllib.parse.urlencode({"serviceKey": data_go_kr_key(), **params})
    root = ET.fromstring(http_text(f"{NARA_URL}/{op}?{q}"))
    code = (root.findtext("header/resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if code not in ("00", "0"):
        msg = root.findtext("header/resultMsg") or root.findtext(".//errMsg") or ""
        raise RuntimeError(f"나라일터 API 오류 ({code}) {msg}")
    return root


def fetch_nara() -> tuple[list[dict], int]:
    if not data_go_kr_key():
        raise LookupError("ALIO_API_KEY 가 없어 나라일터 수집을 건너뜁니다.")
    base = {"numOfRows": 50, "Pblanc_ty": "", "Instt_se": "", "Sort_order": ""}
    first = nara_call("getList", pageNo=1, **{**base, "numOfRows": 1})
    total = int(first.findtext("body/totalCount") or 0)
    last_page = max(1, -(-total // 50))
    today = datetime.now(KST)
    today_s = today.strftime("%Y%m%d")
    cutoff = (today - timedelta(days=NARA_LOOKBACK_DAYS)).strftime("%Y%m%d")

    raw: list[dict] = []
    for page in range(last_page, max(0, last_page - NARA_SCAN_PAGES), -1):  # 목록이 오래된 순이라 뒤에서부터
        try:
            root = nara_call("getList", pageNo=page, **base)
        except Exception as e:  # 한 페이지가 느려도 나머지는 계속
            print(f"  나라일터 {page}페이지 건너뜀: {e}")
            continue
        items = [{c.tag: (c.text or "").strip() for c in it} for it in root.findall("body/items/item")]
        raw.extend(items)
        if items and max(i.get("regdate", "") for i in items) < cutoff:
            break
        time.sleep(0.2)

    jobs = []
    for r in raw:
        end = r.get("enddate", "")
        if end and end < today_s:
            continue
        agency, title = clean(r.get("insttname")), clean(r.get("title"))
        title_ok = not any(fp in title for fp in FALSE_POSITIVE_TITLE)
        cats = classify(agency, title if title_ok else "", use_agency_rules=True)
        if not cats:
            continue
        idx = r.get("idx", "")
        positions, region = [], ""
        try:  # 채용 직급(직무) 이름과 지역 — 관련 공고에 대해서만 추가 조회
            pos = nara_call("getItemPosition", idx=idx, numOfRows=50, pageNo=1)
            positions = [clean(p.findtext("name")) for p in pos.findall("body/items/item") if p.findtext("name")]
            det = nara_call("getItem", idx=idx)
            region = clean(det.findtext("body/item/areaname"))
        except Exception:
            pass
        kinds = [NARA_TYPE[c] for c in (r.get("type01", ""), r.get("type02", "")) if c in NARA_TYPE]
        field = ", ".join(dict.fromkeys(positions)) or " · ".join(kinds) or "직무분야 미기재"
        # 직급명에도 분야 단어가 있으면 분류에 반영 (예: 공업연구사(원자력))
        cats = [c for c in CATEGORY_ORDER if c in set(cats) | set(classify("", field, use_agency_rules=False))]
        jobs.append({
            "id": f"nara-{idx}",
            "sector": "공공기관",
            "agency": agency,
            "field": field,
            "title": title,
            "start": fmt_date(r.get("regdate")),
            "end": fmt_date(end),
            "endLabel": "",
            "region": "" if region == "전국" else region,
            "hireType": " · ".join(kinds),
            "categories": cats,
            "source": "나라일터",
            "url": f"https://www.gojobs.go.kr/apmView.do?empmnsn={idx}",
        })
        time.sleep(0.1)
    return jobs, len(raw)


# ---------------------------------------------------------------------------
# 3) 민간기업 — 사람인
# ---------------------------------------------------------------------------
def fetch_saramin() -> tuple[list[dict], int]:
    key = os.environ.get("SARAMIN_ACCESS_KEY", "").strip()
    if not key:
        raise LookupError("SARAMIN_ACCESS_KEY 가 없어 민간기업 수집을 건너뜁니다.")

    found: dict[str, dict] = {}
    scanned = 0
    for query_cat, keywords in SARAMIN_QUERIES.items():
        for start in range(SARAMIN_PAGES):
            q = urllib.parse.urlencode({
                "access-key": key, "keywords": keywords, "start": start,
                "count": SARAMIN_COUNT, "sort": "pd", "sr": "directhire",
            })
            data = http_json(f"{SARAMIN_URL}?{q}")
            if "jobs" not in data:
                raise RuntimeError(f"사람인 API 오류 (code={data.get('code')}) {data.get('message', '')}")
            block = data["jobs"]
            items = block.get("job") or []
            if isinstance(items, dict):
                items = [items]
            scanned += len(items)
            for j in items:
                if str(j.get("active", "1")) != "1":
                    continue
                pos = j.get("position") or {}
                company = clean(((j.get("company") or {}).get("detail") or {}).get("name"))
                title = clean(pos.get("title"))
                field = clean((pos.get("job-code") or {}).get("name")) or clean((pos.get("job-mid-code") or {}).get("name"))
                industry = clean((pos.get("industry") or {}).get("name"))
                keyword = clean(j.get("keyword"))
                cats = classify(company, f"{title} {field} {keyword}", use_agency_rules=False)
                if query_cat not in cats:
                    cats = [c for c in CATEGORY_ORDER if c in set(cats) | {query_cat}]
                jid = f"saramin-{j.get('id')}"
                if jid in found:
                    prev = found[jid]
                    prev["categories"] = [c for c in CATEGORY_ORDER if c in set(prev["categories"]) | set(cats)]
                    continue
                close = j.get("close-type") or {}
                close_code = str(close.get("code", "1"))
                found[jid] = {
                    "id": jid,
                    "sector": "민간기업",
                    "agency": company,
                    "field": re.sub(r"\s*,\s*", ", ", field) or industry or "직무분야 미기재",
                    "title": title,
                    "start": ts_date(j.get("opening-timestamp")),
                    "end": ts_date(j.get("expiration-timestamp")) if close_code == "1" else "",
                    "endLabel": "" if close_code == "1" else (clean(close.get("name")) or "채용시"),
                    "region": re.sub(r"\s*,\s*", ", ", re.sub(r"\s*>\s*", " ", clean((pos.get("location") or {}).get("name")))),
                    "hireType": clean((pos.get("job-type") or {}).get("name")),
                    "categories": cats,
                    "source": "사람인",
                    "url": clean(j.get("url")),
                }
            total = int(re.sub(r"\D", "", str(block.get("total") or "0")) or 0)
            if (start + 1) * SARAMIN_COUNT >= total:
                break
            time.sleep(0.5)
    return list(found.values()), scanned


# ---------------------------------------------------------------------------
def load_previous() -> dict:
    try:
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    prev_jobs = load_previous().get("jobs", [])

    status: dict[str, dict] = {}
    collected: list[dict] = []
    ok_count = 0
    for label, sector, fn in (("잡알리오", "공공기관", fetch_alio), ("나라일터", "공공기관", fetch_nara),
                              ("사람인", "민간기업", fetch_saramin)):
        try:
            jobs, scanned = fn()
            collected.extend(jobs)
            status[label] = {"ok": True, "source": label, "count": len(jobs), "scanned": scanned}
            ok_count += 1
            print(f"[성공] {sector}({label}): {scanned}건 확인 → 관련 공고 {len(jobs)}건")
        except LookupError as e:  # 키 미등록 → 건너뜀
            status[label] = {"ok": False, "source": label, "skipped": True, "message": str(e)}
            print(f"[건너뜀] {e}")
        except Exception as e:  # 실패 → 전날 자료 유지
            kept = [j for j in prev_jobs if j.get("source") == label]
            collected.extend(kept)
            status[label] = {"ok": False, "source": label, "message": str(e), "keptFromPrevious": len(kept)}
            print(f"[실패] {sector}({label}): {e} → 전날 자료 {len(kept)}건 유지", file=sys.stderr)

    if ok_count == 0:
        print("어느 출처에서도 수집하지 못했습니다. jobs.json 을 바꾸지 않습니다.", file=sys.stderr)
        return 1

    unique, seen = {}, set()
    for j in collected:
        if j.get("end") and j["end"] < today:  # 마감 지난 공고 제외
            continue
        # 잡알리오와 나라일터에 같은 공고가 함께 올라온 경우 하나만 표시
        sig = (re.sub(r"\W", "", j["agency"]), re.sub(r"\W", "", j["title"]))
        if sig in seen:
            continue
        seen.add(sig)
        unique[j["id"]] = j
    ordered = sorted(unique.values(), key=lambda j: (j["end"] or "9999-99-99", j["agency"], j["title"]))

    payload = {
        "updatedAt": now.isoformat(timespec="minutes"),
        "count": len(ordered),
        "categoryCounts": {c: sum(1 for j in ordered if c in j["categories"]) for c in CATEGORY_ORDER},
        "sectorCounts": {s: sum(1 for j in ordered if j["sector"] == s) for s in ("공공기관", "민간기업")},
        "sources": status,
        "jobs": ordered,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"총 {len(ordered)}건 저장 → {OUT_FILE.relative_to(ROOT)}")
    print("분야별:", payload["categoryCounts"], "/ 구분별:", payload["sectorCounts"])
    # 한 출처라도 실패했으면 GitHub가 알림 메일을 보내도록 실패로 끝냄 (저장은 이미 완료)
    return 0 if all(s["ok"] or s.get("skipped") for s in status.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
