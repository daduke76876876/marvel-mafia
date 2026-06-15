import asyncio
import json
import logging
import os
import random
import time
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# ---------- Настройки ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "mafia_bot.db")
COOLDOWN_SECONDS = 2  # кулдаун между ходами

# Максимальные размеры сетки
MAX_ROWS = 7
MAX_COLS = 4
MAX_CELLS = MAX_ROWS * MAX_COLS  # 28

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🆕 Новая игра Мафия"],
        ["📤 Опубликовать игру", "🛑 Остановить игру"],
        ["🔍 Раскрыть роли", "🏆 Вручить награды"],
        ["📊 Статистика", "🎁 Настроить награды"],
        ["❓ Помощь"]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

# ---------- База данных ----------
async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL DEFAULT '',
                message_id INTEGER,
                rows INTEGER NOT NULL DEFAULT 7,
                cols INTEGER NOT NULL DEFAULT 2,
                roles TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by INTEGER NOT NULL,
                rewards TEXT NOT NULL DEFAULT '[]',
                last_move_at REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS game_moves (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL DEFAULT '',
                cell_index INTEGER NOT NULL,
                role_name TEXT,
                role_emoji TEXT,
                role_side TEXT,
                role_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (game_id) REFERENCES games(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_move_per_user 
                ON game_moves(game_id, user_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_move_per_cell 
                ON game_moves(game_id, cell_index);
        """)
        # Миграции
        for col in ["role_type", "rows", "cols", "rewards", "last_move_at"]:
            try:
                if col == "rows":
                    await db.execute("ALTER TABLE games ADD COLUMN rows INTEGER NOT NULL DEFAULT 7")
                elif col == "cols":
                    await db.execute("ALTER TABLE games ADD COLUMN cols INTEGER NOT NULL DEFAULT 2")
                else:
                    await db.execute(f"ALTER TABLE games ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'")
            except aiosqlite.OperationalError:
                pass
        try:
            await db.execute("ALTER TABLE games ADD COLUMN last_move_at REAL DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE game_moves ADD COLUMN role_type TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()

# ---------- Роли (базовый набор, будет обрезаться под количество) ----------
ALL_ROLES = [
    # МИРНЫЕ
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
     "desc": "Простой житель Нью-Йорка.", "powers": "Голосуешь днём."},
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
     "desc": "Простой житель Нью-Йорка.", "powers": "Голосуешь днём."},
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
     "desc": "Простой житель Нью-Йорка.", "powers": "Голосуешь днём."},
    {"name": "Бомж", "emoji": "🏚️", "side": "neutral", "role_type": "bum",
     "desc": "Бездомный. Тебе никто не верит.", "powers": "Не лечится. Виден как нейтрал."},
    {"name": "Капитан Америка", "emoji": "🦸", "side": "civilian", "role_type": "sergeant",
     "desc": "Сержант. Лидер.", "powers": "Двойной голос."},
    {"name": "Сорвиголова", "emoji": "⚖️", "side": "civilian", "role_type": "lawyer",
     "desc": "Адвокат. Защитник.", "powers": "Спасает от казни."},
    {"name": "Ник Фьюри", "emoji": "🕵️", "side": "civilian", "role_type": "commissioner",
     "desc": "Комиссар. Глава Щ.И.Т.", "powers": "Проверяет сторону."},
    {"name": "Доктор Стрэндж", "emoji": "💉", "side": "civilian", "role_type": "doctor",
     "desc": "Верховный маг.", "powers": "Лечит."},
    {"name": "Булзай", "emoji": "🎯", "side": "civilian", "role_type": "sniper",
     "desc": "Снайпер.", "powers": "Один выстрел."},
    # МАФИЯ
    {"name": "Кингпин", "emoji": "👑", "side": "mafia", "role_type": "don",
     "desc": "Дон мафии.", "powers": "Решающий голос."},
    {"name": "Чёрная Вдова", "emoji": "🕷️", "side": "mafia", "role_type": "mistress",
     "desc": "Любовница.", "powers": "Блокирует роль."},
    # НЕЙТРАЛЫ
    {"name": "Дэдпул", "emoji": "🃏", "side": "neutral", "role_type": "suicide",
     "desc": "Самоубийца.", "powers": "Забирает убийцу."},
    {"name": "Домино", "emoji": "🍀", "side": "neutral", "role_type": "lucky",
     "desc": "Счастливчик.", "powers": "Выживает раз."},
    {"name": "Клетус Кесседи", "emoji": "🩸", "side": "neutral", "role_type": "maniac",
     "desc": "Маньяк.", "powers": "Убивает ночью."},
    {"name": "Зелёный Гоблин", "emoji": "🎃", "side": "neutral", "role_type": "kamikaze",
     "desc": "Камикадзе.", "powers": "Взрывает."},
]

# ---------- Клавиатуры ----------
def build_game_keyboard(game_id: int, rows: int, cols: int, taken_cells: dict) -> InlineKeyboardMarkup:
    keyboard = []
    for r in range(rows):
        row_buttons = []
        for c in range(cols):
            idx = r * cols + c
            if idx in taken_cells:
                username = taken_cells[idx]
                display = f"@{username}" if len(username) <= 10 else f"@{username[:8]}…"
                cb = f"occupied:{game_id}:{idx}"
            else:
                display = f"🔒 {idx+1}"
                cb = f"pick:{game_id}:{idx}"
            row_buttons.append(InlineKeyboardButton(display, callback_data=cb))
        keyboard.append(row_buttons)
    return InlineKeyboardMarkup(keyboard)

async def update_message_keyboard(app, chat_id, message_id, reply_markup):
    try:
        await app.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка обновления: {e}")

# ---------- Команда /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "🦸 <b>МАФИЯ MARVEL</b>\n\n"
        "Классическая мафия с героями Marvel!\n"
        "🎁 Награды для победителей!\n"
        "👥 Выбор количества участников!\n\n"
        "Нажмите «🆕 Новая игра Мафия».",
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.HTML
    )

# ---------- Настройка наград ----------
async def setup_rewards_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id FROM games WHERE created_by=? AND (status='pending' OR status='active') ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ Сначала создайте игру.", reply_markup=MAIN_KEYBOARD)
            return
        game_id = game[0][0]

    context.user_data["reward_game_id"] = game_id
    context.user_data["reward_state"] = "awaiting_civilian_text"
    await update.message.reply_text(
        "🎁 <b>НАСТРОЙКА НАГРАД</b>\n\n"
        "Этап 1/3: Введите название награды для <b>МИРНЫХ</b>.\n(или «-» если нет)",
        parse_mode=ParseMode.HTML
    )

async def process_reward_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    state = context.user_data.get("reward_state")
    if not state:
        return
    text = update.message.text.strip()

    if state == "awaiting_civilian_text":
        context.user_data["civilian_reward_text"] = "" if text == "-" else text
        context.user_data["reward_state"] = "awaiting_civilian_photo"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
             InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
        ])
        await update.message.reply_text("Прикрепить фото для мирных?", reply_markup=keyboard)

    elif state == "awaiting_mafia_text":
        context.user_data["mafia_reward_text"] = "" if text == "-" else text
        context.user_data["reward_state"] = "awaiting_mafia_photo"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
             InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
        ])
        await update.message.reply_text("Прикрепить фото для мафии?", reply_markup=keyboard)

    elif state == "awaiting_neutral_text":
        context.user_data["neutral_reward_text"] = "" if text == "-" else text
        context.user_data["reward_state"] = "awaiting_neutral_photo"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
             InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
        ])
        await update.message.reply_text("Прикрепить фото для нейтралов?", reply_markup=keyboard)

async def handle_reward_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    state = context.user_data.get("reward_state")
    if not state or "photo" not in state:
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Отправьте фото.")
        return

    photo_id = update.message.photo[-1].file_id
    if state == "awaiting_civilian_photo":
        context.user_data["civilian_reward_photo"] = photo_id
        context.user_data["reward_state"] = "awaiting_mafia_text"
        await update.message.reply_text("👑 Введите награду для МАФИИ (или «-»):")
    elif state == "awaiting_mafia_photo":
        context.user_data["mafia_reward_photo"] = photo_id
        context.user_data["reward_state"] = "awaiting_neutral_text"
        await update.message.reply_text("⚡ Введите награду для НЕЙТРАЛОВ (или «-»):")
    elif state == "awaiting_neutral_photo":
        context.user_data["neutral_reward_photo"] = photo_id
        await save_all_rewards(update, context)

async def save_all_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("reward_game_id")
    if not game_id:
        await update.message.reply_text("❌ Игра не найдена.")
        return
    rewards = {
        "civilian": {"text": context.user_data.get("civilian_reward_text", ""), "photo": context.user_data.get("civilian_reward_photo")},
        "mafia": {"text": context.user_data.get("mafia_reward_text", ""), "photo": context.user_data.get("mafia_reward_photo")},
        "neutral": {"text": context.user_data.get("neutral_reward_text", ""), "photo": context.user_data.get("neutral_reward_photo")}
    }
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET rewards=? WHERE id=?", (json.dumps(rewards), game_id))
        await db.commit()
    for key in list(context.user_data.keys()):
        if "reward" in key:
            del context.user_data[key]
    text = "✅ Награды:\n"
    text += f"🛡️ Мирные: {rewards['civilian']['text'] or 'Нет'}\n"
    text += f"👑 Мафия: {rewards['mafia']['text'] or 'Нет'}\n"
    text += f"⚡ Нейтралы: {rewards['neutral']['text'] or 'Нет'}"
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)

# ---------- Вручение наград ----------
async def give_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id, chat_id, rewards, status FROM games WHERE created_by=? AND status='finished' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ Нет завершённой игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, chat_id, rewards_json, _ = game[0]
        rewards = json.loads(rewards_json) if rewards_json else {}
        if not rewards:
            await update.message.reply_text("❌ Награды не настроены.", reply_markup=MAIN_KEYBOARD)
            return

        moves = await db.execute_fetchall("SELECT username, role_side FROM game_moves WHERE game_id=?", (game_id,))
        alive_civilian = sum(1 for m in moves if m[1] == "civilian")
        alive_mafia = sum(1 for m in moves if m[1] == "mafia")
        alive_neutral = sum(1 for m in moves if m[1] == "neutral")

        if alive_mafia >= alive_civilian and alive_mafia > 0:
            winning_side = "mafia"
            winners = [m[0] for m in moves if m[1] == "mafia"]
        elif alive_neutral > alive_civilian and alive_neutral > alive_mafia:
            winning_side = "neutral"
            winners = [m[0] for m in moves if m[1] == "neutral"]
        else:
            winning_side = "civilian"
            winners = [m[0] for m in moves if m[1] == "civilian"]

        reward = rewards.get(winning_side, {})
        reward_text = reward.get("text", "")
        reward_photo = reward.get("photo")

        if not reward_text and not reward_photo:
            await update.message.reply_text(f"🏆 Победили {winning_side}, но награда не настроена.", reply_markup=MAIN_KEYBOARD)
            return

        side_names = {"civilian": "🛡️ МИРНЫЕ", "mafia": "👑 МАФИЯ", "neutral": "⚡ НЕЙТРАЛЫ"}
        winner_list = "\n".join([f"@{w}" for w in winners])
        caption = f"🏆 <b>ПОБЕДИТЕЛИ #{game_id}!</b>\n\n{side_names[winning_side]}\n\n<b>Победители ({len(winners)}):</b>\n{winner_list}\n\n🎁 <b>Награда:</b> {reward_text}"

        try:
            if reward_photo:
                await context.bot.send_photo(chat_id=chat_id, photo=reward_photo, caption=caption, parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML)
            await update.message.reply_text("🏆 Награды отправлены!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_KEYBOARD)

# ---------- Раскрытие ролей ----------
async def reveal_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id, roles FROM games WHERE created_by=? AND status='active' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ Нет активной игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, roles_json = game[0]
        roles = json.loads(roles_json)
        moves = await db.execute_fetchall(
            "SELECT cell_index, username, role_name, role_emoji, role_side, role_type FROM game_moves WHERE game_id=?", (game_id,)
        )
        taken = {m[0]: m for m in moves}

    type_names = {"citizen": "Мирный", "bum": "Бомж", "sergeant": "Сержант", "lawyer": "Адвокат", "commissioner": "Комиссар", "doctor": "Доктор", "sniper": "Снайпер", "don": "Дон", "mistress": "Любовница", "suicide": "Самоубийца", "lucky": "Счастливчик", "maniac": "Маньяк", "kamikaze": "Камикадзе"}

    text = f"🔍 <b>РОЛИ ИГРЫ #{game_id}</b>\n"
    for side, header in [("civilian", "🛡️ МИРНЫЕ"), ("mafia", "👑 МАФИЯ"), ("neutral", "⚡ НЕЙТРАЛЫ")]:
        text += f"\n<b>{header}:</b>\n"
        for i, role in enumerate(roles):
            if role["side"] == side:
                if i in taken:
                    _, uname, rname, remoji, _, rtype = taken[i]
                    text += f"  {remoji} {rname} ({type_names.get(rtype, '')}) → @{uname}\n"
                else:
                    text += f"  {role['emoji']} {role['name']} ({type_names.get(role['role_type'], '')}) — свободна\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)

# ---------- Статистика ----------
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id, rows, cols, roles FROM games WHERE created_by=? AND status='active' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ Нет активной игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, rows, cols, roles_json = game[0]
        roles = json.loads(roles_json)
        total = rows * cols
        moves = await db.execute_fetchall("SELECT COUNT(*) FROM game_moves WHERE game_id=?", (game_id,))
        taken = moves[0][0] if moves else 0

    civilians = sum(1 for r in roles if r["side"] == "civilian")
    mafia = sum(1 for r in roles if r["side"] == "mafia")
    neutral = sum(1 for r in roles if r["side"] == "neutral")
    await update.message.reply_text(
        f"📊 <b>СТАТИСТИКА #{game_id}</b>\n\n"
        f"Сетка: {rows}×{cols} = {total}\n"
        f"Выбрано: {taken}\nОсталось: {total - taken}\n\n"
        f"🛡️ Мирные: {civilians}\n👑 Мафия: {mafia}\n⚡ Нейтралы: {neutral}",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
    )

# ---------- Обработчик меню ----------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Если в режиме настройки наград
    if context.user_data.get("reward_state") and "text" in context.user_data["reward_state"]:
        await process_reward_text(update, context)
        return

    if text == "🆕 Новая игра Мафия":
        context.user_data.clear()
        context.user_data["state"] = "awaiting_players"
        await update.message.reply_text(
            f"👥 <b>ВЫБОР КОЛИЧЕСТВА УЧАСТНИКОВ</b>\n\n"
            f"Введите число игроков (от 2 до {MAX_CELLS}).\n"
            f"Бот подберёт оптимальную сетку.\n\n"
            f"<i>Рекомендуется от 10 до 14 для полного набора ролей.</i>",
            parse_mode=ParseMode.HTML
        )
        return

    if text == "📤 Опубликовать игру":
        context.user_data.clear()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await db.execute_fetchall(
                "SELECT id FROM games WHERE created_by=? AND status='pending' ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            if not row:
                await update.message.reply_text("❌ Нет готовой игры.", reply_markup=MAIN_KEYBOARD)
                return
            game_id = row[0][0]
        context.user_data["game_id"] = game_id
        context.user_data["state"] = "awaiting_chat"
        await update.message.reply_text("Введите @username чата/канала для публикации:")
        return

    if text == "🛑 Остановить игру":
        await stop_active_game(update, context)
        return
    if text == "🔍 Раскрыть роли":
        await reveal_roles(update, context)
        return
    if text == "🏆 Вручить награды":
        await give_rewards(update, context)
        return
    if text == "📊 Статистика":
        await show_stats(update, context)
        return
    if text == "🎁 Настроить награды":
        await setup_rewards_start(update, context)
        return
    if text == "❓ Помощь":
        await update.message.reply_text(
            "🦸 <b>МАФИЯ MARVEL</b>\n\n"
            "1️⃣ «🆕 Новая игра» → введите число игроков\n"
            "2️⃣ «🎁 Настроить награды» → призы\n"
            "3️⃣ «📤 Опубликовать» → @чат\n"
            "4️⃣ После игры «🛑 Остановить» → «🏆 Вручить»\n\n"
            "<b>Роли:</b> Мирные, Мафия, Нейтралы\n"
            "<b>Кулдаун:</b> 2 сек между ходами",
            parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
        )
        return

    state = context.user_data.get("state")
    if state == "awaiting_players":
        await process_players(update, context)
    elif state == "awaiting_chat":
        await process_chat_input(update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=MAIN_KEYBOARD)

async def process_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        num = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введите число.")
        return
    if num < 2 or num > MAX_CELLS:
        await update.message.reply_text(f"❌ От 2 до {MAX_CELLS}.")
        return

    # Подбираем сетку
    if num <= 14:
        rows, cols = (num + 1) // 2, 2
    elif num <= 21:
        rows, cols = (num + 2) // 3, 3
    else:
        rows, cols = (num + 3) // 4, 4
    total = rows * cols
    # Берём первые num ролей из списка
    selected_roles = ALL_ROLES[:num]
    if len(selected_roles) < total:
        # Добавляем мирных жителей до заполнения
        while len(selected_roles) < total:
            selected_roles.append({"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
                                   "desc": "Простой житель.", "powers": "Голосуешь."})
    # Если ролей больше, чем ячеек (не должно быть), обрезаем
    selected_roles = selected_roles[:total]

    shuffled = random.sample(selected_roles, len(selected_roles))
    roles_json = json.dumps(shuffled)
    user_id = update.effective_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO games (chat_id, rows, cols, roles, status, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            ("", rows, cols, roles_json, "pending", user_id)
        )
        game_id = cursor.lastrowid
        await db.commit()

    context.user_data["state"] = "ready"
    civilians = sum(1 for r in shuffled if r["side"] == "civilian")
    mafia = sum(1 for r in shuffled if r["side"] == "mafia")
    neutral = sum(1 for r in shuffled if r["side"] == "neutral")

    await update.message.reply_text(
        f"🦸 Игра #{game_id} создана!\n\n"
        f"📐 Сетка: {rows}×{cols} = {total} ячеек\n"
        f"🛡️ Мирные: {civilians}\n👑 Мафия: {mafia}\n⚡ Нейтралы: {neutral}\n\n"
        "Нажмите «🎁 Настроить награды», затем «📤 Опубликовать».",
        reply_markup=MAIN_KEYBOARD
    )

# ---------- Callback-обработчик ----------
async def grid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data in ("reward_photo_yes", "reward_photo_no"):
        state = context.user_data.get("reward_state")
        if data == "reward_photo_yes":
            await query.edit_message_text("📷 Отправьте фото.")
        else:
            if state == "awaiting_civilian_photo":
                context.user_data["civilian_reward_photo"] = None
                context.user_data["reward_state"] = "awaiting_mafia_text"
                await query.edit_message_text("👑 Введите награду для МАФИИ (или «-»):")
            elif state == "awaiting_mafia_photo":
                context.user_data["mafia_reward_photo"] = None
                context.user_data["reward_state"] = "awaiting_neutral_text"
                await query.edit_message_text("⚡ Введите награду для НЕЙТРАЛОВ (или «-»):")
            elif state == "awaiting_neutral_photo":
                context.user_data["neutral_reward_photo"] = None
                await save_all_rewards_callback(update, context)
        await query.answer()
        return

    if data.startswith("pick:"):
        _, game_id_str, idx_str = data.split(":")
        game_id = int(game_id_str)
        cell_idx = int(idx_str)
        user = query.from_user
        user_id = user.id
        username = user.username or user.first_name

        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall(
                "SELECT chat_id, message_id, rows, cols, roles, status, last_move_at FROM games WHERE id=?",
                (game_id,)
            )
            if not game or game[0][5] != "active":
                await query.answer("Игра неактивна.", show_alert=True)
                return
            chat_id, message_id, rows, cols, roles_json, _, last_move = game[0]
            roles = json.loads(roles_json)

            # Кулдаун
            now = time.time()
            if last_move and (now - last_move) < COOLDOWN_SECONDS:
                remain = round(COOLDOWN_SECONDS - (now - last_move), 1)
                await query.answer(f"⏳ Подождите {remain} сек.", show_alert=True)
                return

            # Проверка, не выбрал ли уже игрок роль
            exists = await db.execute_fetchall(
                "SELECT 1 FROM game_moves WHERE game_id=? AND user_id=?", (game_id, user_id)
            )
            if exists:
                await query.answer("❗ Вы уже выбрали роль.", show_alert=True)
                return

            # Проверка занятости ячейки
            cell_taken = await db.execute_fetchall(
                "SELECT 1 FROM game_moves WHERE game_id=? AND cell_index=?", (game_id, cell_idx)
            )
            if cell_taken:
                await query.answer("⛔ Ячейка уже занята.", show_alert=True)
                return

            # Транзакция с повторной проверкой
            try:
                await db.execute("BEGIN IMMEDIATE")
                if await db.execute_fetchall("SELECT 1 FROM game_moves WHERE game_id=? AND user_id=?", (game_id, user_id)):
                    await db.execute("ROLLBACK")
                    await query.answer("❗ Вы уже выбрали роль.", show_alert=True)
                    return
                if await db.execute_fetchall("SELECT 1 FROM game_moves WHERE game_id=? AND cell_index=?", (game_id, cell_idx)):
                    await db.execute("ROLLBACK")
                    await query.answer("⛔ Ячейка уже занята.", show_alert=True)
                    return

                role = roles[cell_idx]
                await db.execute(
                    "INSERT INTO game_moves (game_id, user_id, username, cell_index, role_name, role_emoji, role_side, role_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (game_id, user_id, username, cell_idx, role["name"], role["emoji"], role["side"], role["role_type"])
                )
                await db.execute("UPDATE games SET last_move_at=? WHERE id=?", (now, game_id))
                await db.execute("COMMIT")
            except aiosqlite.IntegrityError as e:
                await db.execute("ROLLBACK")
                if "cell_index" in str(e):
                    await query.answer("⛔ Ячейка уже занята.", show_alert=True)
                else:
                    await query.answer("❗ Вы уже выбрали роль.", show_alert=True)
                return

            moves = await db.execute_fetchall("SELECT cell_index, username FROM game_moves WHERE game_id=?", (game_id,))
            taken_cells = {cell: uname for cell, uname in moves}

        # Отправка роли в личку
        role_side = role["side"]
        if role_side == "civilian":
            side_text = "🛡️ МИРНЫЙ"
            team_text = "Ты с Мстителями!"
        elif role_side == "mafia":
            side_text = "👑 МАФИЯ"
            team_text = "Ты с Кингпином!"
        else:
            side_text = "⚡ НЕЙТРАЛ"
            team_text = "Ты сам за себя!"

        role_message = (
            f"🎭 <b>ТВОЯ РОЛЬ:</b> {role['emoji']} <b>{role['name']}</b>\n\n"
            f"{side_text}\n{team_text}\n\n"
            f"📜 {role['desc']}\n\n⚡ {role['powers']}\n\n"
            f"<i>🤫 Не показывай никому!</i>"
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=role_message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка отправки роли: {e}")
            await query.answer("❌ Напишите боту в личку /start", show_alert=True)
            return

        await update_message_keyboard(context.application, chat_id, message_id, build_game_keyboard(game_id, rows, cols, taken_cells))
        await query.answer("🎭 Роль в личке!", show_alert=True)
        return

    if data.startswith("occupied:"):
        await query.answer("⛔ Занято.", show_alert=True)
        return
    await query.answer()

async def save_all_rewards_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("reward_game_id")
    if not game_id:
        return
    rewards = {
        "civilian": {"text": context.user_data.get("civilian_reward_text", ""), "photo": context.user_data.get("civilian_reward_photo")},
        "mafia": {"text": context.user_data.get("mafia_reward_text", ""), "photo": context.user_data.get("mafia_reward_photo")},
        "neutral": {"text": context.user_data.get("neutral_reward_text", ""), "photo": context.user_data.get("neutral_reward_photo")}
    }
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET rewards=? WHERE id=?", (json.dumps(rewards), game_id))
        await db.commit()
    for key in list(context.user_data.keys()):
        if "reward" in key:
            del context.user_data[key]
    await update.callback_query.answer("✅ Награды сохранены!")

# ---------- Публикация ----------
async def process_chat_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.text.strip()
    user_id = update.effective_user.id
    context.user_data.pop("state", None)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT id, rows, cols FROM games WHERE created_by=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not row:
            await update.message.reply_text("❌ Нет игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, rows, cols = row[0]
        try:
            msg = await context.bot.send_message(
                chat_id=chat,
                text=f"🦸 <b>МАФИЯ MARVEL</b>\n\n{rows}×{cols} = {rows*cols} ролей!\nВыбери ячейку → роль в личку 🤫",
                reply_markup=build_game_keyboard(game_id, rows, cols, {}),
                parse_mode=ParseMode.HTML
            )
            await db.execute("UPDATE games SET chat_id=?, message_id=?, status='active' WHERE id=?", (chat, msg.message_id, game_id))
            await db.commit()
            await update.message.reply_text("✅ Опубликовано!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_KEYBOARD)

# ---------- Остановка ----------
async def stop_active_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT id, chat_id, message_id FROM games WHERE created_by=? AND status='active'",
            (user_id,)
        )
        if not row:
            await update.message.reply_text("❌ Нет активных игр.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, chat_id, message_id = row[0]
        await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
        await db.commit()
    if message_id:
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
        except:
            pass
    await update.message.reply_text(f"🏁 Игра #{game_id} остановлена!\nНажмите «🏆 Вручить награды».", reply_markup=MAIN_KEYBOARD)

# ---------- Запуск ----------
async def post_init(application: Application):
    await init_db()

def main():
    if not BOT_TOKEN:
        raise ValueError("Не задан BOT_TOKEN")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reveal", reveal_roles))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_menu))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_reward_photo))
    app.add_handler(CallbackQueryHandler(grid_callback))
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
