import os
import json
import logging
from datetime import datetime, timedelta
import asyncio
import aiosqlite
import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = "9004754440:AAHh3KbECKapVbbSJPyAq5WSGX3_-S4r0Ws"
ADMIN_CHAT_ID = 6243658367
GROQ_API_KEY = "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"

SEND_HOUR = 15
SEND_MINUTE = 30
PORT = int(os.environ.get("PORT", 10000))

# Расписание 8-Б класса
SCHEDULE = {
    0: ["рум. яз.", "англ. яз.", "история", "математика", "физика", "география", "информ."],
    1: ["муз. восп.", "рум. яз.", "история", "болг. яз.", "гв", "рус. яз.", "англ. яз."],
    2: ["рус. язык", "болг. язык", "рус. язык", "физ. восп.", "математика", "биология", "рум. яз."],
    3: ["рум. яз.", "химия", "литература", "литература", "математика", "физика", "физ. восп."],
    4: ["труд", "химия", "биология", "иктбн", "болг. яз.", "математика", "рл"]
}

DAYS_MAP = {
    0: "понедельник", 1: "вторник", 2: "среда",
    3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье"
}
# ===============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone="Europe/Chisinau")

class EditHW(StatesGroup):
    waiting_for_text = State()
    waiting_for_subject = State()

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
        await db.commit()

async def sync_backup():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text, photo_id, send_date FROM homework WHERE is_sent = 0") as cursor:
            rows = await cursor.fetchall()
            data = [dict(row) for row in rows]

    backup_text = f"BACKUP_DATA:\n{json.dumps(data, ensure_ascii=False)}"
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
            json_str = pinned.text.split("BACKUP_DATA:\n", 1)[1]
            data = json.loads(json_str)
            async with aiosqlite.connect("school.db") as db:
                for item in data:
                    async with db.execute(
                        "SELECT id FROM homework WHERE id = ?",
                        (item.get("id"),)
                    ) as cursor:
                        if not await cursor.fetchone():
                            await db.execute(
                                "INSERT INTO homework (id, subject, task_text, photo_id, send_date) VALUES (?, ?, ?, ?, ?)",
                                (item.get("id"), item["subject"], item["task_text"], item["photo_id"], item["send_date"])
                            )
                await db.commit()
            print("База восстановлена из резервной копии.")
    except Exception as e:
        logging.error(f"Ошибка восстановления: {e}")

async def improve_with_ai(subject: str, raw_task: str) -> str:
    prompt = f"""Ты помощник старосты школьного класса.
Твоя задача — сделать черновик домашнего задания понятным, аккуратным и структурированным.

Предмет: {subject}
Черновик: {raw_task}

Правила:
1. Исправь грамматические и пунктуационные ошибки, оформи пункты понятно.
2. СТРОЖАЙШЕЕ ПРАВИЛО: Не изменяй, не перефразируй и не сокращай названия тем, термины и цитаты. Если написано "объяснить смысл темы X", само название темы X должно остаться точно в таком виде, как написал автор.
3. Если в задании чего-то явно не хватает для понимания (например: сказано перевести текст, но нет страницы; сказано сделать номер, но нет номера упражнения), напиши в самом конце отдельной строкой:
Вопрос для уточнения: [напиши, что именно нужно уточнить]
4. Ни в коем случае не используй смайлики и эмодзи.
5. Выведи только готовый итоговый текст задания без вступительных фраз.
"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                }
            )
            if resp.status_code == 200:
                result = resp.json()
                return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"Ошибка вызова ИИ: {e}")
    return raw_task

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
        "лит": "литература", "литература": "литература", "лит-ра": "литература",
        "рл": "рл", "развитие": "рл", "разв": "рл", "развитие личности": "рл",
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
    for offset in range(1, 15):
        target_date = today + timedelta(days=offset)
        weekday = target_date.weekday()
        day_subjects = SCHEDULE.get(weekday, [])
        for real_subj in day_subjects:
            if norm_subj in real_subj or real_subj in norm_subj:
                return target_date, real_subj.upper()
    return None, None

def make_hw_kb(hw_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"del_hw:{hw_id}"),
            InlineKeyboardButton(text="Изменить текст", callback_data=f"edit_text:{hw_id}"),
            InlineKeyboardButton(text="Изменить предмет", callback_data=f"edit_subj:{hw_id}")
        ]
    ])

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "Бот 8-Б класса готов к работе.\n\n"
        "Команды:\n"
        "/dz — список всех сохраненных заданий (можно удалить или изменить)\n"
        "/расписание — расписание уроков на неделю\n"
        "/cancel — отмена текущего действия\n\n"
        "Отправка заданий:\n"
        "- Фото доски: фото с названием предмета в подписи (например: Литература или Химия: задачи 1 и 2)\n"
        "- Текстом: Литература: объяснить смысл темы 'Понятие о чести'"
    )

@dp.message(Command("cancel"))
async def cancel_fsm(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("Нет активных действий для отмены.")
        return
    await state.clear()
    await msg.answer("Действие отменено.")

@dp.message(Command("расписание"))
async def view_schedule_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    lines = ["Расписание 8-Б класса:"]
    for day_idx in range(5):
        day_name = DAYS_MAP[day_idx].capitalize()
        lines.append(f"\n{day_name}:")
        for num, subj in enumerate(SCHEDULE[day_idx], start=1):
            lines.append(f"  {num}. {subj.capitalize()}")
    await msg.answer("\n".join(lines))

@dp.message(Command("dz"))
async def view_homework_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, subject, task_text, photo_id, send_date FROM homework WHERE is_sent = 0 ORDER BY send_date ASC"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await msg.answer("Активных сохраненных заданий нет.")
        return

    await msg.answer(f"Всего заданий в очереди: {len(rows)}")
    for row in rows:
        text = (
            f"Задание #{row['id']}\n"
            f"Предмет: {row['subject']}\n"
            f"Дата отправки: {row['send_date']} в 15:30\n"
            f"Содержание:\n{row['task_text']}"
        )
        kb = make_hw_kb(row["id"])
        if row["photo_id"]:
            await msg.answer_photo(photo=row["photo_id"], caption=text, reply_markup=kb)
        else:
            await msg.answer(text=text, reply_markup=kb)

@dp.callback_query(F.data.startswith("del_hw:"))
async def delete_hw_callback(call: CallbackQuery):
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
        await db.commit()
    await sync_backup()
    await call.answer("Задание удалено.")
    del_text = f"Задание #{hw_id} удалено."
    if call.message.photo:
        await call.message.edit_caption(caption=del_text)
    else:
        await call.message.edit_text(text=del_text)

@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_text)
    await call.answer()
    await call.message.reply(f"Отправь новый текст задания для #{hw_id}.\nДля отмены напиши /cancel.")

@dp.message(EditHW.waiting_for_text, F.from_user.id == ADMIN_CHAT_ID)
async def edit_text_finish(msg: Message, state: FSMContext):
    new_text = msg.text or msg.caption or ""
    if not new_text:
        await msg.answer("Текст не может быть пустым. Отправь новый текст или напиши /cancel.")
        return
    data = await state.get_data()
    hw_id = data["hw_id"]

    wait_msg = await msg.answer("Обработка текста через ИИ...")
    async with aiosqlite.connect("school.db") as db:
        async with db.execute("SELECT subject FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
            subj = row[0] if row else "Предмет"

    better_text = await improve_with_ai(subj, new_text)
    await wait_msg.delete()

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (better_text, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()
    await msg.answer(f"Текст задания #{hw_id} успешно обновлен на:\n{better_text}")

@dp.callback_query(F.data.startswith("edit_subj:"))
async def edit_subj_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_subject)
    await call.answer()
    await call.message.reply(f"Отправь новое название предмета для #{hw_id}.\nДля отмены напиши /cancel.")

@dp.message(EditHW.waiting_for_subject, F.from_user.id == ADMIN_CHAT_ID)
async def edit_subj_finish(msg: Message, state: FSMContext):
    new_subj = (msg.text or "").strip()
    lesson_date, matched_name = find_next_lesson_date(new_subj)
    if not lesson_date:
        await msg.answer(f"Предмет «{new_subj}» не найден в расписании. Попробуй еще раз или напиши /cancel.")
        return

    data = await state.get_data()
    hw_id = data["hw_id"]
    send_date_str = lesson_date.strftime("%Y-%m-%d")

    async with aiosqlite.connect("school.db") as db:
        await db.execute(
            "UPDATE homework SET subject = ?, send_date = ? WHERE id = ?",
            (matched_name, send_date_str, hw_id)
        )
        await db.commit()

    await sync_backup()
    await state.clear()
    weekday_ru = DAYS_MAP[lesson_date.weekday()].capitalize()
    await msg.answer(
        f"Предмет для задания #{hw_id} изменен на: {matched_name}\n"
        f"Новая дата отправки: {weekday_ru}, {lesson_date.strftime('%d.%m')} в 15:30"
    )

@dp.message(StateFilter(None), F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    photo_id = None
    raw_text = ""

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        raw_text = (msg.caption or "").strip()
        if not raw_text:
            await msg.answer("Ошибка: фото отправлено без названия предмета. Напиши предмет в подписи к фото.")
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
        await msg.answer(f"Предмет «{subject}» не найден в расписании 8-Б класса.")
        return

    if photo_id:
        if task:
            wait_msg = await msg.answer("Обработка подписи к фото...")
            refined_task = await improve_with_ai(matched_name, task)
            await wait_msg.delete()
            final_text = f"Задание на фото с доски.\nПримечание: {refined_task}"
        else:
            final_text = "Задание на фото с доски."
    else:
        wait_msg = await msg.answer("Обработка текста через ИИ...")
        final_text = await improve_with_ai(matched_name, task if task else "Смотри задание")
        await wait_msg.delete()

    send_date_str = lesson_date.strftime("%Y-%m-%d")

    async with aiosqlite.connect("school.db") as db:
        cursor = await db.execute("""
            INSERT INTO homework (subject, task_text, photo_id, send_date)
            VALUES (?, ?, ?, ?)
        """, (matched_name, final_text, photo_id, send_date_str))
        hw_id = cursor.lastrowid
        await db.commit()

    await sync_backup()

    weekday_ru = DAYS_MAP[lesson_date.weekday()].capitalize()
    confirm = (
        f"Задание #{hw_id} сохранено.\n\n"
        f"Предмет: {matched_name}\n"
        f"Ближайший урок: {weekday_ru}, {lesson_date.strftime('%d.%m')}\n"
        f"Отправка: в день урока в 15:30\n\n"
        f"Запись:\n{final_text}"
    )

    kb = make_hw_kb(hw_id)
    if photo_id:
        await msg.answer_photo(photo=photo_id, caption=confirm, reply_markup=kb)
    else:
        await msg.answer(confirm, reply_markup=kb)

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
                    f"ДОМАШНЕЕ ЗАДАНИЕ НА СЕГОДНЯ\n"
                    f"Предмет: {row['subject']}\n\n"
                    f"{row['task_text']}"
                )
                if row["photo_id"]:
                    await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=row["photo_id"], caption=text)
                else:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)

                await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
            await db.commit()

    await sync_backup()

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    
    scheduler.add_job(send_daily, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    print("Бот 8-Б класса запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
