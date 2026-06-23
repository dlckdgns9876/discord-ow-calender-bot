import io
import os
import re
import gzip
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

_logo_cache: dict[str, str | None] = {}


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


async def _fetch_team_logo_url(team_name: str) -> str | None:
    """팀 위키페이지 → 로고 파일명 → 이미지 URL"""
    if team_name in _logo_cache:
        return _logo_cache[team_name]

    try:
        async with aiohttp.ClientSession() as session:
            # 1) 팀 페이지 위키텍스트에서 |imagedark= or |image= 파일명 파싱
            params = {"action": "parse", "page": team_name, "prop": "wikitext", "format": "json"}
            async with session.get(LIQUIPEDIA_API, params=params, headers=HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                raw = await resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                data = json.loads(raw.decode("utf-8")) if raw else {}
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
                return None

            # 2) File: URL 조회
            params2 = {
                "action": "query",
                "titles": f"File:{filename}",
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            }
            async with session.get(LIQUIPEDIA_API, params=params2, headers=HEADERS,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp2:
                raw2 = await resp2.read()
                if resp2.headers.get("Content-Encoding") == "gzip":
                    raw2 = gzip.decompress(raw2)
                data2 = json.loads(raw2.decode("utf-8")) if raw2 else {}
            pages = data2.get("query", {}).get("pages", {})
            url = None
            for page in pages.values():
                info = page.get("imageinfo", [{}])
                if info:
                    url = info[0].get("url")
            _logo_cache[team_name] = url
            return url
    except Exception as e:
        print(f"[OWCS 로고] {team_name} 실패: {e}")
        _logo_cache[team_name] = None
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
        # 로컬 없으면 URL 다운로드
        logo_imgs[team] = await _download_logo(url_map.get(team, ""))

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
    draw.line([(S_PAD, S_HEAD_H - 14), (S_W - S_PAD, S_HEAD_H - 14)], fill=S_LINE, width=1)
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
