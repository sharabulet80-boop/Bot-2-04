import asyncio
import logging
import os
import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

USERS_FILE = "users.json"
AUTO_CONFIG_FILE = "auto_config.json"
DB_FILE = "responses.db"
scheduler = AsyncIOScheduler()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            created_at TEXT,
            q1 TEXT,
            q2 TEXT,
            q2_custom TEXT,
            q3 TEXT,
            q4 TEXT,
            q5 TEXT,
            q6 TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            joined_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS click_stats (
            lesson_type TEXT PRIMARY KEY,
            click_count INTEGER DEFAULT 0
        )
    """)
    # Инициализируем клики нулями, если их еще нет
    for i in [1, 2, 3]:
        c.execute("INSERT OR IGNORE INTO click_stats (lesson_type, click_count) VALUES (?, 0)", (f"lesson_{i}",))
    
    conn.commit()
    conn.close()


init_db()


def save_response(user_id: int, username: str, answers: dict):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO responses (user_id, username, created_at, q1, q2, q2_custom, q3, q4, q5, q6)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            user_id,
            username,
            datetime.now().isoformat(),
            answers.get("q1"),
            answers.get("q2"),
            answers.get("q2_custom"),
            answers.get("q3"),
            answers.get("q4"),
            answers.get("q5"),
            answers.get("q6"),
        ),
    )
    conn.commit()
    conn.close()


def get_all_responses():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM responses ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows


def get_responses_count():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM responses")
    count = c.fetchone()[0]
    conn.close()
    return count


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)


users = load_users()


def add_user(user_id, username=None, full_name=None):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at) VALUES (?, ?, ?, ?)",
              (user_id, username, full_name, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Всего пользователей
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    
    # Новые за 30 дней
    month_ago = (datetime.now() - timedelta(days=30)).isoformat()
    c.execute("SELECT COUNT(*) FROM users WHERE joined_at > ?", (month_ago,))
    new_users_month = c.fetchone()[0]
    
    # Клики
    c.execute("SELECT lesson_type, click_count FROM click_stats")
    clicks = dict(c.fetchall())
    
    conn.close()
    return {
        "total_users": total_users,
        "new_users_month": new_users_month,
        "clicks": clicks
    }

def record_click(lesson_type):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE click_stats SET click_count = click_count + 1 WHERE lesson_type = ?", (lesson_type,))
    conn.commit()
    conn.close()


def load_auto_config():
    if os.path.exists(AUTO_CONFIG_FILE):
        with open(AUTO_CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"enabled": False, "time": "10:00", "content": {}, "button": None}


def save_auto_config(config):
    with open(AUTO_CONFIG_FILE, "w") as f:
        json.dump(config, f)


auto_config = load_auto_config()

QUESTIONS = [
    {"id": "q1", "text": "1. Пештар курс харидӣ?", "options": {"a": "Бале", "b": "Не"}},
    {
        "id": "q2",
        "text": "2. Агар Бале, барои чӣ харидӣ?",
        "options": {
            "a": "дард доштам",
            "b": "натиҷа мехостам",
            "c": "муаллим писанд омад",
            "d": "нарх хуб буд",
        },
    },
    {
        "id": "q3",
        "text": "3. Дар он курс чӣ намерасид?",
        "options": {
            "a": "фаҳмондан",
            "b": "амал",
            "c": "мисол",
            "d": "дастгирӣ",
            "e": "натиҷа",
        },
    },
    {
        "id": "q4",
        "text": "4. Ҳоло бештар кадом мушкил туро азоб медиҳад?",
        "options": {
            "a": "вобастагӣ",
            "b": "хиёнат",
            "c": "беқадрӣ",
            "d": "оромӣ нест",
            "e": "фикри зиёд",
        },
    },
    {
        "id": "q5",
        "text": "5. Агар ин ҳал шавад, ту бештар чӣ мехоҳӣ?",
        "options": {"a": "оромӣ", "b": "қувват", "c": "муҳаббат", "d": "худбоварӣ"},
    },
    {
        "id": "q6",
        "text": "6. Барои ҳал шудани ин мушкил чӣ кадар пул дода метавонӣ?",
        "options": {"a": "20$", "b": "50$", "c": "100$", "d": "500$"},
    },
]

QUESTION_ORDER = ["q1", "q2", "q3", "q4", "q5", "q6"]


class SurveyStates(StatesGroup):
    waiting_q1 = State()
    waiting_q2 = State()
    waiting_q2_custom = State()
    waiting_q3 = State()
    waiting_q4 = State()
    waiting_q5 = State()
    waiting_q6 = State()


class MailingStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_button_choice = State()
    waiting_for_link = State()
    waiting_for_confirm = State()
    waiting_for_time = State()


def get_start_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Тест 📝",
                    callback_data="start_survey",
                )
            ]
        ]
    )


def get_options_kb(question_id: str):
    btns = []
    for letter, text in QUESTIONS[QUESTION_ORDER.index(question_id)]["options"].items():
        btns.append(
            [
                InlineKeyboardButton(
                    text=text, callback_data=f"ans_{question_id}_{letter}"
                )
            ]
        )
    btns.append([InlineKeyboardButton(text="❌ Бекор", callback_data="cancel_survey")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


def get_retry_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Ворид шудан ба канал 📺",
                    url="https://t.me/jannat_abdullaeva_kanal",
                )
            ],
        ]
    )


def is_admin(user_id):
    return user_id in ADMIN_IDS


admin_main_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📤 Экспорт в Excel", callback_data="admin_export")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")],
    ]
)

lesson_choice_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Тамошои Дарси 1", callback_data="lesson_1")],
        [InlineKeyboardButton(text="Тамошои Дарси 2", callback_data="lesson_2")],
        [InlineKeyboardButton(text="Тамошои Дарси 3", callback_data="lesson_3")],
        [InlineKeyboardButton(text="❌ Без кнопки", callback_data="lesson_none")],
    ]
)

mailing_confirm_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Отправить сейчас", callback_data="send_now")],
        [InlineKeyboardButton(text="⏰ Через 1 час", callback_data="send_1h")],
        [InlineKeyboardButton(text="📅 Своё время", callback_data="send_custom")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")],
    ]
)

time_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Сейчас", callback_data="time_now"),
            InlineKeyboardButton(text="⏰ Через 1 час", callback_data="time_1h"),
        ],
        [
            InlineKeyboardButton(text="⏰ Через 3 часа", callback_data="time_3h"),
            InlineKeyboardButton(text="⌨️ Свой вариант", callback_data="time_custom"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")],
    ]
)

confirm_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send")],
        [
            InlineKeyboardButton(text="✏️ Текст", callback_data="confirm_edit_text"),
            InlineKeyboardButton(text="🖼️ Фото", callback_data="confirm_edit_photo"),
        ],
        [
            InlineKeyboardButton(text="🎥 Видео", callback_data="confirm_edit_video"),
            InlineKeyboardButton(text="🔘 Кнопку", callback_data="confirm_edit_button"),
        ],
        [
            InlineKeyboardButton(text="⏰ Время", callback_data="confirm_edit_time"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel"),
        ],
    ]
)

cancel_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel")]
    ]
)

auto_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Включить", callback_data="auto_enable"),
            InlineKeyboardButton(text="❌ Выключить", callback_data="auto_disable"),
        ],
        [
            InlineKeyboardButton(text="⏰ Время", callback_data="auto_set_time"),
            InlineKeyboardButton(text="📝 Контент", callback_data="auto_set_content"),
        ],
        [
            InlineKeyboardButton(text="🔘 Кнопка", callback_data="auto_set_button"),
            InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"),
        ],
    ]
)


async def send_content(chat_id, content, reply_markup=None):
    t = content["type"]
    cap = content.get("caption", "")
    if t == "text":
        await bot.send_message(chat_id, content["text"], reply_markup=reply_markup)
    elif t == "photo":
        await bot.send_photo(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "video":
        await bot.send_video(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "audio":
        await bot.send_audio(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "voice":
        await bot.send_voice(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "document":
        await bot.send_document(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "animation":
        await bot.send_animation(
            chat_id, content["file_id"], caption=cap, reply_markup=reply_markup
        )
    elif t == "video_note":
        await bot.send_video_note(
            chat_id, content["file_id"], reply_markup=reply_markup
        )


def build_reply_markup(button_data):
    if not button_data:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button_data["text"], url=button_data["url"])]
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    add_user(
        message.from_user.id, 
        message.from_user.username, 
        message.from_user.full_name
    )
    username = message.from_user.first_name or "друг"
    await message.answer(
        f"👋 Салом, {username}! 🌸\n\n"
        "Барои гирифтани дарси 3-рӯзаи бепул, лутфан суолномаро пур кунед.\n\n"
        "📝 Интихобҳоро бо тугмаҳо зер кунед.",
        reply_markup=get_start_kb(),
    )


@dp.callback_query(lambda c: c.data == "start_survey")
async def start_survey(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SurveyStates.waiting_q1)
    q = QUESTIONS[0]
    await callback.message.edit_text(
        f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q1"),
    )


@dp.callback_query(lambda c: c.data == "cancel_survey")
async def cancel_survey(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "❌ Бекор карда шуд. Барои боз оғоз кардан /start -ро пахш кунед."
    )


@dp.callback_query(lambda c: c.data == "retry_survey")
async def retry_survey(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await start_survey(callback, state)


@dp.callback_query(lambda c: c.data.startswith("ans_q1_"))
async def answer_q1(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q1=answer)

    if answer == "a":
        await state.set_state(SurveyStates.waiting_q2)
        q = QUESTIONS[1]
        await callback.message.edit_text(
            f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
            reply_markup=get_options_kb("q2"),
        )
    else:
        await state.update_data(q2=None, q2_custom=None)
        await state.set_state(SurveyStates.waiting_q3)
        q = QUESTIONS[2]
        await callback.message.edit_text(
            f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
            reply_markup=get_options_kb("q3"),
        )


@dp.callback_query(lambda c: c.data.startswith("ans_q2_"))
async def answer_q2(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q2=answer)
    await state.set_state(SurveyStates.waiting_q3)
    q = QUESTIONS[2]
    await callback.message.edit_text(
        f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q3"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q3_"))
async def answer_q3(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q3=answer)
    await state.set_state(SurveyStates.waiting_q4)
    q = QUESTIONS[3]
    await callback.message.edit_text(
        f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q4"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q4_"))
async def answer_q4(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q4=answer)
    await state.set_state(SurveyStates.waiting_q5)
    q = QUESTIONS[4]
    await callback.message.edit_text(
        f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q5"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q5_"))
async def answer_q5(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q5=answer)
    await state.set_state(SurveyStates.waiting_q6)
    q = QUESTIONS[5]
    await callback.message.edit_text(
        f"📋 Барои гирифтани дарси 3-рӯзаи бепул, ба инҳо ҷавоб деҳ:\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q6"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q6_"))
async def answer_q6(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    data = await state.get_data()
    data["q6"] = answer

    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.full_name

    save_response(user_id, username, data)
    await state.clear()

    result_text = (
        "✅ Ташаккур! Ҷавобҳои шумо сабт шуд.\n\n"
        "📋 Натиҷа:\n"
        f"1️⃣ Курс харидӣ: {QUESTIONS[0]['options'].get(data.get('q1', ''), '-')}\n"
        f"2️⃣ Сабаб: {QUESTIONS[1]['options'].get(data.get('q2', ''), '-') or '-'}\n"
        f"3️⃣ Намерасид: {QUESTIONS[2]['options'].get(data.get('q3', ''), '-')}\n"
        f"4️⃣ Мушкил: {QUESTIONS[3]['options'].get(data.get('q4', ''), '-')}\n"
        f"5️⃣ Мехоҳӣ: {QUESTIONS[4]['options'].get(data.get('q5', ''), '-')}\n"
        f"6️⃣ Бюджет: {QUESTIONS[5]['options'].get(data.get('q6', ''), '-')}\n\n"
        "Дар ин 3 рӯз ту мефаҳмӣ:\n"
        "1️⃣ Чаро ту дар муносибат ин қадар вобаста мешавӣ\n"
        "2️⃣ Чаро фикрҳоят ором намешаванд\n"
        "3️⃣ Қадами аввал, ки туро ба оромӣ бармегардонад\n"
        "Ин дарсҳо содаанд, кӯтоҳанд ва бевосита ба ҳолати ту равона шудаанд.\n\n"
        "⛔️ Ин барои ҳама нест.\n"
        "Фақат барои касоне, ки ҳақиқатан мехоҳанд аз ин ҳолат барорад\n\n"
        "Омода боши  ба Дарси ройгон ворид шав !"
    )
    await callback.message.edit_text(result_text, reply_markup=get_retry_kb())


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = get_responses_count()
    await message.answer(
        f"🔧 Админ\n\n📝 Жавобҳо: {count}",
        reply_markup=admin_main_kb,
    )


@dp.callback_query(lambda c: c.data == "admin_export")
async def admin_export(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return
    rows = get_all_responses()
    if not rows:
        await callback.message.edit_text("📋 Ҷавобҳо нестанд.", reply_markup=admin_main_kb)
        return

    q1_opts = QUESTIONS[0]["options"]
    q2_opts = QUESTIONS[1]["options"]
    q3_opts = QUESTIONS[2]["options"]
    q4_opts = QUESTIONS[3]["options"]
    q5_opts = QUESTIONS[4]["options"]
    q6_opts = QUESTIONS[5]["options"]

    csv = "ID;UserID;Username;Сана;Пештар курс харидӣ?;Агар Бале, барои чӣ харидӣ?;Дар он курс чӣ намерасид?;Ҳоло бештар кадом мушкил?;Агар ин ҳал шавад, ту бештар чӣ мехоҳӣ?;Бюджет\n"
    for row in rows:
        q1 = q1_opts.get(row[4], "-") if row[4] else "-"
        q2 = q2_opts.get(row[5], "-") if row[5] else "-"
        q3 = q3_opts.get(row[6], "-") if row[6] else "-"
        q4 = q4_opts.get(row[7], "-") if row[7] else "-"
        q5 = q5_opts.get(row[8], "-") if row[8] else "-"
        q6 = q6_opts.get(row[9], "-") if row[9] else "-"
        created = row[3][:19] if row[3] else "-"
        row_user = str(row[2]) if row[2] else str(row[1])
        csv += f"{row[0]};{row[1]};{row_user};{created};{q1};{q2};{q3};{q4};{q5};{q6}\n"

    with open("responses_export.csv", "w", encoding="utf-8") as f:
        f.write(csv)

    await callback.message.answer_document(FSInputFile("responses_export.csv"), caption="📤 Экспорт ҷавобҳо")


# --- СТАТИСТИКА ---

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id): return
    s = get_stats()
    text = (
        "📊 **Статистика Бот**\n\n"
        f"👥 Джамъи корбарон: {s['total_users']}\n"
        f"📅 Нав дар 30 рӯз (моҳ): {s['new_users_month']}\n\n"
        "📈 **Клики Тугмаҳо (Урокҳо):**\n"
        f"🔹 Дарси 1: {s['clicks'].get('lesson_1', 0)}\n"
        f"🔹 Дарси 2: {s['clicks'].get('lesson_2', 0)}\n"
        f"🔹 Дарси 3: {s['clicks'].get('lesson_3', 0)}\n"
    )
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=admin_main_kb)

# Обработка клика по уроку (трекинг)
@dp.callback_query(lambda c: c.data.startswith("cl_"))
async def track_lesson_click(callback: types.CallbackQuery):
    # Формат: cl_{num}_{url}
    parts = callback.data.split("_")
    lesson_num = parts[1]
    url = "_".join(parts[2:]) # На случай если в URL есть подчеркивания
    
    # Записываем клик в БД
    record_click(f"lesson_{lesson_num}")
    
    # Отправляем ссылку
    btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Гузаштан ба дарс", url=url)]])
    await callback.message.answer(f"🚀 Шумо Дарси {lesson_num}-ро интихоб кардед. Барои тамошо тугмаи зерро пахш кунед:", reply_markup=btn)
    await callback.answer()


# --- РАССЫЛКА (Mailing) ---

@dp.callback_query(lambda c: c.data == "admin_mailing")
async def start_mailing(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await state.set_state(MailingStates.waiting_for_text)
    await callback.message.edit_text("📝 Введите ТЕКСТ для рассылки:", reply_markup=cancel_kb)

@dp.message(MailingStates.waiting_for_text)
async def mailing_text_received(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(MailingStates.waiting_for_photo)
    await message.answer("🖼 Отправьте ФОТО для рассылки или нажмите /skip , если фото не нужно:", reply_markup=cancel_kb)

@dp.message(MailingStates.waiting_for_photo)
async def mailing_photo_received(message: types.Message, state: FSMContext):
    if message.text == "/skip":
        await state.update_data(photo=None)
    elif message.photo:
        await state.update_data(photo=message.photo[-1].file_id)
    else:
        await message.answer("Пожалуйста, отправьте фото или /skip")
        return
        
    await state.set_state(MailingStates.waiting_for_button_choice)
    await message.answer("Выберите тип кнопки (Дарси 1/2/3):", reply_markup=lesson_choice_kb)

@dp.callback_query(lambda c: c.data.startswith("lesson_"))
async def mailing_button_choice(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.split("_")[1]
    await state.update_data(choice_num=choice)
    
    if choice == "none":
        await state.update_data(button_text=None)
        await show_mailing_preview(callback.message, state)
    else:
        text = f"Тамошои Дарси {choice}"
        await state.update_data(button_text=text)
        await state.set_state(MailingStates.waiting_for_link)
        await callback.message.edit_text(f"🔗 Отправьте ССЫЛКУ для кнопки '{text}':", reply_markup=cancel_kb)

@dp.message(MailingStates.waiting_for_link)
async def mailing_link_received(message: types.Message, state: FSMContext):
    if not (message.text.startswith("http") or message.text.startswith("t.me")):
        await message.answer("❌ Это не похоже на ссылку. Попробуйте еще раз:")
        return
    await state.update_data(link=message.text)
    await show_mailing_preview(message, state)

async def show_mailing_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text")
    photo = data.get("photo")
    btn_text = data.get("button_text")
    
    kb = None
    if btn_text:
        # Для превью показываем обычную кнопку, чтобы админ проверил ссылку
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, url=data.get("link", "#"))]])
        
    await message.answer("👀 ПРЕДПРОСМОТР СООБЩЕНИЯ:")
    
    if photo:
        await message.answer_photo(photo, caption=text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)
        
    await message.answer("Все верно? Выберите время отправки:", reply_markup=mailing_confirm_kb)
    await state.set_state(MailingStates.waiting_for_confirm)

@dp.callback_query(lambda c: c.data == "send_now")
async def mailing_send_now(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🚀 Рассылка запущена...")
    await run_broadcast(state)
    await state.clear()

@dp.callback_query(lambda c: c.data == "send_1h")
async def mailing_send_1h(callback: types.CallbackQuery, state: FSMContext):
    run_at = datetime.now() + timedelta(hours=1)
    data = await state.get_data()
    scheduler.add_job(run_broadcast, DateTrigger(run_at=run_at), args=[data])
    await callback.message.edit_text(f"⏰ Рассылка запланирована на {run_at.strftime('%H:%M')}")
    await state.clear()

@dp.callback_query(lambda c: c.data == "send_custom")
async def mailing_send_custom(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(MailingStates.waiting_for_time)
    await callback.message.edit_text("📅 Введите время в формате ГГГГ-ММ-ДД ЧЧ:ММ (например: 2024-05-01 15:00):", reply_markup=cancel_kb)

@dp.message(MailingStates.waiting_for_time)
async def mailing_time_custom(message: types.Message, state: FSMContext):
    try:
        run_at = datetime.strptime(message.text, "%Y-%m-%d %H:%M")
        if run_at < datetime.now():
            await message.answer("❌ Время не может быть в прошлом!")
            return
        data = await state.get_data()
        scheduler.add_job(run_broadcast, DateTrigger(run_at=run_at), args=[data])
        await message.answer(f"📅 Рассылка запланирована на {run_at}")
        await state.clear()
    except ValueError:
        await message.answer("❌ Неверный формат. Нужно: ГГГГ-ММ-ДД ЧЧ:ММ")

async def run_broadcast(state_or_data):
    if isinstance(state_or_data, FSMContext):
        data = await state_or_data.get_data()
    else:
        data = state_or_data
        
    text = data.get("text")
    photo = data.get("photo")
    btn_text = data.get("button_text")
    choice_num = data.get("choice_num")
    link = data.get("link")
    
    kb = None
    if btn_text and link:
        # Для трекинга меняем URL-кнопку на CALLBACK с данными для редиректа
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=btn_text, callback_data=f"cl_{choice_num}_{link}")]])
    
    # Загружаем всех пользователей из БД вместо json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users_list = [r[0] for r in c.fetchall()]
    conn.close()
    
    count = 0
    for user_id in users_list:
        try:
            if photo:
                await bot.send_photo(user_id, photo, caption=text, reply_markup=kb)
            else:
                await bot.send_message(user_id, text, reply_markup=kb)
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Failed to send to {user_id}: {e}")
            
    for admin_id in ADMIN_IDS:
        await bot.send_message(admin_id, f"✅ Рассылка завершена!\n📩 Видели (отправлено): {count}\n👤 Всего в базе: {len(users_list)}")

@dp.callback_query(lambda c: c.data == "admin_cancel")
async def admin_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await admin_panel(callback.message)

@dp.callback_query(lambda c: c.data == "admin_close")
async def admin_close(callback: types.CallbackQuery):
    await callback.message.delete()


async def main():
    scheduler.start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
