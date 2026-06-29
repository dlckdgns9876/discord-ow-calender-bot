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
        print("OWCS: 경고: LIQUIPEDIA_API_KEY 환경변수가 설정되지 않았습니다!")
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
            print(f"OWCS: 디스크 캐시 로드: {len(matches)}경기")
    except Exception as e:
        print(f"OWCS: 캐시 로드 실패: {e}")


def _save_cache():
    try:
        data = {
            "updated_at": _cache["updated_at"],
            "matches": [
                {**m, "dt": m["dt"].isoformat()}
                for m in _cache["matches"]
            ],
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"OWCS: 캐시 저장 실패: {e}")


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
        "score1":   int(t1.get("score") or 0),
        "score2":   int(t2.get("score") or 0),
        "logo1":    t1.get("teamtemplate", {}).get("imageurl", ""),
        "logo2":    t2.get("teamtemplate", {}).get("imageurl", ""),
        "finished": bool(raw.get("finished")),
    }


async def fetch_schedules() -> list:
    global _cache
    if time.time() - _cache["updated_at"] < CACHE_TTL:
        return _cache["matches"]
    async with _fetch_lock:
        if time.time() - _cache["updated_at"] < CACHE_TTL:
            return _cache["matches"]
        conditions = " OR ".join(f"[[tournament::{t}]]" for t in TOURNAMENTS)
        params = {"wiki": "overwatch", "conditions": conditions, "limit": "500"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(API_BASE, params=params, headers=_headers(),
                                       timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        print("OWCS: 429 — 한도 초과, 다음 갱신 주기까지 대기")
                        _cache["updated_at"] = time.time()
                        _save_cache()
                        return _cache["matches"]
                    if resp.status != 200:
                        print(f"OWCS: HTTP {resp.status}")
                        _cache["updated_at"] = time.time()
                        _save_cache()
                        return _cache["matches"]
                    data = await resp.json()
            if data.get("error"):
                print(f"OWCS: API 오류: {data['error']}")
                _cache["updated_at"] = time.time()
                _save_cache()
                return _cache["matches"]
            all_matches = [m for raw in data.get("result", []) if (m := _parse_match(raw))]
            print(f"OWCS: {len(all_matches)}경기 로드")
        except Exception as e:
            print(f"OWCS: 로드 실패: {e}")
            _cache["updated_at"] = time.time()
            _save_cache()
            return _cache["matches"]
        if not all_matches:
            print("OWCS: 데이터 없음 — 기존 캐시 유지 (1시간 후 재시도)")
            _cache["updated_at"] = time.time()
            _save_cache()
            return _cache["matches"]
        unique = list({(m["dt"].isoformat(), m["team1"], m["team2"]): m for m in all_matches}.values())
        _cache = {"matches": sorted(unique, key=lambda x: x["dt"]), "updated_at": time.time()}
        _save_cache()
        return _cache["matches"]


STANDINGS_TOURNAMENT = "Overwatch Champions Series 2026 - Korea Stage 2 - Regular Season"


def fetch_standings() -> list:
    """캐시된 경기 데이터로 순위 계산 (API 호출 없음)"""
    matches = _cache.get("matches", [])
    target_matches = [
        m for m in matches
        if m.get("label") == STANDINGS_TOURNAMENT and m.get("finished")
    ]

    table: dict[str, dict] = {}
    for m in target_matches:
        s1, s2 = m.get("score1"), m.get("score2")
        if s1 is None or s2 is None or s1 < 0 or s2 < 0:
            continue
        for name, score_for, score_against, logo in [
            (m.get("team1",""), s1, s2, m.get("logo1","")),
            (m.get("team2",""), s2, s1, m.get("logo2","")),
        ]:
            if not name:
                continue
            if name not in table:
                table[name] = {"W": 0, "L": 0, "diff": 0, "logo": logo}
            entry = table[name]
            if score_for > score_against:
                entry["W"] += 1
            else:
                entry["L"] += 1
            entry["diff"] += score_for - score_against

    sorted_teams = sorted(
        table.items(),
        key=lambda x: (-x[1]["W"], x[1]["L"], -x[1]["diff"])
    )

    standings = []
    rank = 1
    for i, (name, stats) in enumerate(sorted_teams):
        if i > 0:
            prev = sorted_teams[i - 1][1]
            # W, L, diff 모두 같아야 동일 순위 (득실까지 반영)
            if (stats["W"] != prev["W"]
                    or stats["L"] != prev["L"]
                    or stats["diff"] != prev["diff"]):
                rank = i + 1
        standings.append({
            "rank": rank,
            "team": name,
            "W":    stats["W"],
            "L":    stats["L"],
            "diff": stats["diff"],
            "logo": stats["logo"],
        })
    return standings


def get_week_last_matches(matches: list) -> list:
    """각 주차의 마지막 경기 목록 반환 (주차 = 5일 이상 간격으로 구분)"""
    if not matches:
        return []
    sorted_m = sorted(matches, key=lambda x: x["dt"])
    weeks, current = [], [sorted_m[0]]
    for m in sorted_m[1:]:
        if (m["dt"] - current[-1]["dt"]).days >= 5:
            weeks.append(current)
            current = [m]
        else:
            current.append(m)
    if current:
        weeks.append(current)
    return [max(week, key=lambda x: x["dt"]) for week in weeks]


def is_week_just_ended(last_match: dict, tolerance_min: int = 30) -> bool:
    """주차 마지막 경기가 방금 끝났는지 (종료 후 tolerance_min 이내)"""
    now    = datetime.now(KST)
    end_dt = last_match["dt"] + timedelta(hours=3)
    diff   = (now - end_dt).total_seconds() / 60
    return 0 <= diff <= tolerance_min


def is_ongoing(m: dict) -> bool:
    now = datetime.now(KST)
    return m["dt"] <= now <= m["dt"] + timedelta(hours=3)


def get_upcoming(matches: list, days: int = 7) -> list:
    now = datetime.now(KST)
    return [m for m in matches if now - timedelta(hours=3) <= m["dt"] <= now + timedelta(days=days)]


def get_notify_targets(matches: list) -> list:
    now = datetime.now(KST)
    return [m for m in matches if 45 <= (m["dt"] - now).total_seconds() / 60 <= 75]


def group_by_day(matches: list) -> dict:
    groups: dict[str, list] = {}
    for m in matches:
        groups.setdefault(m["dt"].strftime("%Y-%m-%d"), []).append(m)
    return dict(sorted(groups.items()))


def match_id(m: dict) -> str:
    return m["dt"].isoformat()


def format_info(m: dict) -> dict:
    prefix = "🔴 **ON AIR** " if is_ongoing(m) else ""
    return {
        "label":   m.get("label", "OWCS"),
        "time":    m["dt"].strftime("%Y-%m-%d %H:%M KST"),
        "matchup": f"{prefix}**{m.get('team1','?')}** vs **{m.get('team2','?')}**",
    }


_load_cache()
