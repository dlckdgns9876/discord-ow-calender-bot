import re
import time
import gzip
import json
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

LIQUIPEDIA_API = "https://liquipedia.net/overwatch/api.php"
KST = timezone(timedelta(hours=9))
SOOP_URL = "https://www.sooplive.co.kr/station/owesports"

HEADERS = {
    "User-Agent": "DiscordOWCSBot/1.0 (personal Discord bot; contact: chang431@gmail.com)",
    "Accept-Encoding": "gzip",
}

TOURNAMENT_PAGES = [
    ("OWCS Korea ST1 정규시즌",  "Overwatch_Champions_Series/2026/Asia/Stage_1/Korea/Regular_Season"),
    ("OWCS Korea ST1 플레이오프", "Overwatch_Champions_Series/2026/Asia/Stage_1/Korea"),
    ("OWCS Korea ST2 정규시즌",  "Overwatch_Champions_Series/2026/Asia/Stage_2/Korea/Regular_Season"),
    ("OWCS Korea ST2 플레이오프", "Overwatch_Champions_Series/2026/Asia/Stage_2/Korea"),
]

_cache: dict = {"matches": [], "updated_at": 0}
CACHE_TTL  = 3600
_fetch_lock = asyncio.Lock()

_logo_cache: dict[str, str | None] = {}


async def _get_json(session: aiohttp.ClientSession, url: str, params: dict) -> dict:
    """gzip 압축 응답을 수동으로 해제하여 JSON 반환 (429 시 30초 대기 후 재시도)"""
    for attempt in range(2):
        async with session.get(url, params=params, headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 429:
                if attempt == 0:
                    print(f"[OWCS] 429 Rate Limited — 30초 대기 후 재시도")
                    await asyncio.sleep(30)
                    continue
                print(f"[OWCS] 429 재시도도 실패")
                return {}
            if resp.status != 200:
                print(f"[OWCS] HTTP {resp.status}: {url}")
                return {}
            raw = await resp.read()
            try:
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
            except Exception as e:
                print(f"[OWCS] 파싱 실패: {e} (len={len(raw)})")
                return {}
    return {}


async def _fetch_wikitext(page: str) -> str:
    params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json"}
    async with aiohttp.ClientSession() as session:
        data = await _get_json(session, LIQUIPEDIA_API, params)
        return data.get("parse", {}).get("wikitext", {}).get("*", "")


async def fetch_team_logo(team_name: str) -> str | None:
    """팀 로고 URL 반환 (Liquipedia pageimages, 캐싱)"""
    if team_name in _logo_cache:
        return _logo_cache[team_name]
    try:
        params = {
            "action": "query",
            "titles": team_name,
            "prop": "pageimages",
            "pithumbsize": 64,
            "format": "json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                LIQUIPEDIA_API, params=params, headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json(content_type=None)
                pages = data.get("query", {}).get("pages", {})
                url = None
                for page in pages.values():
                    url = page.get("thumbnail", {}).get("source")
                    if url:
                        break
                _logo_cache[team_name] = url
                return url
    except Exception:
        _logo_cache[team_name] = None
        return None


def _parse_matches(wikitext: str, label: str) -> list:
    matches = []
    current: dict = {}

    for line in wikitext.split("\n"):
        line = line.strip()

        m = re.match(
            r"\|date=(\d{4}-\d{2}-\d{2})\s*-\s*(\d{1,2}:\d{2})\s*\{\{Abbr/KST\}\}", line
        )
        if m:
            if current.get("dt") and current.get("team1") and current.get("team2"):
                matches.append(dict(current))
            dt = datetime.strptime(
                f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=KST)
            current = {"dt": dt, "label": label}
            continue

        op1 = re.match(r"\|opponent1=\{\{TeamOpponent\|([^|}\n]+)", line)
        if op1 and "dt" in current:
            current["team1"] = op1.group(1).strip()
            continue

        op2 = re.match(r"\|opponent2=\{\{TeamOpponent\|([^|}\n]+)", line)
        if op2 and "dt" in current:
            current["team2"] = op2.group(1).strip()

    if current.get("dt") and current.get("team1") and current.get("team2"):
        matches.append(current)

    return matches


async def fetch_schedules() -> list:
    global _cache
    if time.time() - _cache["updated_at"] < CACHE_TTL:
        return _cache["matches"]

    async with _fetch_lock:
        if time.time() - _cache["updated_at"] < CACHE_TTL:
            return _cache["matches"]

        all_matches = []
        for i, (label, page) in enumerate(TOURNAMENT_PAGES):
            if i > 0:
                await asyncio.sleep(5)
            try:
                wikitext = await _fetch_wikitext(page)
                if wikitext:
                    all_matches.extend(_parse_matches(wikitext, label))
            except Exception as e:
                print(f"[OWCS] {label} 로드 실패: {e}")

        if not all_matches:
            print("[OWCS] 데이터 없음 — 기존 캐시 유지")
            _cache["updated_at"] = time.time() - CACHE_TTL + 300
            return _cache["matches"]

        seen = set()
        unique = []
        for m in all_matches:
            key = (m["dt"].isoformat(), m.get("team1"), m.get("team2"))
            if key not in seen:
                seen.add(key)
                unique.append(m)

        _cache = {"matches": sorted(unique, key=lambda x: x["dt"]), "updated_at": time.time()}
        return _cache["matches"]


def is_ongoing(m: dict) -> bool:
    """경기 시작 후 3시간 이내이면 진행 중으로 판단"""
    now = datetime.now(KST)
    return m["dt"] <= now <= m["dt"] + timedelta(hours=3)


def get_upcoming(matches: list, days: int = 7) -> list:
    now = datetime.now(KST)
    cutoff = now + timedelta(days=days)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= cutoff]


def group_by_day(matches: list) -> dict:
    """날짜 문자열(YYYY-MM-DD) → 경기 리스트 딕셔너리"""
    groups: dict[str, list] = {}
    for m in matches:
        key = m["dt"].strftime("%Y-%m-%d")
        groups.setdefault(key, []).append(m)
    return dict(sorted(groups.items()))


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 50 <= (m["dt"] - now).total_seconds() / 60 <= 70]


def match_id(m: dict) -> str:
    return m["dt"].isoformat()


def format_info(m: dict) -> dict:
    ongoing = is_ongoing(m)
    prefix = "🔴 **ON AIR** " if ongoing else ""
    return {
        "label": m.get("label", "OWCS"),
        "time": m["dt"].strftime("%Y-%m-%d %H:%M KST"),
        "matchup": f"{prefix}**{m.get('team1', '?')}** vs **{m.get('team2', '?')}**",
        "ongoing": ongoing,
        "team1": m.get("team1", ""),
        "team2": m.get("team2", ""),
    }
