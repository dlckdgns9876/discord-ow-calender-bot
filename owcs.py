import aiohttp
from datetime import datetime, timezone, timedelta

SCHEDULE_URL = "https://godgameow.com/api/schedules/calendar"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://godgameow.com/",
}
KST = timezone(timedelta(hours=9))

STAGE_KO = {
    "PRE_SEASON": "프리시즌",
    "STAGE_1": "스테이지 1",
    "STAGE_2": "스테이지 2",
    "STAGE_3": "스테이지 3",
    "PLAYOFF": "플레이오프",
    "GRAND_FINAL": "그랜드 파이널",
}
PHASE_KO = {
    "REGULAR": "정규 시즌",
    "BOOTCAMP": "부트캠프",
    "PLAYOFF": "플레이오프",
    "FINAL": "파이널",
    "GRAND_FINAL": "그랜드 파이널",
}


async def fetch_schedules() -> list:
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(SCHEDULE_URL, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            return data.get("data", [])


def parse_dt(utc_str: str) -> datetime:
    return datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(KST)


def get_upcoming(schedules: list, days: int = 7) -> list:
    now = datetime.now(KST)
    cutoff = now + timedelta(days=days)
    result = []
    for s in schedules:
        if not s.get("matches"):
            continue
        dt = parse_dt(s["matchDateTime"])
        if now.date() <= dt.date() <= cutoff.date():
            result.append(s)
    return sorted(result, key=lambda x: x["matchDateTime"])


def get_notify_targets(schedules: list) -> list:
    """시작 50~70분 전 경기 반환 (10분 폴링 기준 안전 범위)"""
    now = datetime.now(KST)
    result = []
    for s in schedules:
        if not s.get("matches"):
            continue
        dt = parse_dt(s["matchDateTime"])
        diff = (dt - now).total_seconds() / 60
        if 50 <= diff <= 70:
            result.append(s)
    return result


def format_info(schedule: dict) -> dict:
    dt = parse_dt(schedule["matchDateTime"])
    stage = STAGE_KO.get(schedule.get("stageNameEn", ""), schedule.get("stageNameEn", ""))
    phase = PHASE_KO.get(schedule.get("phaseNameAbbr", ""), schedule.get("phaseNameAbbr", ""))
    matchups = "\n".join(
        f"**{m['homeTeamNameAbbr']}** vs **{m['awayTeamNameAbbr']}**"
        for m in schedule.get("matches", [])
    )
    return {
        "stage": f"{stage} — {phase}" if stage and phase else (stage or phase),
        "time": dt.strftime("%Y-%m-%d %H:%M KST"),
        "matchups": matchups or "경기 정보 없음",
        "venue": schedule.get("matchVenue", ""),
        "video_url": schedule.get("videoUrl", ""),
    }
