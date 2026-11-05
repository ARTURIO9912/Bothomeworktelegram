import os
import json
import html
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
    waiting_for_ai_prompt = State()

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
                lesson_date TEXT,
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

# Автоматический пересчет существующих дат в базе (сдвиг на день раньше)
async def fix_existing_dates():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject FROM homework WHERE is_sent = 0") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                lesson_d, _ = find_next_lesson_date(r["subject"])
                if lesson_d:
                    reminder_d = lesson_d - timedelta(days=1)
                    await db.execute(
                        "UPDATE homework SET send_date = ? WHERE id = ?",
                        (reminder_d.strftime("%Y-%m-%d"), r["id"])
                    )
        await db.commit()
    await sync_backup()
    print("Существующие даты успешно обновлены на день накануне!")

async def improve_with_ai(subject: str, raw_task: str, user_instruction: str = "") -> str:
    instruction_part = f"Дополнительное указание от ученика: {user_instruction}" if user_instruction else "Сделай текст аккуратным, понятным и структурированным."

    prompt = f"""Ты помощник старосты школьного класса.
Твоя задача — оформить черновик домашнего задания.

Предмет: {subject}
Черновик задания: {raw_task}
{instruction_part}

Правила:
1. Исправь грамматические и пунктуационные ошибки, оформи красиво.
2. СТРОЖАЙШЕЕ ПРАВИЛО: Не изменяй, не перефразируй и не сокращай названия тем, термины и цитаты. Если написано "объяснить смысл темы X", само название темы X должно остаться точно в таком виде, как написал автор.
3. Если в задании чего-то явно не хватает для понимания (например: сказано перевести текст, но нет страницы; сказано сделать номер, но нет номера упражнения), напиши в самом конце отдельной строкой:
⚠️ Вопрос для уточнения: [напиши, что именно нужно уточнить]
4. Выведи только готовый итоговый текст задания без вступительных слов и без смайликов-пустышек.
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
            InlineKeyboardButton(text="✏️ Свой текст", callback_data=f"edit_text:{hw_id}")
        ],
        [
            InlineKeyboardButton(text="🤖 Попросить ИИ переписать", callback_data=f"ai_edit:{hw_id}"),
            InlineKeyboardButton(text="🔄 Сменить предмет", callback_data=f"edit_subj:{hw_id}")
        ]
    ])

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "👋 <b>Бот 8-Б класса готов к работе!</b>\n\n"
        "Управляй ботом через кнопки снизу:\n"
        "• <b>📚 Список ДЗ</b> — просмотр всех заданий, удаление и редактирование\n"
        "• <b>📅 Расписание</b> — уроки на неделю\n"
        "• <b>❌ Отмена</b> — отменить редактирование\n\n"
        "📝 <b>Как отправить задание на перемене:</b>\n"
        "• Текстом: <code>Румынский: выучить слова на стр 15</code>\n"
        "• Фото с доски: прикрепи фото с подписью предмета (например, <code>Химия</code> или <code>Химия: упр 2</code>)",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

@dp.message(F.text == "❌ Отмена")
@dp.message(Command("cancel"))
async def cancel_fsm(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("👌 Нет активных действий для отмены.", reply_markup=MAIN_KEYBOARD, parse_mode="HTML")
        return
    await state.clear()
    await msg.answer("❌ Действие отменено.", reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

@dp.message(F.text == "📅 Расписание")
@dp.message(Command("расписание"))
async def view_schedule_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    lines = ["📅 <b>Расписание 8-Б класса:</b>\n"]
    for day_idx in range(5):
        day_name = DAYS_MAP[day_idx].capitalize()
        lines.append(f"🔹 <b>{day_name}:</b>")
        for num, subj in enumerate(SCHEDULE[day_idx], start=1):
            lines.append(f"  {num}. {html.escape(subj.capitalize())}")
        lines.append("")
    await msg.answer("\n".join(lines), reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

# Список всех ДЗ (отправка строго НАКАНУНЕ в 15:30)
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
        await msg.answer("📭 Активных сохраненных заданий нет. В очереди пусто!", reply_markup=MAIN_KEYBOARD, parse_mode="HTML")
        return

    total = len(rows)
    await msg.answer(f"📋 <b>В очереди заданий: {total}</b>\nНиже карточки каждого урока:", reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

    for idx, row in enumerate(rows, start=1):
        send_date_obj = datetime.strptime(row["send_date"], "%Y-%m-%d").date()
        lesson_date_obj = send_date_obj + timedelta(days=1)
        
        send_weekday = DAYS_MAP[send_date_obj.weekday()].capitalize()
        lesson_weekday = DAYS_MAP[lesson_date_obj.weekday()].capitalize()

        escaped_task = html.escape(row["task_text"])

        card_text = (
            f"📌 <b>Задание {idx} из {total}</b>\n"
            f"📚 Предмет: <b>{html.escape(row['subject'])}</b>\n"
            f"📅 Урок будет: <b>{lesson_weekday}, {lesson_date_obj.strftime('%d.%m')}</b>\n"
            f"⏰ Отправка: <b>{send_weekday}, {send_date_obj.strftime('%d.%m')} в 15:30</b> (накануне)\n\n"
            f"📝 <b>Что задано:</b>\n"
            f"<blockquote>{escaped_task}</blockquote>"
        )

        kb = make_hw_kb(row["id"])
        if row["photo_id"]:
            await msg.answer_photo(photo=row["photo_id"], caption=card_text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.answer(text=card_text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("del_hw:"))
async def delete_hw_callback(call: CallbackQuery):
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
        await db.commit()
    await sync_backup()
    await call.answer("🗑 Задание удалено.")
    del_text = "🗑 <b>Задание успешно удалено из очереди.</b>\nЧтобы увидеть обновленный список, нажми «📚 Список ДЗ»."
    if call.message.photo:
        await call.message.edit_caption(caption=del_text, parse_mode="HTML")
    else:
        await call.message.edit_text(text=del_text, parse_mode="HTML")

# Ручное изменение текста
@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_text)
    await call.answer()
    await call.message.reply("✏️ <b>Отправь свой новый текст задания сообщением.</b>\nЕсли передумал, нажми «❌ Отмена».", parse_mode="HTML")

@dp.message(EditHW.waiting_for_text, F.from_user.id == ADMIN_CHAT_ID)
async def edit_text_finish(msg: Message, state: FSMContext):
    new_text = msg.text or msg.caption or ""
    if not new_text:
        await msg.answer("⚠️ Текст не может быть пустым. Отправь текст задания заново.", parse_mode="HTML")
        return
    data = await state.get_data()
    hw_id = data["hw_id"]

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (new_text, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()
    
    escaped_new = html.escape(new_text)
    await msg.answer(
        f"✅ <b>Текст задания успешно обновлен!</b>\n\n"
        f"📝 <b>Новая формулировка:</b>\n<blockquote>{escaped_new}</blockquote>",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

# Изменение через ИИ
@dp.callback_query(F.data.startswith("ai_edit:"))
async def ai_edit_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_ai_prompt)
    await call.answer()
    await call.message.reply(
        "🤖 <b>Редактирование с помощью ИИ:</b>\n\n"
        "Напиши, что именно изменить (например: <i>«сделай короче»</i>, <i>«разбей на пункты 1, 2, 3»</i>).\n\n"
        "Или просто отправь знак плюс <b>+</b>, чтобы ИИ автоматически переписал текст понятнее и красивее.\n\n"
        "Для отмены нажми кнопку «❌ Отмена».",
        parse_mode="HTML"
    )

@dp.message(EditHW.waiting_for_ai_prompt, F.from_user.id == ADMIN_CHAT_ID)
async def ai_edit_finish(msg: Message, state: FSMContext):
    user_instruction = (msg.text or "").strip()
    if user_instruction == "+":
        user_instruction = ""

    data = await state.get_data()
    hw_id = data["hw_id"]

    wait_msg = await msg.answer("🧠 ИИ переписывает задание...", parse_mode="HTML")

    async with aiosqlite.connect("school.db") as db:
        async with db.execute("SELECT subject, task_text FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
            subj, old_text = row if row else ("Предмет", "")

    better_text = await improve_with_ai(subj, old_text, user_instruction)
    await wait_msg.delete()

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (better_text, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()

    escaped_better = html.escape(better_text)
    await msg.answer(
        f"✅ <b>ИИ успешно переписал задание!</b>\n\n"
        f"📝 <b>Результат:</b>\n<blockquote>{escaped_better}</blockquote>",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

# Смена предмета (дата ставится накануне урока)
@dp.callback_query(F.data.startswith("edit_subj:"))
async def edit_subj_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_subject)
    await call.answer()
    await call.message.reply("🔄 <b>Напиши новое название предмета.</b>\nЕсли передумал, нажми кнопку «❌ Отмена».", parse_mode="HTML")

@dp.message(EditHW.waiting_for_subject, F.from_user.id == ADMIN_CHAT_ID)
async def edit_subj_finish(msg: Message, state: FSMContext):
    new_subj = (msg.text or "").strip()
    lesson_date, matched_name = find_next_lesson_date(new_subj)
    if not lesson_date:
        await msg.answer(f"❌ Предмет «{html.escape(new_subj)}» не найден в расписании 8-Б класса. Попробуй еще раз или нажми «❌ Отмена».", parse_mode="HTML")
        return

    # Отправка строго НАКАНУНЕ
    reminder_date = lesson_date - timedelta(days=1)
    send_date_str = reminder_date.strftime("%Y-%m-%d")

    data = await state.get_data()
    hw_id = data["hw_id"]

    async with aiosqlite.connect("school.db") as db:
        await db.execute(
            "UPDATE homework SET subject = ?, send_date = ? WHERE id = ?",
            (matched_name, send_date_str, hw_id)
        )
        await db.commit()

    await sync_backup()
    await state.clear()
    
    send_weekday = DAYS_MAP[reminder_date.weekday()].capitalize()
    lesson_weekday = DAYS_MAP[lesson_date.weekday()].capitalize()

    await msg.answer(
        f"✅ <b>Предмет успешно изменен!</b>\n\n"
        f"📚 Новый предмет: <b>{html.escape(matched_name)}</b>\n"
        f"📅 Урок будет: <b>{lesson_weekday}, {lesson_date.strftime('%d.%m')}</b>\n"
        f"⏰ Отправка: <b>{send_weekday}, {reminder_date.strftime('%d.%m')} в 15:30</b> (накануне)",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

# Прием нового задания (отправка за день до урока в 15:30)
@dp.message(StateFilter(None), F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    photo_id = None
    raw_text = ""

    if msg.photo:
        photo_id = msg.photo[-1].file_id
        raw_text = (msg.caption or "").strip()
        if not raw_text:
            await msg.answer("⚠️ Ты отправил фото без названия предмета! Напиши предмет в подписи к фото (например: <code>Химия</code>).", parse_mode="HTML")
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
        await msg.answer(f"❌ Предмет «{html.escape(subject)}» не найден в расписании 8-Б класса. Проверь название в расписании!", parse_mode="HTML")
        return

    if photo_id:
        if task:
            wait_msg = await msg.answer("🧠 ИИ форматирует подпись к фото...", parse_mode="HTML")
            refined_task = await improve_with_ai(matched_name, task)
            await wait_msg.delete()
            final_text = f"📸 Задание на фото с доски!\nПримечание: {refined_task}"
        else:
            final_text = "📸 Задание на фото с доски 👆"
    else:
        wait_msg = await msg.answer("🧠 ИИ делает текст понятнее...", parse_mode="HTML")
        final_text = await improve_with_ai(matched_name, task if task else "Смотри задание от учителя")
        await wait_msg.delete()

    # НАПОМИНАНИЕ СТРОГО НАКАНУНЕ
    reminder_date = lesson_date - timedelta(days=1)
    send_date_str = reminder_date.strftime("%Y-%m-%d")

    async with aiosqlite.connect("school.db") as db:
        cursor = await db.execute("""
            INSERT INTO homework (subject, task_text, photo_id, send_date)
            VALUES (?, ?, ?, ?)
        """, (matched_name, final_text, photo_id, send_date_str))
        hw_id = cursor.lastrowid
        await db.commit()

    await sync_backup()

    send_weekday = DAYS_MAP[reminder_date.weekday()].capitalize()
    lesson_weekday = DAYS_MAP[lesson_date.weekday()].capitalize()
    escaped_task = html.escape(final_text)
    
    confirm = (
        f"✅ <b>Задание успешно сохранено!</b>\n\n"
        f"📚 Предмет: <b>{html.escape(matched_name)}</b>\n"
        f"📅 Урок будет: <b>{lesson_weekday}, {lesson_date.strftime('%d.%m')}</b>\n"
        f"⏰ Отправка: <b>{send_weekday}, {reminder_date.strftime('%d.%m')} в 15:30</b> (накануне урока)\n\n"
        f"📝 <b>Что будет отправлено:</b>\n"
        f"<blockquote>{escaped_task}</blockquote>"
    )

    kb = make_hw_kb(hw_id)
    if photo_id:
        await msg.answer_photo(photo=photo_id, caption=confirm, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(confirm, reply_markup=kb, parse_mode="HTML")

# Рассылка в 15:30 (приходит накануне урока)
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
                escaped_task = html.escape(row['task_text'])
                text = (
                    f"🔔 <b>ДОМАШНЕЕ ЗАДАНИЕ НА ЗАВТРА</b>\n"
                    f"📖 Предмет: <b>{html.escape(row['subject'])}</b>\n\n"
                    f"<blockquote>{escaped_task}</blockquote>\n\n"
                    f"<i>(Перешли это сообщение в группу класса)</i>"
                )
                if row["photo_id"]:
                    await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=row["photo_id"], caption=text, parse_mode="HTML")
                else:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="HTML")

                await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
            await db.commit()

    await sync_backup()

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    await fix_existing_dates()
    
    scheduler.add_job(send_daily, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    print("Бот 8-Б класса запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
