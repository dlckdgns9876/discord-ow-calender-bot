import io
import os
import re
import time
import asyncio
import json
import aiohttp
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont

_BASE = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD    = os.path.join(_BASE, "fonts", "malgunbd.ttf")
FONT_REGULAR = os.path.join(_BASE, "fonts", "malgun.ttf")

LIQUIPEDIA_API = "https://liquipedia.net/overwatch/api.php"
HEADERS = {
    "User-Agent": "DiscordOWCSBot/1.0 (personal Discord bot; contact: chang431@gmail.com)",
    "Accept-Encoding": "gzip",
}

IMG_W    = 960
PAD      = 36
HEADER_H = 110
ROW_H    = 120
LOGO_SZ  = 64

BG        = (255, 255, 255)
CARD_ODD  = (255, 255, 255)
CARD_EVEN = (245, 247, 252)
LINE      = (218, 222, 232)
TEXT      = (20,  25,  45)
GRAY      = (110, 118, 140)
ACCENT    = (220, 90,  0)
ON_AIR    = (200, 30,  30)

_logo_cache: dict[str, str | None] = {}   # team → URL (None = 없음, 미등록 = 미조회)
_wiki_last_req: float = 0.0               # 마지막 위키 API 호출 시각 (rate limit용)
_WIKI_INTERVAL = 2.5                      # 최소 호출 간격 (초)
_LOGO_URL_CACHE_FILE = os.path.join(_BASE, "logo_url_cache.json")


def _load_logo_url_cache():
    try:
        if os.path.exists(_LOGO_URL_CACHE_FILE):
            with open(_LOGO_URL_CACHE_FILE, encoding="utf-8") as f:
                _logo_cache.update(json.load(f))
            print(f"[로고 URL 캐시] {len(_logo_cache)}개 로드")
    except Exception:
        pass


def _save_logo_url_cache():
    try:
        with open(_LOGO_URL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_logo_cache, f, ensure_ascii=False)
    except Exception:
        pass


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


async def _wiki_get(session: aiohttp.ClientSession, params: dict) -> dict | None:
    """Rate-limited Liquipedia 위키 API 호출. 429 시 None 반환 (캐시 안 함)."""
    global _wiki_last_req
    wait = _WIKI_INTERVAL - (time.monotonic() - _wiki_last_req)
    if wait > 0:
        await asyncio.sleep(wait)
    try:
        async with session.get(
            LIQUIPEDIA_API, params=params, headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            _wiki_last_req = time.monotonic()
            if resp.status == 429:
                return None   # rate limited — 캐시하지 않음
            if resp.status != 200:
                return {}
            return await resp.json(content_type=None)
    except Exception:
        return {}


async def _fetch_team_logo_url(team_name: str) -> str | None:
    """팀 위키페이지 → 로고 파일명 → 이미지 URL"""
    if team_name in _logo_cache:
        return _logo_cache[team_name]

    try:
        async with aiohttp.ClientSession() as session:
            # 1) 팀 페이지 위키텍스트에서 |imagedark= or |image= 파일명 파싱
            data = await _wiki_get(session, {
                "action": "parse", "page": team_name,
                "prop": "wikitext", "format": "json", "redirects": "1",
            })
            if data is None:            # 429 — 나중에 재시도 가능하도록 캐시 안 함
                return None
            wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")

            filename = None
            for line in wikitext.split("\n"):
                m = re.match(r"\|imagedark=(.+)", line.strip())
                if not m:
                    m = re.match(r"\|image=(.+)", line.strip())
                if m:
                    filename = m.group(1).strip()
                    break

            if not filename:
                _logo_cache[team_name] = None
                _save_logo_url_cache()
                return None

            # 2) File: URL 조회
            data2 = await _wiki_get(session, {
                "action": "query", "titles": f"File:{filename}",
                "prop": "imageinfo", "iiprop": "url", "format": "json",
            })
            if data2 is None:           # 429 — 캐시하지 않고 다음 기회에 재시도
                return None
            pages = data2.get("query", {}).get("pages", {})
            url = None
            for page in pages.values():
                info_list = page.get("imageinfo", [])
                if info_list:
                    url = info_list[0].get("url")
            _logo_cache[team_name] = url
            _save_logo_url_cache()      # 성공 시 디스크에 영구 저장
            return url
    except Exception as e:
        print(f"[OWCS 로고] {team_name} 실패: {e}")
        _logo_cache[team_name] = None
        _save_logo_url_cache()
        return None


async def _download_logo(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers={"User-Agent": HEADERS["User-Agent"]},
                                   timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                    img.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
                    canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0, 0, 0, 0))
                    ox = (LOGO_SZ - img.width) // 2
                    oy = (LOGO_SZ - img.height) // 2
                    canvas.paste(img, (ox, oy))
                    return canvas
    except Exception:
        return None


def _paste(base: Image.Image, logo: Image.Image | None, x: int, y: int):
    if logo is None:
        return
    try:
        base.paste(logo, (x, y), logo)
    except Exception:
        pass


async def draw_match_day(day_matches: list) -> io.BytesIO:
    """같은 날 경기 목록 → PNG BytesIO"""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    day_matches = sorted(day_matches, key=lambda m: m["dt"])
    n = len(day_matches)
    img_h = HEADER_H + n * ROW_H + PAD // 2

    # 팀 로고: 로컬 파일 우선, 없으면 URL 다운로드
    logo_mapping_path = os.path.join(_BASE, "logos", "mapping.json")
    try:
        with open(logo_mapping_path, encoding="utf-8") as f:
            logo_mapping = json.load(f)
    except Exception:
        logo_mapping = {}

    logo_imgs: dict[str, Image.Image | None] = {}
    all_teams = {m.get("team1") for m in day_matches} | {m.get("team2") for m in day_matches}
    all_teams.discard(None)
    all_teams.discard("")

    # URL 맵 구성 (fallback용)
    url_map: dict[str, str] = {}
    for m in day_matches:
        if m.get("team1") and m.get("logo1"):
            url_map[m["team1"]] = m["logo1"]
        if m.get("team2") and m.get("logo2"):
            url_map[m["team2"]] = m["logo2"]

    for team in all_teams:
        fname = logo_mapping.get(team)
        local = os.path.join(_BASE, "logos", fname) if fname else None
        if local and os.path.exists(local):
            try:
                img_logo = Image.open(local).convert("RGBA")
                img_logo.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
                canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0, 0, 0, 0))
                ox = (LOGO_SZ - img_logo.width) // 2
                oy = (LOGO_SZ - img_logo.height) // 2
                canvas.paste(img_logo, (ox, oy))
                logo_imgs[team] = canvas
                continue
            except Exception:
                pass
        # 로컬 없으면 match 데이터 URL → 없으면 위키에서 팀 페이지 조회
        url = url_map.get(team, "")
        if not url:
            url = await _fetch_team_logo_url(team) or ""
        logo_imgs[team] = await _download_logo(url)

    img  = Image.new("RGB", (IMG_W, img_h), BG)
    draw = ImageDraw.Draw(img)

    f_label = _font(FONT_BOLD,    18)
    f_date  = _font(FONT_BOLD,    34)
    f_team  = _font(FONT_BOLD,    24)
    f_vs    = _font(FONT_REGULAR, 17)
    f_meta  = _font(FONT_REGULAR, 14)

    # ── 헤더 ─────────────────────────────────
    label    = day_matches[0].get("label", "OWCS Korea")
    date_str = day_matches[0]["dt"].strftime("%Y.%m.%d (%a)")
    draw.text((PAD, 18), label,    font=f_label, fill=ACCENT)
    draw.text((PAD, 46), date_str, font=f_date,  fill=TEXT)
    draw.line([(PAD, HEADER_H - 10), (IMG_W - PAD, HEADER_H - 10)], fill=LINE, width=1)

    CX = IMG_W // 2

    # ── 경기 행 ──────────────────────────────
    for i, m in enumerate(day_matches):
        y  = HEADER_H + i * ROW_H
        cy = y + ROW_H // 2

        row_bg = CARD_ODD if i % 2 == 0 else CARD_EVEN
        draw.rectangle([0, y, IMG_W, y + ROW_H - 1], fill=row_bg)
        draw.line([(0, y + ROW_H - 1), (IMG_W, y + ROW_H - 1)], fill=LINE, width=1)

        team1 = m.get("team1", "?")
        team2 = m.get("team2", "?")
        dt    = m["dt"]

        ongoing = 0 <= (now - dt).total_seconds() <= 3 * 3600

        # Match 번호
        draw.text((PAD, cy - 14), f"Match {i + 1}", font=f_meta, fill=GRAY)

        # 시간 or ON AIR (이모지 대신 Pillow로 빨간 원 직접 그림)
        if ongoing:
            r = 5
            dot_x, dot_y = PAD, cy + 6
            draw.ellipse([dot_x, dot_y, dot_x + r * 2, dot_y + r * 2], fill=ON_AIR)
            draw.text((dot_x + r * 2 + 4, cy + 2), "ON AIR", font=f_meta, fill=ON_AIR)
        else:
            draw.text((PAD, cy + 2), dt.strftime("%H:%M KST"), font=f_meta, fill=GRAY)

        venue = m.get("venue", "")
        if venue:
            draw.text((PAD, cy + 18), venue, font=f_meta, fill=GRAY)

        # ── 팀1 (오른쪽 정렬, 중앙 왼쪽) ──
        t1w = int(draw.textlength(team1, font=f_team))
        t1x = CX - 80 - t1w
        draw.text((t1x, cy - 14), team1, font=f_team, fill=TEXT)
        _paste(img, logo_imgs.get(team1), t1x - LOGO_SZ - 8, cy - LOGO_SZ // 2)

        # ── vs ──
        vsw = int(draw.textlength("vs", font=f_vs))
        draw.text((CX - vsw // 2, cy - 10), "vs", font=f_vs, fill=GRAY)

        # ── 팀2 (왼쪽 정렬, 중앙 오른쪽) ──
        t2x = CX + 80
        draw.text((t2x, cy - 14), team2, font=f_team, fill=TEXT)
        t2w = int(draw.textlength(team2, font=f_team))
        _paste(img, logo_imgs.get(team2), t2x + t2w + 8, cy - LOGO_SZ // 2)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── 순위표 이미지 ────────────────────────────────────────────

S_W       = 720
S_PAD     = 36
S_LOGO    = 44
S_ROW_H   = 68
S_HEAD_H  = 130

S_BG      = (12,  12,  18)
S_HDR     = (20,  20,  30)
S_ROW_ODD = (18,  18,  28)
S_ROW_EVN = (24,  24,  36)
S_LINE    = (40,  40,  60)
S_WHITE   = (240, 242, 248)
S_GRAY    = (140, 145, 165)
S_RED     = (210, 40,  40)
S_GOLD    = (255, 200, 60)


def _load_logo_local(team: str) -> Image.Image | None:
    mapping_path = os.path.join(_BASE, "logos", "mapping.json")
    try:
        with open(mapping_path, encoding="utf-8") as f:
            mapping = json.load(f)
        fname = mapping.get(team)
        if fname:
            path = os.path.join(_BASE, "logos", fname)
            if os.path.exists(path):
                img = Image.open(path).convert("RGBA")
                img.thumbnail((S_LOGO, S_LOGO), Image.LANCZOS)
                canvas = Image.new("RGBA", (S_LOGO, S_LOGO), (0, 0, 0, 0))
                ox = (S_LOGO - img.width) // 2
                oy = (S_LOGO - img.height) // 2
                canvas.paste(img, (ox, oy))
                return canvas
    except Exception:
        pass
    return None


async def draw_standings(standings: list, title: str = "STANDINGS") -> io.BytesIO:
    n      = len(standings)
    img_h  = S_HEAD_H + n * S_ROW_H + S_PAD

    img  = Image.new("RGB", (S_W, img_h), S_BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(FONT_BOLD,    38)
    f_sub   = _font(FONT_REGULAR, 15)
    f_rank  = _font(FONT_BOLD,    22)
    f_team  = _font(FONT_BOLD,    20)
    f_stat  = _font(FONT_BOLD,    20)
    f_head  = _font(FONT_BOLD,    14)

    # ── 헤더 ─────────────────────────────────────────────────
    draw.rectangle([0, 0, S_W, S_HEAD_H], fill=S_HDR)

    tw = int(draw.textlength(title, font=f_title))
    draw.text(((S_W - tw) // 2, 22), title, font=f_title, fill=S_WHITE)

    sub = "OWCS Korea Stage 2 - Regular Season"
    sw = int(draw.textlength(sub, font=f_sub))
    draw.text(((S_W - sw) // 2, 78), sub, font=f_sub, fill=S_GRAY)

    # 컬럼 헤더
    cols = {"TEAM": 130, "W": S_W - 180, "L": S_W - 120, "+/-": S_W - 55}
    draw.line([(S_PAD, S_HEAD_H - 8), (S_W - S_PAD, S_HEAD_H - 8)], fill=S_LINE, width=1)
    draw.text((cols["TEAM"], S_HEAD_H - 34), "TEAM", font=f_head, fill=S_GRAY)
    for key in ("W", "L", "+/-"):
        kw = int(draw.textlength(key, font=f_head))
        draw.text((cols[key] - kw // 2, S_HEAD_H - 34), key, font=f_head, fill=S_GRAY)

    # ── 팀 행 ────────────────────────────────────────────────
    for i, entry in enumerate(standings):
        y      = S_HEAD_H + i * S_ROW_H
        cy     = y + S_ROW_H // 2
        row_bg = S_ROW_ODD if i % 2 == 0 else S_ROW_EVN
        draw.rectangle([0, y, S_W, y + S_ROW_H - 1], fill=row_bg)
        draw.line([(0, y + S_ROW_H - 1), (S_W, y + S_ROW_H - 1)], fill=S_LINE, width=1)

        rank = entry["rank"]
        rank_color = S_GOLD if rank == 1 else S_RED if rank <= 3 else S_WHITE
        rw = int(draw.textlength(str(rank), font=f_rank))
        draw.text((S_PAD + (30 - rw) // 2, cy - 13), str(rank), font=f_rank, fill=rank_color)

        logo = _load_logo_local(entry["team"])
        if logo:
            _paste(img, logo, 72, cy - S_LOGO // 2)

        draw.text((cols["TEAM"], cy - 12), entry["team"], font=f_team, fill=S_WHITE)

        for key, val in [("W", entry["W"]), ("L", entry["L"]), ("+/-", entry["diff"])]:
            text  = f"+{val}" if key == "+/-" and val > 0 else str(val)
            color = S_RED if key == "L" or (key == "+/-" and val < 0) else S_WHITE
            if key == "W":
                color = (100, 220, 120)
            vw = int(draw.textlength(text, font=f_stat))
            draw.text((cols[key] - vw // 2, cy - 12), text, font=f_stat, fill=color)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


_load_logo_url_cache()


# ── OWWC Group Stage 스탠딩 이미지 ─────────────────────────────

_GS_W   = 1240
_GS_PAD = 20
_GS_TH  = 55                                          # 타이틀 헤더 높이
_GS_CH  = 268                                         # 카드 높이
_GS_CW  = (_GS_W - _GS_PAD * 3) // 2                 # 카드 너비 ≈ 590
_GS_H   = _GS_TH + _GS_PAD * 3 + _GS_CH * 2          # 전체 높이 ≈ 651

_GS_DARK  = (15,  20,  45)
_GS_BG    = (248, 249, 252)
_GS_CARD  = (255, 255, 255)
_GS_CHDR  = (238, 241, 252)
_GS_ADV   = (232, 245, 233)
_GS_ELIM  = (253, 232, 237)
_GS_LINE  = (220, 224, 236)
_GS_TEXT  = (20,  28,  55)
_GS_GRAY  = (130, 136, 160)
_GS_GREEN = (46,  125, 50)
_GS_RED   = (198, 40,  40)
_GS_GOLD  = (200, 150, 0)

_COL_RANK = 14
_COL_CODE = 46
_COL_NAME = 104
_COL_W    = _GS_CW - 245
_COL_L    = _GS_CW - 195
_COL_MAP  = _GS_CW - 148
_COL_DIFF = _GS_CW - 68


async def draw_owwc_group_stage(
    groups: list,
    date_range: str = "8월 20일 – 8월 23일",
) -> io.BytesIO:
    """
    groups = owwc.compute_group_standings(matches) 결과
    4개 그룹을 2×2 그리드로 렌더링 → PNG BytesIO
    """
    img  = Image.new("RGB", (_GS_W, _GS_H), _GS_BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(FONT_BOLD,    22)
    f_sub   = _font(FONT_REGULAR, 18)
    f_dr    = _font(FONT_REGULAR, 13)
    f_grp   = _font(FONT_BOLD,    17)
    f_head  = _font(FONT_BOLD,    13)
    f_rank  = _font(FONT_BOLD,    18)
    f_code  = _font(FONT_BOLD,    14)
    f_name  = _font(FONT_REGULAR, 14)
    f_stat  = _font(FONT_BOLD,    15)
    f_leg   = _font(FONT_REGULAR, 11)

    # ── 전체 헤더 ──────────────────────────────────────────────
    draw.rectangle([0, 0, _GS_W, _GS_TH], fill=_GS_DARK)
    draw.text((22, 14), "OWWC 2026", font=f_title, fill=(255, 255, 255))
    sep = int(draw.textlength("OWWC 2026", font=f_title))
    draw.text((22 + sep + 12, 17), "| Group Stage", font=f_sub, fill=(170, 182, 220))
    if date_range:
        dw = int(draw.textlength(date_range, font=f_dr))
        draw.text((_GS_W - dw - 20, 21), date_range, font=f_dr, fill=(155, 165, 200))

    # ── 카드 배치 ──────────────────────────────────────────────
    card_pos = [
        (_GS_PAD,              _GS_TH + _GS_PAD),
        (_GS_PAD * 2 + _GS_CW, _GS_TH + _GS_PAD),
        (_GS_PAD,              _GS_TH + _GS_PAD * 2 + _GS_CH),
        (_GS_PAD * 2 + _GS_CW, _GS_TH + _GS_PAD * 2 + _GS_CH),
    ]

    for idx, group in enumerate(groups[:4]):
        cx, cy = card_pos[idx]
        teams  = group.get("teams", [])

        draw.rectangle([cx, cy, cx + _GS_CW, cy + _GS_CH],
                       fill=_GS_CARD, outline=_GS_LINE, width=1)

        # 그룹 타이틀 바
        draw.rectangle([cx, cy, cx + _GS_CW, cy + 38], fill=_GS_CHDR)
        draw.text((cx + 14, cy + 9), group.get("name", ""), font=f_grp, fill=_GS_TEXT)

        # 컬럼 헤더
        hy = cy + 43
        draw.text((cx + _COL_RANK, hy), "#",  font=f_head, fill=_GS_GRAY)
        draw.text((cx + _COL_CODE, hy), "팀", font=f_head, fill=_GS_GRAY)
        for label, col, col_w in [("W", _COL_W, 38), ("L", _COL_L, 38),
                                   ("맵", _COL_MAP, 48), ("+/-", _COL_DIFF, 60)]:
            lw = int(draw.textlength(label, font=f_head))
            draw.text((cx + col + (col_w - lw) // 2, hy), label, font=f_head, fill=_GS_GRAY)

        draw.line([(cx, cy + 66), (cx + _GS_CW, cy + 66)], fill=_GS_LINE, width=1)

        # ── 팀 행 ──────────────────────────────────────────────
        ROW_TOP  = cy + 68
        ROW_H_px = 44

        for i, team in enumerate(teams[:4]):
            ry     = ROW_TOP + i * ROW_H_px
            status = team.get("status", "")

            row_bg = _GS_ADV if status == "advanced" else _GS_ELIM if status == "eliminated" else _GS_CARD
            draw.rectangle([cx + 1, ry, cx + _GS_CW - 1, ry + ROW_H_px - 1], fill=row_bg)

            bar = _GS_GREEN if status == "advanced" else _GS_RED if status == "eliminated" else None
            if bar:
                draw.rectangle([cx + 1, ry, cx + 4, ry + ROW_H_px - 1], fill=bar)

            draw.line([(cx, ry + ROW_H_px - 1), (cx + _GS_CW, ry + ROW_H_px - 1)],
                      fill=_GS_LINE, width=1)

            mid = ry + ROW_H_px // 2 - 9

            # 순위
            rank_s = str(team.get("rank", i + 1))
            rc     = _GS_GOLD if team.get("rank") == 1 else _GS_TEXT
            rw     = int(draw.textlength(rank_s, font=f_rank))
            draw.text((cx + _COL_RANK + (24 - rw) // 2, mid), rank_s, font=f_rank, fill=rc)

            # 국가 코드 배지
            code  = team.get("code", "???")
            bx    = cx + _COL_CODE
            bw, bh = 50, 24
            by    = ry + ROW_H_px // 2 - bh // 2
            bc    = (55, 120, 55) if status == "advanced" else (175, 45, 55) if status == "eliminated" else (80, 90, 120)
            draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=4, fill=bc)
            cw = int(draw.textlength(code, font=f_code))
            draw.text((bx + (bw - cw) // 2, by + (bh - 16) // 2), code, font=f_code, fill=(255, 255, 255))

            # 팀 이름
            name     = team.get("name", "")
            max_name = _COL_W - _COL_NAME - 10
            while name and int(draw.textlength(name, font=f_name)) > max_name:
                name = name[:-1]
            draw.text((cx + _COL_NAME, mid + 1), name, font=f_name, fill=_GS_TEXT)

            # W
            w_s = str(team.get("W", 0))
            ww  = int(draw.textlength(w_s, font=f_stat))
            draw.text((cx + _COL_W + (38 - ww) // 2, mid), w_s, font=f_stat, fill=_GS_GREEN)

            # L
            l_s = str(team.get("L", 0))
            lw2 = int(draw.textlength(l_s, font=f_stat))
            draw.text((cx + _COL_L + (38 - lw2) // 2, mid), l_s, font=f_stat, fill=_GS_RED)

            # 맵
            ms  = f"{team.get('map_w', 0)}-{team.get('map_l', 0)}"
            msw = int(draw.textlength(ms, font=f_stat))
            draw.text((cx + _COL_MAP + (48 - msw) // 2, mid), ms, font=f_stat, fill=_GS_TEXT)

            # +/-
            diff     = team.get("diff", 0)
            diff_s   = f"+{diff}" if diff > 0 else str(diff)
            diff_col = _GS_GREEN if diff > 0 else _GS_RED if diff < 0 else _GS_GRAY
            dw2      = int(draw.textlength(diff_s, font=f_stat))
            draw.text((cx + _COL_DIFF + (60 - dw2) // 2, mid), diff_s, font=f_stat, fill=diff_col)

        # 범례
        ly = cy + _GS_CH - 20
        draw.ellipse([cx + 12, ly + 4, cx + 20, ly + 12], fill=_GS_GREEN)
        draw.text((cx + 24, ly), "Playoffs 진출", font=f_leg, fill=_GS_GRAY)
        draw.ellipse([cx + 122, ly + 4, cx + 130, ly + 12], fill=_GS_RED)
        draw.text((cx + 134, ly), "탈락", font=f_leg, fill=_GS_GRAY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
