import calendar
import io
from datetime import date as Date

from PIL import Image, ImageDraw, ImageFont
from pilmoji import Pilmoji

import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
FONT_BOLD    = _os.path.join(_BASE, "fonts", "malgunbd.ttf")
FONT_REGULAR = _os.path.join(_BASE, "fonts", "malgun.ttf")

# 최종 출력 해상도
TARGET_W = 1920
TARGET_H = 1400

# 내부 렌더 배율 (LANCZOS 다운샘플로 선명도 확보)
SCALE = 2

COLS = 7

# 1x 기준 고정 여백·높이
OUTER_PAD = 8
HEADER_H  = 80
DAY_ROW_H = 40
PILL_H_1X = 54
PILL_GAP  = 3

# 1x 기준 폰트 크기
FS_HEADER = 44
FS_DAY    = 16
FS_DATE   = 22
FS_NICK   = 14
FS_TIME   = 12
FS_TITLE  = 14
FS_MORE   = 11

DAY_NAMES = ["일", "월", "화", "수", "목", "금", "토"]

OUTER_BG  = (28,  40,  70)
CARD_BG   = (255, 255, 255)
HEADER_BG = (66,  120, 210)
HEADER_FG = (255, 255, 255)
DAY_BG    = (245, 247, 252)
DAY_FG    = (140, 148, 165)
ACCENT    = (66,  120, 210)
DATE_FG   = (28,  33,  48)
GRID      = (228, 231, 238)
SCH_BG    = (230, 241, 255)
SCH_FG    = (0,   0,   0)
META_FG   = (0,   0,   0)
MORE_FG   = (145, 150, 165)


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _truncate(draw, text: str, font, max_w: int) -> str:
    while draw.textlength(text, font=font) > max_w and len(text) > 1:
        text = text[:-1]
    return text


def _wrap_text(draw, text: str, font, max_w: int) -> list:
    if draw.textlength(text, font=font) <= max_w:
        return [text]
    line1 = text
    while draw.textlength(line1, font=font) > max_w and len(line1) > 1:
        line1 = line1[:-1]
    line2 = _truncate(draw, text[len(line1):], font, max_w)
    return [line1, line2]


def _text_center(draw, pm, rect, text, font, fill):
    bx = draw.textbbox((0, 0), text, font=font)
    tw, th = bx[2] - bx[0], bx[3] - bx[1]
    x = rect[0] + (rect[2] - rect[0] - tw) // 2
    y = rect[1] + (rect[3] - rect[1] - th) // 2
    pm.text((x, y), text, font=font, fill=fill)


def draw_calendar(year: int, month: int, schedules: list) -> io.BytesIO:
    day_map: dict[int, list] = {}
    for row in schedules:
        day = int(row["date"].split("-")[2])
        day_map.setdefault(day, []).append(row)

    weeks     = calendar.Calendar(firstweekday=6).monthdayscalendar(year, month)
    num_weeks = len(weeks)

    # 1x 셀 크기를 TARGET 해상도로부터 역산
    cell_w_1x = (TARGET_W - OUTER_PAD * 2) // COLS
    cell_h_1x = (TARGET_H - OUTER_PAD * 2 - HEADER_H - DAY_ROW_H) // num_weeks

    # 셀 높이 기준 최대 표시 가능 필 수 (날짜 숫자 영역 38px 제외)
    max_pills = max(1, (cell_h_1x - 38 + PILL_GAP) // (PILL_H_1X + PILL_GAP))

    # SCALE 적용값 (내부 렌더링 좌표)
    S         = SCALE
    sOP       = OUTER_PAD * S
    sHH       = HEADER_H  * S
    sDR       = DAY_ROW_H * S
    sCW       = cell_w_1x * S
    sCH       = cell_h_1x * S
    sPILL     = PILL_H_1X * S
    sPGAP     = PILL_GAP  * S
    sWIDTH    = sCW * COLS
    sCARD_H   = sHH + sDR + sCH * num_weeks
    sHPAD     = 20 * S
    sCARD_R   = 14 * S

    img_w = sWIDTH + sOP * 2
    img_h = sCARD_H + sOP * 2

    img  = Image.new("RGB", (img_w, img_h), OUTER_BG)
    draw = ImageDraw.Draw(img)

    with Pilmoji(img) as pm:
        # ── 카드 ─────────────────────────────────────────────
        cx0, cy0 = sOP, sOP
        cx1, cy1 = cx0 + sWIDTH, cy0 + sCARD_H
        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=sCARD_R, fill=CARD_BG)

        # ── 헤더 ─────────────────────────────────────────────
        hx0, hy0 = cx0, cy0
        hx1, hy1 = cx1, cy0 + sHH
        draw.rounded_rectangle([hx0, hy0, hx1, hy1 + sCARD_R], radius=sCARD_R, fill=HEADER_BG)
        draw.rectangle([hx0, hy1 - sCARD_R, hx1, hy1], fill=HEADER_BG)

        f_hdr = _font(FONT_BOLD, FS_HEADER * S)
        hty   = hy0 + (sHH - FS_HEADER * S) // 2
        pm.text((hx0 + sHPAD, hty), f"{month}월", font=f_hdr, fill=HEADER_FG)
        yr_w = draw.textlength(str(year), font=f_hdr)
        pm.text((hx1 - sHPAD - int(yr_w), hty), str(year), font=f_hdr, fill=HEADER_FG)

        # ── 요일 행 ──────────────────────────────────────────
        f_day = _font(FONT_REGULAR, FS_DAY * S)
        dy0   = cy0 + sHH
        draw.rectangle([cx0, dy0, cx1, dy0 + sDR], fill=DAY_BG)
        for i, name in enumerate(DAY_NAMES):
            col = ACCENT if i in (0, 6) else DAY_FG
            _text_center(draw, pm, (cx0 + i * sCW, dy0, cx0 + (i + 1) * sCW, dy0 + sDR),
                         name, f_day, col)

        # ── 날짜 셀 ──────────────────────────────────────────
        today  = Date.today()
        f_date = _font(FONT_BOLD,    FS_DATE  * S)
        f_nick = _font(FONT_BOLD,    FS_NICK  * S)
        f_time = _font(FONT_BOLD,    FS_TIME  * S)
        f_sch  = _font(FONT_REGULAR, FS_TITLE * S)
        f_more = _font(FONT_REGULAR, FS_MORE  * S)

        for r, week in enumerate(weeks):
            for c, day in enumerate(week):
                cx = cx0 + c * sCW
                cy = cy0 + sHH + sDR + r * sCH

                draw.line([(cx, cy), (cx + sCW, cy)],      fill=GRID, width=S)
                draw.line([(cx, cy), (cx, cy + sCH)],       fill=GRID, width=S)
                if c == COLS - 1:
                    draw.line([(cx + sCW, cy), (cx + sCW, cy + sCH)], fill=GRID, width=S)
                if r == num_weeks - 1:
                    draw.line([(cx, cy + sCH), (cx + sCW, cy + sCH)], fill=GRID, width=S)

                if day == 0:
                    continue

                is_today   = (year == today.year and month == today.month and day == today.day)
                date_color = ACCENT if c in (0, 6) else DATE_FG
                date_str   = str(day)
                tx, ty     = cx + 10 * S, cy + 8 * S

                if is_today:
                    bx  = draw.textbbox((tx, ty), date_str, font=f_date)
                    c_x = (bx[0] + bx[2]) // 2
                    c_y = (bx[1] + bx[3]) // 2
                    rad = max(bx[2] - bx[0], bx[3] - bx[1]) // 2 + 8 * S
                    draw.ellipse([c_x - rad, c_y - rad, c_x + rad, c_y + rad], fill=ACCENT)
                    pm.text((tx, ty), date_str, font=f_date, fill=(255, 255, 255))
                else:
                    pm.text((tx, ty), date_str, font=f_date, fill=date_color)

                # ── 일정 필 ──────────────────────────────────
                scheds = day_map.get(day, [])
                max_w  = sCW - 16 * S
                pill_y = cy + 38 * S
                shown  = 0

                for s in scheds:
                    if shown >= max_pills:
                        break
                    nick_text  = f"[{s['user_name']}]" if s["user_name"] else ""
                    nick_lines = _wrap_text(draw, nick_text, f_nick, max_w) if nick_text else []
                    pill_h     = sPILL + (16 * S if len(nick_lines) > 1 else 0)

                    if pill_y + pill_h > cy + sCH - 14 * S:
                        break

                    draw.rounded_rectangle(
                        [cx + 4 * S, pill_y, cx + sCW - 4 * S, pill_y + pill_h],
                        radius=4 * S, fill=SCH_BG,
                    )

                    pty = pill_y + 3 * S
                    for line in nick_lines:
                        pm.text((cx + 6 * S, pty), line, font=f_nick, fill=META_FG)
                        pty += 16 * S

                    if s["start_time"] or s["end_time"]:
                        t = f"{s['start_time'] or '?'} ~ {s['end_time'] or '?'}"
                        t = _truncate(draw, t, f_time, max_w)
                        pm.text((cx + 6 * S, pty), t, font=f_time, fill=META_FG)
                        pty += 14 * S

                    title_str = _truncate(draw, s["title"], f_sch, max_w)
                    pm.text((cx + 6 * S, pty), title_str, font=f_sch, fill=SCH_FG)

                    pill_y += pill_h + sPGAP
                    shown  += 1

                if len(scheds) > shown:
                    pm.text(
                        (cx + 6 * S, cy + sCH - 14 * S),
                        f"+{len(scheds) - shown}개 더",
                        font=f_more, fill=MORE_FG,
                    )

    # LANCZOS 다운샘플 → 정확히 1920×1080 출력
    img = img.resize((TARGET_W, TARGET_H), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
