import asyncio
import json
import logging
import os
import random
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

ROWS = 7
COLS = 2
TOTAL_CELLS = ROWS * COLS  # 14 ячеек

# ===== РОЛИ =====
ROLES = [
    # МИРНЫЕ (7 ролей)
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
     "desc": "Простой житель Нью-Йорка. Твоя задача — вычислить мафию.",
     "powers": "Голосуешь днём. Никаких особых способностей."},
    
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "role_type": "citizen",
     "desc": "Простой житель Нью-Йорка. Твоя задача — вычислить мафию.",
     "powers": "Голосуешь днём. Никаких особых способностей."},
    
    {"name": "Бомж", "emoji": "🏚️", "side": "neutral", "role_type": "bum",
     "desc": "Бездомный с улиц Адской Кухни. Ты видел всё, но тебе никто не верит.",
     "powers": "Голосуешь днём. Доктор не может тебя лечить. Комиссар видит тебя как нейтрала."},
    
    {"name": "Капитан Америка", "emoji": "🦸", "side": "civilian", "role_type": "sergeant",
     "desc": "Сержант армии и лидер Мстителей. Твой голос имеет больший вес.",
     "powers": "На дневном голосовании твой голос считается за ДВА."},
    
    {"name": "Сорвиголова", "emoji": "⚖️", "side": "civilian", "role_type": "lawyer",
     "desc": "Адвокат днём, защитник ночью. Можешь спасти невиновного.",
     "powers": "Ночью выбери игрока — если его казнят днём, он выживет."},
    
    {"name": "Ник Фьюри", "emoji": "🕵️", "side": "civilian", "role_type": "commissioner",
     "desc": "Глава Щ.И.Т. и комиссар полиции. Знаешь всё про всех.",
     "powers": "Ночью проверяешь игрока и узнаёшь: МИРНЫЙ, МАФИЯ или НЕЙТРАЛ."},
    
    {"name": "Доктор Стрэндж", "emoji": "💉", "side": "civilian", "role_type": "doctor",
     "desc": "Верховный маг Земли и главный врач. Можешь исцелить раненого.",
     "powers": "Ночью выбери игрока — мафия не сможет его убить. Бомжа лечить нельзя."},
    
    {"name": "Булзай", "emoji": "🎯", "side": "civilian", "role_type": "sniper",
     "desc": "Снайпер-ас. Один выстрел — одна смерть.",
     "powers": "ОДИН раз за игру можешь выстрелить в любого игрока и убить его."},
    
    # МАФИЯ (2 роли)
    {"name": "Кингпин", "emoji": "👑", "side": "mafia", "role_type": "don",
     "desc": "Криминальный босс Нью-Йорка. Дон мафии.",
     "powers": "Ночью ведёшь обсуждение с мафией. Твой голос — РЕШАЮЩИЙ."},
    
    {"name": "Чёрная Вдова", "emoji": "🕷️", "side": "mafia", "role_type": "mistress",
     "desc": "Любовница и шпионка. Можешь отвлечь любого.",
     "powers": "Ночью выбери игрока — он проведёт ночь с тобой и не выполнит свою роль."},
    
    # НЕЙТРАЛЫ (5 ролей)
    {"name": "Дэдпул", "emoji": "🃏", "side": "neutral", "role_type": "suicide",
     "desc": "Самоубийца с языком без костей. Твоя смерть — часть плана.",
     "powers": "Если тебя убьют днём или ночью — забираешь с собой одного убийцу."},
    
    {"name": "Домино", "emoji": "🍀", "side": "neutral", "role_type": "lucky",
     "desc": "Счастливчик. Удача всегда на твоей стороне.",
     "powers": "Первый раз, когда тебя попытаются убить — ты выживаешь."},
    
    {"name": "Клетус Кесседи", "emoji": "🩸", "side": "neutral", "role_type": "maniac",
     "desc": "Маньяк-убийца. Ты живёшь ради хаоса.",
     "powers": "Каждую ночь можешь убить одного игрока. Твоя цель — остаться последним."},
    
    {"name": "Зелёный Гоблин", "emoji": "🎃", "side": "neutral", "role_type": "kamikaze",
     "desc": "Камикадзе на глайдере. Взорвать всё — твой план.",
     "powers": "Один раз за игру можешь взорвать себя и одного игрока (умираете оба)."},
]

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
                roles TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_by INTEGER NOT NULL,
                rewards TEXT NOT NULL DEFAULT '[]',
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
        try:
            await db.execute("ALTER TABLE game_moves ADD COLUMN role_type TEXT")
        except aiosqlite.OperationalError:
            pass
        try:
            await db.execute("ALTER TABLE games ADD COLUMN rewards TEXT NOT NULL DEFAULT '[]'")
        except aiosqlite.OperationalError:
            pass
        await db.commit()

# ---------- Клавиатуры ----------
def build_game_keyboard(game_id: int, taken_cells: dict) -> InlineKeyboardMarkup:
    keyboard = []
    for r in range(ROWS):
        row_buttons = []
        for c in range(COLS):
            idx = r * COLS + c
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
        "Классическая мафия с героями и злодеями Marvel!\n"
        "14 ролей, 7 мирных, 2 мафии, 5 нейтралов.\n\n"
        "🎁 <b>Новое:</b> награды для победителей!\n"
        "Настройте призы перед игрой.",
        reply_markup=MAIN_KEYBOARD,
        parse_mode=ParseMode.HTML
    )

# ---------- Настройка наград ----------
async def setup_rewards_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начинает настройку наград."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    
    # Проверяем, есть ли активная игра
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
        "Вы можете установить награды для трёх сторон:\n"
        "🛡️ Мирные\n"
        "👑 Мафия\n"
        "⚡ Нейтралы\n\n"
        "<b>Этап 1/3:</b> Введите название награды для <b>МИРНЫХ</b>.\n"
        "(или «-» если награды нет)",
        parse_mode=ParseMode.HTML
    )

async def process_reward_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текст награды."""
    if update.effective_chat.type != "private":
        return

    state = context.user_data.get("reward_state")
    game_id = context.user_data.get("reward_game_id")
    if not state or not game_id:
        return

    text = update.message.text.strip()

    if state == "awaiting_civilian_text":
        if text != "-":
            context.user_data["civilian_reward_text"] = text
            context.user_data["reward_state"] = "awaiting_civilian_photo"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
                 InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
            ])
            await update.message.reply_text(
                f"🛡️ Награда для мирных: <b>{text}</b>\n"
                "Хотите прикрепить фото?",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            context.user_data["civilian_reward_text"] = ""
            context.user_data["civilian_reward_photo"] = None
            context.user_data["reward_state"] = "awaiting_mafia_text"
            await update.message.reply_text(
                "👑 <b>Этап 2/3:</b> Введите название награды для <b>МАФИИ</b>.\n"
                "(или «-» если награды нет)",
                parse_mode=ParseMode.HTML
            )

    elif state == "awaiting_mafia_text":
        if text != "-":
            context.user_data["mafia_reward_text"] = text
            context.user_data["reward_state"] = "awaiting_mafia_photo"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
                 InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
            ])
            await update.message.reply_text(
                f"👑 Награда для мафии: <b>{text}</b>\n"
                "Хотите прикрепить фото?",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            context.user_data["mafia_reward_text"] = ""
            context.user_data["mafia_reward_photo"] = None
            context.user_data["reward_state"] = "awaiting_neutral_text"
            await update.message.reply_text(
                "⚡ <b>Этап 3/3:</b> Введите название награды для <b>НЕЙТРАЛОВ</b>.\n"
                "(или «-» если награды нет)",
                parse_mode=ParseMode.HTML
            )

    elif state == "awaiting_neutral_text":
        if text != "-":
            context.user_data["neutral_reward_text"] = text
            context.user_data["reward_state"] = "awaiting_neutral_photo"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Да", callback_data="reward_photo_yes"),
                 InlineKeyboardButton("❌ Нет", callback_data="reward_photo_no")]
            ])
            await update.message.reply_text(
                f"⚡ Награда для нейтралов: <b>{text}</b>\n"
                "Хотите прикрепить фото?",
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        else:
            context.user_data["neutral_reward_text"] = ""
            context.user_data["neutral_reward_photo"] = None
            await save_all_rewards(update, context)

async def handle_reward_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимает фото для награды."""
    if update.effective_chat.type != "private":
        return

    state = context.user_data.get("reward_state")
    if not state or "photo" not in state:
        return

    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
        
        if state == "awaiting_civilian_photo":
            context.user_data["civilian_reward_photo"] = photo_id
            context.user_data["reward_state"] = "awaiting_mafia_text"
            await update.message.reply_text(
                "👑 <b>Этап 2/3:</b> Введите название награды для <b>МАФИИ</b>.\n"
                "(или «-» если награды нет)",
                parse_mode=ParseMode.HTML
            )
        elif state == "awaiting_mafia_photo":
            context.user_data["mafia_reward_photo"] = photo_id
            context.user_data["reward_state"] = "awaiting_neutral_text"
            await update.message.reply_text(
                "⚡ <b>Этап 3/3:</b> Введите название награды для <b>НЕЙТРАЛОВ</b>.\n"
                "(или «-» если награды нет)",
                parse_mode=ParseMode.HTML
            )
        elif state == "awaiting_neutral_photo":
            context.user_data["neutral_reward_photo"] = photo_id
            await save_all_rewards(update, context)
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте фото.")

async def save_all_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет все награды в БД."""
    game_id = context.user_data.get("reward_game_id")
    if not game_id:
        await update.message.reply_text("❌ Ошибка: игра не найдена.")
        return

    rewards = {
        "civilian": {
            "text": context.user_data.get("civilian_reward_text", ""),
            "photo": context.user_data.get("civilian_reward_photo", None)
        },
        "mafia": {
            "text": context.user_data.get("mafia_reward_text", ""),
            "photo": context.user_data.get("mafia_reward_photo", None)
        },
        "neutral": {
            "text": context.user_data.get("neutral_reward_text", ""),
            "photo": context.user_data.get("neutral_reward_photo", None)
        }
    }

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET rewards=? WHERE id=?", (json.dumps(rewards), game_id))
        await db.commit()

    # Очистка
    for key in ["reward_game_id", "reward_state", "civilian_reward_text", "civilian_reward_photo",
                "mafia_reward_text", "mafia_reward_photo", "neutral_reward_text", "neutral_reward_photo"]:
        context.user_data.pop(key, None)

    # Показываем сводку
    text = "✅ <b>НАГРАДЫ НАСТРОЕНЫ!</b>\n\n"
    text += f"🛡️ Мирные: {rewards['civilian']['text'] or 'Нет награды'}\n"
    text += f"👑 Мафия: {rewards['mafia']['text'] or 'Нет награды'}\n"
    text += f"⚡ Нейтралы: {rewards['neutral']['text'] or 'Нет награды'}\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)

# ---------- Вручение наград ----------
async def give_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет награды победителям в чат."""
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id, chat_id, rewards, status FROM games WHERE created_by=? AND (status='active' OR status='finished') ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ У вас нет игры.", reply_markup=MAIN_KEYBOARD)
            return
        
        game_id, chat_id, rewards_json, status = game[0]
        
        if status == 'active':
            await update.message.reply_text("⚠️ Игра ещё активна. Сначала остановите игру кнопкой «🛑 Остановить игру».", reply_markup=MAIN_KEYBOARD)
            return

        rewards = json.loads(rewards_json) if rewards_json else {}
        if not rewards:
            await update.message.reply_text("❌ Награды не были настроены. Нажмите «🎁 Настроить награды».", reply_markup=MAIN_KEYBOARD)
            return

        # Собираем победителей
        moves = await db.execute_fetchall(
            "SELECT username, role_side FROM game_moves WHERE game_id=?", (game_id,)
        )
        
        # Определяем победившую сторону
        alive_civilian = sum(1 for m in moves if m[1] == "civilian")
        alive_mafia = sum(1 for m in moves if m[1] == "mafia")
        alive_neutral = sum(1 for m in moves if m[1] == "neutral")

        # Логика победы: мафия побеждает если их столько же или больше мирных
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
        reward_photo = reward.get("photo", None)

        if not reward_text and not reward_photo:
            await update.message.reply_text(f"🏆 Победила сторона: {winning_side}, но награда не была настроена.", reply_markup=MAIN_KEYBOARD)
            return

        # Отправляем награду в чат
        side_names = {"civilian": "🛡️ МИРНЫЕ (Мстители)", "mafia": "👑 МАФИЯ (Кингпин)", "neutral": "⚡ НЕЙТРАЛЫ"}
        winner_list = "\n".join([f"@{w}" for w in winners])

        caption = (
            f"🏆 <b>ПОБЕДИТЕЛИ ИГРЫ #{game_id}!</b>\n\n"
            f"{side_names[winning_side]}\n\n"
            f"<b>Победители ({len(winners)}):</b>\n{winner_list}\n\n"
            f"🎁 <b>Награда:</b> {reward_text}"
        )

        try:
            if reward_photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=reward_photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode=ParseMode.HTML
                )
            await update.message.reply_text("🏆 Награды отправлены в чат!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка отправки: {e}", reply_markup=MAIN_KEYBOARD)

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
            await update.message.reply_text("❌ У вас нет активной игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, roles_json = game[0]
        roles = json.loads(roles_json)

        moves = await db.execute_fetchall(
            "SELECT cell_index, username, role_name, role_emoji, role_side, role_type FROM game_moves WHERE game_id=?", (game_id,)
        )
        taken = {m[0]: m for m in moves}

    text = f"🔍 <b>РАСКРЫТИЕ РОЛЕЙ — Игра #{game_id}</b>\n\n"

    text += "🛡️ <b>МИРНЫЕ:</b>\n"
    for i, role in enumerate(roles):
        if role["side"] == "civilian":
            if i in taken:
                _, uname, rname, remoji, rside, rtype = taken[i]
                type_desc = {"citizen": "Мирный", "sergeant": "Сержант", "lawyer": "Адвокат", "commissioner": "Комиссар", "doctor": "Доктор", "sniper": "Снайпер"}.get(rtype, "")
                text += f"  {remoji} {rname} ({type_desc}) → @{uname}\n"
            else:
                type_desc = {"citizen": "Мирный", "sergeant": "Сержант", "lawyer": "Адвокат", "commissioner": "Комиссар", "doctor": "Доктор", "sniper": "Снайпер"}.get(role["role_type"], "")
                text += f"  {role['emoji']} {role['name']} ({type_desc}) — свободна\n"

    text += "\n👑 <b>МАФИЯ:</b>\n"
    for i, role in enumerate(roles):
        if role["side"] == "mafia":
            if i in taken:
                _, uname, rname, remoji, rside, rtype = taken[i]
                type_desc = {"don": "Дон", "mistress": "Любовница"}.get(rtype, "")
                text += f"  {remoji} {rname} ({type_desc}) → @{uname}\n"
            else:
                type_desc = {"don": "Дон", "mistress": "Любовница"}.get(role["role_type"], "")
                text += f"  {role['emoji']} {role['name']} ({type_desc}) — свободна\n"

    text += "\n⚡ <b>НЕЙТРАЛЫ:</b>\n"
    for i, role in enumerate(roles):
        if role["side"] == "neutral":
            if i in taken:
                _, uname, rname, remoji, rside, rtype = taken[i]
                type_desc = {"suicide": "Самоубийца", "lucky": "Счастливчик", "maniac": "Маньяк", "kamikaze": "Камикадзе", "bum": "Бомж"}.get(rtype, "")
                text += f"  {remoji} {rname} ({type_desc}) → @{uname}\n"
            else:
                type_desc = {"suicide": "Самоубийца", "lucky": "Счастливчик", "maniac": "Маньяк", "kamikaze": "Камикадзе", "bum": "Бомж"}.get(role["role_type"], "")
                text += f"  {role['emoji']} {role['name']} ({type_desc}) — свободна\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)

# ---------- Статистика ----------
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall(
            "SELECT id, roles FROM games WHERE created_by=? AND status='active' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not game:
            await update.message.reply_text("❌ У вас нет активной игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, roles_json = game[0]
        roles = json.loads(roles_json)

        moves = await db.execute_fetchall(
            "SELECT COUNT(*) FROM game_moves WHERE game_id=?", (game_id,)
        )
        taken_count = moves[0][0] if moves else 0

    civilians = sum(1 for r in roles if r["side"] == "civilian")
    mafia = sum(1 for r in roles if r["side"] == "mafia")
    neutral = sum(1 for r in roles if r["side"] == "neutral")
    remaining = TOTAL_CELLS - taken_count

    text = (
        f"📊 <b>СТАТИСТИКА ИГРЫ #{game_id}</b>\n\n"
        f"<b>Всего ячеек:</b> {TOTAL_CELLS}\n"
        f"<b>Выбрано:</b> {taken_count}\n"
        f"<b>Осталось:</b> {remaining}\n\n"
        f"<b>По сторонам:</b>\n"
        f"🛡️ Мирные: {civilians}\n"
        f"👑 Мафия: {mafia}\n"
        f"⚡ Нейтралы: {neutral}\n"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)

# ---------- Обработчик меню ----------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Проверяем, не в режиме ли настройки наград
    reward_state = context.user_data.get("reward_state")
    if reward_state and "text" in reward_state:
        await process_reward_text(update, context)
        return

    if text == "🆕 Новая игра Мафия":
        context.user_data.clear()
        
        shuffled = random.sample(ROLES, len(ROLES))
        roles_json = json.dumps(shuffled)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "INSERT INTO games (chat_id, roles, status, created_by) VALUES (?, ?, ?, ?)",
                ("", roles_json, "pending", user_id)
            )
            game_id = cursor.lastrowid
            await db.commit()

        context.user_data["game_id"] = game_id
        context.user_data["state"] = "ready"

        civilians = sum(1 for r in shuffled if r["side"] == "civilian")
        mafia = sum(1 for r in shuffled if r["side"] == "mafia")
        neutral = sum(1 for r in shuffled if r["side"] == "neutral")

        await update.message.reply_text(
            f"🦸 Игра Мафия #{game_id} создана!\n\n"
            f"📊 <b>Состав:</b>\n"
            f"🛡️ Мирные: {civilians}\n"
            f"👑 Мафия: {mafia}\n"
            f"⚡ Нейтралы: {neutral}\n\n"
            "Нажмите «🎁 Настроить награды», чтобы установить призы.\n"
            "Затем «📤 Опубликовать игру».",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD
        )
        return

    if text == "📤 Опубликовать игру":
        context.user_data.clear()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await db.execute_fetchall(
                "SELECT id, roles FROM games WHERE created_by=? AND status='pending' ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            if not row:
                await update.message.reply_text("❌ Нет готовой игры.", reply_markup=MAIN_KEYBOARD)
                return
            game_id, roles_json = row[0]

        context.user_data["game_id"] = game_id
        context.user_data["state"] = "awaiting_chat"
        await update.message.reply_text(
            "Введите @username чата или канала для публикации игры.\n"
            "Бот должен быть администратором в этом чате/канале."
        )
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
            "🦸 <b>МАФИЯ MARVEL — ПРАВИЛА</b>\n\n"
            "<b>14 ролей (7×2):</b>\n\n"
            "🛡️ <b>МИРНЫЕ (7):</b>\n"
            "👤 Мирный житель (2)\n"
            "🏚️ Бомж\n"
            "🦸 Капитан Америка — Сержант\n"
            "⚖️ Сорвиголова — Адвокат\n"
            "🕵️ Ник Фьюри — Комиссар\n"
            "🎯 Булзай — Снайпер\n"
            "💉 Доктор Стрэндж — Доктор\n\n"
            "👑 <b>МАФИЯ (2):</b>\n"
            "👑 Кингпин — Дон\n"
            "🕷️ Чёрная Вдова — Любовница\n\n"
            "⚡ <b>НЕЙТРАЛЫ (5):</b>\n"
            "🃏 Дэдпул — Самоубийца\n"
            "🍀 Домино — Счастливчик\n"
            "🩸 Клетус Кесседи — Маньяк\n"
            "🎃 Зелёный Гоблин — Камикадзе\n\n"
            "<b>🏆 НАГРАДЫ:</b>\n"
            "1️⃣ Нажмите «🎁 Настроить награды»\n"
            "2️⃣ Введите текст и фото для каждой стороны\n"
            "3️⃣ После игры нажмите «🏆 Вручить награды»\n"
            "Бот отправит призы в чат!",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD
        )
        return

    state = context.user_data.get("state")
    if state == "awaiting_chat":
        await process_chat_input(update, context)
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=MAIN_KEYBOARD)

# ---------- Callback-обработчик ----------
async def grid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Обработка колбэков настройки наград
    if data in ("reward_photo_yes", "reward_photo_no"):
        state = context.user_data.get("reward_state")
        if data == "reward_photo_yes":
            await query.edit_message_text("📷 Отправьте фото для награды.")
        else:
            # Пропускаем фото
            if state == "awaiting_civilian_photo":
                context.user_data["civilian_reward_photo"] = None
                context.user_data["reward_state"] = "awaiting_mafia_text"
                await query.edit_message_text("👑 Введите название награды для МАФИИ (или «-»).")
            elif state == "awaiting_mafia_photo":
                context.user_data["mafia_reward_photo"] = None
                context.user_data["reward_state"] = "awaiting_neutral_text"
                await query.edit_message_text("⚡ Введите название награды для НЕЙТРАЛОВ (или «-»).")
            elif state == "awaiting_neutral_photo":
                context.user_data["neutral_reward_photo"] = None
                await query.edit_message_text("✅ Награды настроены!")
                # Сохраняем
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
                "SELECT chat_id, message_id, roles, status FROM games WHERE id=?", (game_id,)
            )
            if not game or game[0][3] != "active":
                await query.answer("Игра неактивна.", show_alert=True)
                return
            chat_id, message_id, roles_json, _ = game[0]
            roles = json.loads(roles_json)

            exists = await db.execute_fetchall(
                "SELECT 1 FROM game_moves WHERE game_id=? AND user_id=?", (game_id, user_id)
            )
            if exists:
                await query.answer("❗ Вы уже выбрали роль.", show_alert=True)
                return

            cell_taken = await db.execute_fetchall(
                "SELECT 1 FROM game_moves WHERE game_id=? AND cell_index=?", (game_id, cell_idx)
            )
            if cell_taken:
                await query.answer("⛔ Эта ячейка уже занята.", show_alert=True)
                return

            role = roles[cell_idx]
            role_name = role["name"]
            role_emoji = role["emoji"]
            role_side = role["side"]
            role_type = role["role_type"]
            role_desc = role["desc"]
            role_powers = role["powers"]

            try:
                await db.execute(
                    "INSERT INTO game_moves (game_id, user_id, username, cell_index, role_name, role_emoji, role_side, role_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (game_id, user_id, username, cell_idx, role_name, role_emoji, role_side, role_type)
                )
                await db.commit()
            except aiosqlite.IntegrityError as e:
                if "cell_index" in str(e):
                    await query.answer("⛔ Ячейка уже занята.", show_alert=True)
                else:
                    await query.answer("❗ Вы уже выбрали роль.", show_alert=True)
                return

            moves = await db.execute_fetchall(
                "SELECT cell_index, username FROM game_moves WHERE game_id=?", (game_id,)
            )
            taken_cells = {cell: uname for cell, uname in moves}

        if role_side == "civilian":
            side_text = "🛡️ Ты — МИРНЫЙ ЖИТЕЛЬ"
            team_text = "Ты на стороне МСТИТЕЛЕЙ. Вычисли мафию!"
        elif role_side == "mafia":
            side_text = "👑 Ты — МАФИЯ"
            team_text = "Ты на стороне КИНГПИНА. Убей всех мирных!"
        else:
            side_text = "⚡ Ты — НЕЙТРАЛ"
            team_text = "Ты сам за себя. Выживи любой ценой!"

        role_type_desc = {
            "citizen": "Мирный житель", "bum": "Бомж", "sergeant": "Сержант",
            "lawyer": "Адвокат", "commissioner": "Комиссар", "doctor": "Доктор",
            "sniper": "Снайпер", "don": "Дон мафии", "mistress": "Любовница",
            "suicide": "Самоубийца", "lucky": "Счастливчик", "maniac": "Маньяк",
            "kamikaze": "Камикадзе"
        }.get(role_type, "Неизвестно")

        role_message = (
            f"🎭 <b>ТВОЯ РОЛЬ:</b> {role_emoji} <b>{role_name}</b>\n"
            f"📋 <b>Тип:</b> {role_type_desc}\n\n"
            f"{side_text}\n"
            f"{team_text}\n\n"
            f"📜 <b>Описание:</b> {role_desc}\n\n"
            f"⚡ <b>Способности:</b> {role_powers}\n\n"
            f"<i>🤫 Не показывай это сообщение другим игрокам!</i>"
        )

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=role_message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки роли: {e}")
            await query.answer("❌ Сначала напишите боту в личку /start", show_alert=True)
            return

        await update_message_keyboard(
            context.application, chat_id, message_id,
            build_game_keyboard(game_id, taken_cells)
        )
        await query.answer(f"🎭 Роль получена! Проверьте личные сообщения.", show_alert=True)
        return

    if data.startswith("occupied:"):
        await query.answer("⛔ Эта ячейка уже занята.", show_alert=True)
        return

    await query.answer()

async def save_all_rewards_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохраняет награды при использовании колбэка."""
    game_id = context.user_data.get("reward_game_id")
    if not game_id:
        await update.callback_query.answer("❌ Ошибка: игра не найдена.")
        return

    rewards = {
        "civilian": {
            "text": context.user_data.get("civilian_reward_text", ""),
            "photo": context.user_data.get("civilian_reward_photo", None)
        },
        "mafia": {
            "text": context.user_data.get("mafia_reward_text", ""),
            "photo": context.user_data.get("mafia_reward_photo", None)
        },
        "neutral": {
            "text": context.user_data.get("neutral_reward_text", ""),
            "photo": context.user_data.get("neutral_reward_photo", None)
        }
    }

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET rewards=? WHERE id=?", (json.dumps(rewards), game_id))
        await db.commit()

    # Очистка
    for key in ["reward_game_id", "reward_state", "civilian_reward_text", "civilian_reward_photo",
                "mafia_reward_text", "mafia_reward_photo", "neutral_reward_text", "neutral_reward_photo"]:
        context.user_data.pop(key, None)

    await update.callback_query.answer("✅ Награды сохранены!")

# ---------- Публикация в чат/канал ----------
async def process_chat_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.text.strip()
    user_id = update.effective_user.id
    context.user_data.pop("state", None)

    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT id FROM games WHERE created_by=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (user_id,)
        )
        if not row:
            await update.message.reply_text("❌ Нет готовой игры.", reply_markup=MAIN_KEYBOARD)
            return
        game_id = row[0][0]

        try:
            msg = await context.bot.send_message(
                chat_id=chat,
                text=(
                    "🦸 <b>МАФИЯ MARVEL</b>\n\n"
                    "14 ролей! Мирные против Мафии.\n"
                    "Выбери ячейку и узнай свою роль.\n"
                    "Роль придёт тебе в личные сообщения. 🤫\n\n"
                    "<i>Кто твой союзник, а кто — враг?</i>"
                ),
                reply_markup=build_game_keyboard(game_id, {}),
                parse_mode=ParseMode.HTML
            )
            await db.execute(
                "UPDATE games SET chat_id=?, message_id=?, status='active' WHERE id=?",
                (chat, msg.message_id, game_id)
            )
            await db.commit()
            await update.message.reply_text("✅ Игра опубликована в чате/канале!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка публикации: {e}", reply_markup=MAIN_KEYBOARD)

# ---------- Остановка ----------
async def stop_active_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT id, chat_id, message_id FROM games WHERE created_by=? AND status='active'",
            (user_id,)
        )
        if not row:
            await update.message.reply_text("❌ У вас нет активных игр.", reply_markup=MAIN_KEYBOARD)
            return
        game_id, chat_id, message_id = row[0]
        await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
        await db.commit()

    if message_id:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None
            )
        except Exception:
            pass
    await update.message.reply_text(
        f"🏁 Игра #{game_id} остановлена!\n\n"
        "Теперь нажмите «🏆 Вручить награды», чтобы отправить призы победителям.",
        reply_markup=MAIN_KEYBOARD
    )

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
    logger.info("Бот Мафия Marvel запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
