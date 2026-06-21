import re
import time
import aiohttp
from datetime import datetime, timezone, timedelta

LIQUIPEDIA_API = "https://liquipedia.net/overwatch/api.php"
KST = timezone(timedelta(hours=9))

# Liquipedia 이용약관 준수: User-Agent에 연락처 명시
HEADERS = {
    "User-Agent": "DiscordOWCSBot/1.0 (personal Discord bot; contact: chang431@gmail.com)",
    "Accept-Encoding": "gzip",
}

# 시즌별 페이지 목록 (스테이지 추가될 때 여기에 추가)
TOURNAMENT_PAGES = [
    ("OWCS Korea ST1 정규시즌", "Overwatch_Champions_Series/2026/Asia/Stage_1/Korea/Regular_Season"),
    ("OWCS Korea ST1 플레이오프", "Overwatch_Champions_Series/2026/Asia/Stage_1/Korea"),
    ("OWCS Korea ST2",           "Overwatch_Champions_Series/2026/Asia/Stage_2/Korea"),
]

# 캐시: 1시간마다 갱신 (Liquipedia 부하 최소화)
_cache: dict = {"matches": [], "updated_at": 0}
CACHE_TTL = 3600


async def _fetch_wikitext(page: str) -> str:
    params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            LIQUIPEDIA_API, params=params, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)
            return data.get("parse", {}).get("wikitext", {}).get("*", "")


def _parse_matches(wikitext: str, label: str) -> list:
    """위키텍스트 → 경기 리스트"""
    matches = []
    current: dict = {}

    for line in wikitext.split("\n"):
        line = line.strip()

        # 날짜 파싱: |date=2026-03-20 - 17:30 {{Abbr/KST}}
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

        # 팀1
        op1 = re.match(r"\|opponent1=\{\{TeamOpponent\|([^|}\n]+)", line)
        if op1 and "dt" in current:
            current["team1"] = op1.group(1).strip()
            continue

        # 팀2
        op2 = re.match(r"\|opponent2=\{\{TeamOpponent\|([^|}\n]+)", line)
        if op2 and "dt" in current:
            current["team2"] = op2.group(1).strip()

    if current.get("dt") and current.get("team1") and current.get("team2"):
        matches.append(current)

    return matches


async def fetch_schedules() -> list:
    """캐시된 경기 목록 반환 (TTL 1시간)"""
    global _cache
    if time.time() - _cache["updated_at"] < CACHE_TTL:
        return _cache["matches"]

    all_matches = []
    for label, page in TOURNAMENT_PAGES:
        try:
            wikitext = await _fetch_wikitext(page)
            if wikitext:
                all_matches.extend(_parse_matches(wikitext, label))
        except Exception as e:
            print(f"[OWCS] {label} 로드 실패: {e}")

    # 중복 제거 (같은 dt+team 조합)
    seen = set()
    unique = []
    for m in all_matches:
        key = (m["dt"].isoformat(), m.get("team1"), m.get("team2"))
        if key not in seen:
            seen.add(key)
            unique.append(m)

    _cache = {"matches": sorted(unique, key=lambda x: x["dt"]), "updated_at": time.time()}
    return _cache["matches"]


def get_upcoming(matches: list, days: int = 7) -> list:
    now = datetime.now(KST)
    cutoff = now + timedelta(days=days)
    return [m for m in matches if now <= m["dt"] <= cutoff]


def get_notify_targets(matches: list) -> list:
    """시작 50~70분 전 경기 반환"""
    now = datetime.now(KST)
    result = []
    for m in matches:
        diff = (m["dt"] - now).total_seconds() / 60
        if 50 <= diff <= 70:
            result.append(m)
    return result


def match_id(m: dict) -> str:
    return m["dt"].isoformat()


def format_info(m: dict) -> dict:
    return {
        "label": m.get("label", "OWCS"),
        "time": m["dt"].strftime("%Y-%m-%d %H:%M KST"),
        "matchup": f"**{m.get('team1', '?')}** vs **{m.get('team2', '?')}**",
    }
