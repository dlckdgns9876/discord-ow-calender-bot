import os
import time
import json
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

KST      = timezone(timedelta(hours=9))
SOOP_URL = "https://www.sooplive.co.kr/station/owesports"

API_BASE = "https://api.liquipedia.net/api/v3/match"

TOURNAMENTS = [
    "Overwatch Champions Series 2026 - Korea Stage 1 - Regular Season",
    "Overwatch Champions Series 2026 - Korea Stage 1 - Playoffs",
    "Overwatch Champions Series 2026 - Korea Stage 2 - Regular Season",
    "Overwatch Champions Series 2026 - Korea Stage 2 - Playoffs",
]

_BASE      = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_BASE, "owcs_cache.json")
CACHE_TTL  = 3600

_cache: dict      = {"matches": [], "updated_at": 0}
_fetch_lock       = asyncio.Lock()
_logo_cache: dict = {}


def _headers() -> dict:
    key = os.getenv("LIQUIPEDIA_API_KEY", "")
    if not key:
        print("[OWCS] 경고: LIQUIPEDIA_API_KEY 환경변수가 설정되지 않았습니다!")
    else:
        print(f"[OWCS] API 키 확인: 설정됨 ({len(key)}자, ...{key[-6:]})")
    return {"Authorization": f"Apikey {key}", "Accept": "application/json"}


def _load_cache():
    global _cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            matches = []
            for m in data.get("matches", []):
                m["dt"] = datetime.fromisoformat(m["dt"])
                matches.append(m)
            _cache = {"matches": matches, "updated_at": data.get("updated_at", 0)}
            print(f"[OWCS] 디스크 캐시 로드: {len(matches)}경기")
    except Exception as e:
        print(f"[OWCS] 캐시 로드 실패: {e}")


def _save_cache():
    try:
        data = {
            "updated_at": _cache["updated_at"],
            "matches": [
                {**{k: v for k, v in m.items() if k != "dt"}, "dt": m["dt"].isoformat()}
                for m in _cache["matches"]
            ],
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[OWCS] 캐시 저장 실패: {e}")


def _parse_match(raw: dict) -> dict | None:
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
        "label":    raw.get("tournament", "OWCS Korea"),
        "team1":    t1.get("name", "?"),
        "team2":    t2.get("name", "?"),
        "logo1":    t1.get("teamtemplate", {}).get("imagedarkurl", ""),
        "logo2":    t2.get("teamtemplate", {}).get("imagedarkurl", ""),
        "finished": bool(raw.get("finished")),
    }


async def _fetch_tournament(session: aiohttp.ClientSession, tournament: str) -> list:
    params = {"wiki": "overwatch", "conditions": f"[[tournament::{tournament}]]",
              "limit": "100"}
    for attempt in range(2):
        try:
            async with session.get(API_BASE, params=params, headers=_headers(),
                                   timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 429:
                    if attempt == 0:
                        print(f"[OWCS] 429 — URL: {API_BASE}, tournament: {tournament}")
                        await asyncio.sleep(30)
                        continue
                    print(f"[OWCS] 429 재시도 실패: {tournament}")
                    return []
                if resp.status != 200:
                    print(f"[OWCS] HTTP {resp.status}: {tournament}")
                    return []
                data = await resp.json()
                if data.get("error"):
                    print(f"[OWCS] API 오류: {data['error']}")
                    return []
                matches = [m for raw in data.get("result", []) if (m := _parse_match(raw))]
                print(f"[OWCS] {tournament}: {len(matches)}경기")
                return matches
        except Exception as e:
            print(f"[OWCS] {tournament} 로드 실패: {e}")
            return []
    return []


async def fetch_schedules() -> list:
    global _cache
    if time.time() - _cache["updated_at"] < CACHE_TTL:
        return _cache["matches"]
    async with _fetch_lock:
        if time.time() - _cache["updated_at"] < CACHE_TTL:
            return _cache["matches"]
        all_matches = []
        async with aiohttp.ClientSession() as session:
            for i, t in enumerate(TOURNAMENTS):
                if i > 0:
                    await asyncio.sleep(3)
                all_matches.extend(await _fetch_tournament(session, t))
        if not all_matches:
            print("[OWCS] 데이터 없음 — 기존 캐시 유지")
            _cache["updated_at"] = time.time() - CACHE_TTL + 300
            return _cache["matches"]
        seen, unique = set(), []
        for m in all_matches:
            key = (m["dt"].isoformat(), m["team1"], m["team2"])
            if key not in seen:
                seen.add(key)
                unique.append(m)
        _cache = {"matches": sorted(unique, key=lambda x: x["dt"]), "updated_at": time.time()}
        _save_cache()
        return _cache["matches"]


def is_ongoing(m: dict) -> bool:
    now = datetime.now(KST)
    return m["dt"] <= now <= m["dt"] + timedelta(hours=3)


def get_upcoming(matches: list, days: int = 7) -> list:
    now = datetime.now(KST)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= now + timedelta(days=days)]


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 50 <= (m["dt"] - now).total_seconds() / 60 <= 70]


def group_by_day(matches: list) -> dict:
    groups: dict[str, list] = {}
    for m in matches:
        groups.setdefault(m["dt"].strftime("%Y-%m-%d"), []).append(m)
    return dict(sorted(groups.items()))


def match_id(m: dict) -> str:
    return m["dt"].isoformat()


def format_info(m: dict) -> dict:
    ongoing = is_ongoing(m)
    prefix  = "🔴 **ON AIR** " if ongoing else ""
    return {
        "label":   m.get("label", "OWCS"),
        "time":    m["dt"].strftime("%Y-%m-%d %H:%M KST"),
        "matchup": f"{prefix}**{m.get('team1','?')}** vs **{m.get('team2','?')}**",
        "ongoing": ongoing,
        "team1":   m.get("team1", ""),
        "team2":   m.get("team2", ""),
        "logo1":   m.get("logo1", ""),
        "logo2":   m.get("logo2", ""),
    }


_load_cache()
