import os
import json
import logging
from datetime import datetime, timedelta
import asyncio
import aiosqlite
import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
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

# Постоянная клавиатура с кнопками внизу экрана
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📚 Список ДЗ"), KeyboardButton(text="📅 Расписание")],
        [KeyboardButton(text="❌ Отмена")]
    ],
    resize_keyboard=True
)

class EditHW(StatesGroup):
    waiting_for_text = State()
    waiting_for_subject = State()

def to_quote(text: str) -> str:
    lines = text.strip().split("\n")
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)

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
⚠️ Вопрос для уточнения: [напиши, что именно нужно уточнить]
4. Сделай текст читаемым и красивым.
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
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_hw:{hw_id}"),
            InlineKeyboardButton(text="✏️ Текст", callback_data=f"edit_text:{hw_id}"),
            InlineKeyboardButton(text="🔄 Предмет", callback_data=f"edit_subj:{hw_id}")
        ]
    ])

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "👋 **Бот 8-Б класса готов к работе!**\n\n"
        "Управляй ботом с помощью кнопок снизу клавиатуры:\n"
        "• **📚 Список ДЗ** — посмотреть задания, удалить или изменить\n"
        "• **📅 Расписание** — узнать уроки на неделю\n"
        "• **❌ Отмена** — сбросить редактирование\n\n"
        "📝 **Как отправить задание на перемене:**\n"
        "• Текстом: `Румынский: выучить слова на стр 15`\n"
        "• Фото с доски: прикрепи фото, а в подписи напиши предмет (например, `Химия` или `Химия: упр 2`)",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown"
    )

# Отмена действия через кнопку или команду
@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def cancel_fsm(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("👌 Нет активных действий для отмены.", reply_markup=MAIN_KEYBOARD)
        return
    await state.clear()
    await msg.answer("❌ Действие отменено.", reply_markup=MAIN_KEYBOARD)

# Просмотр расписания
@dp.message(F.text == "📅 Расписание")
@dp.message(Command("расписание"))
async def view_schedule_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    lines = ["📅 **Расписание 8-Б класса:**\n"]
    for day_idx in range(5):
        day_name = DAYS_MAP[day_idx].capitalize()
        lines.append(f"🔹 **{day_name}:**")
        for num, subj in enumerate(SCHEDULE[day_idx], start=1):
            lines.append(f"  {num}. {subj.capitalize()}")
        lines.append("")
    await msg.answer("\n".join(lines), reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")

# Просмотр всех сохраненных ДЗ (с динамической нумерацией 1 из N, днями недели и цитированием)
@dp.message(F.text == "📚 Список ДЗ")
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
        await msg.answer("📭 Активных сохраненных заданий нет. В очереди пусто!", reply_markup=MAIN_KEYBOARD)
        return

    total = len(rows)
    await msg.answer(f"📋 **В очереди заданий: {total}**\nНиже карточки каждого урока:", reply_markup=MAIN_KEYBOARD, parse_mode="Markdown")

    for idx, row in enumerate(rows, start=1):
        target_date = datetime.strptime(row["send_date"], "%Y-%m-%d").date()
        weekday_name = DAYS_MAP[target_date.weekday()].capitalize()
        formatted_date = f"{weekday_name}, {target_date.strftime('%d.%m')}"

        quoted_task = to_quote(row["task_text"])

        card_text = (
            f"📌 **Задание {idx} из {total}**\n"
            f"📚 Предмет: **{row['subject']}**\n"
            f"📅 Отправка: **{formatted_date} в 15:30**\n\n"
            f"📝 **Что задано:**\n"
            f"{quoted_task}"
        )

        kb = make_hw_kb(row["id"])
        if row["photo_id"]:
            await msg.answer_photo(photo=row["photo_id"], caption=card_text, reply_markup=kb, parse_mode="Markdown")
        else:
            await msg.answer(text=card_text, reply_markup=kb, parse_mode="Markdown")

# Удаление ДЗ
@dp.callback_query(F.data.startswith("del_hw:"))
async def delete_hw_callback(call: CallbackQuery):
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
        await db.commit()
    await sync_backup()
    await call.answer("🗑 Задание удалено.")
    del_text = "🗑 **Задание успешно удалено из очереди.**\nЧтобы обновить нумерацию, нажми «📚 Список ДЗ»."
    if call.message.photo:
        await call.message.edit_caption(caption=del_text, parse_mode="Markdown")
    else:
        await call.message.edit_text(text=del_text, parse_mode="Markdown")

# Изменение текста: запрос
@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_text)
    await call.answer()
    await call.message.reply("✏️ **Отправь новый текст задания сообщением.**\nЕсли передумал, нажми кнопку «❌ Отмена».", parse_mode="Markdown")

# Изменение текста: сохранение
@dp.message(EditHW.waiting_for_text, F.from_user.id == ADMIN_CHAT_ID)
async def edit_text_finish(msg: Message, state: FSMContext):
    new_text = msg.text or msg.caption or ""
    if not new_text:
        await msg.answer("⚠️ Текст не может быть пустым. Отправь текст задания заново.")
        return
    data = await state.get_data()
    hw_id = data["hw_id"]

    wait_msg = await msg.answer("🧠 ИИ обрабатывает новый текст...")
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
    
    quoted_new = to_quote(better_text)
    await msg.answer(
        f"✅ **Текст задания успешно обновлен!**\n\n"
        f"📝 **Новая формулировка:**\n{quoted_new}",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown"
    )

# Изменение предмета: запрос
@dp.callback_query(F.data.startswith("edit_subj:"))
async def edit_subj_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_subject)
    await call.answer()
    await call.message.reply("🔄 **Напиши новое название предмета.**\nЕсли передумал, нажми кнопку «❌ Отмена».", parse_mode="Markdown")

# Изменение предмета: сохранение и пересчет даты
@dp.message(EditHW.waiting_for_subject, F.from_user.id == ADMIN_CHAT_ID)
async def edit_subj_finish(msg: Message, state: FSMContext):
    new_subj = (msg.text or "").strip()
    lesson_date, matched_name = find_next_lesson_date(new_subj)
    if not lesson_date:
        await msg.answer(f"❌ Предмет «{new_subj}» не найден в расписании 8-Б класса. Попробуй еще раз или нажми «❌ Отмена».")
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
        f"✅ **Предмет успешно изменен!**\n\n"
        f"📚 Новый предмет: **{matched_name}**\n"
        f"📅 Новая дата отправки: **{weekday_ru}, {lesson_date.strftime('%d.%m')} в 15:30**",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="Markdown"
    )

# Прием нового задания
@dp.message(StateFilter(None), F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    photo_id = None
    raw_text = ""

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        raw_text = (msg.caption or "").strip()
        if not raw_text:
            await msg.answer("⚠️ Ты отправил фото без названия предмета! Напиши предмет в подписи к фото (например: `Химия`).")
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
        await msg.answer(f"❌ Предмет «{subject}» не найден в расписании 8-Б класса. Проверь название в расписании!")
        return

    # Обработка через ИИ
    if photo_id:
        if task:
            wait_msg = await msg.answer("🧠 ИИ форматирует подпись...")
            refined_task = await improve_with_ai(matched_name, task)
            await wait_msg.delete()
            final_text = f"📸 Задание на фото с доски!\nПримечание: {refined_task}"
        else:
            final_text = "📸 Задание на фото с доски 👆"
    else:
        wait_msg = await msg.answer("🧠 ИИ делает текст понятнее...")
        final_text = await improve_with_ai(matched_name, task if task else "Смотри задание от учителя")
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
    quoted_task = to_quote(final_text)
    
    confirm = (
        f"✅ **Задание успешно сохранено!**\n\n"
        f"📚 Предмет: **{matched_name}**\n"
        f"📅 Ближайший урок: **{weekday_ru}, {lesson_date.strftime('%d.%m')}**\n"
        f"⏰ Отправка: **в этот же день в 15:30**\n\n"
        f"📝 **Что будет отправлено:**\n"
        f"{quoted_task}"
    )

    kb = make_hw_kb(hw_id)
    if photo_id:
        await msg.answer_photo(photo=photo_id, caption=confirm, reply_markup=kb, parse_mode="Markdown")
    else:
        await msg.answer(confirm, reply_markup=kb, parse_mode="Markdown")

# Ежедневная отправка в 15:30
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
                quoted = to_quote(row['task_text'])
                text = (
                    f"🔔 **ДОМАШНЕЕ ЗАДАНИЕ НА СЕГОДНЯ**\n"
                    f"📖 Предмет: **{row['subject']}**\n\n"
                    f"{quoted}\n\n"
                    f"*(Перешли это сообщение в группу класса)*"
                )
                if row["photo_id"]:
                    await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=row["photo_id"], caption=text, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")

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
