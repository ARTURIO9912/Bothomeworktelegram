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
    InputMediaPhoto, WebAppInfo
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

WEBAPP_URL = os.environ.get("WEBAPP_URL", os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")

TZ = ZoneInfo("Europe/Chisinau")

SEND_HOUR = 15
SEND_MINUTE = 10
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

ACCUSATIVE_DAYS = {
    0: "понедельник",
    1: "вторник",
    2: "среду",
    3: "четверг",
    4: "пятницу",
    5: "субботу",
    6: "воскресенье"
}
# ===============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ)

media_group_cache = {}

# КРАСИВЫЙ СОВРЕМЕННЫЙ WEB APP ИНТЕРФЕЙС
WEBAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>ДЗ 8-Б Класс</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {
      --bg: #0f141c;
      --card-bg: #18222e;
      --card-hover: #1f2c3a;
      --input-bg: #121922;
      --text: #ffffff;
      --hint: #8292a1;
      --accent: #2ea6ff;
      --accent-glow: rgba(46, 166, 255, 0.25);
      --danger: #ff5252;
      --success: #00e676;
      --border: rgba(255, 255, 255, 0.08);
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; font-family: var(--font); }
    body {
      margin: 0; padding: 12px 14px 90px 14px;
      background-color: var(--bg);
      color: var(--text);
    }
    /* Навигация */
    .nav {
      display: flex;
      background: rgba(24, 34, 46, 0.85);
      backdrop-filter: blur(10px);
      border-radius: 14px;
      padding: 4px;
      margin-bottom: 16px;
      border: 1px solid var(--border);
    }
    .nav-btn {
      flex: 1;
      padding: 10px 4px;
      font-size: 13px;
      font-weight: 700;
      text-align: center;
      background: transparent;
      color: var(--hint);
      border: none;
      border-radius: 10px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .nav-btn.active {
      background: var(--accent);
      color: #fff;
      box-shadow: 0 3px 12px var(--accent-glow);
    }
    .tab-content { display: none; animation: fadeIn 0.25s ease; }
    .tab-content.active { display: block; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    /* Карточки */
    .card {
      background: var(--card-bg);
      border-radius: 16px;
      padding: 16px;
      margin-bottom: 16px;
      border: 1px solid var(--border);
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
      gap: 10px;
    }
    /* Кастомная кнопка выбора предмета */
    .subj-pill {
      background: rgba(46, 166, 255, 0.12);
      color: var(--accent);
      border: 1px solid rgba(46, 166, 255, 0.3);
      padding: 7px 12px;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 700;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .subj-pill:active { transform: scale(0.96); background: rgba(46, 166, 255, 0.2); }
    .subj-pill .arrow { font-size: 10px; opacity: 0.7; }
    .date-info {
      font-size: 12px;
      font-weight: 500;
      color: var(--hint);
      text-align: right;
    }
    textarea {
      width: 100%;
      background: var(--input-bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 12px;
      font-size: 14px;
      line-height: 1.45;
      resize: vertical;
      min-height: 90px;
      outline: none;
      transition: border 0.2s;
    }
    textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }

    /* Панель ИИ */
    .ai-row {
      display: flex;
      gap: 6px;
      margin: 10px 0 14px 0;
      overflow-x: auto;
      scrollbar-width: none;
    }
    .ai-row::-webkit-scrollbar { display: none; }
    .ai-tag {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      color: #79c0ff;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      white-space: nowrap;
      cursor: pointer;
      transition: all 0.15s;
    }
    .ai-tag:active { transform: scale(0.95); background: rgba(46, 166, 255, 0.2); }

    /* Кнопки управления */
    .action-row {
      display: flex;
      gap: 8px;
    }
    .btn {
      flex: 1;
      padding: 11px;
      border-radius: 10px;
      border: none;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s;
    }
    .btn:active { transform: scale(0.97); }
    .btn-save { background: var(--accent); color: #fff; box-shadow: 0 3px 10px var(--accent-glow); }
    .btn-send { background: rgba(0, 230, 118, 0.14); color: var(--success); border: 1px solid rgba(0, 230, 118, 0.3); }
    .btn-del { background: rgba(255, 82, 82, 0.12); color: var(--danger); border: 1px solid rgba(255, 82, 82, 0.25); flex: 0.55; }

    /* Модальное окно выбора предмета */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(4px);
      z-index: 999;
      display: none;
      align-items: flex-end;
    }
    .modal-overlay.active { display: flex; animation: fadeIn 0.2s ease; }
    .sheet {
      background: #18222e;
      width: 100%;
      border-top-left-radius: 22px;
      border-top-right-radius: 22px;
      padding: 18px 16px 30px 16px;
      max-height: 80vh;
      overflow-y: auto;
      border-top: 1px solid var(--border);
      animation: slideUp 0.25s cubic-bezier(0.1, 0.9, 0.2, 1);
    }
    @keyframes slideUp { from { transform: translateY(100%); } to { transform: translateY(0); } }
    .sheet-title {
      font-size: 16px;
      font-weight: 700;
      text-align: center;
      margin-bottom: 16px;
      color: var(--text);
    }
    .subj-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
    }
    .subj-item {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 12px 10px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 600;
      text-align: center;
      cursor: pointer;
      transition: all 0.15s;
    }
    .subj-item:active { background: var(--accent); color: #fff; transform: scale(0.97); }

    /* Расписание */
    .sched-card {
      background: var(--card-bg);
      border-radius: 14px;
      padding: 14px 16px;
      margin-bottom: 12px;
      border: 1px solid var(--border);
    }
    .sched-head {
      font-size: 15px;
      font-weight: 700;
      color: var(--accent);
      margin-bottom: 8px;
    }
    .sched-line {
      font-size: 13px;
      padding: 4px 0;
      color: var(--text);
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    }

    /* Всплывающее уведомление */
    .toast {
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: rgba(30, 41, 56, 0.96);
      backdrop-filter: blur(10px);
      color: #fff;
      padding: 10px 22px;
      border-radius: 30px;
      font-size: 13px;
      font-weight: 600;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.45);
      border: 1px solid var(--border);
      opacity: 0;
      transition: all 0.25s cubic-bezier(0.175, 0.885, 0.32, 1.275);
      z-index: 1000;
      pointer-events: none;
    }
    .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
    .status-msg { text-align: center; color: var(--hint); padding: 40px 10px; font-size: 14px; }
  </style>
</head>
<body>

  <!-- Вкладки меню -->
  <div class="nav">
    <button class="nav-btn active" onclick="switchTab('tasks')">Задания</button>
    <button class="nav-btn" onclick="switchTab('add')">+ Добавить ДЗ</button>
    <button class="nav-btn" onclick="switchTab('schedule')">Расписание</button>
  </div>

  <!-- ВКЛАДКА 1: Список ДЗ -->
  <div id="tab-tasks" class="tab-content active">
    <div id="tasks-box" class="status-msg">Загрузка очереди...</div>
  </div>

  <!-- ВКЛАДКА 2: Добавление задания -->
  <div id="tab-add" class="tab-content">
    <div class="card">
      <div style="margin-bottom: 12px;">
        <label style="font-size: 12px; color: var(--hint); font-weight: 600; margin-bottom: 6px; display: block;">ПРЕДМЕТ</label>
        <div class="subj-pill" id="new-subj-btn" onclick="openPicker('new')">
          <span id="new-subj-label">Химия</span> <span class="arrow">▼</span>
        </div>
      </div>
      <div style="margin-bottom: 8px;">
        <label style="font-size: 12px; color: var(--hint); font-weight: 600; margin-bottom: 6px; display: block;">ЧТО ЗАДАНО</label>
        <textarea id="new-task-text" placeholder="Напиши текст задания..."></textarea>
      </div>
      <div class="ai-row">
        <div class="ai-tag" onclick="aiNew('Оформи красиво и понятно')">✨ Оформить ИИ</div>
        <div class="ai-tag" onclick="aiNew('Сделай короче')">✂️ Короче</div>
        <div class="ai-tag" onclick="aiNew('Разбей по пунктам 1, 2, 3')">📋 Списком</div>
      </div>
      <button class="btn btn-save" style="width: 100%; padding: 13px;" onclick="saveNewTask()">Добавить в расписание</button>
    </div>
  </div>

  <!-- ВКЛАДКА 3: Расписание -->
  <div id="tab-schedule" class="tab-content">
    <div id="sched-box" class="status-msg">Загрузка расписания...</div>
  </div>

  <!-- Кастомный выбор предмета (Шторка снизу) -->
  <div id="picker-modal" class="modal-overlay" onclick="closePicker()">
    <div class="sheet" onclick="event.stopPropagation()">
      <div class="sheet-title">Выбери предмет</div>
      <div class="subj-grid" id="subj-grid-box"></div>
    </div>
  </div>

  <div id="toast" class="toast">Сохранено</div>

  <script>
    const tg = window.Telegram?.WebApp;
    if (tg) { tg.ready(); tg.expand(); }

    // Точный список предметов 8-Б класса со всеми уроками
    const SUBJECTS = [
      "Химия", "Рум. яз.", "ИКТ (ИКТБН)", "Математика", "Литература", 
      "Физика", "Биология", "История", "Рус. яз.", "Англ. яз.", 
      "Болг. яз.", "Информ.", "География", "РЛ (Развитие личности)", 
      "ГВ", "Труд", "Физ. восп.", "Муз. восп."
    ];

    let currentPickerTarget = null; // 'new' или ID задачи

    function showToast(text) {
      const t = document.getElementById('toast');
      t.innerText = text;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2000);
      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    }

    function switchTab(name) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      event.target.classList.add('active');
      if (name === 'tasks') loadTasks();
      if (name === 'schedule') loadSchedule();
    }

    // Инициализация шторки выбора предметов
    function buildPickerGrid() {
      const box = document.getElementById('subj-grid-box');
      box.innerHTML = '';
      SUBJECTS.forEach(s => {
        const item = document.createElement('div');
        item.className = 'subj-item';
        item.innerText = s;
        item.onclick = () => selectSubject(s);
        box.appendChild(item);
      });
    }
    buildPickerGrid();

    function openPicker(targetId) {
      currentPickerTarget = targetId;
      document.getElementById('picker-modal').classList.add('active');
    }

    function closePicker() {
      document.getElementById('picker-modal').classList.remove('active');
    }

    function selectSubject(subjName) {
      if (currentPickerTarget === 'new') {
        document.getElementById('new-subj-label').innerText = subjName;
      } else {
        document.getElementById('subj-label-' + currentPickerTarget).innerText = subjName;
      }
      closePicker();
    }

    async function loadTasks() {
      const box = document.getElementById('tasks-box');
      try {
        const res = await fetch('/api/homework');
        const tasks = await res.json();
        if (!tasks || tasks.length === 0) {
          box.innerHTML = '<div class="status-msg">📭 В очереди нет активных заданий.<br>Нажми «+ Добавить ДЗ» чтобы создать!</div>';
          return;
        }
        box.innerHTML = '';
        tasks.forEach(t => {
          const card = document.createElement('div');
          card.className = 'card';
          card.id = 'task-box-' + t.id;

          let cleanText = t.task_text.replace(/<[^>]+>/g, '').trim();

          card.innerHTML = `
            <div class="card-header">
              <div class="subj-pill" onclick="openPicker(${t.id})">
                <span id="subj-label-${t.id}">${t.subject}</span>
                <span class="arrow">▼</span>
              </div>
              <div class="date-info">${t.date_info}</div>
            </div>
            <textarea id="text-${t.id}">${cleanText}</textarea>
            <div class="ai-row">
              <div class="ai-tag" onclick="aiTask(${t.id}, 'Оформи аккуратно и понятно')">✨ Оформить ИИ</div>
              <div class="ai-tag" onclick="aiTask(${t.id}, 'Сделай короче')">✂️ Короче</div>
              <div class="ai-tag" onclick="aiTask(${t.id}, 'Разбей по пунктам')">📋 Списком</div>
            </div>
            <div class="action-row">
              <button class="btn btn-del" onclick="deleteTask(${t.id})">Удалить</button>
              <button class="btn btn-send" onclick="sendTaskNow(${t.id})">В чат</button>
              <button class="btn btn-save" onclick="saveTask(${t.id})">Сохранить</button>
            </div>
          `;
          box.appendChild(card);
        });
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки очереди.</div>';
      }
    }

    async function saveTask(id) {
      const text = document.getElementById('text-' + id).value.trim();
      const subject = document.getElementById('subj-label-' + id).innerText;
      if (!text) { alert('Текст не может быть пустым'); return; }

      try {
        const res = await fetch('/api/save', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id, text, subject })
        });
        const data = await res.json();
        if (data.ok) showToast('Сохранено!');
        else alert('Ошибка сохранения');
      } catch (e) { alert('Ошибка сети'); }
    }

    async function deleteTask(id) {
      if (!confirm('Удалить это задание из очереди?')) return;
      try {
        const res = await fetch('/api/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id })
        });
        const data = await res.json();
        if (data.ok) {
          document.getElementById('task-box-' + id)?.remove();
          showToast('Удалено');
          if (document.querySelectorAll('.card').length === 0) loadTasks();
        }
      } catch (e) { alert('Ошибка при удалении'); }
    }

    async function sendTaskNow(id) {
      try {
        const res = await fetch('/api/send_item', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id })
        });
        const data = await res.json();
        if (data.ok) showToast('Отправлено в чат!');
      } catch (e) { alert('Ошибка отправки'); }
    }

    async function aiTask(id, instruction) {
      const textarea = document.getElementById('text-' + id);
      const subject = document.getElementById('subj-label-' + id).innerText;
      const original = textarea.value.trim();
      if (!original) return;

      textarea.value = 'ИИ формулирует задание...';
      try {
        const res = await fetch('/api/ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ subject, text: original, instruction })
        });
        const data = await res.json();
        if (data.ok) {
          textarea.value = data.text;
          showToast('ИИ оформил текст');
        } else {
          textarea.value = original;
        }
      } catch (e) { textarea.value = original; }
    }

    async function aiNew(instruction) {
      const textarea = document.getElementById('new-task-text');
      const subject = document.getElementById('new-subj-label').innerText;
      const original = textarea.value.trim();
      if (!original) { alert('Сначала напиши черновик задания'); return; }

      textarea.value = 'ИИ формулирует задание...';
      try {
        const res = await fetch('/api/ai', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ subject, text: original, instruction })
        });
        const data = await res.json();
        if (data.ok) {
          textarea.value = data.text;
          showToast('ИИ оформил текст');
        } else {
          textarea.value = original;
        }
      } catch (e) { textarea.value = original; }
    }

    async function saveNewTask() {
      const subject = document.getElementById('new-subj-label').innerText;
      const text = document.getElementById('new-task-text').value.trim();
      if (!text) { alert('Напиши текст задания'); return; }

      try {
        const res = await fetch('/api/add', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ subject, text })
        });
        const data = await res.json();
        if (data.ok) {
          document.getElementById('new-task-text').value = '';
          showToast('Задание добавлено в очередь!');
          setTimeout(() => { document.querySelectorAll('.nav-btn')[0].click(); }, 350);
        } else {
          alert('Ошибка при добавлении');
        }
      } catch (e) { alert('Ошибка сети'); }
    }

    async function loadSchedule() {
      const box = document.getElementById('sched-box');
      try {
        const res = await fetch('/api/schedule');
        const sched = await res.json();
        const days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"];
        let html = '';
        days.forEach((dayName, idx) => {
          html += `<div class="sched-card"><div class="sched-head">${dayName}</div>`;
          const items = sched[idx] || [];
          items.forEach((item, num) => {
            html += `<div class="sched-line">${num + 1}. ${item.charAt(0).toUpperCase() + item.slice(1)}</div>`;
          });
          html += `</div>`;
        });
        box.innerHTML = html;
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки расписания</div>';
      }
    }

    loadTasks();
  </script>
</body>
</html>
"""

def get_main_keyboard():
    has_web = bool(WEBAPP_URL and WEBAPP_URL.startswith("https://"))
    buttons = [
        [KeyboardButton(text="📚 Список ДЗ"), KeyboardButton(text="📅 Расписание")]
    ]
    if has_web:
        buttons.append([KeyboardButton(text="📱 Веб-редактор", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))])
    buttons.append([KeyboardButton(text="⚡️ Отправить сейчас"), KeyboardButton(text="❌ Отмена")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, is_persistent=True)

class EditHW(StatesGroup):
    waiting_for_text = State()
    waiting_for_subject = State()
    waiting_for_ai_prompt = State()

async def web_handle(request):
    return web.Response(text=WEBAPP_HTML, content_type="text/html")

async def api_get_homework(request):
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text, photo_id, send_date FROM homework WHERE is_sent = 0 ORDER BY send_date ASC") as cursor:
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                send_d = datetime.strptime(r["send_date"], "%Y-%m-%d").date()
                lesson_d, _ = find_next_lesson_date(r["subject"])
                l_date = lesson_d if lesson_d else (send_d + timedelta(days=1))
                result.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "task_text": r["task_text"],
                    "send_date": r["send_date"],
                    "date_info": f"Урок: {DAYS_MAP[l_date.weekday()].capitalize()} ({l_date.strftime('%d.%m')})"
                })
            return web.json_response(result)

async def api_save_homework(request):
    try:
        data = await request.json()
        hw_id = int(data.get("id"))
        new_text = str(data.get("text", "")).strip()
        new_subj = str(data.get("subject", "")).strip()
        if not new_text:
            return web.json_response({"ok": False, "error": "Empty text"})

        formatted = format_task_text(new_text, 1)

        lesson_date, matched_name = find_next_lesson_date(new_subj)
        if lesson_date:
            rem_d = calculate_reminder_date(lesson_date).strftime("%Y-%m-%d")
            async with aiosqlite.connect("school.db") as db:
                await db.execute("UPDATE homework SET task_text = ?, subject = ?, send_date = ? WHERE id = ?", (formatted, matched_name, rem_d, hw_id))
                await db.commit()
        else:
            async with aiosqlite.connect("school.db") as db:
                await db.execute("UPDATE homework SET task_text = ?, subject = ? WHERE id = ?", (formatted, new_subj.capitalize(), hw_id))
                await db.commit()

        await sync_backup()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_add_homework(request):
    try:
        data = await request.json()
        subj = str(data.get("subject", "")).strip()
        text = str(data.get("text", "")).strip()
        if not subj or not text:
            return web.json_response({"ok": False, "error": "Empty data"})

        lesson_date, matched_name = find_next_lesson_date(subj)
        if not lesson_date:
            matched_name = subj.capitalize()
            lesson_date = datetime.now(TZ).date() + timedelta(days=1)

        reminder_date = calculate_reminder_date(lesson_date)
        send_date_str = reminder_date.strftime("%Y-%m-%d")
        final_text = format_task_text(text, 0)

        async with aiosqlite.connect("school.db") as db:
            cursor = await db.execute("""
                INSERT INTO homework (subject, task_text, photo_id, send_date)
                VALUES (?, ?, ?, ?)
            """, (matched_name, final_text, None, send_date_str))
            hw_id = cursor.lastrowid
            await db.commit()

        await sync_backup()
        return web.json_response({"ok": True, "id": hw_id})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_delete_homework(request):
    try:
        data = await request.json()
        hw_id = int(data.get("id"))
        async with aiosqlite.connect("school.db") as db:
            await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
            await db.commit()
        await sync_backup()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_ai_improve(request):
    try:
        data = await request.json()
        subject = str(data.get("subject", "Предмет"))
        text = str(data.get("text", ""))
        instruction = str(data.get("instruction", ""))
        better = await improve_with_ai(subject, text, instruction)
        return web.json_response({"ok": True, "text": better})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_send_item_now(request):
    try:
        data = await request.json()
        hw_id = int(data.get("id"))
        async with aiosqlite.connect("school.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT subject, task_text, photo_id FROM homework WHERE id = ?", (hw_id,)) as cursor:
                row = await cursor.fetchone()

        if row:
            await send_hw_item(row["subject"], row["task_text"], row["photo_id"])
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "Not found"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_get_schedule(request):
    return web.json_response(SCHEDULE)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", web_handle)
    app.router.add_get("/webapp", web_handle)
    app.router.add_get("/api/homework", api_get_homework)
    app.router.add_post("/api/save", api_save_homework)
    app.router.add_post("/api/add", api_add_homework)
    app.router.add_post("/api/delete", api_delete_homework)
    app.router.add_post("/api/ai", api_ai_improve)
    app.router.add_post("/api/send_item", api_send_item_now)
    app.router.add_get("/api/schedule", api_get_schedule)

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
                    clean_text = item["task_text"].replace("📸 ", "").replace(" 👆", "")
                    async with db.execute(
                        "SELECT id FROM homework WHERE id = ?",
                        (item.get("id"),)
                    ) as cursor:
                        if not await cursor.fetchone():
                            await db.execute(
                                "INSERT INTO homework (id, subject, task_text, photo_id, send_date) VALUES (?, ?, ?, ?, ?)",
                                (item.get("id"), item["subject"].capitalize(), clean_text, item["photo_id"], item["send_date"])
                            )
                await db.commit()
            print("База восстановлена из резервной копии.")
    except Exception as e:
        logging.error(f"Ошибка восстановления: {e}")

def calculate_reminder_date(lesson_date: datetime.date) -> datetime.date:
    if lesson_date.weekday() == 0:
        return lesson_date - timedelta(days=3)
    return lesson_date - timedelta(days=1)

async def fix_database_quotes_and_dates():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text FROM homework WHERE is_sent = 0") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                t = r["task_text"]
                if "<blockquote>" not in t:
                    clean = t.replace("📸 ", "").replace(" 👆", "").strip()
                    t = f"<blockquote>{clean}</blockquote>"
                
                lesson_d, matched = find_next_lesson_date(r["subject"])
                if lesson_d:
                    reminder_d = calculate_reminder_date(lesson_d)
                    await db.execute(
                        "UPDATE homework SET subject = ?, task_text = ?, send_date = ? WHERE id = ?",
                        (matched, t, reminder_d.strftime("%Y-%m-%d"), r["id"])
                    )
        await db.commit()
    await sync_backup()

def format_task_text(task_raw: str, num_photos: int) -> str:
    if not task_raw:
        return "<blockquote>Задание на фото с доски.</blockquote>" if num_photos > 0 else ""

    lines = [line.strip() for line in task_raw.strip().split("\n") if line.strip()]
    
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

    clean_task = re.sub(r'<[^>]+>', '', raw_task).strip()
    user_note = f"\nПожелание: {user_instruction}" if user_instruction else ""

    prompt = f"""Ты школьный помощник. Перепиши текст домашнего задания так, чтобы оно было аккуратным, понятным и готовым для отправки в беседу класса.

Предмет: {subject}
Черновик: {clean_task}{user_note}

Инструкция:
1. Исправь орфографические и пунктуационные ошибки.
2. Оформи нумерованным списком (1, 2, 3), если пунктов несколько.
3. ВАЖНО: Не изменяй и не сокращай названия тем, термины и цитаты. Если написано "объяснить смысл темы Х", название темы Х должно остаться точным, слово в слово.
4. Если не хватает деталей (нет номера упражнения или страницы), добавь в конце:
Вопрос для уточнения: [что нужно уточнить]
5. СТРОЖАЙШЕЕ ПРАВИЛО: Не используй смайлики, эмодзи и капс.
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

    return clean_task

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
        "инф": "информ.", "информатика": "информ.",
        "икт": "иктбн", "иктбн": "иктбн",
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
                return target_date, real_subj.capitalize()
    return None, None

def make_hw_kb(hw_id: int) -> InlineKeyboardMarkup:
    has_web = bool(WEBAPP_URL and WEBAPP_URL.startswith("https://"))
    buttons = [
        [
            InlineKeyboardButton(text="📤 Отправить в чат сейчас", callback_data=f"send_one:{hw_id}")
        ]
    ]
    if has_web:
        buttons.append([
            InlineKeyboardButton(text="📱 Открыть в Web", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))
        ])
    buttons.extend([
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_hw:{hw_id}"),
            InlineKeyboardButton(text="✏️ Свой текст", callback_data=f"edit_text:{hw_id}")
        ],
        [
            InlineKeyboardButton(text="🤖 Переписать через ИИ", callback_data=f"ai_edit:{hw_id}"),
            InlineKeyboardButton(text="🔄 Сменить предмет", callback_data=f"edit_subj:{hw_id}")
        ]
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(CommandStart())
async def start_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    await msg.answer(
        "👋 <b>Бот 8-Б класса готов к работе.</b>\n\n"
        "Кнопки меню закреплены снизу:\n"
        "• <b>📚 Список ДЗ</b> — просмотр заданий\n"
        "• <b>📱 Веб-редактор</b> — удобное добавление и редактирование через Web App\n"
        "• <b>📅 Расписание</b> — уроки на неделю\n"
        "• <b>⚡️ Отправить сейчас</b> — выгрузить задания\n"
        "• <b>❌ Отмена</b> — сбросить действие\n\n"
        "Отправка заданий на перемене:\n"
        "• Текстом: <code>Румынский: выучить слова на стр 15</code>\n"
        "• Фото (1 или альбом): фото с названием предмета (например, <code>Химия</code>)",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(F.text.in_(["Отмена", "❌ Отмена"]))
@dp.message(Command("cancel"))
async def cancel_fsm(msg: Message, state: FSMContext):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    current_state = await state.get_state()
    if current_state is None:
        await msg.answer("👌 Нет активных действий для отмены.", reply_markup=get_main_keyboard())
        return
    await state.clear()
    await msg.answer("❌ Действие отменено.", reply_markup=get_main_keyboard())

@dp.message(F.text.in_(["Расписание", "📅 Расписание"]))
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
    await msg.answer("\n".join(lines), reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(F.text.in_(["Список ДЗ", "📚 Список ДЗ"]))
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
        await msg.answer("📭 Активных заданий нет. В очереди пусто.", reply_markup=get_main_keyboard())
        return

    total = len(rows)
    await msg.answer(f"📋 <b>В очереди заданий: {total}</b>", reply_markup=get_main_keyboard(), parse_mode="HTML")

    for idx, row in enumerate(rows, start=1):
        send_date_obj = datetime.strptime(row["send_date"], "%Y-%m-%d").date()
        
        lesson_d, _ = find_next_lesson_date(row["subject"])
        if lesson_d:
            lesson_date_obj = lesson_d
        else:
            lesson_date_obj = send_date_obj + timedelta(days=1)
        
        send_weekday = DAYS_MAP[send_date_obj.weekday()].capitalize()
        lesson_weekday = DAYS_MAP[lesson_date_obj.weekday()].capitalize()

        photos = get_photo_list(row["photo_id"])
        
        task_display = row["task_text"]
        if "<blockquote>" not in task_display:
            task_display = f"<blockquote>{html.escape(task_display)}</blockquote>"

        card_text = (
            f"📌 <b>Задание {idx} из {total}</b>\n"
            f"📚 <b>Предмет:</b> {html.escape(row['subject'].capitalize())}\n"
            f"📅 <b>Урок:</b> {lesson_weekday}, {lesson_date_obj.strftime('%d.%m')}\n"
            f"⏰ <b>Отправка:</b> {send_weekday}, {send_date_obj.strftime('%d.%m')} в 15:10\n\n"
            f"📝 <b>Задание:</b>\n"
            f"{task_display}"
        )

        kb = make_hw_kb(row["id"])
        if len(photos) > 1:
            note = f"\n\n<i>(Прикреплено фото: {len(photos)} шт.)</i>"
            await msg.answer_photo(photo=photos[0], caption=card_text + note, reply_markup=kb, parse_mode="HTML")
        elif len(photos) == 1:
            await msg.answer_photo(photo=photos[0], caption=card_text, reply_markup=kb, parse_mode="HTML")
        else:
            await msg.answer(text=card_text, reply_markup=kb, parse_mode="HTML")

async def send_hw_item(subject: str, task_text: str, photo_field: str | None):
    photos = get_photo_list(photo_field)
    
    task_display = task_text
    if "<blockquote>" not in task_display:
        task_display = f"<blockquote>{html.escape(task_display)}</blockquote>"

    lesson_d, _ = find_next_lesson_date(subject)
    if lesson_d:
        target_day_name = ACCUSATIVE_DAYS.get(lesson_d.weekday(), "следующий урок")
    else:
        target_day_name = "следующий урок"

    header = (
        f"<b>Домашнее задание на {target_day_name}:</b>\n"
        f"<b>Предмет:</b> {html.escape(subject.capitalize())}\n\n"
        f"{task_display}"
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
    await call.answer("🗑 Задание удалено.")
    del_text = "🗑 <b>Задание удалено из очереди.</b>"
    if call.message.photo:
        await call.message.edit_caption(caption=del_text, parse_mode="HTML")
    else:
        await call.message.edit_text(text=del_text, parse_mode="HTML")

@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_text_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_text)
    await call.answer()
    await call.message.reply("✏️ <b>Отправь новый текст задания.</b> Для отмены нажми кнопку «❌ Отмена».", parse_mode="HTML")

@dp.message(EditHW.waiting_for_text, F.from_user.id == ADMIN_CHAT_ID)
async def edit_text_finish(msg: Message, state: FSMContext):
    new_text = msg.text or msg.caption or ""
    if not new_text:
        await msg.answer("⚠️ Текст не может быть пустым. Отправь текст задания заново.")
        return
    data = await state.get_data()
    hw_id = data["hw_id"]

    formatted = format_task_text(new_text, 1)

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (formatted, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()
    
    await msg.answer(
        f"✅ <b>Текст задания обновлен:</b>\n\n{formatted}",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("ai_edit:"))
async def ai_edit_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_ai_prompt)
    await call.answer()
    await call.message.reply(
        "🤖 <b>Редактирование через ИИ:</b>\n\n"
        "Напиши, как изменить задание (например: <i>«сделай короче»</i>, <i>«разбей по пунктам»</i>).\n"
        "Или отправь знак плюс <b>+</b>, чтобы ИИ переписал текст автоматически.\n\n"
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
    formatted = f"<blockquote>{html.escape(better_text)}</blockquote>"
    await wait_msg.delete()

    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET task_text = ? WHERE id = ?", (formatted, hw_id))
        await db.commit()
    await sync_backup()
    await state.clear()

    await msg.answer(
        f"✅ <b>Результат от ИИ:</b>\n\n{formatted}",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("edit_subj:"))
async def edit_subj_start(call: CallbackQuery, state: FSMContext):
    hw_id = int(call.data.split(":")[1])
    await state.update_data(hw_id=hw_id)
    await state.set_state(EditHW.waiting_for_subject)
    await call.answer()
    await call.message.reply("🔄 <b>Напиши новое название предмета.</b> Для отмены нажми кнопку «❌ Отмена».", parse_mode="HTML")

@dp.message(EditHW.waiting_for_subject, F.from_user.id == ADMIN_CHAT_ID)
async def edit_subj_finish(msg: Message, state: FSMContext):
    new_subj = (msg.text or "").strip()
    lesson_date, matched_name = find_next_lesson_date(new_subj)
    if not lesson_date:
        await msg.answer(f"❌ Предмет «{html.escape(new_subj)}» не найден в расписании 8-Б класса.", parse_mode="HTML")
        return

    reminder_date = calculate_reminder_date(lesson_date)
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
        f"✅ <b>Предмет изменен на:</b> {html.escape(matched_name)}\n"
        f"📅 <b>Урок будет:</b> {lesson_weekday}, {lesson_date.strftime('%d.%m')}\n"
        f"⏰ <b>Отправка:</b> {send_weekday}, {reminder_date.strftime('%d.%m')} в 15:10",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

@dp.message(StateFilter(None), F.from_user.id == ADMIN_CHAT_ID)
async def handle_homework(msg: Message):
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

        await asyncio.sleep(1.0)

        if mg_id in media_group_cache:
            data = media_group_cache.pop(mg_id)
            photos = data["photos"]
            raw_text = (data["caption"] or "").strip()
            msg = data["msg"]
        else:
            return
    else:
        photos = [msg.photo[-1].file_id] if msg.photo else []
        raw_text = (msg.caption if msg.photo else (msg.text or "")).strip()

    if photos and not raw_text:
        await msg.answer("⚠️ Ты отправил фото без названия предмета. Напиши предмет в подписи к фото (например: <code>Химия</code>).", reply_markup=get_main_keyboard(), parse_mode="HTML")
        return

    if ":" in raw_text:
        subject, task = raw_text.split(":", 1)
        subject, task = subject.strip(), task.strip()
    else:
        subject, task = raw_text.strip(), ""

    lesson_date, matched_name = find_next_lesson_date(subject)
    if not lesson_date:
        await msg.answer(f"❌ Предмет «{html.escape(subject)}» не найден в расписании 8-Б класса.", reply_markup=get_main_keyboard(), parse_mode="HTML")
        return

    final_text = format_task_text(task, len(photos))

    reminder_date = calculate_reminder_date(lesson_date)
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
        f"✅ <b>Задание сохранено.</b>\n\n"
        f"📚 <b>Предмет:</b> {html.escape(matched_name)}\n"
        f"📅 <b>Урок:</b> {lesson_weekday}, {lesson_date.strftime('%d.%m')}\n"
        f"⏰ <b>Отправка:</b> {send_weekday}, {reminder_date.strftime('%d.%m')} в 15:10\n\n"
        f"📝 <b>Задание:</b>\n"
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
        await msg.answer("📭 В базе нет заданий на сегодня или ранее.", reply_markup=get_main_keyboard())
    else:
        await msg.answer(f"✅ Отправлено заданий: {count}", reply_markup=get_main_keyboard())

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    await fix_database_quotes_and_dates()
    
    scheduler.add_job(send_due_homework, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    now = datetime.now(TZ)
    if now.hour > SEND_HOUR or (now.hour == SEND_HOUR and now.minute >= SEND_MINUTE):
        await send_due_homework(force_resend=False)

    print("Бот 8-Б класса запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
