import os
import re
import time
import json
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

KST        = timezone(timedelta(hours=9))
TOURNAMENT = "Overwatch World Cup 2026"
API_BASE   = "https://api.liquipedia.net/api/v3/match"
_BASE      = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_BASE, "owwc_cache.json")
CACHE_TTL  = 3600

_cache      = {"matches": [], "updated_at": 0}
_fetch_lock = asyncio.Lock()


def _headers():
    key = os.getenv("LIQUIPEDIA_API_KEY", "")
    return {"Authorization": f"Apikey {key}", "Accept": "application/json"}


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
            print(f"OWWC: 디스크 캐시 로드: {len(matches)}경기")
    except Exception as e:
        print(f"OWWC: 캐시 로드 실패: {e}")


def _save_cache():
    try:
        data = {
            "updated_at": _cache["updated_at"],
            "matches": [{**m, "dt": m["dt"].isoformat()} for m in _cache["matches"]],
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"OWWC: 캐시 저장 실패: {e}")


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
        "label":    raw.get("tournament", "OWWC 2026"),
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
        try:
            params = {
                "wiki": "overwatch",
                "conditions": f"[[tournament::{TOURNAMENT}]]",
                "limit": "100",
            }
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
                        print(f"OWWC: 429 — {retry_after}초 후 재시도 예정")
                        _cache["updated_at"] = time.time() + retry_after - CACHE_TTL
                        _save_cache()
                        return _cache["matches"]
                    if resp.status != 200:
                        print(f"OWWC: HTTP {resp.status}")
                        _cache["updated_at"] = time.time()
                        _save_cache()
                        return _cache["matches"]
                    data = await resp.json()
            matches = [m for raw in data.get("result", []) if (m := _parse_match(raw))]
            print(f"OWWC: {len(matches)}경기 로드")
            _cache = {"matches": matches, "updated_at": time.time()}
            _save_cache()
        except Exception as e:
            print(f"OWWC: 로드 실패: {e}")
            _cache["updated_at"] = time.time()
            _save_cache()
        return _cache["matches"]


def get_upcoming(matches: list, days: int = 30) -> list:
    now = datetime.now(KST)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= now + timedelta(days=days)]


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 20 <= (m["dt"] - now).total_seconds() / 60 <= 40]


def is_ongoing(m: dict) -> bool:
    now = datetime.now(KST)
    return m["dt"] <= now <= m["dt"] + timedelta(hours=3)


def group_by_day(matches: list) -> dict:
    groups: dict = {}
    for m in matches:
        groups.setdefault(m["dt"].strftime("%Y-%m-%d"), []).append(m)
    return dict(sorted(groups.items()))


def match_id(m: dict) -> str:
    return f"owwc_{m['dt'].isoformat()}_{m['team1']}_{m['team2']}"


_COUNTRY_CODE = {
    "South Korea": "KOR", "Korea": "KOR",
    "United States": "USA", "United States of America": "USA",
    "Sweden": "SWE", "China": "CHN", "Japan": "JPN",
    "Saudi Arabia": "KSA", "Australia": "AUS",
    "Great Britain": "GBR", "United Kingdom": "GBR",
    "Mexico": "MEX", "Germany": "GER", "Spain": "ESP",
    "Canada": "CAN", "Thailand": "THA", "France": "FRA",
    "Denmark": "DEN", "Colombia": "COL", "Brazil": "BRA",
    "Chinese Taipei": "TPE", "Taiwan": "TWN",
    "Philippines": "PHI", "New Zealand": "NZL",
    "Italy": "ITA", "Portugal": "POR", "Netherlands": "NED",
    "Belgium": "BEL", "Poland": "POL", "Finland": "FIN",
    "Norway": "NOR", "Switzerland": "SUI", "Austria": "AUT",
    "Argentina": "ARG", "Chile": "CHI", "Peru": "PER",
    "Israel": "ISR", "Turkey": "TUR", "Indonesia": "IDN",
    "Singapore": "SGP", "Malaysia": "MAS", "Vietnam": "VIE",
    "Russia": "RUS", "Ukraine": "UKR", "Greece": "GRE",
    "Romania": "ROU", "Hungary": "HUN",
    "United Arab Emirates": "UAE", "Hong Kong": "HKG",
}


# ── OWWC Group Stage Wiki 파싱 ─────────────────────────────────
WIKI_API      = "https://liquipedia.net/overwatch/api.php"
_GS_WIKI_PAGES = [
    "Overwatch_World_Cup/2026/Group_Stage",
    "Overwatch_World_Cup/2026",
]
GS_CACHE_FILE = os.path.join(_BASE, "owwc_group_stage_cache.json")
GS_CACHE_TTL  = 1800   # 경기 중 30분 갱신

_gs_cache = {"groups": [], "updated_at": 0}
_gs_lock  = asyncio.Lock()


def _load_gs_cache():
    global _gs_cache
    try:
        if os.path.exists(GS_CACHE_FILE):
            with open(GS_CACHE_FILE, encoding="utf-8") as f:
                _gs_cache = json.load(f)
            print(f"OWWC GS: 캐시 로드 {len(_gs_cache.get('groups', []))}그룹")
    except Exception:
        pass


def _save_gs_cache():
    try:
        with open(GS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_gs_cache, f, ensure_ascii=False)
    except Exception:
        pass


async def _wiki_fetch(session: aiohttp.ClientSession, page: str) -> str:
    hdrs = {"User-Agent": "DiscordOWCSBot/1.0 (contact: chang431@gmail.com)"}
    try:
        async with session.get(
            WIKI_API,
            params={"action": "parse", "page": page, "prop": "wikitext",
                    "format": "json", "redirects": "1"},
            headers=hdrs, timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status == 429:
                print(f"OWWC GS: 위키 429 ({page})")
                return ""
            if r.status != 200:
                print(f"OWWC GS: 위키 HTTP {r.status} ({page})")
                return ""
            d = await r.json(content_type=None)
            return d.get("parse", {}).get("wikitext", {}).get("*", "")
    except Exception as e:
        print(f"OWWC GS: 위키 오류 {e}")
        return ""


def _strip_tmpl(s: str) -> str:
    """{{Team|name}} 등 위키 템플릿/링크 제거 → 팀 이름만 추출"""
    s = re.sub(r"\{\{[Tt]eam\|([^|}]+)[^}]*\}\}", r"\1", s)
    s = re.sub(r"\{\{[^|{]+\|([^|}]+)[^}]*\}\}", r"\1", s)
    s = re.sub(r"\{\{[^}]+\}\}", "", s)
    s = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", s)
    return s.strip()


def _param_val(block: str, key: str) -> str:
    """block에서 key= 값을 중첩 {{ }} 포함해 추출 (|로 끊기지 않음)"""
    m = re.search(r"\|" + re.escape(key) + r"\s*=\s*", block)
    if not m:
        return ""
    start, depth, i = m.end(), 0, m.end()
    while i < len(block) - 1:
        two = block[i:i+2]
        if two == "{{":
            depth += 1; i += 2
        elif two == "}}":
            if depth == 0:
                break
            depth -= 1; i += 2
        elif block[i] in ("|", "\n") and depth == 0:
            break
        else:
            i += 1
    return block[start:i].strip()


def _parse_single_group(block: str) -> dict | None:
    m = re.search(r"\|title\s*=\s*([^|\n}]+)", block)
    if not m:
        return None
    title = m.group(1).strip()
    if not re.search(r"Group\s+[A-Z]", title, re.IGNORECASE):
        return None

    def _int(key: str) -> int:
        im = re.search(rf"\|{re.escape(key)}\s*=\s*(-?\d+)", block)
        return int(im.group(1)) if im else 0

    teams = []
    for n in range(1, 17):
        pbg_m = re.search(rf"\|pbg{n}\s*=\s*([^|\n}}]+)", block)
        if not pbg_m:
            break
        pbg = pbg_m.group(1).strip().lower()

        raw = _param_val(block, f"p{n}") or _param_val(block, f"team{n}")
        name = _strip_tmpl(raw)
        if not name:
            continue

        w  = _int(f"p{n}win")
        l  = _int(f"p{n}loss")
        mw = _int(f"p{n}score")
        ml = _int(f"p{n}scoreagainst")
        status = "advanced" if "up" in pbg else "eliminated" if "down" in pbg else ""

        teams.append({
            "rank":   n,
            "code":   _COUNTRY_CODE.get(name, name[:3].upper()),
            "name":   name,
            "W":      w,   "L":     l,
            "map_w":  mw,  "map_l": ml,
            "diff":   mw - ml,
            "status": status,
        })

    return {"name": title, "teams": teams} if teams else None


def _parse_group_tables(wikitext: str) -> list:
    """위키텍스트의 GroupTableLeague 블록 전체 파싱"""
    groups = []
    pos = 0
    while True:
        start = wikitext.find("{{GroupTableLeague", pos)
        if start == -1:
            break
        # {{ }} 깊이 추적으로 블록 끝 찾기
        depth, i, end = 0, start, start
        while i < len(wikitext) - 1:
            two = wikitext[i:i+2]
            if two == "{{":
                depth += 1
                i += 2
            elif two == "}}":
                depth -= 1
                i += 2
                if depth == 0:
                    end = i
                    break
            else:
                i += 1
        block = wikitext[start:end]
        pos   = end if end > start else start + 20
        g = _parse_single_group(block)
        if g:
            groups.append(g)
    return sorted(groups, key=lambda g: g["name"])


async def fetch_group_standings() -> list:
    """Liquipedia MediaWiki API로 OWWC Group Stage 순위 파싱 (캐시 30분)"""
    global _gs_cache
    if time.time() - _gs_cache.get("updated_at", 0) < GS_CACHE_TTL:
        return _gs_cache.get("groups", [])

    async with _gs_lock:
        if time.time() - _gs_cache.get("updated_at", 0) < GS_CACHE_TTL:
            return _gs_cache.get("groups", [])

        async with aiohttp.ClientSession() as session:
            wikitext = ""
            for i, page in enumerate(_GS_WIKI_PAGES):
                if i > 0:
                    await asyncio.sleep(3)
                wikitext = await _wiki_fetch(session, page)
                if wikitext and "GroupTableLeague" in wikitext:
                    print(f"OWWC GS: '{page}' 사용")
                    break

        groups = _parse_group_tables(wikitext) if wikitext else []
        print(f"OWWC GS: {len(groups)}개 그룹 파싱")
        _gs_cache = {"groups": groups, "updated_at": time.time()}
        _save_gs_cache()
        return groups


def compute_group_standings(matches: list) -> list:
    """완료된 경기 데이터로 그룹 스탠딩 계산. venue/section에서 'Group X' 추출."""
    groups: dict[str, dict] = {}

    for m in matches:
        if not m.get("finished"):
            continue
        venue = m.get("venue", "")
        gm = re.search(r"Group\s+([A-Z])", venue, re.IGNORECASE)
        if not gm:
            continue
        gname = f"Group {gm.group(1).upper()}"
        if gname not in groups:
            groups[gname] = {}

        t1, t2 = m["team1"], m["team2"]
        s1, s2 = m["score1"], m["score2"]
        for name, won, mw, ml in [(t1, s1 > s2, s1, s2), (t2, s2 > s1, s2, s1)]:
            if name not in groups[gname]:
                groups[gname][name] = {"W": 0, "L": 0, "map_w": 0, "map_l": 0}
            e = groups[gname][name]
            e["W" if won else "L"] += 1
            e["map_w"] += mw
            e["map_l"] += ml

    result = []
    for gname in sorted(groups.keys()):
        teams = sorted(
            groups[gname].items(),
            key=lambda x: (-x[1]["W"], x[1]["L"], -(x[1]["map_w"] - x[1]["map_l"])),
        )
        team_list = []
        for i, (name, s) in enumerate(teams, 1):
            diff = s["map_w"] - s["map_l"]
            team_list.append({
                "rank":   i,
                "code":   _COUNTRY_CODE.get(name, name[:3].upper()),
                "name":   name,
                "W":      s["W"],
                "L":      s["L"],
                "map_w":  s["map_w"],
                "map_l":  s["map_l"],
                "diff":   diff,
                "status": "advanced" if i <= 2 else "eliminated",
            })
        result.append({"name": gname, "teams": team_list})

    return result


_load_cache()
_load_gs_cache()
