import os
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
                        print("OWWC: 429 — 재시도 생략")
                        _cache["updated_at"] = time.time() - CACHE_TTL + 300
                        return _cache["matches"]
                    if resp.status != 200:
                        print(f"OWWC: HTTP {resp.status}")
                        _cache["updated_at"] = time.time() - CACHE_TTL + 300
                        return _cache["matches"]
                    data = await resp.json()
            matches = [m for raw in data.get("result", []) if (m := _parse_match(raw))]
            print(f"OWWC: {len(matches)}경기 로드")
            _cache = {"matches": matches, "updated_at": time.time()}
            _save_cache()
        except Exception as e:
            print(f"OWWC: 로드 실패: {e}")
            _cache["updated_at"] = time.time() - CACHE_TTL + 300
        return _cache["matches"]


def get_upcoming(matches: list, days: int = 30) -> list:
    now = datetime.now(KST)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= now + timedelta(days=days)]


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 50 <= (m["dt"] - now).total_seconds() / 60 <= 70]


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


_load_cache()
