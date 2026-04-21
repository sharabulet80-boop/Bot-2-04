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


def add_user(user_id):
    if user_id not in users:
        users.add(user_id)
        save_users(users)


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
    {"id": "q1", "text": "1. Пештар курс харидӣ?", "options": {"a": "Ҳа", "b": "Не"}},
    {
        "id": "q2",
        "text": "2. Агар ҳа, барои чӣ харидӣ?",
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
        "text": "6. Барои ҳал шудани ин мушкил чанд пул дода метавонӣ?",
        "options": {"a": "30$", "b": "50$", "c": "100$", "d": "500$"},
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


class BroadcastStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_video_link = State()
    waiting_for_button = State()
    waiting_for_time = State()
    confirming = State()


class VideoNoteStates(StatesGroup):
    waiting_for_video_note = State()
    confirming_video_note = State()


class AutoConfigStates(StatesGroup):
    waiting_for_time = State()
    waiting_for_content = State()
    waiting_for_button = State()


def get_start_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Барои гирифтани дарси 3-рӯзаи бепул 📝",
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
            [InlineKeyboardButton(text="🔄 Аз нав", callback_data="retry_survey")],
            [
                InlineKeyboardButton(
                    text="📝 Суолномаи нав", callback_data="start_survey"
                )
            ],
        ]
    )


def is_admin(user_id):
    return user_id in ADMIN_IDS


admin_main_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📢 Рассылка", callback_data="admin_new_broadcast"
            ),
            InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        ],
        [
            InlineKeyboardButton(text="📋 Жавобҳо", callback_data="admin_responses"),
            InlineKeyboardButton(text="📤 Экспорт", callback_data="admin_export"),
        ],
        [
            InlineKeyboardButton(
                text="🎥 Круглое видео", callback_data="admin_video_note"
            ),
            InlineKeyboardButton(text="📋 Список", callback_data="admin_users_list"),
        ],
        [
            InlineKeyboardButton(text="🔔 Автоответчик", callback_data="admin_auto"),
            InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close"),
        ],
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


EXAMPLE_TEXT = """📝 МИСОЛ:

1. Пештар курс харидӣ?
➜ Ҳа

2. Агар ҳа, барои чӣ харидӣ?
➜ дард доштам

3. Дар он курс чӣ намерасид?
➜ натиҷа

4. Ҳоло бештар кадом мушкил туро азоб медиҳад?
➜ вобастагӣ

5. Агар ин ҳал шавад, ту бештар чӣ мехоҳӣ?
➜ оромӣ

6. Барои ҳал шудани ин мушкил чанд пул дода метавонӣ?
➜ 50$

---
Шумо танһо ҷавобҳоро интихоб кунед! ✅"""


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    add_user(message.from_user.id)
    await message.answer(
        "👋 Салом! Барои гирифтани дарси 3-рӯзаи бепул, лутфан суолномаро пур кунед.\n\n"
        "📝 Интихобҳоро бо тугмаҳо зер кунед.\n\n"
        "ℹ️ МИСОЛ:\n" + EXAMPLE_TEXT,
        reply_markup=get_start_kb(),
    )


@dp.callback_query(lambda c: c.data == "start_survey")
async def start_survey(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SurveyStates.waiting_q1)
    q = QUESTIONS[0]
    await callback.message.edit_text(
        f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
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
            f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
            reply_markup=get_options_kb("q2"),
        )
    else:
        await state.update_data(q2=None, q2_custom=None)
        await state.set_state(SurveyStates.waiting_q3)
        q = QUESTIONS[2]
        await callback.message.edit_text(
            f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
            reply_markup=get_options_kb("q3"),
        )


@dp.callback_query(lambda c: c.data.startswith("ans_q2_"))
async def answer_q2(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q2=answer)
    await state.set_state(SurveyStates.waiting_q3)
    q = QUESTIONS[2]
    await callback.message.edit_text(
        f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q3"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q3_"))
async def answer_q3(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q3=answer)
    await state.set_state(SurveyStates.waiting_q4)
    q = QUESTIONS[3]
    await callback.message.edit_text(
        f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q4"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q4_"))
async def answer_q4(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q4=answer)
    await state.set_state(SurveyStates.waiting_q5)
    q = QUESTIONS[4]
    await callback.message.edit_text(
        f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
        reply_markup=get_options_kb("q5"),
    )


@dp.callback_query(lambda c: c.data.startswith("ans_q5_"))
async def answer_q5(callback: types.CallbackQuery, state: FSMContext):
    answer = callback.data.split("_")[2]
    await state.update_data(q5=answer)
    await state.set_state(SurveyStates.waiting_q6)
    q = QUESTIONS[5]
    await callback.message.edit_text(
        f"📋 Суолнома\n\n{q['text']}\n\nИнтихоб кунед:",
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

    await callback.message.edit_text(
        "✅ Ташаккур! Ҷавобҳои шумо сабт шуд.\n\n"
        "Барои боз гирифтани дарси бепул, мо бо шумо дар алоқа мешавем.\n\n"
        "📋 Натиҷа:\n"
        f"1️⃣ Курс харидӣ: {QUESTIONS[0]['options'].get(data.get('q1', ''), '-')}\n"
        f"2️⃣ Сабаб: {QUESTIONS[1]['options'].get(data.get('q2', ''), '-') or '-'}\n"
        f"3️⃣ Намерасид: {QUESTIONS[2]['options'].get(data.get('q3', ''), '-')}\n"
        f"4️⃣ Мушкил: {QUESTIONS[3]['options'].get(data.get('q4', ''), '-')}\n"
        f"5️⃣ Мехоҳӣ: {QUESTIONS[4]['options'].get(data.get('q5', ''), '-')}\n"
        f"6️⃣ Бюджет: {QUESTIONS[5]['options'].get(data.get('q6', ''), '-')}",
        reply_markup=get_retry_kb(),
    )


@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = get_responses_count()
    await message.answer(
        f"🔧 Админ-панель\n\n👥 Пользователей: {len(users)}\n📝 Ответов: {count}",
        reply_markup=admin_main_kb,
    )


@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    count = get_responses_count()
    await callback.message.edit_text(
        f"📊 Статистика\n\n👥 Всего пользователей: {len(users)}\n📝 Всего ответов: {count}",
        reply_markup=admin_main_kb,
    )


@dp.callback_query(lambda c: c.data == "admin_responses")
async def admin_responses(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = get_all_responses()
    if not rows:
        await callback.message.edit_text(
            "📋 Ҷавобҳо нестанд.", reply_markup=admin_main_kb
        )
        return

    text = f"📋 Ҷавобҳо ({len(rows)})\n\n"
    for i, row in enumerate(rows[:20], 1):
        created = row[3][:16] if row[3] else "-"
        text += f"{i}. ID:{row[1]} | {created}\n"
        text += f"   Q1:{row[4]} | Q2:{row[5]} | Q3:{row[6]}\n"
        text += f"   Q4:{row[7]} | Q5:{row[8]} | Q6:{row[9]}\n\n"

    if len(rows) > 20:
        text += f"... ва ещё {len(rows) - 20}"

    await callback.message.edit_text(text, reply_markup=admin_main_kb)


@dp.callback_query(lambda c: c.data == "admin_export")
async def admin_export(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rows = get_all_responses()
    if not rows:
        await callback.message.edit_text(
            "📋 Ҷавобҳо нестанд.", reply_markup=admin_main_kb
        )
        return

    csv = "ID,UserID,Username,Created,Q1,Q2,Q2_Custom,Q3,Q4,Q5,Q6\n"
    for row in rows:
        csv += f"{row[0]},{row[1]},{row[2]},{row[3]},{row[4]},{row[5]},{row[6]},{row[7]},{row[8]},{row[9]},{row[10]}\n"

    with open("responses_export.csv", "w", encoding="utf-8") as f:
        f.write(csv)

    await callback.message.answer_document(
        FSInputFile("responses_export.csv"), caption="📤 Экспорт ҷавобҳо"
    )


@dp.callback_query(lambda c: c.data == "admin_users_list")
async def admin_users_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    if not users:
        await callback.message.edit_text("Список пуст.", reply_markup=admin_main_kb)
        return
    lst = list(users)[:20]
    txt = f"📋 Пользователи ({len(users)})\n\n" + "\n".join(
        f"• <code>{uid}</code>" for uid in lst
    )
    if len(users) > 20:
        txt += f"\n... и ещё {len(users) - 20}"
    await callback.message.edit_text(txt, parse_mode="HTML", reply_markup=admin_main_kb)


@dp.callback_query(lambda c: c.data == "admin_close")
async def admin_close(callback: types.CallbackQuery):
    await callback.message.delete()


@dp.callback_query(lambda c: c.data == "admin_back")
async def admin_back(callback: types.CallbackQuery):
    await admin_panel(callback.message)


@dp.callback_query(lambda c: c.data == "admin_new_broadcast")
async def broadcast_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastStates.waiting_for_text)
    await callback.message.edit_text(
        "📝 Введите текст рассылки (можно HTML)", reply_markup=cancel_kb
    )


@dp.message(BroadcastStates.waiting_for_text)
async def broadcast_text(message: types.Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(BroadcastStates.waiting_for_photo)
    await message.answer(
        "📸 Отправьте фото или напишите 'пропустить'", reply_markup=cancel_kb
    )


@dp.message(BroadcastStates.waiting_for_photo)
async def broadcast_photo(message: types.Message, state: FSMContext):
    photo = None
    if message.photo:
        photo = message.photo[-1].file_id
        await message.answer("✅ Фото получено")
    elif message.text and message.text.lower() == "пропустить":
        photo = None
    else:
        await message.answer(
            "❌ Отправьте фото или 'пропустить'", reply_markup=cancel_kb
        )
        return
    await state.update_data(photo=photo)
    await state.set_state(BroadcastStates.waiting_for_video_link)
    await message.answer(
        "🎥 Введите ссылку на видео (или 'пропустить')", reply_markup=cancel_kb
    )


@dp.message(BroadcastStates.waiting_for_video_link)
async def broadcast_video(message: types.Message, state: FSMContext):
    link = None
    if message.text and message.text.lower() == "пропустить":
        link = None
    elif message.text and message.text.startswith("http"):
        link = message.text.strip()
    else:
        await message.answer(
            "❌ Введите ссылку или 'пропустить'", reply_markup=cancel_kb
        )
        return
    await state.update_data(video_link=link)
    await state.set_state(BroadcastStates.waiting_for_button)
    await message.answer(
        "🔘 Добавить кнопку? Отправьте текст и URL через | или 'пропустить'",
        reply_markup=cancel_kb,
    )


@dp.message(BroadcastStates.waiting_for_button)
async def broadcast_button(message: types.Message, state: FSMContext):
    btn = None
    if message.text and message.text.lower() == "пропустить":
        btn = None
    elif message.text and "|" in message.text:
        parts = message.text.split("|")
        if len(parts) == 2:
            btn = {"text": parts[0].strip(), "url": parts[1].strip()}
    else:
        await message.answer(
            "❌ Формат: Текст кнопки | https://...", reply_markup=cancel_kb
        )
        return
    await state.update_data(button=btn)
    await state.set_state(BroadcastStates.waiting_for_time)
    await message.answer(
        f"⏰ Выберите время (получателей: {len(users)})", reply_markup=time_kb
    )


@dp.callback_query(lambda c: c.data.startswith("time_") and c.data != "time_custom")
async def broadcast_time_preset(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    d = callback.data
    delay = 0
    if d == "time_1h":
        delay = 3600
    elif d == "time_3h":
        delay = 10800
    await state.update_data(delay=delay)
    await show_broadcast_preview(callback.message, state)


@dp.callback_query(lambda c: c.data == "time_custom")
async def broadcast_time_custom(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(BroadcastStates.waiting_for_time)
    await callback.message.edit_text(
        "Введите +X, ЧЧ:ММ или ЧЧ:ММ ДД.ММ", reply_markup=cancel_kb
    )


@dp.message(BroadcastStates.waiting_for_time)
async def broadcast_custom_time(message: types.Message, state: FSMContext):
    delay = parse_time(message.text)
    if delay is None:
        await message.answer("❌ Неверный формат", reply_markup=cancel_kb)
        return
    await state.update_data(delay=delay)
    await show_broadcast_preview(message, state)


def parse_time(s: str) -> Optional[int]:
    now = datetime.now()
    s = s.strip()
    if s.startswith("+"):
        try:
            h = float(s[1:])
            return int(h * 3600)
        except:
            return None
    if re.match(r"^\d{1,2}:\d{2}$", s):
        try:
            t = datetime.strptime(s, "%H:%M").time()
            target = datetime.combine(now.date(), t)
            if target < now:
                target += timedelta(days=1)
            return (target - now).seconds
        except:
            return None
    if " " in s:
        parts = s.split()
        if len(parts) == 2 and ":" in parts[0] and "." in parts[1]:
            try:
                t = datetime.strptime(parts[0], "%H:%M").time()
                d = datetime.strptime(parts[1], "%d.%m").date()
                year = now.year
                target = datetime.combine(d.replace(year=year), t)
                if target < now:
                    target = datetime.combine(d.replace(year=year + 1), t)
                return (target - now).total_seconds()
            except:
                return None
    return None


async def show_broadcast_preview(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text", "")
    photo = data.get("photo")
    video = data.get("video_link")
    btn = data.get("button")
    delay = data.get("delay", 0)
    if video:
        text += f"\n\n🎥 {video}"
    if delay == 0:
        time_str = "сейчас"
    else:
        h = delay // 3600
        m = (delay % 3600) // 60
        time_str = f"через {h} ч {m} мин" if h else f"через {m} мин"
    preview = f"📬 Предпросмотр\n👥 {len(users)}\n⏱ {time_str}\n{'─' * 20}\n\n"
    markup = build_reply_markup(btn)
    if photo:
        await bot.send_photo(
            msg.chat.id, photo, caption=preview + text, reply_markup=markup
        )
    else:
        await bot.send_message(msg.chat.id, preview + text, reply_markup=markup)
    await state.set_state(BroadcastStates.confirming)
    await msg.answer("Подтвердите:", reply_markup=confirm_kb)


@dp.callback_query(lambda c: c.data.startswith("confirm_"))
async def broadcast_confirm(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    act = callback.data
    data = await state.get_data()
    if act == "confirm_send":
        delay = data.get("delay", 0)
        content = {
            "type": "text",
            "text": data["text"],
            "photo": data.get("photo"),
            "video_link": data.get("video_link"),
            "button": data.get("button"),
        }
        if delay == 0:
            await callback.message.answer("🚀 Рассылка началась")
            await perform_broadcast(content)
        else:
            await callback.message.answer(f"⏳ Запланировано через {delay // 60} мин")
            asyncio.create_task(
                scheduled_broadcast(delay, content, callback.message.chat.id)
            )
        await state.clear()
    elif act == "confirm_edit_text":
        await state.set_state(BroadcastStates.waiting_for_text)
        await callback.message.edit_text("Введите новый текст", reply_markup=cancel_kb)
    elif act == "confirm_edit_photo":
        await state.set_state(BroadcastStates.waiting_for_photo)
        await callback.message.edit_text(
            "Отправьте новое фото или 'пропустить'", reply_markup=cancel_kb
        )
    elif act == "confirm_edit_video":
        await state.set_state(BroadcastStates.waiting_for_video_link)
        await callback.message.edit_text(
            "Введите новую ссылку или 'пропустить'", reply_markup=cancel_kb
        )
    elif act == "confirm_edit_button":
        await state.set_state(BroadcastStates.waiting_for_button)
        await callback.message.edit_text(
            "Отправьте текст и URL через | или 'пропустить'", reply_markup=cancel_kb
        )
    elif act == "confirm_edit_time":
        await state.set_state(BroadcastStates.waiting_for_time)
        await callback.message.edit_text("Выберите время", reply_markup=time_kb)
    elif act == "admin_cancel":
        await state.clear()
        await callback.message.edit_text("Отменено")


@dp.callback_query(lambda c: c.data == "admin_cancel")
async def cancel_action(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено")


async def perform_broadcast(content):
    total = len(users)
    success = fail = 0
    text = content["text"]
    if content.get("video_link"):
        text += f"\n\n🎥 {content['video_link']}"
    markup = build_reply_markup(content.get("button"))
    for uid in users:
        try:
            if content.get("photo"):
                await bot.send_photo(
                    uid, content["photo"], caption=text, reply_markup=markup
                )
            else:
                await bot.send_message(uid, text, reply_markup=markup)
            success += 1
        except:
            fail += 1
        await asyncio.sleep(0.05)
    logging.info(f"Broadcast done: {success}/{total}")


async def scheduled_broadcast(delay, content, admin_id):
    await asyncio.sleep(delay)
    await bot.send_message(admin_id, "🔔 Начинаю запланированную рассылку")
    await perform_broadcast(content)
    await bot.send_message(admin_id, "✅ Рассылка завершена")


@dp.callback_query(lambda c: c.data == "admin_video_note")
async def video_note_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(VideoNoteStates.waiting_for_video_note)
    await callback.message.edit_text(
        "🎥 Отправьте круглое видео", reply_markup=cancel_kb
    )


@dp.message(VideoNoteStates.waiting_for_video_note)
async def video_note_receive(message: types.Message, state: FSMContext):
    if not message.video_note:
        await message.answer("❌ Это не круглое видео", reply_markup=cancel_kb)
        return
    await state.update_data(video_note_id=message.video_note.file_id)
    await state.set_state(VideoNoteStates.confirming_video_note)
    await message.answer("👁 Предпросмотр")
    await message.answer_video_note(
        message.video_note.file_id,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить всем", callback_data="vn_confirm_send"
                    ),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="vn_cancel"),
                ]
            ]
        ),
    )


@dp.callback_query(lambda c: c.data == "vn_confirm_send")
async def video_note_send(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    vid = data["video_note_id"]
    total = len(users)
    success = fail = 0
    for uid in users:
        try:
            await bot.send_video_note(uid, vid)
            success += 1
        except:
            fail += 1
        await asyncio.sleep(0.05)
    await callback.message.answer(
        f"✅ Рассылка завершена\nУспешно: {success}, ошибок: {fail}"
    )
    await state.clear()


@dp.callback_query(lambda c: c.data == "vn_cancel")
async def video_note_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отменено")


@dp.callback_query(lambda c: c.data == "admin_auto")
async def auto_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    st = "✅ Включен" if auto_config["enabled"] else "❌ Выключен"
    await callback.message.edit_text(
        f"🔔 Автоответчик\nСтатус: {st}\nВремя: {auto_config['time']}\nКнопка: {'есть' if auto_config.get('button') else 'нет'}",
        reply_markup=auto_kb,
    )


@dp.callback_query(lambda c: c.data == "auto_enable")
async def auto_enable(callback: types.CallbackQuery):
    auto_config["enabled"] = True
    save_auto_config(auto_config)
    await auto_menu(callback)


@dp.callback_query(lambda c: c.data == "auto_disable")
async def auto_disable(callback: types.CallbackQuery):
    auto_config["enabled"] = False
    save_auto_config(auto_config)
    await auto_menu(callback)


@dp.callback_query(lambda c: c.data == "auto_set_time")
async def auto_set_time(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AutoConfigStates.waiting_for_time)
    await callback.message.edit_text("Введите время ЧЧ:ММ", reply_markup=cancel_kb)


@dp.message(AutoConfigStates.waiting_for_time)
async def auto_time_receive(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text.strip(), "%H:%M")
        auto_config["time"] = message.text.strip()
        save_auto_config(auto_config)
        await state.clear()
        await message.answer("✅ Время сохранено")
        await auto_menu(message)
    except:
        await message.answer("❌ Неверный формат")


@dp.callback_query(lambda c: c.data == "auto_set_content")
async def auto_set_content(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AutoConfigStates.waiting_for_content)
    await callback.message.edit_text(
        "Отправьте контент (текст, фото, видео, аудио, голос, документ, GIF, кружок)",
        reply_markup=cancel_kb,
    )


@dp.message(AutoConfigStates.waiting_for_content)
async def auto_content_receive(message: types.Message, state: FSMContext):
    content = {}
    if message.text:
        content["type"] = "text"
        content["text"] = message.text
    elif message.photo:
        content["type"] = "photo"
        content["file_id"] = message.photo[-1].file_id
        content["caption"] = message.caption or ""
    elif message.video:
        content["type"] = "video"
        content["file_id"] = message.video.file_id
        content["caption"] = message.caption or ""
    elif message.audio:
        content["type"] = "audio"
        content["file_id"] = message.audio.file_id
        content["caption"] = message.caption or ""
    elif message.voice:
        content["type"] = "voice"
        content["file_id"] = message.voice.file_id
        content["caption"] = message.caption or ""
    elif message.document:
        content["type"] = "document"
        content["file_id"] = message.document.file_id
        content["caption"] = message.caption or ""
    elif message.animation:
        content["type"] = "animation"
        content["file_id"] = message.animation.file_id
        content["caption"] = message.caption or ""
    elif message.video_note:
        content["type"] = "video_note"
        content["file_id"] = message.video_note.file_id
    else:
        await message.answer("❌ Неподдерживаемый тип")
        return
    auto_config["content"] = content
    save_auto_config(auto_config)
    await state.clear()
    await message.answer("✅ Контент сохранён")
    await auto_menu(message)


@dp.callback_query(lambda c: c.data == "auto_set_button")
async def auto_set_button(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AutoConfigStates.waiting_for_button)
    await callback.message.edit_text(
        "Отправьте текст и URL через | или 'пропустить'", reply_markup=cancel_kb
    )


@dp.message(AutoConfigStates.waiting_for_button)
async def auto_button_receive(message: types.Message, state: FSMContext):
    if message.text and message.text.lower() == "пропустить":
        auto_config["button"] = None
    elif message.text and "|" in message.text:
        parts = message.text.split("|")
        if len(parts) == 2:
            auto_config["button"] = {"text": parts[0].strip(), "url": parts[1].strip()}
    else:
        await message.answer("❌ Неверный формат")
        return
    save_auto_config(auto_config)
    await state.clear()
    await message.answer("✅ Кнопка сохранена")
    await auto_menu(message)


async def auto_sender_loop():
    while True:
        now = datetime.now()
        if auto_config["enabled"] and auto_config.get("content"):
            t = datetime.strptime(auto_config["time"], "%H:%M").time()
            target = datetime.combine(now.date(), t)
            if now >= target and (now - target).seconds < 60:
                content = auto_config["content"]
                markup = build_reply_markup(auto_config.get("button"))
                for uid in users:
                    try:
                        await send_content(uid, content, reply_markup=markup)
                    except:
                        pass
                    await asyncio.sleep(0.05)
                await asyncio.sleep(60)
        await asyncio.sleep(30)


async def main():
    asyncio.create_task(auto_sender_loop())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
