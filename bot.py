import os
import json
import logging
from datetime import datetime, timedelta
import asyncio
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart, Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "9004754440:AAHh3KbECKapVbbSJPyAq5WSGX3_-S4r0Ws"
ADMIN_CHAT_ID = 6243658367

SEND_HOUR = 15
SEND_MINUTE = 30
PORT = int(os.environ.get("PORT", 10000))
# =============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Chisinau")

DAYS_MAP = {
    0: "понедельник", 1: "вторник", 2: "среда",
    3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье"
}

# Веб-сервер для жизни на хостинге
async def web_handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

async def init_db():
    async with aiosqlite.connect("school.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT,
                task_text TEXT,
                photo_id TEXT,
                send_date TEXT,
                is_sent INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule (
                day_index INTEGER PRIMARY KEY,
                subjects_json TEXT
            )
        """)
        await db.commit()

async def get_schedule():
    schedule = {}
    async with aiosqlite.connect("school.db") as db:
        async with db.execute("SELECT day_index, subjects_json FROM schedule") as cursor:
            async for row in cursor:
                schedule[row[0]] = json.loads(row[1])
    return schedule

async def find_lesson_date(subject: str) -> datetime.date | None:
    subject = subject.strip().lower()
    schedule = await get_schedule()
    if not schedule:
        return None

    today = datetime.now().date()
    for offset in range(1, 15):
        target_date = today + timedelta(days=offset)
        weekday = target_date.weekday()
        day_subjects = schedule.get(weekday, [])
        for item in day_subjects:
            if subject in item.lower() or item.lower() in subject:
                return target_date
    return None

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "👋 Бот готов!\n\n"
        "1. Настрой расписание через команду:\n"
        "`/расписание`\n\n"
        "2. Скидывай домашку:\n"
        "• Текстом: `Румынский: упр 3 стр 45`\n"
        "• Фото с доски: отправь фото с подписью `Химия`"
    )

# Настройка расписания прямо в чате
@dp.message(Command("расписание"))
async def set_schedule_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return

    text = msg.text.replace("/расписание", "").strip()
    if not text:
        await msg.answer(
            "📋 Отправь расписание в таком виде:\n\n"
            "`/расписание\n"
            "Пн: Румынский, Алгебра, Химия\n"
            "Вт: Английский, Биология\n"
            "Ср: Физика, Румынский\n"
            "Чт: Химия, Геометрия\n"
            "Пт: История, Литература`"
        )
        return

    day_aliases = {
        "пн": 0, "понедельник": 0, "вт": 1, "вторник": 1,
        "ср": 2, "среда": 2, "чт": 3, "четверг": 3,
        "пт": 4, "пятница": 4, "сб": 5, "суббота": 5, "вс": 6, "воскресенье": 6
    }

    count = 0
    async with aiosqlite.connect("school.db") as db:
        for line in text.split("\n"):
            if ":" in line:
                day_str, subjs = line.split(":", 1)
                day_key = day_str.strip().lower()
                if day_key in day_aliases:
                    idx = day_aliases[day_key]
                    subjects_list = [s.strip().lower() for s in subjs.split(",") if s.strip()]
                    await db.execute(
                        "INSERT OR REPLACE INTO schedule (day_index, subjects_json) VALUES (?, ?)",
                        (idx, json.dumps(subjects_list, ensure_ascii=False))
                    )
                    count += 1
        await db.commit()

    await msg.answer(f"✅ Расписание сохранено (дней: {count})!")

# Прием домашних заданий (фото или текст)
@dp.message(F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    photo_id = None
    raw_text = ""

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        raw_text = (msg.caption or "").strip()
        if not raw_text:
            await msg.answer("⚠️ Ты не подписал предмет! Отправь фото заново с подписью, например: «Химия».")
            return
    else:
        raw_text = (msg.text or "").strip()

    if ":" in raw_text:
        subject, task = raw_text.split(":", 1)
        subject, task = subject.strip(), task.strip()
    else:
        subject, task = raw_text.strip(), ""

    lesson_date = await find_lesson_date(subject)
    if not lesson_date:
        await msg.answer(
            f"❌ Предмет «{subject}» не найден в твоем расписании!\n"
            f"Настрой его командой /расписание"
        )
        return

    if photo_id:
        final_text = f"📸 Задание на фото с доски!\nДополнительно: {task}" if task else "📸 Задание на фото с доски 👆"
    else:
        final_text = f"📝 {task}" if task else "📝 Задание от учителя"

    send_date_str = lesson_date.strftime("%Y-%m-%d")

    async with aiosqlite.connect("school.db") as db:
        await db.execute("""
            INSERT INTO homework (subject, task_text, photo_id, send_date)
            VALUES (?, ?, ?, ?)
        """, (subject, final_text, photo_id, send_date_str))
        await db.commit()

    weekday_ru = DAYS_MAP[lesson_date.weekday()].capitalize()
    confirm = (
        f"✅ **Задание сохранено!**\n\n"
        f"📚 Предмет: **{subject.capitalize()}**\n"
        f"📅 Ближайший урок: **{weekday_ru}, {lesson_date.strftime('%d.%m')}**\n"
        f"⏰ Бот пришлет его: **в этот же день ровно в 15:30**\n\n"
        f"{final_text}"
    )

    if photo_id:
        await msg.answer_photo(photo=photo_id, caption=confirm, parse_mode="Markdown")
    else:
        await msg.answer(confirm, parse_mode="Markdown")

# Отправка в 15:30
async def send_daily():
    today_str = datetime.now().date().strftime("%Y-%m-%d")
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, subject, task_text, photo_id FROM homework WHERE send_date = ? AND is_sent = 0",
            (today_str,)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                text = (
                    f"🔔 **ДОМАШНЕЕ ЗАДАНИЕ НА СЕГОДНЯ**\n"
                    f"📖 Предмет: **{row['subject'].capitalize()}**\n\n"
                    f"{row['task_text']}"
                )
                if row["photo_id"]:
                    await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=row["photo_id"], caption=text, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")

                await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
            await db.commit()

async def main():
    await init_db()
    await start_web_server()
    scheduler.add_job(send_daily, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    print("Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
