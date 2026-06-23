import os
import re
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db
import calendar_image
import owcs as owcs_module
import owcs_image

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await db.init_db()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    if not check_owcs.is_running():
        check_owcs.start()


@tasks.loop(minutes=10)
async def check_owcs():
    try:
        schedules = await owcs_module.fetch_schedules()

        # ── 경기 1시간 전 알림 ───────────────────────────────
        targets = owcs_module.get_notify_targets(schedules)
        for match in targets:
            mid = owcs_module.match_id(match)
            if await db.is_owcs_notified(mid):
                continue
            await db.mark_owcs_notified(mid)

            info = owcs_module.format_info(match)
            embed = discord.Embed(
                title="🎮 OWCS 경기 1시간 전 알림!",
                color=discord.Color.orange(),
            )
            embed.add_field(name="대회",    value=info["label"],   inline=False)
            embed.add_field(name="시작 시간", value=info["time"],   inline=True)
            embed.add_field(name="경기",    value=info["matchup"], inline=False)
            embed.add_field(name="📺 공식 방송", value=f"[SOOP 바로가기]({owcs_module.SOOP_URL})", inline=False)

            channels = await db.get_all_owcs_channels()
            for guild_id, channel_id in channels:
                ch = bot.get_channel(channel_id)
                if ch:
                    await ch.send(embed=embed)

        # ── 주차 종료 알림 ───────────────────────────────────
        week_lasts = owcs_module.get_week_last_matches(schedules)
        for week_no, last_match in enumerate(week_lasts, start=1):
            if not owcs_module.is_week_just_ended(last_match):
                continue
            wid = f"week_end_{owcs_module.match_id(last_match)}"
            if await db.is_owcs_notified(wid):
                continue
            await db.mark_owcs_notified(wid)

            # 순위표 이미지 생성
            standings = owcs_module.fetch_standings()
            buf  = await owcs_image.draw_standings(
                standings, title=f"WEEK {week_no} STANDINGS"
            )
            file = discord.File(buf, filename="owcs_standings.png")

            week_channels = await db.get_all_owcs_week_channels()
            for guild_id, channel_id in week_channels:
                ch = bot.get_channel(channel_id)
                if ch:
                    await ch.send(
                        content=f"📊 **{week_no}주차 경기가 모두 종료됐습니다! 현재 순위입니다.**",
                        file=file,
                    )
                    buf.seek(0)

    except Exception as e:
        print(f"[OWCS 알림 오류] {e}")


@check_owcs.before_loop
async def before_check_owcs():
    await bot.wait_until_ready()
    import time as _time
    from owcs import _cache, CACHE_TTL
    remaining = CACHE_TTL - (_time.time() - _cache["updated_at"])
    if remaining > 0:
        print(f"[OWCS] 캐시 유효 — {int(remaining)}초 후 첫 API 호출 예정")
    else:
        await __import__("asyncio").sleep(60)


@bot.tree.command(name="ping", description="봇 응답 확인")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong! 응답 지연: {round(bot.latency * 1000)}ms"
    )


def validate_time(t: str) -> bool:
    match = re.fullmatch(r"(오전|오후)\s+(\d{1,2}):([0-5]\d)", t.strip())
    if not match:
        return False
    hour = int(match.group(2))
    return 1 <= hour <= 12


def _to_minutes(t: str) -> int:
    m = re.fullmatch(r"(오전|오후)\s+(\d{1,2}):([0-5]\d)", t.strip())
    ampm, h, mi = m.group(1), int(m.group(2)), int(m.group(3))
    if ampm == "오후" and h != 12:
        h += 12
    elif ampm == "오전" and h == 12:
        h = 0
    return h * 60 + mi


@bot.tree.command(name="일정추가", description="일정을 추가합니다")
@discord.app_commands.describe(
    날짜="날짜 (형식: YYYY-MM-DD, 예: 2025-07-10) 또는 오늘",
    제목="일정 제목",
    시작시간="시작 시간 (예: 오전 9:00, 오후 2:30, 선택)",
    종료시간="종료 시간 (예: 오전 11:00, 오후 6:00, 선택)",
    설명="일정 설명 (선택)",
)
async def add_schedule(
    interaction: discord.Interaction,
    날짜: str,
    제목: str,
    시작시간: str = None,
    종료시간: str = None,
    설명: str = None,
):
    if 날짜.strip() == "오늘":
        날짜 = datetime.today().strftime("%Y-%m-%d")
    else:
        try:
            datetime.strptime(날짜, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "날짜 형식이 올바르지 않습니다. `YYYY-MM-DD` 형식으로 입력하거나 `오늘`을 입력해주세요.\n예) `2025-07-10`",
                ephemeral=True,
            )
            return

    if 시작시간 and not validate_time(시작시간):
        await interaction.response.send_message(
            "시작 시간 형식이 올바르지 않습니다.\n예) `오전 9:00` / `오후 2:30`",
            ephemeral=True,
        )
        return

    if 종료시간 and not validate_time(종료시간):
        await interaction.response.send_message(
            "종료 시간 형식이 올바르지 않습니다.\n예) `오전 11:00` / `오후 6:00`",
            ephemeral=True,
        )
        return

    await db.add_schedule(
        interaction.guild_id, 날짜, 제목, 설명, 시작시간, 종료시간,
        user_id=interaction.user.id,
        user_name=interaction.user.display_name,
    )

    # 자정을 넘는 일정이면 다음 날에도 자동 등록
    next_date = None
    if 시작시간 and 종료시간 and _to_minutes(시작시간) > _to_minutes(종료시간):
        next_date = (datetime.strptime(날짜, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        await db.add_schedule(
            interaction.guild_id, next_date, 제목, 설명, 시작시간, 종료시간,
            user_id=interaction.user.id,
            user_name=interaction.user.display_name,
        )

    embed = discord.Embed(title="일정 추가 완료", color=discord.Color.green())
    날짜표시 = f"{날짜} ~ {next_date}" if next_date else 날짜
    embed.add_field(name="날짜", value=날짜표시, inline=True)
    embed.add_field(name="제목", value=제목, inline=True)
    if 시작시간 or 종료시간:
        시간표시 = f"{시작시간 or '?'} ~ {종료시간 or '?'}"
        embed.add_field(name="시간", value=시간표시, inline=True)
    if next_date:
        embed.add_field(name="안내", value="자정을 넘는 일정으로 다음 날에도 자동 등록됐습니다.", inline=False)
    if 설명:
        embed.add_field(name="설명", value=설명, inline=False)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="일정목록", description="특정 날짜의 일정 목록을 보여줍니다")
@discord.app_commands.describe(날짜="날짜 (YYYY-MM-DD 또는 오늘)")
async def list_schedules(interaction: discord.Interaction, 날짜: str):
    if 날짜.strip() == "오늘":
        날짜 = datetime.today().strftime("%Y-%m-%d")
    else:
        try:
            datetime.strptime(날짜, "%Y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "날짜 형식이 올바르지 않습니다. `YYYY-MM-DD` 또는 `오늘`을 입력해주세요.",
                ephemeral=True,
            )
            return

    rows = await db.get_schedules_by_date(interaction.guild_id, 날짜)

    if not rows:
        await interaction.response.send_message(f"`{날짜}` 에 등록된 일정이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title=f"{날짜} 일정 목록", color=discord.Color.blue())
    for row in rows:
        lines = [f"ID: `{row['id']}`"]
        if row["start_time"] or row["end_time"]:
            lines.append(f"시간: {row['start_time'] or '?'} ~ {row['end_time'] or '?'}")
        if row["description"]:
            lines.append(row["description"])
        if row["user_name"]:
            lines.append(f"등록자: {row['user_name']}")
        embed.add_field(name=row["title"], value="\n".join(lines), inline=False)

    embed.set_footer(text="수정: /일정수정 [ID]  |  삭제: /일정삭제 [ID]")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="일정삭제", description="일정을 삭제합니다")
@discord.app_commands.describe(id="삭제할 일정 ID (/일정목록에서 확인)")
async def delete_schedule(interaction: discord.Interaction, id: int):
    result = await db.delete_schedule(id, interaction.guild_id, interaction.user.id)
    messages = {
        "ok":        f"ID `{id}` 일정을 삭제했습니다.",
        "not_found": f"ID `{id}` 일정을 찾을 수 없습니다.",
        "forbidden": "본인이 등록한 일정만 삭제할 수 있습니다.",
    }
    await interaction.response.send_message(messages[result], ephemeral=True)


@bot.tree.command(name="일정수정", description="등록한 일정을 수정합니다")
@discord.app_commands.describe(
    id="수정할 일정 ID (/일정목록에서 확인)",
    날짜="변경할 날짜 (YYYY-MM-DD 또는 오늘, 선택)",
    제목="변경할 제목 (선택)",
    시작시간="변경할 시작 시간, 삭제하려면 없음 (선택)",
    종료시간="변경할 종료 시간, 삭제하려면 없음 (선택)",
    설명="변경할 설명, 삭제하려면 없음 (선택)",
)
async def edit_schedule(
    interaction: discord.Interaction,
    id: int,
    날짜: str = None,
    제목: str = None,
    시작시간: str = None,
    종료시간: str = None,
    설명: str = None,
):
    if 날짜 is not None:
        if 날짜.strip() == "오늘":
            날짜 = datetime.today().strftime("%Y-%m-%d")
        else:
            try:
                datetime.strptime(날짜, "%Y-%m-%d")
            except ValueError:
                await interaction.response.send_message(
                    "날짜 형식이 올바르지 않습니다. `YYYY-MM-DD` 또는 `오늘`을 입력해주세요.",
                    ephemeral=True,
                )
                return

    if 시작시간 is not None and 시작시간 != "없음" and not validate_time(시작시간):
        await interaction.response.send_message(
            "시작 시간 형식이 올바르지 않습니다.\n예) `오전 9:00` / `오후 2:30`",
            ephemeral=True,
        )
        return

    if 종료시간 is not None and 종료시간 != "없음" and not validate_time(종료시간):
        await interaction.response.send_message(
            "종료 시간 형식이 올바르지 않습니다.\n예) `오전 11:00` / `오후 6:00`",
            ephemeral=True,
        )
        return

    updates = {}
    if 날짜     is not None: updates["date"]        = 날짜
    if 제목     is not None: updates["title"]       = 제목
    if 시작시간 is not None: updates["start_time"]  = None if 시작시간 == "없음" else 시작시간
    if 종료시간 is not None: updates["end_time"]    = None if 종료시간 == "없음" else 종료시간
    if 설명     is not None: updates["description"] = None if 설명     == "없음" else 설명

    if not updates:
        await interaction.response.send_message(
            "수정할 내용을 하나 이상 입력해주세요.", ephemeral=True
        )
        return

    result = await db.update_schedule(id, interaction.guild_id, interaction.user.id, updates)
    messages = {
        "ok":        f"ID `{id}` 일정을 수정했습니다.",
        "not_found": f"ID `{id}` 일정을 찾을 수 없습니다.",
        "forbidden": "본인이 등록한 일정만 수정할 수 있습니다.",
    }
    await interaction.response.send_message(messages[result], ephemeral=True)


@bot.tree.command(name="캘린더", description="월간 캘린더를 이미지로 보여줍니다")
@discord.app_commands.describe(
    년도="조회할 년도 (기본: 올해)",
    월="조회할 월 1~12 (기본: 이번 달)",
)
async def show_calendar(
    interaction: discord.Interaction,
    년도: int = None,
    월: int = None,
):
    today = datetime.today()
    year  = 년도 or today.year
    month = 월  or today.month

    if not (1 <= month <= 12):
        await interaction.response.send_message(
            "월은 1~12 사이로 입력해주세요.", ephemeral=True
        )
        return

    await interaction.response.defer()

    schedules = await db.get_schedules_by_month(interaction.guild_id, year, month)
    buf = calendar_image.draw_calendar(year, month, schedules)
    file = discord.File(buf, filename=f"calendar_{year}_{month:02d}.png")
    await interaction.followup.send(file=file)


@bot.tree.command(name="owcs알림설정", description="OWCS 경기 1시간 전 알림을 받을 채널을 설정합니다")
@discord.app_commands.describe(채널="알림을 받을 채널")
async def set_owcs_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    await db.set_owcs_channel(interaction.guild_id, 채널.id)
    await interaction.response.send_message(
        f"{채널.mention} 채널에 OWCS 경기 시작 1시간 전 알림을 보냅니다.", ephemeral=True
    )


@bot.tree.command(name="owcs일정", description="다가오는 OWCS 경기 일정을 날짜별 이미지로 보여줍니다")
@discord.app_commands.describe(일수="며칠 이내 일정을 볼지 (기본: 7일)")
async def show_owcs_schedule(interaction: discord.Interaction, 일수: int = 7):
    await interaction.response.defer()
    try:
        schedules = await owcs_module.fetch_schedules()
        upcoming  = owcs_module.get_upcoming(schedules, days=일수)
    except Exception as e:
        await interaction.followup.send(f"일정을 불러오지 못했습니다: {e}", ephemeral=True)
        return

    if not upcoming:
        await interaction.followup.send(f"향후 {일수}일 내 예정된 OWCS 경기가 없습니다.")
        return

    day_groups = owcs_module.group_by_day(upcoming)
    has_ongoing = any(owcs_module.is_ongoing(m) for m in upcoming)

    files = []
    for day_key, day_matches in list(day_groups.items())[:4]:
        buf = await owcs_image.draw_match_day(day_matches)
        files.append(discord.File(buf, filename=f"owcs_{day_key}.png"))

    content = f"📅 **OWCS 경기 일정 (향후 {일수}일)**"
    if has_ongoing:
        content += f"\n🔴 현재 경기 진행 중! 📺 [SOOP 바로가기]({owcs_module.SOOP_URL})"
    content += "\n*출처: Liquipedia*"

    await interaction.followup.send(content=content, files=files)


@bot.tree.command(name="owcs순위", description="OWCS Korea 현재 순위표를 보여줍니다")
async def show_owcs_standings(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        standings = owcs_module.fetch_standings()
    except Exception as e:
        await interaction.followup.send(f"순위를 불러오지 못했습니다: {e}", ephemeral=True)
        return

    if not standings:
        await interaction.followup.send("순위 데이터가 없습니다.", ephemeral=True)
        return

    buf  = await owcs_image.draw_standings(standings)
    file = discord.File(buf, filename="owcs_standings.png")
    await interaction.followup.send(file=file)


@bot.tree.command(name="owcs주차알림설정", description="주차 마지막 경기 종료 후 순위 알림을 받을 채널을 설정합니다")
@discord.app_commands.describe(채널="알림을 받을 채널")
async def set_owcs_week_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    await db.set_owcs_week_channel(interaction.guild_id, 채널.id)
    await interaction.response.send_message(
        f"{채널.mention} 채널에 주차 종료 시 순위표 알림을 보냅니다.", ephemeral=True
    )


bot.run(TOKEN)
