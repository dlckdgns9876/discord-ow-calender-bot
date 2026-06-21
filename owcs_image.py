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

    # 팀 로고 수집
    all_teams = set()
    for m in day_matches:
        all_teams.add(m.get("team1", ""))
        all_teams.add(m.get("team2", ""))
    all_teams.discard("")

    logo_urls  = {t: await _fetch_team_logo_url(t) for t in all_teams}
    logo_imgs  = {t: await _download_logo(url) for t, url in logo_urls.items()}

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
