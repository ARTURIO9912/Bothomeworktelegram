import os
import json
import logging
from datetime import datetime, timedelta
import asyncio
import aiosqlite
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "9004754440:AAHh3KbECKapVbbSJPyAq5WSGX3_-S4r0Ws"
ADMIN_CHAT_ID = 6243658367

SEND_HOUR = 15
SEND_MINUTE = 30
PORT = int(os.environ.get("PORT", 10000))

# ТВОЕ ТОЧНОЕ РАСПИСАНИЕ (8-Б КЛАСС)
SCHEDULE = {
    0: ["рум. яз.", "англ. яз.", "история", "математика", "физика", "география", "информ."],
    1: ["муз. восп.", "рум. яз.", "история", "болг. яз.", "гв", "рус. яз.", "англ. яз."],
    2: ["рус. язык", "болг. язык", "рус. язык", "физ. восп.", "математика", "биология", "рум. яз."],
    3: ["рум. яз.", "химия", "рус. яз.", "рус. яз.", "математика", "физика", "физ. восп."],
    4: ["труд", "химия", "биология", "иктбн", "болг. яз.", "математика", "рл"]
}

DAYS_MAP = {
    0: "понедельник", 1: "вторник", 2: "среда",
    3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье"
}
# ===============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Chisinau")

# Фоновый сервер для Render
async def web_handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", web_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

# --- База данных ---
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
        await db.commit()

# --- Резервная копия в Telegram (чтобы ДЗ не пропадало при перезапуске сервера) ---
async def sync_backup():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT subject, task_text, photo_id, send_date FROM homework WHERE is_sent = 0") as cursor:
            rows = await cursor.fetchall()
            data = [dict(row) for row in rows]

    backup_text = f"📦 BACKUP_DATA:\n{json.dumps(data, ensure_ascii=False)}"
    
    try:
        chat = await bot.get_chat(ADMIN_CHAT_ID)
        pinned = chat.pinned_message
        if pinned and "BACKUP_DATA" in (pinned.text or ""):
            await bot.edit_message_text(backup_text, chat_id=ADMIN_CHAT_ID, message_id=pinned.message_id)
        else:
            msg = await bot.send_message(ADMIN_CHAT_ID, backup_text, disable_notification=True)
            await bot.pin_chat_message(ADMIN_CHAT_ID, msg.message_id, disable_notification=True)
    except Exception as e:
        logging.error(f"Ошибка бэкапа: {e}")

async def restore_from_backup():
    try:
        chat = await bot.get_chat(ADMIN_CHAT_ID)
        pinned = chat.pinned_message
        if pinned and "BACKUP_DATA" in (pinned.text or ""):
            json_str = pinned.text.split("📦 BACKUP_DATA:\n", 1)[1]
            data = json.loads(json_str)
            async with aiosqlite.connect("school.db") as db:
                for item in data:
                    # Проверяем, есть ли уже такая запись
                    async with db.execute(
                        "SELECT id FROM homework WHERE subject = ? AND send_date = ? AND is_sent = 0",
                        (item["subject"], item["send_date"])
                    ) as cursor:
                        if not await cursor.fetchone():
                            await db.execute(
                                "INSERT INTO homework (subject, task_text, photo_id, send_date) VALUES (?, ?, ?, ?)",
                                (item["subject"], item["task_text"], item["photo_id"], item["send_date"])
                            )
                await db.commit()
            print("Успешно восстановлено ДЗ из резервной копии!")
    except Exception as e:
        logging.error(f"Ошибка восстановления: {e}")

# --- Умный поиск предмета ---
def normalize_subject(name: str) -> str:
    s = name.strip().lower()
    aliases = {
        "рум": "рум. яз.", "румынский": "рум. яз.", "рум яз": "рум. яз.",
        "болг": "болг. яз.", "болгарский": "болг. яз.", "болг яз": "болг. яз.",
        "рус": "рус. яз.", "русский": "рус. яз.", "рус яз": "рус. яз.",
        "мат": "математика", "матеша": "математика", "алгебра": "математика", "геометрия": "математика",
        "физ": "физика",
        "био": "биология",
        "хим": "химия",
        "гео": "география",
        "инф": "информ.", "информатика": "информ.", "икт": "иктбн",
        "ист": "история",
        "англ": "англ. яз.", "английский": "англ. яз.",
        "физра": "физ. восп.", "физкультура": "физ. восп.",
        "муз": "муз. восп.", "музыка": "муз. восп.",
        "рл": "рл", "лит": "рл", "литература": "рл",
        "труд": "труд", "труды": "труд",
        "гв": "гв"
    }
    for key, val in aliases.items():
        if key in s:
            return val
    return s

def find_next_lesson_date(subj_query: str) -> tuple[datetime.date, str] | tuple[None, None]:
    norm_subj = normalize_subject(subj_query)
    today = datetime.now().date()
    
    # Ищем урок на ближайшие 14 дней (начиная со следующего дня)
    for offset in range(1, 15):
        target_date = today + timedelta(days=offset)
        weekday = target_date.weekday()
        day_subjects = SCHEDULE.get(weekday, [])
        for real_subj in day_subjects:
            if norm_subj in real_subj or real_subj in norm_subj:
                return target_date, real_subj.upper()
    return None, None

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "👋 **Бот 8-Б класса готов!**\n\n"
        "Расписание уже загружено. Просто отправляй ДЗ на переменках:\n"
        "• **Фото доски:** прикрепи фото и подпиши `Химия` (или `Химия: упр 2`)\n"
        "• **Текстом:** `Румынский: выучить слова на стр 15`\n\n"
        "⏰ Бот сам найдет ближайший день этого урока и пришлет всё ровно в **15:30**."
    )

# --- Прием домашних заданий ---
@dp.message(F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    photo_id = None
    raw_text = ""

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        raw_text = (msg.caption or "").strip()
        if not raw_text:
            await msg.answer("⚠️ Ты отправил фото без названия предмета! Напиши в подписи, например: `Химия`.")
            return
    else:
        raw_text = (msg.text or "").strip()

    if ":" in raw_text:
        subject, task = raw_text.split(":", 1)
        subject, task = subject.strip(), task.strip()
    else:
        subject, task = raw_text.strip(), ""

    lesson_date, matched_name = find_next_lesson_date(subject)
    if not lesson_date:
        await msg.answer(f"❌ Не нашел предмет «{subject}» в расписании 8-Б класса. Проверь название!")
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
        """, (matched_name, final_text, photo_id, send_date_str))
        await db.commit()

    # Синхронизируем бэкап
    await sync_backup()

    weekday_ru = DAYS_MAP[lesson_date.weekday()].capitalize()
    confirm = (
        f"✅ **Записано в копилку!**\n\n"
        f"📚 Предмет: **{matched_name}**\n"
        f"📅 Ближайший урок: **{weekday_ru}, {lesson_date.strftime('%d.%m')}**\n"
        f"⏰ Отправка: **в этот же день в 15:30**\n\n"
        f"{final_text}"
    )

    if photo_id:
        await msg.answer_photo(photo=photo_id, caption=confirm, parse_mode="Markdown")
    else:
        await msg.answer(confirm, parse_mode="Markdown")

# --- Отправка в 15:30 ---
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
                    f"📖 Предмет: **{row['subject']}**\n\n"
                    f"{row['task_text']}"
                )
                if row["photo_id"]:
                    await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=row["photo_id"], caption=text, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")

                await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
            await db.commit()

    # Обновляем бэкап после отправки
    await sync_backup()

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    
    scheduler.add_job(send_daily, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    print("Бот 8-Б класса успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
