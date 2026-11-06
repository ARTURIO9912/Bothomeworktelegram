import os
import re
import json
import html
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
import aiosqlite
import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, 
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    InputMediaPhoto
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9004754440:AAHh3KbECKapVbbSJPyAq5WSGX3_-S4r0Ws").strip()
ADMIN_CHAT_ID = 6243658367
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

TZ = ZoneInfo("Europe/Chisinau")

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
scheduler = AsyncIOScheduler(timezone=TZ)

# Буфер для сборки альбомов (2 и более фото)
media_group_cache = {}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Список ДЗ"), KeyboardButton(text="Расписание")],
        [KeyboardButton(text="Отправить сейчас"), KeyboardButton(text="Отмена")]
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
    except Exception as e:
        logging.error(f"Ошибка восстановления: {e}")

async def fix_all_dates():
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

# Красивое разбиение подписей к фото на цитаты (Первое фото, Второе фото)
def format_task_text(task_raw: str, num_photos: int) -> str:
    if not task_raw:
        return "<blockquote>Задание на фото с доски.</blockquote>" if num_photos > 0 else ""

    lines = [line.strip() for line in task_raw.strip().split("\n") if line.strip()]
    
    # Паттерн для поиска "#1 фото", "1 фото", "фото 1"
    pattern = re.compile(
        r'^(?:#?\s*(\d+)\s*(?:фото|photo)|(?:фото|photo)\s*#?\s*(\d+))\s*[:,\-]?\s*(.*)$',
        re.IGNORECASE
    )
    
    photo_blocks = {}
    general_lines = []
    current_idx = None
    
    for line in lines:
        m = pattern.match(line)
        if m:
            idx = int(m.group(1) or m.group(2))
            current_idx = idx
            remainder = m.group(3).strip()
            photo_blocks[current_idx] = [remainder] if remainder else []
        elif current_idx is not None:
            photo_blocks[current_idx].append(line)
        else:
            general_lines.append(line)
    
    if photo_blocks:
        num_words = {
            1: "Первое фото",
            2: "Второе фото",
            3: "Третье фото",
            4: "Четвертое фото",
            5: "Пятое фото"
        }
        output_parts = []
        if general_lines:
            gen = html.escape("\n".join(general_lines))
            output_parts.append(f"<blockquote>{gen}</blockquote>")
        
        for idx in sorted(photo_blocks.keys()):
            label = num_words.get(idx, f"Фото {idx}")
            text_content = "\n".join(photo_blocks[idx]).strip()
            if not text_content:
                text_content = "Задание на фото"
            output_parts.append(f"<b>{label}:</b>\n<blockquote>{html.escape(text_content)}</blockquote>")
        
        return "\n\n".join(output_parts)
    else:
        return f"<blockquote>{html.escape(task_raw)}</blockquote>"

def get_photo_list(photo_field: str | None) -> list[str]:
    if not photo_field:
        return []
    try:
        data = json.loads(photo_field)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return [photo_field]

async def improve_with_ai(subject: str, raw_task: str, user_instruction: str = "") -> str:
    key = os.environ.get("GROQ_API_KEY", "").strip() or GROQ_API_KEY
    if not key:
        return raw_task

    user_note = f"\nПожелание: {user_instruction}" if user_instruction else ""

    prompt = f"""Ты школьный помощник. Перепиши текст домашнего задания так, чтобы оно было аккуратным, понятным и готовым для отправки в беседу класса.

Предмет: {subject}
Черновик: {raw_task}{user_note}

Инструкция:
1. Исправь орфографические и пунктуационные ошибки.
2. Оформи нумерованным списком (1, 2, 3), если пунктов несколько.
3. ВАЖНО: Не изменяй и не сокращай названия тем, термины и цитаты. Если написано "объяснить смысл темы Х", название темы Х должно остаться точным, слово в слово.
4. Если не хватает деталей (нет номера упражнения или страницы), добавь в конце:
Вопрос для уточнения: [что нужно уточнить]
5. СТРОЖАЙШЕЕ ПРАВИЛО: Не используй смайлики и эмодзи.
6. Выведи ТОЛЬКО готовый текст домашнего задания без вступительных слов.
"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3
                }
            )
            if resp.status_code == 200:
                result = resp.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                logging.error(f"Groq API Error {resp.status_code}: {resp.text}")
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
    today = datetime.now(TZ).date()
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
            InlineKeyboardButton(text="Отправить в чат сейчас", callback_data=f"send_one:{hw_id}")
        ],
        [
            InlineKeyboardButton(text="Удалить", callback_data=f"del_hw:{hw_id}"),
            InlineKeyboardButton(text="Свой текст", callback_data=f"edit_text:{hw_id}")
        ],
        [
            InlineKeyboardButton(text="Переписать через ИИ", callback_data=f"ai_edit:{hw_id}"),
            InlineKeyboardButton(text="Сменить предмет", callback_data=f"edit_subj:{hw_id}")
        ]
    ])

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "Бот 8-Б класса готов к работе.\n\n"
        "Кнопки меню:\n"
        "• Список ДЗ — просмотр очереди заданий\n"
        "• Расписание — уроки на неделю\n"
        "• Отправить сейчас — выгрузить задания прямо сейчас\n"
        "• Отмена — сбросить редактирование\n\n"
        "Отправка заданий:\n"
        "• Текстом: Румынский: выучить слова на стр 15\n"
        "• Фото (можно сразу 1 или 2 фото): прикрепи фото с названием предмета (например, Химия)",
        reply_markup=MAIN_KEYBOARD
    )

@dp.message(F.text.in_(["Отмена", "❌ Отмена"]))
@dp.message(Command("cancel"))
async def cancel_fsm(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("Нет активных действий для отмены.", reply_markup=MAIN_KEYBOARD)
        return
    await state.clear()
    await msg.answer("Действие отменено.", reply_markup=MAIN_KEYBOARD)

@dp.message(F.text.in_(["Расписание", "📅 Расписание"]))
@dp.message(Command("расписание"))
async def view_schedule_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    lines = ["Расписание 8-Б класса:\n"]
    for day_idx in range(5):
        day_name = DAYS_MAP[day_idx].capitalize()
        lines.append(f"{day_name}:")
        for num, subj in enumerate(SCHEDULE[day_idx], start=1):
            lines.append(f"  {num}. {html.escape(subj.capitalize())}")
        lines.append("")
    await msg.answer("\n".join(lines), reply_markup=MAIN_KEYBOARD, parse_mode="HTML")

@dp.message(F.text.in_(["Список ДЗ", "📚 Список ДЗ"]))
@dp.message(Command("dz"))
async def view_homework_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, subject, task_text, photo_id, send_date FROM homework ORDER BY send_date ASC"
        ) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await msg.answer("Активных заданий нет. В очереди пусто.", reply_markup=MAIN_KEYBOARD)
        return

    total = len(rows)
    await msg.answer(f"В очереди заданий: {total}", reply_markup=MAIN_KEYBOARD)

    for idx, row in enumerate(rows, start=1):
        send_date_obj = datetime.strptime(row["send_date"], "%Y-%m-%d").date()
        lesson_date_obj = send_date_obj + timedelta(days=1)
        
        send_weekday = DAYS_MAP[send_date_obj.weekday()].capitalize()
        lesson_weekday = DAYS_MAP[lesson_date_obj.weekday()].capitalize()

        photos = get_photo_list(row["photo_id"])
        card_text = (
            f"Задание {idx} из {total}\n"
            f"Предмет: <b>{html.escape(row['subject'])}</b>\n"
            f"Урок будет: <b>{lesson_weekday}, {lesson_date_obj.strftime('%d.%m')}</b>\n"
            f"Отправка: <b>{send_weekday}, {send_date_obj.strftime('%d.%m')} в 15:30</b> (накануне)\n\n"
            f"Текст:\n"
            f"{row['task_text']}"
        )

        kb = make_hw_kb(row["id"])
        if len(photos) > 1:
            note = f"\n\n<i>(Прикреплено фото: {len(photos)} шт.)</i>"
            await msg.answer_photo(photo=photos[0], caption=card_text + note, reply_markup=kb, parse_mode="HTML")
        elif len(photos) == 1:
            await msg.answer_photo(photo=photos[0], caption=card_text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.answer(text=card_text, reply_markup=kb, parse_mode="HTML")

# Отправка задания (одиночного или с пачкой фото)
async def send_hw_item(subject: str, task_text: str, photo_field: str | None):
    photos = get_photo_list(photo_field)
    header = (
        f"<b>ДОМАШНЕЕ ЗАДАНИЕ НА ЗАВТРА</b>\n"
        f"<b>Предмет:</b> {html.escape(subject)}\n\n"
        f"{task_text}"
    )

    if not photos:
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=header, parse_mode="HTML")
    elif len(photos) == 1:
        await bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photos[0], caption=header, parse_mode="HTML")
    else:
        media = [InputMediaPhoto(media=photos[0], caption=header, parse_mode="HTML")]
        for p in photos[1:]:
            media.append(InputMediaPhoto(media=p))
        await bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media)

@dp.callback_query(F.data.startswith("send_one:"))
async def send_one_callback(call: CallbackQuery):
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT subject, task_text, photo_id FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()

    if not row:
        await call.answer("Задание не найдено.")
        return

    await send_hw_item(row["subject"], row["task_text"], row["photo_id"])
    await call.answer("Отправлено в чат!")

@dp.callback_query(F.data.startswith("del_hw:"))
async def delete_hw_callback(call: CallbackQuery):
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
        await db.commit()
    await sync_backup()
    await call.answer("Задание удалено.")
    del_text = "Задание удалено из очереди."
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
    await call.message.reply("Отправь новый текст задания. Для отмены нажми «Отмена».")

@dp.message(EditHW.waiting_for_text, F.from_user.id == ADMIN_CHAT_ID)
async def edit_text_finish(msg: Message, state: FSMContext):
    new_text = msg.text or msg.caption or ""
    if not new_text:
        await msg.answer("Текст не может быть пустым. Отправь текст задания заново.")
        return
    data = await state.get_data()
    hw_id = data["hw_id"]

    # Форматируем новый текст в цитаты
    formatted = format_task_text(new_text, 1)

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (formatted, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()
    
    await msg.answer(
        f"Текст задания обновлен:\n\n{formatted}",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("ai_edit:"))
async def ai_edit_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_ai_prompt)
    await call.answer()
    await call.message.reply(
        "Редактирование через ИИ:\n\n"
        "Напиши, как изменить задание (например: «сделай короче», «разбей по пунктам»).\n"
        "Или отправь знак плюс (+), чтобы ИИ переписал текст автоматически.\n"
        "Для отмены нажми «Отмена»."
    )

@dp.message(EditHW.waiting_for_ai_prompt, F.from_user.id == ADMIN_CHAT_ID)
async def ai_edit_finish(msg: Message, state: FSMContext):
    user_instruction = (msg.text or "").strip()
    if user_instruction == "+":
        user_instruction = ""

    data = await state.get_data()
    hw_id = data["hw_id"]

    wait_msg = await msg.answer("ИИ переписывает задание...")

    async with aiosqlite.connect("school.db") as db:
        async with db.execute("SELECT subject, task_text FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
            subj, old_text = row if row else ("Предмет", "")

    better_text = await improve_with_ai(subj, old_text, user_instruction)
    formatted = f"<blockquote>{html.escape(better_text)}</blockquote>"
    await wait_msg.delete()

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (formatted, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()

    await msg.answer(
        f"Результат от ИИ:\n\n{formatted}",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("edit_subj:"))
async def edit_subj_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_subject)
    await call.answer()
    await call.message.reply("Напиши новое название предмета. Для отмены нажми «Отмена».")

@dp.message(EditHW.waiting_for_subject, F.from_user.id == ADMIN_CHAT_ID)
async def edit_subj_finish(msg: Message, state: FSMContext):
    new_subj = (msg.text or "").strip()
    lesson_date, matched_name = find_next_lesson_date(new_subj)
    if not lesson_date:
        await msg.answer(f"Предмет «{html.escape(new_subj)}» не найден в расписании 8-Б класса.", parse_mode="HTML")
        return

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
        f"Предмет изменен на: <b>{html.escape(matched_name)}</b>\n"
        f"Урок будет: <b>{lesson_weekday}, {lesson_date.strftime('%d.%m')}</b>\n"
        f"Отправка: <b>{send_weekday}, {reminder_date.strftime('%d.%m')} в 15:30</b> (накануне)",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML"
    )

# Прием домашних заданий (сборка альбомов из 2 фото, без авто-ИИ)
@dp.message(StateFilter(None), F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
    # Если пришел альбом из нескольких фото
    if msg.media_group_id:
        mg_id = msg.media_group_id
        if mg_id not in media_group_cache:
            media_group_cache[mg_id] = {
                "photos": [msg.photo[-1].file_id] if msg.photo else [],
                "caption": msg.caption or "",
                "msg": msg
            }
        else:
            if msg.photo:
                media_group_cache[mg_id]["photos"].append(msg.photo[-1].file_id)
            if msg.caption:
                media_group_cache[mg_id]["caption"] = msg.caption

        # Ждем 1 секунду, чтобы все части альбома дошли
        await asyncio.sleep(1.0)

        if mg_id in media_group_cache:
            data = media_group_cache.pop(mg_id)
            photos = data["photos"]
            raw_text = (data["caption"] or "").strip()
            msg = data["msg"]
        else:
            # Уже обработано соседней задачей альбома
            return
    else:
        photos = [msg.photo[-1].file_id] if msg.photo else []
        raw_text = (msg.caption if msg.photo else (msg.text or "")).strip()

    if photos and not raw_text:
        await msg.answer("Ты отправил фото без названия предмета. Напиши предмет в подписи к фото (например: Химия).")
        return

    # Разбираем предмет и подпись
    if ":" in raw_text:
        subject, task = raw_text.split(":", 1)
        subject, task = subject.strip(), task.strip()
    else:
        subject, task = raw_text.strip(), ""

    lesson_date, matched_name = find_next_lesson_date(subject)
    if not lesson_date:
        await msg.answer(f"Предмет «{html.escape(subject)}» не найден в расписании 8-Б класса.", parse_mode="HTML")
        return

    # Форматируем подпись красиво по цитатам БЕЗ автоматического вмешательства ИИ
    final_text = format_task_text(task, len(photos))

    reminder_date = lesson_date - timedelta(days=1)
    send_date_str = reminder_date.strftime("%Y-%m-%d")
    photo_db_val = json.dumps(photos) if photos else None

    async with aiosqlite.connect("school.db") as db:
        cursor = await db.execute("""
            INSERT INTO homework (subject, task_text, photo_id, send_date)
            VALUES (?, ?, ?, ?)
        """, (matched_name, final_text, photo_db_val, send_date_str))
        hw_id = cursor.lastrowid
        await db.commit()

    await sync_backup()

    send_weekday = DAYS_MAP[reminder_date.weekday()].capitalize()
    lesson_weekday = DAYS_MAP[lesson_date.weekday()].capitalize()
    
    confirm = (
        f"Задание сохранено.\n\n"
        f"Предмет: <b>{html.escape(matched_name)}</b>\n"
        f"Урок будет: <b>{lesson_weekday}, {lesson_date.strftime('%d.%m')}</b>\n"
        f"Отправка: <b>{send_weekday}, {reminder_date.strftime('%d.%m')} в 15:30</b> (накануне урока)\n\n"
        f"Текст для отправки:\n"
        f"{final_text}"
    )

    kb = make_hw_kb(hw_id)
    if len(photos) > 1:
        media = [InputMediaPhoto(media=p) for p in photos]
        await msg.answer_media_group(media=media)
        await msg.answer(confirm, reply_markup=kb, parse_mode="HTML")
    elif len(photos) == 1:
        await msg.answer_photo(photo=photos[0], caption=confirm, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(confirm, reply_markup=kb, parse_mode="HTML")

# Функция отправки по расписанию или кнопке
async def send_due_homework(force_resend: bool = False) -> int:
    now_md = datetime.now(TZ)
    today_str = now_md.strftime("%Y-%m-%d")
    sent_count = 0

    query = "SELECT id, subject, task_text, photo_id FROM homework WHERE send_date <= ?"
    if not force_resend:
        query += " AND is_sent = 0"
    query += " ORDER BY id DESC"

    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, (today_str,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                try:
                    await send_hw_item(row["subject"], row["task_text"], row["photo_id"])
                    await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
                    sent_count += 1
                except Exception as e:
                    logging.error(f"Ошибка отправки ДЗ #{row['id']}: {e}")

            await db.commit()

    if sent_count > 0:
        await sync_backup()
    return sent_count

@dp.message(F.text.in_(["Отправить сейчас", "⚡️ Отправить сейчас", "⚡ Отправить сейчас"]))
@dp.message(Command("send"))
async def manual_send_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    count = await send_due_homework(force_resend=False)
    if count == 0:
        count = await send_due_homework(force_resend=True)

    if count == 0:
        await msg.answer("В базе нет заданий на сегодня или ранее.", reply_markup=MAIN_KEYBOARD)
    else:
        await msg.answer(f"Отправлено заданий: {count}", reply_markup=MAIN_KEYBOARD)

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    await fix_all_dates()
    
    scheduler.add_job(send_due_homework, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    now = datetime.now(TZ)
    if now.hour > SEND_HOUR or (now.hour == SEND_HOUR and now.minute >= SEND_MINUTE):
        await send_due_homework(force_resend=False)

    print("Бот 8-Б класса запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
