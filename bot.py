import os
import re
import time
import json
import html
import base64
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import asyncio
import aiosqlite
import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, BotCommand,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove, MenuButtonWebApp, MenuButtonCommands,
    InputMediaPhoto, BufferedInputFile, WebAppInfo
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ================= КОНФИГУРАЦИЯ =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9004754440:AAHh3KbECKapVbbSJPyAq5WSGX3_-S4r0Ws").strip()
ADMIN_CHAT_ID = 6243658367
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

WEBAPP_URL = os.environ.get("WEBAPP_URL", os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")

TZ = ZoneInfo("Europe/Chisinau")

# Время отправки: 15:10
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
    0: "понедельник", 1: "вторник", 2: "среду",
    3: "четверг", 4: "пятницу", 5: "субботу", 6: "воскресенье"
}
# ===============================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=TZ)

media_group_cache = {}

# Хранилище для системы АНТИ-СПАМ
banned_users_cache = set()
last_action_time = {}
spam_strikes = {}
muted_until = {}
msg_timestamps = {}

def check_antispam(user_id: int, is_text_msg: bool = False) -> tuple[bool, str | None]:
    if user_id == ADMIN_CHAT_ID:
        return False, None
    if user_id in banned_users_cache:
        return True, None  # Полный игнор забаненных

    now = time.time()
    if muted_until.get(user_id, 0) > now:
        return True, None  # Полный игнор во время мута

    # Проверка скорости нажатий / сообщений
    last_t = last_action_time.get(user_id, 0)
    if now - last_t < 2.0:
        spam_strikes[user_id] = spam_strikes.get(user_id, 0) + 1
        if spam_strikes[user_id] >= 5:
            muted_until[user_id] = now + 180  # 3 минуты мута
            spam_strikes[user_id] = 0
            return True, "⏳ Слишком много действий подряд. Включена защита от спама на 3 минуты."
        return True, "Не так быстро! Подожди пару секунд."

    last_action_time[user_id] = now
    if now - last_t > 10:
        spam_strikes[user_id] = 0

    # Ограничение на спам текстовыми сообщениями в личку старосте
    if is_text_msg:
        history = [t for t in msg_timestamps.get(user_id, []) if now - t < 60]
        if len(history) >= 3:
            muted_until[user_id] = now + 300  # 5 минут мута
            msg_timestamps[user_id] = []
            return True, "⏳ Слишком много сообщений за минуту. Подожди 5 минут (защита от спама)."
        history.append(now)
        msg_timestamps[user_id] = history

    return False, None

def get_target_lesson_date() -> tuple[datetime.date, str]:
    now = datetime.now(TZ)
    today = now.date()
    weekday = today.weekday()
    
    if weekday == 4:
        target = today + timedelta(days=3)
        day_text = "на понедельник"
    elif weekday == 5:
        target = today + timedelta(days=2)
        day_text = "на понедельник"
    elif weekday == 6:
        target = today + timedelta(days=1)
        day_text = "на понедельник"
    else:
        target = today + timedelta(days=1)
        day_text = f"на {ACCUSATIVE_DAYS.get(target.weekday(), 'завтра')}"
        
    return target, day_text

# ВЕБ-САЙТ С МЕССЕНДЖЕРОМ И БАН-КНОПКОЙ
WEBAPP_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>Староста 8-Б | Панель управления</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {
      --bg: #090d16;
      --card: #131b26;
      --card-hover: #1a2535;
      --input: #0c121c;
      --text: #f0f6fc;
      --muted: #8b9bb4;
      --accent: #2ea6ff;
      --accent-gradient: linear-gradient(135deg, #2ea6ff 0%, #0077ff 100%);
      --accent-glow: rgba(46, 166, 255, 0.3);
      --danger: #ff5252;
      --success: #00e676;
      --border: rgba(255, 255, 255, 0.08);
      --bubble-in: #1c2738;
      --bubble-out: #1b4974;
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; font-family: var(--font); }
    body {
      margin: 0; padding: 0;
      background-color: var(--bg);
      color: var(--text);
      display: flex; flex-direction: column; height: 100vh; overflow: hidden;
    }
    header {
      background: rgba(19, 27, 38, 0.95);
      backdrop-filter: blur(12px);
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex; justify-content: space-between; align-items: center;
      flex-shrink: 0;
    }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 800; font-size: 16px; }
    .brand-badge { background: var(--accent); color: #fff; font-size: 11px; padding: 2px 7px; border-radius: 6px; }
    .header-info { font-size: 12px; color: var(--muted); }

    .nav-bar {
      display: flex; gap: 6px; padding: 10px 14px;
      background: var(--bg); border-bottom: 1px solid var(--border);
      overflow-x: auto; scrollbar-width: none; flex-shrink: 0;
    }
    .nav-bar::-webkit-scrollbar { display: none; }
    .nav-item {
      padding: 8px 14px; font-size: 13px; font-weight: 700;
      border-radius: 10px; border: 1px solid var(--border);
      background: var(--card); color: var(--muted);
      cursor: pointer; white-space: nowrap; transition: all 0.2s;
    }
    .nav-item.active {
      background: var(--accent-gradient); color: #fff;
      border-color: transparent; box-shadow: 0 4px 14px var(--accent-glow);
    }

    main { flex: 1; overflow-y: auto; padding: 14px; position: relative; }
    .tab-view { display: none; }
    .tab-view.active { display: block; animation: fadeIn 0.2s ease; }
    @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

    .chat-list-view { display: flex; flex-direction: column; gap: 8px; }
    .chat-user-item {
      background: var(--card); border: 1px solid var(--border);
      padding: 12px 14px; border-radius: 14px; cursor: pointer;
      display: flex; justify-content: space-between; align-items: center;
    }
    .user-avatar {
      width: 40px; height: 40px; border-radius: 50%;
      background: linear-gradient(135deg, #2ea6ff, #9c27b0);
      display: flex; align-items: center; justify-content: center;
      font-weight: 700; font-size: 16px; color: #fff; margin-right: 12px; flex-shrink: 0;
    }
    .user-meta { flex: 1; min-width: 0; }
    .user-name { font-size: 14px; font-weight: 700; margin-bottom: 2px; }
    .user-last-msg { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .user-time { font-size: 11px; color: var(--muted); }
    .ban-tag { font-size: 10px; background: rgba(255, 82, 82, 0.2); color: var(--danger); padding: 2px 6px; border-radius: 4px; margin-left: 6px; }

    .active-chat-box {
      display: none; flex-direction: column; height: 100%;
      background: var(--bg); position: absolute; top: 0; left: 0; right: 0; bottom: 0;
      z-index: 50; padding: 12px;
    }
    .chat-top-header {
      display: flex; align-items: center; gap: 10px;
      padding-bottom: 10px; border-bottom: 1px solid var(--border); flex-shrink: 0;
    }
    .chat-back-btn {
      background: var(--card); border: 1px solid var(--border);
      color: var(--text); border-radius: 8px; padding: 6px 12px;
      font-weight: 700; font-size: 12px; cursor: pointer;
    }
    .chat-messages {
      flex: 1; overflow-y: auto; padding: 12px 4px;
      display: flex; flex-direction: column; gap: 10px;
    }
    .msg-bubble {
      max-width: 82%; padding: 10px 14px; border-radius: 14px;
      font-size: 13.5px; line-height: 1.45; word-wrap: break-word;
    }
    .msg-in { align-self: flex-start; background: var(--bubble-in); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
    .msg-out { align-self: flex-end; background: var(--bubble-out); color: #fff; border-bottom-right-radius: 4px; }
    .msg-time { font-size: 10px; opacity: 0.6; text-align: right; margin-top: 4px; }
    .chat-input-bar { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid var(--border); flex-shrink: 0; }
    .chat-input-bar input {
      flex: 1; background: var(--card); border: 1px solid var(--border);
      color: var(--text); border-radius: 12px; padding: 11px 14px; font-size: 14px; outline: none;
    }
    .chat-send-btn {
      background: var(--accent-gradient); color: #fff;
      border: none; border-radius: 12px; padding: 0 18px; font-weight: 700; font-size: 14px; cursor: pointer;
    }

    .card {
      background: var(--card); border-radius: 16px; padding: 16px; margin-bottom: 14px;
      border: 1px solid var(--border); box-shadow: 0 6px 20px rgba(0, 0, 0, 0.35);
    }
    .card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .subj-pill {
      background: rgba(46, 166, 255, 0.12); color: var(--accent);
      border: 1px solid rgba(46, 166, 255, 0.35); padding: 7px 12px;
      border-radius: 10px; font-size: 13px; font-weight: 700;
      display: inline-flex; align-items: center; gap: 6px; cursor: pointer;
    }
    .date-badge { font-size: 12px; color: var(--muted); text-align: right; cursor: pointer; text-decoration: underline dotted; }
    textarea {
      width: 100%; background: var(--input); color: var(--text);
      border: 1px solid var(--border); border-radius: 12px;
      padding: 12px; font-size: 14px; line-height: 1.45; resize: vertical; min-height: 85px; outline: none;
    }
    textarea:focus { border-color: var(--accent); }

    .dates-slider { display: flex; gap: 6px; margin-bottom: 12px; overflow-x: auto; scrollbar-width: none; }
    .dates-slider::-webkit-scrollbar { display: none; }
    .date-chip {
      background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border);
      color: var(--muted); padding: 6px 12px; border-radius: 10px; font-size: 12px; font-weight: 600; white-space: nowrap; cursor: pointer;
    }
    .date-chip.active { background: rgba(46, 166, 255, 0.2); border-color: var(--accent); color: var(--accent); font-weight: 700; }

    .ai-row { display: flex; gap: 6px; margin: 10px 0 12px 0; overflow-x: auto; scrollbar-width: none; }
    .ai-row::-webkit-scrollbar { display: none; }
    .ai-tag {
      background: rgba(46, 166, 255, 0.08); border: 1px solid rgba(46, 166, 255, 0.2);
      color: #79c0ff; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; white-space: nowrap; cursor: pointer;
    }

    .action-row { display: flex; gap: 8px; }
    .btn {
      flex: 1; padding: 10px; border-radius: 10px; border: none;
      font-size: 13px; font-weight: 700; cursor: pointer; display: flex; align-items: center; justify-content: center;
    }
    .btn-save { background: var(--accent-gradient); color: #fff; }
    .btn-send { background: rgba(0, 230, 118, 0.14); color: var(--success); border: 1px solid rgba(0, 230, 118, 0.3); }
    .btn-del { background: rgba(255, 82, 82, 0.12); color: var(--danger); border: 1px solid rgba(255, 82, 82, 0.25); flex: 0.55; }

    .photo-preview-grid { display: flex; gap: 8px; margin: 10px 0; flex-wrap: wrap; }
    .photo-thumb { position: relative; width: 72px; height: 72px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
    .photo-thumb img { width: 100%; height: 100%; object-fit: cover; }
    .photo-thumb .del-btn {
      position: absolute; top: 3px; right: 3px; background: rgba(0,0,0,0.8);
      color: #fff; border-radius: 50%; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-size: 12px; cursor: pointer;
    }
    .upload-btn {
      background: rgba(255, 255, 255, 0.04); border: 1px dashed var(--accent);
      color: var(--accent); padding: 12px; border-radius: 12px; font-size: 13px; font-weight: 600; text-align: center; cursor: pointer; margin-bottom: 12px;
    }

    .diary-day { background: var(--card); border-radius: 16px; padding: 16px; margin-bottom: 14px; border: 1px solid var(--border); }
    .diary-day-title { font-size: 15px; font-weight: 700; color: var(--accent); margin-bottom: 12px; display: flex; justify-content: space-between; }
    .diary-task-item { background: var(--input); border-radius: 12px; padding: 12px; margin-bottom: 10px; border: 1px solid var(--border); }
    .diary-task-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .status-chip { font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 600; }
    .status-sent { background: rgba(0, 230, 118, 0.15); color: var(--success); }
    .status-pending { background: rgba(46, 166, 255, 0.15); color: var(--accent); }

    .modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.7); z-index: 999; display: none; align-items: flex-end; }
    .modal-overlay.active { display: flex; }
    .sheet { background: #131b26; width: 100%; border-top-left-radius: 20px; border-top-right-radius: 20px; padding: 18px 16px 30px 16px; max-height: 80vh; overflow-y: auto; border-top: 1px solid var(--border); }
    .sheet-title { font-size: 16px; font-weight: 700; text-align: center; margin-bottom: 16px; }
    .subj-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
    .subj-item { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border); padding: 12px 10px; border-radius: 12px; font-size: 13px; font-weight: 600; text-align: center; cursor: pointer; }
    .subj-item:active { background: var(--accent); color: #fff; }

    .sched-card { background: var(--card); border-radius: 14px; padding: 12px 16px; margin-bottom: 10px; border: 1px solid var(--border); }
    .sched-head { font-size: 15px; font-weight: 700; color: var(--accent); margin-bottom: 6px; }
    .sched-line { font-size: 13px; padding: 5px 0; display: flex; justify-content: space-between; border-bottom: 1px solid rgba(255,255,255,0.03); cursor: pointer; }

    .toast {
      position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(100px);
      background: #1f2c3d; color: #fff; padding: 10px 22px; border-radius: 30px;
      font-size: 13px; font-weight: 600; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5); border: 1px solid var(--border); opacity: 0; transition: all 0.25s ease; z-index: 1000;
    }
    .toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
    .status-msg { text-align: center; color: var(--muted); padding: 40px 10px; font-size: 14px; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <span>8-Б Секретарь</span>
      <span class="brand-badge">PRO</span>
    </div>
    <div class="header-info" id="clock">15:10</div>
  </header>

  <div class="nav-bar">
    <div class="nav-item active" onclick="switchNav('chats')">💬 Переписка</div>
    <div class="nav-item" onclick="switchNav('tasks')">📋 Очередь ДЗ</div>
    <div class="nav-item" onclick="switchNav('add')">➕ Добавить ДЗ</div>
    <div class="nav-item" onclick="switchNav('diary')">📖 Дневник</div>
    <div class="nav-item" onclick="switchNav('schedule')">📅 Расписание</div>
  </div>

  <main>
    <!-- 1. ВКЛАДКА: ПЕРЕПИСКА -->
    <div id="view-chats" class="tab-view active">
      <div id="chats-list" class="chat-list-view">
        <div class="status-msg">Загрузка диалогов...</div>
      </div>

      <div id="active-chat-box" class="active-chat-box">
        <div class="chat-top-header">
          <button class="chat-back-btn" onclick="closeActiveChat()">← Назад</button>
          <div style="flex:1;">
            <div id="chat-title-name" style="font-size:14px; font-weight:700;">Ученик</div>
            <div id="chat-title-user" style="font-size:11px; color:var(--muted);">@username</div>
          </div>
          <button id="chat-ban-btn" class="chat-back-btn" style="color:var(--danger);" onclick="toggleBanActiveUser()">🚫 Бан</button>
        </div>
        <div id="chat-messages-box" class="chat-messages"></div>
        <div class="chat-input-bar">
          <input type="text" id="chat-msg-input" placeholder="Написать сообщение..." onkeydown="if(event.key==='Enter') sendWebReply()">
          <button class="chat-send-btn" onclick="sendWebReply()">Отправить</button>
        </div>
      </div>
    </div>

    <!-- 2. ОЧЕРЕДЬ ДЗ -->
    <div id="view-tasks" class="tab-view">
      <div id="tasks-box" class="status-msg">Загрузка очереди...</div>
    </div>

    <!-- 3. ДОБАВИТЬ ДЗ -->
    <div id="view-add" class="tab-view">
      <div class="card">
        <div style="margin-bottom: 12px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 6px; display: block;">ПРЕДМЕТ</label>
          <div class="subj-pill" onclick="openPicker('new')">
            <span id="new-subj-label">Химия</span> <span style="font-size:10px;">▼</span>
          </div>
        </div>

        <div style="margin-bottom: 12px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 6px; display: block;">ДЕНЬ УРОКА</label>
          <div id="new-dates-slider" class="dates-slider"></div>
        </div>

        <input type="file" id="new-photo-input" accept="image/*" multiple style="display:none;" onchange="handleNewPhotos(event)">
        <div class="upload-btn" onclick="document.getElementById('new-photo-input').click()">
          📷 Прикрепить фото (камера / файлы)
        </div>
        <div id="new-photos-preview" class="photo-preview-grid"></div>

        <div style="margin-bottom: 8px;">
          <label style="font-size: 12px; color: var(--muted); font-weight: 700; margin-bottom: 6px; display: block;">ЗАДАНИЕ</label>
          <textarea id="new-task-text" placeholder="Напиши текст задания..."></textarea>
        </div>
        <div class="ai-row">
          <div class="ai-tag" onclick="aiNew('Оформи красиво и понятно')">✨ Оформить ИИ</div>
          <div class="ai-tag" onclick="aiNew('Сделай короче')">✂️ Короче</div>
          <div class="ai-tag" onclick="aiNew('Разбей по пунктам')">📋 Списком</div>
        </div>
        <button class="btn btn-save" style="width: 100%; padding: 13px;" onclick="saveNewTask()">Добавить в очередь</button>
      </div>
    </div>

    <!-- 4. ДНЕВНИК -->
    <div id="view-diary" class="tab-view">
      <div id="diary-box" class="status-msg">Загрузка дневника...</div>
    </div>

    <!-- 5. РАСПИСАНИЕ -->
    <div id="view-schedule" class="tab-view">
      <div id="sched-box" class="status-msg">Загрузка расписания...</div>
    </div>
  </main>

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

    const ADMIN_USER_ID = 6243658367;
    const currentUserId = tg?.initDataUnsafe?.user?.id || 0;

    if (currentUserId !== ADMIN_USER_ID && currentUserId !== 0) {
      document.body.innerHTML = `
        <div style="text-align:center; padding:70px 20px; font-family:sans-serif;">
          <h2 style="color:#ff5252; margin-bottom:8px;">⛔️ Доступ ограничен</h2>
          <p style="color:#8b9bb4; font-size:14px; line-height:1.5;">Личный веб-кабинет доступен только старосте класса.</p>
        </div>
      `;
      throw new Error("Access Denied");
    }

    setInterval(() => {
      const d = new Date();
      document.getElementById('clock').innerText = d.toLocaleTimeString('ru-RU', {hour: '2-digit', minute:'2-digit'});
    }, 1000);

    const SUBJECTS = [
      "Химия", "Рум. яз.", "ИКТ (ИКТБН)", "Математика", "Литература", 
      "Физика", "Биология", "История", "Рус. яз.", "Англ. яз.", 
      "Болг. яз.", "Информ.", "География", "РЛ (Развитие личности)", 
      "ГВ", "Труд", "Физ. восп.", "Муз. восп."
    ];

    let currentPickerTarget = null;
    let newUploadedPhotos = [];
    let selectedLessonDateNew = null;
    let selectedReminderDateNew = null;
    let activeChatUserId = null;
    let activeChatIsBanned = false;

    function showToast(text) {
      const t = document.getElementById('toast');
      t.innerText = text;
      t.classList.add('show');
      setTimeout(() => t.classList.remove('show'), 2000);
      if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred('success');
    }

    function switchNav(tabName) {
      document.querySelectorAll('.tab-view').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
      document.getElementById('view-' + tabName).classList.add('active');
      event.target.classList.add('active');

      if (tabName === 'chats') loadChats();
      if (tabName === 'tasks') loadTasks();
      if (tabName === 'diary') loadDiary();
      if (tabName === 'add') refreshNewDates('Химия');
      if (tabName === 'schedule') loadSchedule();
    }

    // --- ПЕРЕПИСКА ---
    async function loadChats() {
      const box = document.getElementById('chats-list');
      try {
        const res = await fetch('/api/chats');
        const users = await res.json();
        if (!users || users.length === 0) {
          box.innerHTML = '<div class="status-msg">💬 Пока никто из учеников не писал боту.<br>Диалоги появятся здесь!</div>';
          return;
        }
        box.innerHTML = '';
        users.forEach(u => {
          const item = document.createElement('div');
          item.className = 'chat-user-item';
          const initial = u.full_name ? u.full_name.charAt(0).toUpperCase() : 'У';
          const uname = u.username ? '@' + u.username : 'без юзернейма';
          const banLabel = u.is_banned ? '<span class="ban-tag">БАН</span>' : '';
          item.onclick = () => openActiveChat(u.user_id, u.full_name, uname, u.is_banned);

          item.innerHTML = `
            <div style="display:flex; align-items:center; flex:1; min-width:0;">
              <div class="user-avatar">${initial}</div>
              <div class="user-meta">
                <div class="user-name">${u.full_name} ${banLabel}</div>
                <div class="user-last-msg">${u.last_message || 'Открыл бота'}</div>
              </div>
            </div>
            <div class="user-time">${u.last_time || ''}</div>
          `;
          box.appendChild(item);
        });
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки переписки.</div>';
      }
    }

    async function openActiveChat(userId, name, uname, isBanned) {
      activeChatUserId = userId;
      activeChatIsBanned = Boolean(isBanned);
      document.getElementById('chat-title-name').innerText = name;
      document.getElementById('chat-title-user').innerText = uname;
      updateBanButtonUI();
      document.getElementById('active-chat-box').style.display = 'flex';
      await loadMessages(userId);
    }

    function updateBanButtonUI() {
      const btn = document.getElementById('chat-ban-btn');
      if (activeChatIsBanned) {
        btn.innerText = '✅ Разбан';
        btn.style.color = 'var(--success)';
      } else {
        btn.innerText = '🚫 Бан';
        btn.style.color = 'var(--danger)';
      }
    }

    async function toggleBanActiveUser() {
      if (!activeChatUserId) return;
      try {
        const res = await fetch('/api/toggle_ban', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ user_id: activeChatUserId })
        });
        const data = await res.json();
        if (data.ok) {
          activeChatIsBanned = data.is_banned;
          updateBanButtonUI();
          showToast(activeChatIsBanned ? 'Ученик заблокирован!' : 'Ученик разблокирован!');
        }
      } catch (e) { alert('Ошибка'); }
    }

    function closeActiveChat() {
      document.getElementById('active-chat-box').style.display = 'none';
      activeChatUserId = null;
      loadChats();
    }

    async function loadMessages(userId) {
      const box = document.getElementById('chat-messages-box');
      box.innerHTML = '<div class="status-msg">Загрузка сообщений...</div>';
      try {
        const res = await fetch('/api/messages?user_id=' + userId);
        const msgs = await res.json();
        box.innerHTML = '';
        if (!msgs || msgs.length === 0) {
          box.innerHTML = '<div class="status-msg">Сообщений пока нет.</div>';
          return;
        }
        msgs.forEach(m => {
          const bubble = document.createElement('div');
          bubble.className = 'msg-bubble ' + (m.sender === 'admin' ? 'msg-out' : 'msg-in');
          bubble.innerHTML = `
            <div>${m.text}</div>
            <div class="msg-time">${m.timestamp}</div>
          `;
          box.appendChild(bubble);
        });
        box.scrollTop = box.scrollHeight;
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки.</div>';
      }
    }

    async function sendWebReply() {
      if (!activeChatUserId) return;
      const input = document.getElementById('chat-msg-input');
      const text = input.value.trim();
      if (!text) return;

      try {
        const res = await fetch('/api/send_message', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ user_id: activeChatUserId, text })
        });
        const data = await res.json();
        if (data.ok) {
          input.value = '';
          await loadMessages(activeChatUserId);
          showToast('Отправлено ученику!');
        } else {
          alert('Ошибка при отправке');
        }
      } catch (e) { alert('Ошибка соединения'); }
    }

    // --- ШТОРКА ПРЕДМЕТОВ ---
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
        refreshNewDates(subjName);
      } else {
        document.getElementById('subj-label-' + currentPickerTarget).innerText = subjName;
        refreshCardDates(currentPickerTarget, subjName);
      }
      closePicker();
    }

    async function fetchLessonDates(subj) {
      const res = await fetch('/api/lesson_dates?subject=' + encodeURIComponent(subj));
      return await res.json();
    }

    async function refreshNewDates(subj) {
      const slider = document.getElementById('new-dates-slider');
      slider.innerHTML = '<span style="font-size:12px; color:var(--muted);">Поиск дней...</span>';
      const dates = await fetchLessonDates(subj);
      slider.innerHTML = '';
      dates.forEach((d, idx) => {
        const chip = document.createElement('div');
        chip.className = 'date-chip' + (idx === 0 ? ' active' : '');
        chip.innerText = d.lesson_display;
        chip.onclick = () => {
          slider.querySelectorAll('.date-chip').forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          selectedLessonDateNew = d.lesson_date;
          selectedReminderDateNew = d.reminder_date;
        };
        slider.appendChild(chip);
      });
      selectedLessonDateNew = dates[0].lesson_date;
      selectedReminderDateNew = dates[0].reminder_date;
    }

    async function refreshCardDates(taskId, subj) {
      const slider = document.getElementById('card-dates-' + taskId);
      if (!slider) return;
      slider.innerHTML = 'Загрузка...';
      const dates = await fetchLessonDates(subj);
      slider.innerHTML = '';
      dates.forEach((d, idx) => {
        const chip = document.createElement('div');
        chip.className = 'date-chip' + (idx === 0 ? ' active' : '');
        chip.innerText = d.lesson_display;
        chip.onclick = () => {
          slider.querySelectorAll('.date-chip').forEach(c => c.classList.remove('active'));
          chip.classList.add('active');
          slider.dataset.lessonDate = d.lesson_date;
          slider.dataset.reminderDate = d.reminder_date;
        };
        slider.appendChild(chip);
      });
      slider.dataset.lessonDate = dates[0].lesson_date;
      slider.dataset.reminderDate = dates[0].reminder_date;
    }

    function compressFile(file) {
      return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = (e) => {
          const img = new Image();
          img.onload = () => {
            const canvas = document.createElement('canvas');
            let w = img.width, h = img.height;
            const maxDim = 1280;
            if (w > maxDim || h > maxDim) {
              if (w > h) { h = Math.round((h * maxDim) / w); w = maxDim; }
              else { w = Math.round((w * maxDim) / h); h = maxDim; }
            }
            canvas.width = w; canvas.height = h;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, w, h);
            resolve(canvas.toDataURL('image/jpeg', 0.8));
          };
          img.src = e.target.result;
        };
        reader.readAsDataURL(file);
      });
    }

    async function handleNewPhotos(event) {
      const files = event.target.files;
      for (let i = 0; i < files.length; i++) {
        const b64 = await compressFile(files[i]);
        newUploadedPhotos.push(b64);
      }
      renderNewPhotoPreviews();
    }

    function renderNewPhotoPreviews() {
      const box = document.getElementById('new-photos-preview');
      box.innerHTML = '';
      newUploadedPhotos.forEach((src, idx) => {
        const t = document.createElement('div');
        t.className = 'photo-thumb';
        t.innerHTML = `<img src="${src}"><div class="del-btn" onclick="removeNewPhoto(${idx})">×</div>`;
        box.appendChild(t);
      });
    }

    function removeNewPhoto(idx) {
      newUploadedPhotos.splice(idx, 1);
      renderNewPhotoPreviews();
    }

    async function loadTasks() {
      const box = document.getElementById('tasks-box');
      try {
        const res = await fetch('/api/homework?user_id=' + currentUserId);
        const data = await res.json();
        const tasks = data.tasks || [];

        if (!tasks || tasks.length === 0) {
          box.innerHTML = '<div class="status-msg">📭 В очереди нет заданий.<br>Нажми «+ Добавить ДЗ» чтобы создать!</div>';
          return;
        }
        box.innerHTML = `<div style="font-size:13px; font-weight:700; color:var(--muted); margin-bottom:12px;">Активных заданий: ${tasks.length}</div>`;
        
        for (const t of tasks) {
          const card = document.createElement('div');
          card.className = 'card';
          card.id = 'task-box-' + t.id;

          let cleanText = t.task_text.replace(/<[^>]+>/g, '').trim();

          card.innerHTML = `
            <div class="card-top">
              <div class="subj-pill" onclick="openPicker(${t.id})">
                <span id="subj-label-${t.id}">${t.subject}</span>
                <span style="font-size:10px;">▼</span>
              </div>
              <div class="date-badge" onclick="toggleDateEdit(${t.id}, '${t.subject}')">
                ${t.date_info} ✏️
              </div>
            </div>
            <div id="card-dates-${t.id}" class="dates-slider" style="display:none;" data-lesson-date="${t.lesson_date || ''}" data-reminder-date="${t.send_date}"></div>
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
        }
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки очереди.</div>';
      }
    }

    async function toggleDateEdit(taskId, subj) {
      const slider = document.getElementById('card-dates-' + taskId);
      if (slider.style.display === 'none') {
        slider.style.display = 'flex';
        await refreshCardDates(taskId, subj);
      } else {
        slider.style.display = 'none';
      }
    }

    async function saveTask(id) {
      const text = document.getElementById('text-' + id).value.trim();
      const subject = document.getElementById('subj-label-' + id).innerText;
      const slider = document.getElementById('card-dates-' + id);
      const reminderDate = slider?.dataset?.reminderDate || null;
      const lessonDate = slider?.dataset?.lessonDate || null;

      if (!text) { alert('Текст не может быть пустым'); return; }

      try {
        const res = await fetch('/api/save', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id, text, subject, reminder_date: reminderDate, lesson_date: lessonDate, user_id: currentUserId })
        });
        const data = await res.json();
        if (data.ok) {
          showToast('Сохранено!');
          loadTasks();
        } else alert('Ошибка сохранения');
      } catch (e) { alert('Ошибка сети'); }
    }

    async function deleteTask(id) {
      if (!confirm('Удалить это задание из очереди?')) return;
      try {
        const res = await fetch('/api/delete', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id, user_id: currentUserId })
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
          body: JSON.stringify({ id, user_id: currentUserId })
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
        } else textarea.value = original;
      } catch (e) { textarea.value = original; }
    }

    async function aiNew(instruction) {
      const textarea = document.getElementById('new-task-text');
      const subject = document.getElementById('new-subj-label').innerText;
      const original = textarea.value.trim();
      if (!original) { alert('Сначала напиши текст задания'); return; }

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
        } else textarea.value = original;
      } catch (e) { textarea.value = original; }
    }

    async function saveNewTask() {
      const subject = document.getElementById('new-subj-label').innerText;
      const text = document.getElementById('new-task-text').value.trim();
      if (!text && newUploadedPhotos.length === 0) { alert('Напиши задание или прикрепи фото!'); return; }

      try {
        const res = await fetch('/api/add', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            subject, text,
            photos: newUploadedPhotos,
            lesson_date: selectedLessonDateNew,
            reminder_date: selectedReminderDateNew,
            user_id: currentUserId
          })
        });
        const data = await res.json();
        if (data.ok) {
          document.getElementById('new-task-text').value = '';
          newUploadedPhotos = [];
          renderNewPhotoPreviews();
          showToast('Задание добавлено!');
          setTimeout(() => { document.querySelectorAll('.nav-item')[1].click(); }, 350);
        } else alert('Ошибка при добавлении');
      } catch (e) { alert('Ошибка сети'); }
    }

    async function loadDiary() {
      const box = document.getElementById('diary-box');
      box.innerHTML = '<div class="status-msg">Загрузка дневника...</div>';
      try {
        const res = await fetch('/api/diary');
        const diaryDays = await res.json();
        if (!diaryDays || diaryDays.length === 0) {
          box.innerHTML = '<div class="status-msg">📖 В дневнике пока нет записей.</div>';
          return;
        }
        box.innerHTML = '';
        diaryDays.forEach(day => {
          const dayCard = document.createElement('div');
          dayCard.className = 'diary-day';
          let tasksHtml = '';
          day.tasks.forEach(task => {
            const statusHtml = task.is_sent 
              ? '<span class="status-chip status-sent">Отправлено</span>'
              : '<span class="status-chip status-pending">В очереди</span>';

            let cleanTask = task.task_text.replace(/<[^>]+>/g, '').trim();
            tasksHtml += `
              <div class="diary-task-item">
                <div class="diary-task-top">
                  <div style="font-weight:700; font-size:14px;">${task.subject}</div>
                  <div>${statusHtml}</div>
                </div>
                <div style="font-size:13px; line-height:1.45; white-space:pre-wrap;">${cleanTask}</div>
                <div style="margin-top:8px; display:flex; justify-content:flex-end;">
                  <button class="btn btn-send" style="padding:6px 12px; font-size:11px; flex:initial;" onclick="sendTaskNow(${task.id})">Выгрузить в чат</button>
                </div>
              </div>
            `;
          });
          dayCard.innerHTML = `
            <div class="diary-day-title">
              <span>📅 ${day.date_display}</span>
              <span style="font-size:12px; color:var(--muted);">${day.tasks.length} предм.</span>
            </div>
            ${tasksHtml}
          `;
          box.appendChild(dayCard);
        });
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки дневника.</div>';
      }
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
            const formattedItem = item.charAt(0).toUpperCase() + item.slice(1);
            html += `<div class="sched-line" onclick="addFromSchedule('${formattedItem}')"><span>${num + 1}. ${formattedItem}</span> <span style="font-size:11px; color:var(--accent);">➕ ДЗ</span></div>`;
          });
          html += `</div>`;
        });
        box.innerHTML = html;
      } catch (e) {
        box.innerHTML = '<div class="status-msg">Ошибка загрузки расписания</div>';
      }
    }

    function addFromSchedule(subj) {
      document.querySelectorAll('.nav-item')[2].click();
      document.getElementById('new-subj-label').innerText = subj;
      refreshNewDates(subj);
    }

    loadChats();
  </script>
</body>
</html>
"""

# Кнопки ГЛАВНОГО ИНЛАЙН-МЕНЮ (чистый экран без нижней клавиатуры)
def get_admin_inline_menu() -> InlineKeyboardMarkup:
    has_web = bool(WEBAPP_URL and WEBAPP_URL.startswith("https://"))
    buttons = []
    if has_web:
        buttons.append([InlineKeyboardButton(text="🌐 Открыть Сайт (Переписка, Фото, ИИ)", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))])
    buttons.append([
        InlineKeyboardButton(text="📋 Очередь ДЗ", callback_data="adm_dz"),
        InlineKeyboardButton(text="📅 Расписание", callback_data="adm_sched")
    ])
    buttons.append([
        InlineKeyboardButton(text="⚡️ Отправить ДЗ сейчас", callback_data="adm_send")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_student_inline_menu() -> InlineKeyboardMarkup:
    _, target_title = get_target_lesson_date()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📚 ДЗ {target_title}", callback_data="stud_dz")],
        [InlineKeyboardButton(text="📅 Расписание уроков", callback_data="stud_sched")]
    ])

def calculate_reminder_date(lesson_date: datetime.date) -> datetime.date:
    if lesson_date.weekday() == 0:
        return lesson_date - timedelta(days=3)
    return lesson_date - timedelta(days=1)

def normalize_subject(name: str) -> str:
    s = name.strip().lower()
    aliases = {
        "рум": "рум. яз.", "румынский": "рум. яз.", "рум яз": "рум. яз.",
        "болг": "болг. яз.", "болгарский": "болг. яз.", "болг яз": "болг. яз.",
        "рус": "рус. яз.", "русский": "рус. яз.", "рус яз": "рус. яз.",
        "мат": "математика", "матеша": "математика", "алгебра": "математика", "геометрия": "математика",
        "физ": "физика", "био": "биология", "хим": "химия", "гео": "география",
        "инф": "информ.", "информатика": "информ.", "икт": "иктбн", "иктбн": "иктбн",
        "ист": "история", "англ": "англ. яз.", "английский": "англ. яз.",
        "физра": "физ. восп.", "физкультура": "физ. восп.", "муз": "муз. восп.", "музыка": "муз. восп.",
        "лит": "литература", "литература": "литература", "лит-ра": "литература",
        "рл": "рл", "развитие": "рл", "разв": "рл", "развитие личности": "рл",
        "труд": "труд", "труды": "труд", "гв": "гв"
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

def get_all_upcoming_lessons(subj_query: str, limit: int = 5) -> list[dict]:
    norm_subj = normalize_subject(subj_query)
    today = datetime.now(TZ).date()
    results = []
    
    for offset in range(1, 16):
        check_date = today + timedelta(days=offset)
        weekday = check_date.weekday()
        day_subjects = SCHEDULE.get(weekday, [])
        for real_subj in day_subjects:
            if norm_subj in real_subj or real_subj in norm_subj:
                rem_date = calculate_reminder_date(check_date)
                results.append({
                    "lesson_date": check_date.strftime("%Y-%m-%d"),
                    "lesson_display": f"{DAYS_MAP[weekday].capitalize()}, {check_date.strftime('%d.%m')}",
                    "reminder_date": rem_date.strftime("%Y-%m-%d"),
                    "reminder_display": f"{DAYS_MAP[rem_date.weekday()].capitalize()}, {rem_date.strftime('%d.%m')} в 15:10",
                    "subject": real_subj.capitalize()
                })
                break
        if len(results) >= limit:
            break
    return results

def format_task_text(task_raw: str, num_photos: int) -> str:
    if not task_raw:
        return "<blockquote>Задание на фото с доски.</blockquote>" if num_photos > 0 else ""

    lines = [line.strip() for line in task_raw.strip().split("\n") if line.strip()]
    pattern = re.compile(r'^(?:#?\s*(\d+)\s*(?:фото|photo)|(?:фото|photo)\s*#?\s*(\d+))\s*[:,\-]?\s*(.*)$', re.IGNORECASE)
    
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
        num_words = {1: "Первое фото", 2: "Второе фото", 3: "Третье фото", 4: "Четвертое фото", 5: "Пятое фото"}
        output_parts = []
        if general_lines:
            output_parts.append(f"<blockquote>{html.escape(chr(10).join(general_lines))}</blockquote>")
        
        for idx in sorted(photo_blocks.keys()):
            label = num_words.get(idx, f"Фото {idx}")
            text_content = "\n".join(photo_blocks[idx]).strip() or "Задание на фото"
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

async def log_user_message(user_id: int, username: str, full_name: str, text: str, sender: str = "student"):
    now_str = datetime.now(TZ).strftime("%d.%m %H:%M")
    async with aiosqlite.connect("school.db") as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                last_active=excluded.last_active
        """, (user_id, username or "", full_name or "Ученик", now_str))

        await db.execute("""
            INSERT INTO messages (user_id, sender, text, timestamp)
            VALUES (?, ?, ?, ?)
        """, (user_id, sender, text, now_str))
        await db.commit()

# --- API МАРШРУТЫ WEB APP ---
async def web_handle(request):
    return web.Response(text=WEBAPP_HTML, content_type="text/html")

async def api_get_chats(request):
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.user_id, u.username, u.full_name, u.last_active as last_time, u.is_banned,
                   (SELECT text FROM messages WHERE user_id = u.user_id ORDER BY id DESC LIMIT 1) as last_message
            FROM users u
            WHERE u.user_id != ?
            ORDER BY u.id DESC
        """, (ADMIN_CHAT_ID,)) as cursor:
            rows = await cursor.fetchall()
            return web.json_response([dict(r) for r in rows])

async def api_toggle_ban(request):
    try:
        data = await request.json()
        target_uid = int(data.get("user_id"))
        async with aiosqlite.connect("school.db") as db:
            async with db.execute("SELECT is_banned FROM users WHERE user_id = ?", (target_uid,)) as cursor:
                row = await cursor.fetchone()
                new_status = 0 if (row and row[0] == 1) else 1
            await db.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (new_status, target_uid))
            await db.commit()
        
        if new_status == 1:
            banned_users_cache.add(target_uid)
        else:
            banned_users_cache.discard(target_uid)

        await sync_backup()
        return web.json_response({"ok": True, "is_banned": bool(new_status)})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_get_messages(request):
    user_id = int(request.query.get("user_id", 0))
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, user_id, sender, text, timestamp FROM messages WHERE user_id = ? ORDER BY id ASC", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return web.json_response([dict(r) for r in rows])

async def api_send_message(request):
    try:
        data = await request.json()
        target_uid = int(data.get("user_id"))
        text = str(data.get("text", "")).strip()
        if not text:
            return web.json_response({"ok": False, "error": "Empty text"})

        now_str = datetime.now(TZ).strftime("%d.%m %H:%M")
        outgoing = f"<b>Сообщение от старосты:</b>\n\n<blockquote>{html.escape(text)}</blockquote>"
        await bot.send_message(target_uid, outgoing, parse_mode="HTML")

        async with aiosqlite.connect("school.db") as db:
            await db.execute("""
                INSERT INTO messages (user_id, sender, text, timestamp)
                VALUES (?, ?, ?, ?)
            """, (target_uid, "admin", text, now_str))
            await db.commit()

        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_get_homework(request):
    user_id = int(request.query.get("user_id", 0))
    if user_id != ADMIN_CHAT_ID and user_id != 0:
        return web.json_response({"tasks": [], "error": "Access restricted"})

    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text, photo_id, send_date, lesson_date FROM homework WHERE is_sent = 0 ORDER BY send_date ASC") as cursor:
            rows = await cursor.fetchall()
            result = []
            for r in rows:
                send_d = datetime.strptime(r["send_date"], "%Y-%m-%d").date()
                l_date_str = r["lesson_date"]
                if l_date_str:
                    l_date = datetime.strptime(l_date_str, "%Y-%m-%d").date()
                else:
                    lesson_d, _ = find_next_lesson_date(r["subject"])
                    l_date = lesson_d if lesson_d else (send_d + timedelta(days=1))

                result.append({
                    "id": r["id"],
                    "subject": r["subject"],
                    "task_text": r["task_text"],
                    "send_date": r["send_date"],
                    "lesson_date": l_date.strftime("%Y-%m-%d"),
                    "date_info": f"{DAYS_MAP[l_date.weekday()].capitalize()} ({l_date.strftime('%d.%m')})"
                })
            return web.json_response({"tasks": result})

async def api_get_diary(request):
    try:
        async with aiosqlite.connect("school.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, subject, task_text, photo_id, send_date, lesson_date, is_sent FROM homework ORDER BY id DESC LIMIT 100") as cursor:
                rows = await cursor.fetchall()
                days_dict = {}
                for r in rows:
                    l_date_str = r["lesson_date"]
                    if not l_date_str:
                        if r["send_date"]:
                            try:
                                s_d = datetime.strptime(r["send_date"], "%Y-%m-%d").date()
                                l_date_str = (s_d + timedelta(days=1)).strftime("%Y-%m-%d")
                            except Exception:
                                pass
                    if not l_date_str:
                        l_d, _ = find_next_lesson_date(r["subject"])
                        l_date_str = l_d.strftime("%Y-%m-%d") if l_d else datetime.now(TZ).strftime("%Y-%m-%d")

                    try:
                        l_date = datetime.strptime(l_date_str, "%Y-%m-%d").date()
                        day_name = DAYS_MAP.get(l_date.weekday(), "день").capitalize()
                        date_display = f"{day_name}, {l_date.strftime('%d.%m')}"
                    except Exception:
                        date_display = l_date_str

                    if l_date_str not in days_dict:
                        days_dict[l_date_str] = {
                            "date": l_date_str,
                            "date_display": date_display,
                            "tasks": []
                        }

                    clean_text = r["task_text"] or ""
                    days_dict[l_date_str]["tasks"].append({
                        "id": r["id"],
                        "subject": r["subject"],
                        "task_text": clean_text,
                        "is_sent": bool(r["is_sent"])
                    })

                sorted_days = sorted(days_dict.values(), key=lambda x: x["date"], reverse=True)
                return web.json_response(sorted_days)
    except Exception:
        return web.json_response([])

async def api_get_lesson_dates(request):
    subj = request.query.get("subject", "Химия")
    dates = get_all_upcoming_lessons(subj)
    return web.json_response(dates)

async def api_save_homework(request):
    try:
        data = await request.json()
        hw_id = int(data.get("id"))
        new_text = str(data.get("text", "")).strip()
        new_subj = str(data.get("subject", "")).strip()
        custom_reminder = data.get("reminder_date")
        custom_lesson = data.get("lesson_date")

        if not new_text:
            return web.json_response({"ok": False, "error": "Empty text"})

        formatted = format_task_text(new_text, 1)

        if custom_reminder and custom_lesson:
            rem_d = custom_reminder
            les_d = custom_lesson
            matched_name = normalize_subject(new_subj).capitalize()
        else:
            lesson_date, matched_name = find_next_lesson_date(new_subj)
            if lesson_date:
                rem_d = calculate_reminder_date(lesson_date).strftime("%Y-%m-%d")
                les_d = lesson_date.strftime("%Y-%m-%d")
            else:
                rem_d = datetime.now(TZ).strftime("%Y-%m-%d")
                les_d = rem_d

        async with aiosqlite.connect("school.db") as db:
            await db.execute("""
                UPDATE homework 
                SET task_text = ?, subject = ?, send_date = ?, lesson_date = ? 
                WHERE id = ?
            """, (formatted, matched_name, rem_d, les_d, hw_id))
            await db.commit()

        await sync_backup()
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_add_homework(request):
    try:
        data = await request.json()
        subj = str(data.get("subject", "Химия")).strip()
        text = str(data.get("text", "")).strip()
        photos_b64 = data.get("photos", [])
        custom_reminder = data.get("reminder_date")
        custom_lesson = data.get("lesson_date")

        photo_ids = []
        for p_str in photos_b64:
            try:
                if "," in p_str:
                    _, encoded = p_str.split(",", 1)
                else:
                    encoded = p_str
                img_bytes = base64.b64decode(encoded)
                buf = BufferedInputFile(img_bytes, filename="web_photo.jpg")
                tmp_msg = await bot.send_photo(ADMIN_CHAT_ID, photo=buf, disable_notification=True)
                photo_ids.append(tmp_msg.photo[-1].file_id)
                await bot.delete_message(ADMIN_CHAT_ID, tmp_msg.message_id)
            except Exception as e:
                logging.error(f"Ошибка загрузки фото: {e}")

        if custom_reminder and custom_lesson:
            rem_d = custom_reminder
            les_d = custom_lesson
            matched_name = normalize_subject(subj).capitalize()
        else:
            lesson_date, matched_name = find_next_lesson_date(subj)
            if lesson_date:
                rem_d = calculate_reminder_date(lesson_date).strftime("%Y-%m-%d")
                les_d = lesson_date.strftime("%Y-%m-%d")
            else:
                les_d = (datetime.now(TZ).date() + timedelta(days=1)).strftime("%Y-%m-%d")
                rem_d = datetime.now(TZ).strftime("%Y-%m-%d")
                matched_name = subj.capitalize()

        final_text = format_task_text(text, len(photo_ids))
        photo_db_val = json.dumps(photo_ids) if photo_ids else None

        async with aiosqlite.connect("school.db") as db:
            cursor = await db.execute("""
                INSERT INTO homework (subject, task_text, photo_id, send_date, lesson_date)
                VALUES (?, ?, ?, ?, ?)
            """, (matched_name, final_text, photo_db_val, rem_d, les_d))
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
            async with db.execute("SELECT subject, task_text, photo_id, lesson_date FROM homework WHERE id = ?", (hw_id,)) as cursor:
                row = await cursor.fetchone()

        if row:
            await send_hw_item(row["subject"], row["task_text"], row["photo_id"], row["lesson_date"])
            return web.json_response({"ok": True})
        return web.json_response({"ok": False, "error": "Not found"})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})

async def api_get_schedule(request):
    return web.json_response(SCHEDULE)

async def start_web_server():
    app = web.Application(client_max_size=30 * 1024 * 1024)
    app.router.add_get("/", web_handle)
    app.router.add_get("/webapp", web_handle)
    app.router.add_get("/api/chats", api_get_chats)
    app.router.add_post("/api/toggle_ban", api_toggle_ban)
    app.router.add_get("/api/messages", api_get_messages)
    app.router.add_post("/api/send_message", api_send_message)
    app.router.add_get("/api/homework", api_get_homework)
    app.router.add_get("/api/diary", api_get_diary)
    app.router.add_get("/api/lesson_dates", api_get_lesson_dates)
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
                lesson_date TEXT,
                is_sent INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                full_name TEXT,
                last_active TEXT,
                is_banned INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                sender TEXT,
                text TEXT,
                timestamp TEXT
            )
        """)
        for col in ["ALTER TABLE homework ADD COLUMN lesson_date TEXT", "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0"]:
            try:
                await db.execute(col)
            except Exception:
                pass
        
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 1") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                banned_users_cache.add(r[0])

        await db.commit()

async def sync_backup():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text, photo_id, send_date, lesson_date, is_sent FROM homework") as cursor:
            hw_rows = [dict(r) for r in await cursor.fetchall()]

    backup_payload = {
        "homework": hw_rows,
        "banned": list(banned_users_cache)
    }
    backup_text = f"📦 BACKUP_DATA:\n{json.dumps(backup_payload, ensure_ascii=False)}"
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
            loaded = json.loads(json_str)
            if isinstance(loaded, list):
                hw_items = loaded
                banned_list = []
            else:
                hw_items = loaded.get("homework", [])
                banned_list = loaded.get("banned", [])

            for b_id in banned_list:
                banned_users_cache.add(b_id)

            async with aiosqlite.connect("school.db") as db:
                for item in hw_items:
                    clean_text = item["task_text"].replace("📸 ", "").replace(" 👆", "")
                    les_d = item.get("lesson_date")
                    is_s = item.get("is_sent", 0)
                    async with db.execute("SELECT id FROM homework WHERE id = ?", (item.get("id"),)) as cursor:
                        if not await cursor.fetchone():
                            await db.execute(
                                "INSERT INTO homework (id, subject, task_text, photo_id, send_date, lesson_date, is_sent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (item.get("id"), item["subject"].capitalize(), clean_text, item["photo_id"], item["send_date"], les_d, is_s)
                            )
                await db.commit()
    except Exception as e:
        logging.error(f"Ошибка восстановления: {e}")

async def fix_database_quotes_and_dates():
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, subject, task_text, lesson_date FROM homework WHERE is_sent = 0") as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                t = r["task_text"]
                if "<blockquote>" not in t:
                    clean = t.replace("📸 ", "").replace(" 👆", "").strip()
                    t = f"<blockquote>{clean}</blockquote>"
                
                if r["lesson_date"]:
                    les_d = datetime.strptime(r["lesson_date"], "%Y-%m-%d").date()
                else:
                    les_d, _ = find_next_lesson_date(r["subject"])

                if les_d:
                    reminder_d = calculate_reminder_date(les_d)
                    await db.execute(
                        "UPDATE homework SET task_text = ?, send_date = ?, lesson_date = ? WHERE id = ?",
                        (t, reminder_d.strftime("%Y-%m-%d"), les_d.strftime("%Y-%m-%d"), r["id"])
                    )
        await db.commit()
    await sync_backup()

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
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.error(f"Ошибка ИИ: {e}")

    return clean_task

# Компактные инлайн-кнопки под карточкой ДЗ (все редактирование перенесено на сайт)
def make_hw_kb(hw_id: int) -> InlineKeyboardMarkup:
    has_web = bool(WEBAPP_URL and WEBAPP_URL.startswith("https://"))
    buttons = [
        [
            InlineKeyboardButton(text="📤 В чат", callback_data=f"send_one:{hw_id}"),
            InlineKeyboardButton(text="📅 Сменить день", callback_data=f"change_date_menu:{hw_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_hw:{hw_id}")
        ]
    ]
    if has_web:
        buttons.append([InlineKeyboardButton(text="🌐 Редактировать на сайте", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ================= ИНЛАЙН-МЕНЮ (ВМЕСТО ОГРОМНОЙ КЛАВИАТУРЫ) =================
@dp.message(CommandStart())
@dp.message(Command("menu"))
async def start_cmd(msg: Message):
    is_spam, warn = check_antispam(msg.from_user.id)
    if is_spam:
        if warn: await msg.answer(warn)
        return

    is_admin = (msg.from_user.id == ADMIN_CHAT_ID)
    await log_user_message(
        msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name or "Ученик",
        "/start", sender="admin" if is_admin else "student"
    )

    # Убираем старую громоздкую нижнюю клавиатуру
    rm_msg = await msg.answer("Загрузка меню...", reply_markup=ReplyKeyboardRemove())
    try:
        await rm_msg.delete()
    except Exception:
        pass

    if is_admin:
        await msg.answer(
            "⚙️ <b>Панель управления старосты 8-Б</b>\n\nВыбери действие (или открой Сайт кнопкой в левом нижнем углу):",
            reply_markup=get_admin_inline_menu(),
            parse_mode="HTML"
        )
    else:
        target_d, target_title = get_target_lesson_date()
        await msg.answer(
            f"👋 <b>Бот домашнего задания 8-Б класса</b>\n\nНажми на кнопку ниже:",
            reply_markup=get_student_inline_menu(),
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu_cb(call: CallbackQuery):
    is_spam, warn = check_antispam(call.from_user.id)
    if is_spam:
        await call.answer(warn or "Подождите...", show_alert=False)
        return

    is_admin = (call.from_user.id == ADMIN_CHAT_ID)
    if is_admin:
        await call.message.edit_text(
            "⚙️ <b>Панель управления старосты 8-Б</b>\n\nВыбери действие:",
            reply_markup=get_admin_inline_menu(),
            parse_mode="HTML"
        )
    else:
        await call.message.edit_text(
            "👋 <b>Бот домашнего задания 8-Б класса</b>\n\nВыбери нужный раздел:",
            reply_markup=get_student_inline_menu(),
            parse_mode="HTML"
        )
    await call.answer()

# Просмотр расписания через инлайн (плавно редактирует текущее сообщение)
@dp.callback_query(F.data.in_(["stud_sched", "adm_sched"]))
@dp.message(Command("schedule"))
async def view_schedule_handler(event: Message | CallbackQuery):
    user_id = event.from_user.id
    is_spam, warn = check_antispam(user_id)
    if is_spam:
        if isinstance(event, CallbackQuery):
            await event.answer(warn or "Подождите...")
        elif warn:
            await event.answer(warn)
        return

    await log_user_message(user_id, event.from_user.username or "", event.from_user.full_name or "Ученик", "Расписание", sender="admin" if user_id == ADMIN_CHAT_ID else "student")

    lines = ["📅 <b>Расписание 8-Б класса:</b>\n"]
    for day_idx in range(5):
        day_name = DAYS_MAP[day_idx].capitalize()
        lines.append(f"🔹 <b>{day_name}:</b>")
        for num, subj in enumerate(SCHEDULE[day_idx], start=1):
            lines.append(f"  {num}. {html.escape(subj.capitalize())}")
        lines.append("")
    text = "\n".join(lines)
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]])

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=back_kb, parse_mode="HTML")

# Просмотр ДЗ через инлайн или команду /dz
@dp.callback_query(F.data.in_(["stud_dz", "adm_dz"]))
@dp.message(Command("dz"))
async def view_homework_handler(event: Message | CallbackQuery):
    user_id = event.from_user.id
    is_spam, warn = check_antispam(user_id)
    if is_spam:
        if isinstance(event, CallbackQuery):
            await event.answer(warn or "Подождите...")
        elif warn:
            await event.answer(warn)
        return

    is_admin = (user_id == ADMIN_CHAT_ID)
    await log_user_message(user_id, event.from_user.username or "", event.from_user.full_name or "Ученик", "Список ДЗ", sender="admin" if is_admin else "student")

    target_d, target_title = get_target_lesson_date()
    target_d_str = target_d.strftime("%Y-%m-%d")

    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        if not is_admin:
            query = "SELECT id, subject, task_text, photo_id, send_date, lesson_date FROM homework WHERE lesson_date = ? OR (lesson_date IS NULL AND send_date = ?) ORDER BY id ASC"
            params = (target_d_str, calculate_reminder_date(target_d).strftime("%Y-%m-%d"))
        else:
            query = "SELECT id, subject, task_text, photo_id, send_date, lesson_date FROM homework WHERE is_sent = 0 ORDER BY send_date ASC"
            params = ()

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()

    msg_target = event.message if isinstance(event, CallbackQuery) else event
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="back_to_menu")]])

    if not rows:
        empty_text = f"📭 Домашних заданий {target_title} ({target_d.strftime('%d.%m')}) пока нет." if not is_admin else "📭 В очереди нет активных заданий."
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(empty_text, reply_markup=back_kb)
            await event.answer()
        else:
            await event.answer(empty_text, reply_markup=back_kb)
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    total = len(rows)
    header_msg = f"📋 <b>Задания {target_title} ({target_d.strftime('%d.%m')}):</b>" if not is_admin else f"📋 <b>В очереди заданий: {total}</b>"
    await bot.send_message(user_id, header_msg, parse_mode="HTML")

    for idx, row in enumerate(rows, start=1):
        send_date_obj = datetime.strptime(row["send_date"], "%Y-%m-%d").date()
        lesson_date_obj = datetime.strptime(row["lesson_date"], "%Y-%m-%d").date() if row["lesson_date"] else (send_date_obj + timedelta(days=1))
        send_weekday = DAYS_MAP[send_date_obj.weekday()].capitalize()
        lesson_weekday = DAYS_MAP[lesson_date_obj.weekday()].capitalize()

        photos = get_photo_list(row["photo_id"])
        task_display = row["task_text"] if "<blockquote>" in row["task_text"] else f"<blockquote>{html.escape(row['task_text'])}</blockquote>"

        card_text = (
            f"📌 <b>Задание {idx} из {total}</b>\n"
            f"📚 <b>Предмет:</b> {html.escape(row['subject'].capitalize())}\n"
            f"📅 <b>Урок:</b> {lesson_weekday}, {lesson_date_obj.strftime('%d.%m')}\n"
            f"⏰ <b>Отправка:</b> {send_weekday}, {send_date_obj.strftime('%d.%m')} в 15:10\n\n"
            f"📝 <b>Задание:</b>\n{task_display}"
        )

        card_kb = make_hw_kb(row["id"]) if is_admin else (back_kb if idx == total else None)
        if len(photos) > 1:
            note = f"\n\n<i>(Прикреплено фото: {len(photos)} шт.)</i>"
            await bot.send_photo(user_id, photo=photos[0], caption=card_text + note, reply_markup=card_kb, parse_mode="HTML")
        elif len(photos) == 1:
            await bot.send_photo(user_id, photo=photos[0], caption=card_text, reply_markup=card_kb, parse_mode="HTML")
        else:
            await bot.send_message(user_id, text=card_text, reply_markup=card_kb, parse_mode="HTML")

@dp.callback_query(F.data == "adm_send")
async def adm_send_cb(call: CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return
    count = await send_due_homework(force_resend=False)
    if count == 0:
        count = await send_due_homework(force_resend=True)
    await call.answer(f"Отправлено заданий: {count}" if count > 0 else "Нет заданий на сегодня.", show_alert=True)

@dp.callback_query(F.data.startswith("change_date_menu:"))
async def change_date_menu_callback(call: CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT subject FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        await call.answer("Не найдено")
        return

    upcoming = get_all_upcoming_lessons(row["subject"], limit=4)
    buttons = [[InlineKeyboardButton(text=f"{u['lesson_display']}", callback_data=f"set_ld:{hw_id}:{u['lesson_date']}:{u['reminder_date']}")] for u in upcoming]
    buttons.append([InlineKeyboardButton(text="❌ Назад", callback_data="cancel_date_change")])
    await call.message.reply(f"📅 <b>Выбери новый день для {html.escape(row['subject'].capitalize())}:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await call.answer()

@dp.callback_query(F.data.startswith("set_ld:"))
async def set_lesson_date_callback(call: CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return
    _, hw_id_str, les_d_str, rem_d_str = call.data.split(":")
    async with aiosqlite.connect("school.db") as db:
        await db.execute("UPDATE homework SET lesson_date = ?, send_date = ? WHERE id = ?", (les_d_str, rem_d_str, int(hw_id_str)))
        await db.commit()
    await sync_backup()
    await call.message.delete()
    await call.answer("День урока изменен!")

@dp.callback_query(F.data == "cancel_date_change")
async def cancel_date_change_callback(call: CallbackQuery):
    await call.message.delete()
    await call.answer()

async def send_hw_item(subject: str, task_text: str, photo_field: str | None, lesson_date_str: str | None = None):
    photos = get_photo_list(photo_field)
    task_display = task_text if "<blockquote>" in task_text else f"<blockquote>{html.escape(task_text)}</blockquote>"

    if lesson_date_str:
        les_d = datetime.strptime(lesson_date_str, "%Y-%m-%d").date()
        target_day_name = ACCUSATIVE_DAYS.get(les_d.weekday(), "следующий урок")
    else:
        lesson_d, _ = find_next_lesson_date(subject)
        target_day_name = ACCUSATIVE_DAYS.get(lesson_d.weekday(), "следующий урок") if lesson_d else "следующий урок"

    header = f"<b>Домашнее задание на {target_day_name}:</b>\n<b>Предмет:</b> {html.escape(subject.capitalize())}\n\n{task_display}"

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
    if call.from_user.id != ADMIN_CHAT_ID:
        return
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT subject, task_text, photo_id, lesson_date FROM homework WHERE id = ?", (hw_id,)) as cursor:
            row = await cursor.fetchone()
    if row:
        await send_hw_item(row["subject"], row["task_text"], row["photo_id"], row["lesson_date"])
        await call.answer("Отправлено в чат!")

@dp.callback_query(F.data.startswith("del_hw:"))
async def delete_hw_callback(call: CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return
    hw_id = int(call.data.split(":")[1])
    async with aiosqlite.connect("school.db") as db:
        await db.execute("DELETE FROM homework WHERE id = ?", (hw_id,))
        await db.commit()
    await sync_backup()
    await call.answer("🗑 Удалено.")
    if call.message.photo:
        await call.message.edit_caption(caption="🗑 <b>Задание удалено.</b>", parse_mode="HTML")
    else:
        await call.message.edit_text(text="🗑 <b>Задание удалено.</b>", parse_mode="HTML")

# Прием любых текстовых сообщений и фото (с жестким анти-спамом для учеников)
@dp.message(StateFilter(None))
async def handle_incoming(msg: Message):
    is_admin = (msg.from_user.id == ADMIN_CHAT_ID)
    
    # Проверка на спам и бан для одноклассников
    is_spam, warn = check_antispam(msg.from_user.id, is_text_msg=True)
    if is_spam:
        if warn:
            await msg.answer(warn)
        return

    user_text = msg.text or (msg.caption if msg.photo else "Отправил вложение")
    await log_user_message(
        msg.from_user.id, msg.from_user.username or "", msg.from_user.full_name or "Ученик",
        user_text, sender="admin" if is_admin else "student"
    )

    if not is_admin:
        await msg.answer(
            "✉️ <b>Сообщение передано старосте!</b>\nЧтобы посмотреть ДЗ или расписание, используй меню:",
            reply_markup=get_student_inline_menu(),
            parse_mode="HTML"
        )
        return

    # Если пишет или шлет фото админ
    if msg.media_group_id:
        mg_id = msg.media_group_id
        if mg_id not in media_group_cache:
            media_group_cache[mg_id] = {
                "photos": [msg.photo[-1].file_id] if msg.photo else [],
                "caption": msg.caption or "",
                "msg": msg
            }
        else:
            if msg.photo: media_group_cache[mg_id]["photos"].append(msg.photo[-1].file_id)
            if msg.caption: media_group_cache[mg_id]["caption"] = msg.caption

        await asyncio.sleep(1.0)
        if mg_id in media_group_cache:
            data = media_group_cache.pop(mg_id)
            photos, raw_text, msg = data["photos"], (data["caption"] or "").strip(), data["msg"]
        else:
            return
    else:
        photos = [msg.photo[-1].file_id] if msg.photo else []
        raw_text = (msg.caption if msg.photo else (msg.text or "")).strip()

    if photos and not raw_text:
        await msg.answer("⚠️ Ты отправил фото без названия предмета. Напиши предмет в подписи.")
        return

    if ":" in raw_text:
        subject, task = raw_text.split(":", 1)
        subject, task = subject.strip(), task.strip()
    else:
        subject, task = raw_text.strip(), ""

    lesson_date, matched_name = find_next_lesson_date(subject)
    if not lesson_date:
        await msg.answer(f"❌ Предмет «{html.escape(subject)}» не найден в расписании 8-Б класса.")
        return

    final_text = format_task_text(task, len(photos))
    reminder_date = calculate_reminder_date(lesson_date)
    send_date_str = reminder_date.strftime("%Y-%m-%d")
    les_date_str = lesson_date.strftime("%Y-%m-%d")
    photo_db_val = json.dumps(photos) if photos else None

    async with aiosqlite.connect("school.db") as db:
        cursor = await db.execute("""
            INSERT INTO homework (subject, task_text, photo_id, send_date, lesson_date)
            VALUES (?, ?, ?, ?, ?)
        """, (matched_name, final_text, photo_db_val, send_date_str, les_date_str))
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
        f"📝 <b>Задание:</b>\n{final_text}"
    )

    kb = make_hw_kb(hw_id)
    if len(photos) > 1:
        await msg.answer_media_group(media=[InputMediaPhoto(media=p) for p in photos])
        await msg.answer(confirm, reply_markup=kb, parse_mode="HTML")
    elif len(photos) == 1:
        await msg.answer_photo(photo=photos[0], caption=confirm, reply_markup=kb, parse_mode="HTML")
    else:
        await msg.answer(confirm, reply_markup=kb, parse_mode="HTML")

async def send_due_homework(force_resend: bool = False) -> int:
    now_md = datetime.now(TZ)
    today_str = now_md.strftime("%Y-%m-%d")
    sent_count = 0

    query = "SELECT id, subject, task_text, photo_id, lesson_date FROM homework WHERE send_date <= ?"
    if not force_resend:
        query += " AND is_sent = 0"
    query += " ORDER BY id DESC"

    async with aiosqlite.connect("school.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, (today_str,)) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                try:
                    await send_hw_item(row["subject"], row["task_text"], row["photo_id"], row["lesson_date"])
                    await db.execute("UPDATE homework SET is_sent = 1 WHERE id = ?", (row["id"],))
                    sent_count += 1
                except Exception as e:
                    logging.error(f"Ошибка отправки ДЗ #{row['id']}: {e}")
            await db.commit()

    if sent_count > 0:
        await sync_backup()
    return sent_count

@dp.message(Command("send"))
async def manual_send_cmd(msg: Message):
    if msg.from_user.id != ADMIN_CHAT_ID:
        return
    count = await send_due_homework(force_resend=False)
    if count == 0:
        count = await send_due_homework(force_resend=True)
    await msg.answer(f"✅ Отправлено заданий: {count}" if count > 0 else "📭 Заданий нет.")

async def main():
    await init_db()
    await start_web_server()
    await restore_from_backup()
    await fix_database_quotes_and_dates()

    # Устанавливаем нативные команды меню слева от строки ввода
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="dz", description="Домашнее задание"),
        BotCommand(command="schedule", description="Расписание уроков"),
    ])

    # Для старосты ставим синюю кнопку "Сайт" в левый нижний угол Telegram!
    if WEBAPP_URL and WEBAPP_URL.startswith("https://"):
        try:
            await bot.set_chat_menu_button(
                chat_id=ADMIN_CHAT_ID,
                menu_button=MenuButtonWebApp(text="Сайт", web_app=WebAppInfo(url=f"{WEBAPP_URL}/webapp"))
            )
        except Exception as e:
            logging.error(f"Ошибка установки MenuButton: {e}")

    scheduler.add_job(send_due_homework, "cron", hour=SEND_HOUR, minute=SEND_MINUTE)
    scheduler.start()
    
    now = datetime.now(TZ)
    if now.hour > SEND_HOUR or (now.hour == SEND_HOUR and now.minute >= SEND_MINUTE):
        await send_due_homework(force_resend=False)

    print("Бот 8-Б класса запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
