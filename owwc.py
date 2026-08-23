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
