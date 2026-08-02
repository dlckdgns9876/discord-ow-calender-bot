import os
import re
import time
import json
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

KST      = timezone(timedelta(hours=9))
CEST     = timezone(timedelta(hours=2))
API_BASE = "https://api.liquipedia.net/api/v3/match"
_BASE    = os.path.dirname(os.path.abspath(__file__))

TOURNAMENTS = [
    "Overwatch Champions Series 2026 - Midseason Championship",
    "Overwatch Champions Series 2026 - Midseason Championship - Group Stage",
    "Overwatch Champions Series 2026 - Midseason Championship - Playoffs",
]

CACHE_FILE           = os.path.join(_BASE, "owcs_international_exhibition_cache.json")
INFO_CACHE_FILE      = os.path.join(_BASE, "owcs_international_exhibition_info_cache.json")
WIKI_MATCH_CACHE_FILE = os.path.join(_BASE, "owcs_international_exhibition_wiki_cache.json")
CACHE_TTL       = 7200   # 2시간 — API 호출 빈도 줄이기
INFO_CACHE_TTL  = 21600  # 6시간 — 대회 정보는 잘 안 바뀜

WIKI_API  = "https://liquipedia.net/overwatch/api.php"
WIKI_PAGE = "Overwatch_Champions_Series/2026/Midseason_Championship"
WIKI_SCHEDULE_PAGES = {
    "Group Stage": "Overwatch_Champions_Series/2026/Midseason_Championship/Group_Stage",
    "Playoffs":    "Overwatch_Champions_Series/2026/Midseason_Championship/Playoffs",
}

_TEAM_NAME_FIXES = {
    # Liquipedia 페이지명과 정확히 일치해야 로고 조회 가능
    "9z team":             "9z Team",
    "virtus.pro":          "Virtus.pro",
    "jd gaming":           "JD Gaming",
    "varrel":              "VARREL",
    "t1":                  "T1",
    "zeta division":       "ZETA DIVISION",
    "team falcons":        "Team Falcons",
    "crazy raccoon":       "Crazy Raccoon",
    "twisted minds":       "Twisted Minds",
    "weibo gaming":        "Weibo Gaming",
    "team liquid":         "Team Liquid",
    "dallas fuel":         "Dallas Fuel",
    "team secret":         "Team Secret",
    "spacestation gaming": "Spacestation Gaming",
    "geekay esports":      "Geekay Esports",
    "all gamers":          "All Gamers",
}

_cache       = {"matches": [], "updated_at": 0}
_info_cache  = {"info": None, "updated_at": 0}
_wiki_cache  = {"matches": [], "updated_at": 0}
_fetch_lock  = asyncio.Lock()


def _headers():
    key = os.getenv("LIQUIPEDIA_API_KEY", "")
    return {"Authorization": f"Apikey {key}", "Accept": "application/json"}


def _wiki_headers():
    return {"User-Agent": "DiscordOWCSBot/1.0 (contact: chang431@gmail.com)"}


def _parse_groups(wikitext: str) -> dict:
    """그룹 스테이지 위키텍스트에서 그룹별 팀 목록 파싱"""
    groups = {}
    current_group = None
    current_teams = []

    for line in wikitext.split('\n'):
        gm = re.match(r'===Group ([A-Z])===', line.strip())
        if gm:
            if current_group is not None:
                groups[f"Group {current_group}"] = current_teams
            current_group = gm.group(1)
            current_teams = []
            continue

        if current_group is None:
            continue

        tm = re.search(r'\|opponent[12]=\{\{TeamOpponent\|([^|}]+)\}\}', line)
        if tm:
            name = _normalize_team(tm.group(1).strip())
            if name and name not in current_teams:
                current_teams.append(name)

    if current_group is not None:
        groups[f"Group {current_group}"] = current_teams

    return groups


def _parse_info(wikitext: str) -> dict:
    def field(name):
        m = re.search(rf"^\|{name}\s*=\s*(.+)$", wikitext, re.MULTILINE)
        return m.group(1).strip() if m else ""

    sdate = field("sdate")   # 2026-07-29
    edate = field("edate")   # 2026-08-02
    prize_raw = field("prizepoolusd")
    prize = f"${int(prize_raw):,} USD" if prize_raw.isdigit() else prize_raw

    venue   = field("venue")
    city    = field("city")
    country = field("country")
    location = ", ".join(filter(None, [venue, city, country]))

    team_number = field("team_number")

    # 참가팀: {{Opponent|TeamName 또는 {{TeamOpponent|TeamName
    teams = re.findall(r"\{\{(?:Team)?Opponent\|([^|}>\n]+)", wikitext)
    teams = [t.strip() for t in teams if t.strip() and "TBD" not in t]
    # 중복 제거, 순서 유지
    seen = set()
    unique_teams = [t for t in teams if not (t in seen or seen.add(t))]

    return {
        "name":        field("name") or "OWCS 2026 Midseason Championship",
        "sdate":       sdate,
        "edate":       edate,
        "location":    location,
        "prize":       prize,
        "team_number": team_number,
        "teams":       unique_teams,
    }


async def fetch_tournament_info() -> dict | None:
    global _info_cache
    if time.time() - _info_cache["updated_at"] < INFO_CACHE_TTL and _info_cache["info"]:
        return _info_cache["info"]

    # 디스크 캐시 확인 (groups 키가 있어야 유효한 캐시로 인정)
    try:
        if os.path.exists(INFO_CACHE_FILE):
            with open(INFO_CACHE_FILE, encoding="utf-8") as f:
                saved = json.load(f)
            if (time.time() - saved.get("updated_at", 0) < INFO_CACHE_TTL
                    and saved.get("info", {}).get("groups") is not None):
                _info_cache = saved
                return _info_cache["info"]
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as session:
            # 1) 메인 토너먼트 페이지
            params = {"action": "parse", "page": WIKI_PAGE, "prop": "wikitext", "format": "json"}
            async with session.get(
                WIKI_API, params=params, headers=_wiki_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    print("MSC 정보: 위키 429 — 캐시 반환")
                    return _info_cache.get("info")
                if resp.status != 200:
                    print(f"MSC 정보: 위키 HTTP {resp.status}")
                    return _info_cache.get("info")
                data = await resp.json()
            wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
            info = _parse_info(wikitext)

            # 2) Group Stage 페이지 → 그룹별 팀 파싱
            gs_params = {
                "action": "parse",
                "page": WIKI_SCHEDULE_PAGES["Group Stage"],
                "prop": "wikitext",
                "format": "json",
            }
            async with session.get(
                WIKI_API, params=gs_params, headers=_wiki_headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as gs_resp:
                if gs_resp.status == 200:
                    gs_data = await gs_resp.json()
                    gs_wikitext = gs_data.get("parse", {}).get("wikitext", {}).get("*", "")
                    info["groups"] = _parse_groups(gs_wikitext)
                    print(f"MSC 그룹: {list(info['groups'].keys())} 파싱 완료")
                else:
                    print(f"MSC 그룹: HTTP {gs_resp.status}")
                    info["groups"] = {}

        _info_cache = {"info": info, "updated_at": time.time()}
        with open(INFO_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_info_cache, f, ensure_ascii=False)
        print(f"MSC 정보: 위키 파싱 완료 (팀 {len(info['teams'])}개)")
        return info
    except Exception as e:
        print(f"MSC 정보: 파싱 실패: {e}")
        return _info_cache.get("info")


def _load_cache():
    global _cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            matches = []
            for m in data.get("matches", []):
                m["dt"] = datetime.fromisoformat(m["dt"])
                matches.append(m)
            _cache = {"matches": matches, "updated_at": data.get("updated_at", 0)}
            print(f"MSC: 디스크 캐시 로드: {len(matches)}경기")
    except Exception as e:
        print(f"MSC: 캐시 로드 실패: {e}")


def _save_cache():
    try:
        data = {
            "updated_at": _cache["updated_at"],
            "matches": [{**m, "dt": m["dt"].isoformat()} for m in _cache["matches"]],
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"MSC: 캐시 저장 실패: {e}")


def _parse_match(raw):
    opponents = raw.get("match2opponents", [])
    if len(opponents) < 2:
        return None
    date_str = raw.get("date", "")
    if not date_str or date_str.startswith("0000"):
        return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).astimezone(KST)
    except ValueError:
        return None
    t1, t2 = opponents[0], opponents[1]
    return {
        "dt":       dt,
        "label":    raw.get("tournament", "MSC 2026"),
        "team1":    t1.get("name") or "TBD",
        "team2":    t2.get("name") or "TBD",
        "score1":   max(int(t1.get("score") or 0), 0),
        "score2":   max(int(t2.get("score") or 0), 0),
        "logo1":    t1.get("teamtemplate", {}).get("imageurl", ""),
        "logo2":    t2.get("teamtemplate", {}).get("imageurl", ""),
        "finished": bool(raw.get("finished")),
        "venue":    raw.get("section", ""),
    }


async def fetch_matches() -> list:
    global _cache
    if time.time() - _cache["updated_at"] < CACHE_TTL:
        return _cache["matches"]
    async with _fetch_lock:
        if time.time() - _cache["updated_at"] < CACHE_TTL:
            return _cache["matches"]
        conditions = " OR ".join(f"[[tournament::{t}]]" for t in TOURNAMENTS)
        try:
            params = {"wiki": "overwatch", "conditions": conditions, "limit": "200"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    API_BASE, params=params, headers=_headers(),
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 429:
                        try:
                            retry_after = int(resp.headers.get("Retry-After", CACHE_TTL))
                        except (ValueError, TypeError):
                            retry_after = CACHE_TTL
                        print(f"MSC: 429 — {retry_after}초 후 재시도 예정")
                        _cache["updated_at"] = time.time() + retry_after - CACHE_TTL
                        _save_cache()
                        return _cache["matches"]
                    if resp.status != 200:
                        print(f"MSC: HTTP {resp.status}")
                        _cache["updated_at"] = time.time()
                        _save_cache()
                        return _cache["matches"]
                    data = await resp.json()
            matches = [m for raw in data.get("result", []) if (m := _parse_match(raw))]
            print(f"MSC: {len(matches)}경기 로드")
            _cache = {"matches": matches, "updated_at": time.time()}
            _save_cache()
        except Exception as e:
            print(f"MSC: 로드 실패: {e}")
            _cache["updated_at"] = time.time()
            _save_cache()
        return _cache["matches"]


def get_upcoming(matches: list, days: int = 14) -> list:
    now = datetime.now(KST)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= now + timedelta(days=days)]


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 15 <= (m["dt"] - now).total_seconds() / 60 <= 45]


def match_id(m: dict) -> str:
    return f"msc_{m['dt'].isoformat()}_{m['team1']}_{m['team2']}"


def is_ongoing(m: dict) -> bool:
    now = datetime.now(KST)
    return m["dt"] <= now <= m["dt"] + timedelta(hours=3)


def group_by_day(matches: list) -> dict:
    groups: dict = {}
    for m in matches:
        groups.setdefault(m["dt"].strftime("%Y-%m-%d"), []).append(m)
    return dict(sorted(groups.items()))


def _normalize_team(name: str) -> str:
    lower = name.lower().strip()
    return _TEAM_NAME_FIXES.get(lower, name.title())


def _parse_msc_wikitext(wikitext: str, label: str) -> list:
    matches = []
    lines = wikitext.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        dm = re.search(r'\|date=(\w+ \d+, \d{4})\s*-\s*(\d{2}:\d{2})\s*\{\{Abbr/(\w+)\}\}', line)
        if dm:
            try:
                tz_abbr = dm.group(3).upper()
                TZ_MAP = {"CEST": CEST, "CET": timezone(timedelta(hours=1)), "UTC": timezone.utc, "KST": KST}
                tz = TZ_MAP.get(tz_abbr, CEST)
                dt = datetime.strptime(
                    f"{dm.group(1)} {dm.group(2)}", "%B %d, %Y %H:%M"
                ).replace(tzinfo=tz).astimezone(KST)
            except ValueError:
                i += 1
                continue
            t1 = t2 = ""
            map_winners = []
            for j in range(i + 1, min(i + 40, len(lines))):
                l = lines[j]
                m1 = re.search(r'\|opponent1=\{\{1?TeamOpponent\|([^|}]+)\}\}', l)
                m2 = re.search(r'\|opponent2=\{\{1?TeamOpponent\|([^|}]+)\}\}', l)
                wm = re.search(r'\|winner=([12])\b', l)
                if m1: t1 = _normalize_team(m1.group(1))
                if m2: t2 = _normalize_team(m2.group(1))
                if wm: map_winners.append(int(wm.group(1)))
                if l.strip() == '}}' and t1 and t2:
                    break
            if t1 and t2:
                s1 = sum(1 for w in map_winners if w == 1)
                s2 = sum(1 for w in map_winners if w == 2)
                matches.append({
                    "dt": dt, "label": f"MSC 2026 {label}",
                    "team1": t1, "team2": t2,
                    "score1": s1, "score2": s2,
                    "logo1": "", "logo2": "",
                    "finished": bool(map_winners),
                    "venue": label,
                })
        i += 1
    return matches


def _save_wiki_cache():
    try:
        data = {
            "updated_at": _wiki_cache["updated_at"],
            "matches": [{**m, "dt": m["dt"].isoformat()} for m in _wiki_cache["matches"]],
        }
        with open(WIKI_MATCH_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"MSC: 위키 경기 캐시 저장 실패: {e}")


def _load_wiki_match_cache():
    global _wiki_cache
    try:
        if os.path.exists(WIKI_MATCH_CACHE_FILE):
            with open(WIKI_MATCH_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            matches = []
            for m in data.get("matches", []):
                m["dt"] = datetime.fromisoformat(m["dt"])
                matches.append(m)
            _wiki_cache = {"matches": matches, "updated_at": data.get("updated_at", 0)}
            print(f"MSC: 위키 경기 캐시 로드: {len(matches)}경기")
    except Exception as e:
        print(f"MSC: 위키 경기 캐시 로드 실패: {e}")


async def fetch_page_schedule() -> list:
    global _wiki_cache
    if time.time() - _wiki_cache["updated_at"] < CACHE_TTL:
        return _wiki_cache["matches"]
    all_matches = []
    for label, page in WIKI_SCHEDULE_PAGES.items():
        try:
            params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json"}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    WIKI_API, params=params, headers=_wiki_headers(),
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 404:
                        continue
                    if resp.status != 200:
                        print(f"MSC: 위키 {label} HTTP {resp.status}")
                        continue
                    data = await resp.json()
            wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
            parsed = _parse_msc_wikitext(wikitext, label)
            print(f"MSC: 위키 {label} {len(parsed)}경기 파싱")
            all_matches.extend(parsed)
        except Exception as e:
            print(f"MSC: 위키 {label} 파싱 실패: {e}")
    if all_matches:
        unique = list({(m["dt"].isoformat(), m["team1"], m["team2"]): m for m in all_matches}.values())
        _wiki_cache = {"matches": sorted(unique, key=lambda x: x["dt"]), "updated_at": time.time()}
    else:
        _wiki_cache["updated_at"] = time.time()
    _save_wiki_cache()
    return _wiki_cache["matches"]


_load_cache()
_load_wiki_match_cache()
