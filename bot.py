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
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "mafia_final.db")

NIGHT_DURATION = 30
DAY_DURATION = 60
MIN_PLAYERS = 6
MAX_PLAYERS = 15

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🆕 Новая игра", "📤 Опубликовать"],
        ["🚀 Запустить игру", "🛑 Остановить"],
        ["🔍 Раскрыть роли", "📊 Статус"],
        ["🖼 Настроить фото", "❓ Помощь"]
    ],
    resize_keyboard=True
)

# ---------- База данных ----------
async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT DEFAULT '',
                message_id INTEGER DEFAULT 0,
                status TEXT DEFAULT 'lobby',
                phase TEXT DEFAULT 'lobby',
                created_by INTEGER NOT NULL,
                max_players INTEGER DEFAULT 10,
                phase_end REAL DEFAULT 0,
                night_photo TEXT DEFAULT '',
                day_photo TEXT DEFAULT '',
                vote_photo TEXT DEFAULT '',
                result_photo TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                role_name TEXT DEFAULT '',
                role_emoji TEXT DEFAULT '',
                role_side TEXT DEFAULT '',
                role_type TEXT DEFAULT '',
                is_alive INTEGER DEFAULT 1,
                has_voted INTEGER DEFAULT 0,
                vote_target INTEGER DEFAULT NULL,
                night_target INTEGER DEFAULT NULL,
                FOREIGN KEY (game_id) REFERENCES games(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_player_game ON players(game_id, user_id);
        """)
        for col in ["night_target", "has_voted", "night_photo", "day_photo", "vote_photo", "result_photo"]:
            try:
                if col in ["night_photo", "day_photo", "vote_photo", "result_photo"]:
                    await db.execute(f"ALTER TABLE games ADD COLUMN {col} TEXT DEFAULT ''")
                else:
                    await db.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
        await db.commit()

# ---------- ГАРАНТИРОВАННЫЕ РОЛИ ----------
def generate_roles(player_count: int) -> list:
    roles = [
        {"name": "Кингпин", "emoji": "👑", "side": "mafia", "type": "don", "desc": "Дон мафии.", "powers": "Решающий голос ночью (2 голоса)."},
        {"name": "Чёрная Вдова", "emoji": "🕷️", "side": "mafia", "type": "mistress", "desc": "Мафия.", "powers": "Голосует за жертву ночью."},
        {"name": "Ник Фьюри", "emoji": "🕵️", "side": "civilian", "type": "commissioner", "desc": "Комиссар.", "powers": "Ночью проверяет сторону игрока."},
        {"name": "Доктор Стрэндж", "emoji": "💉", "side": "civilian", "type": "doctor", "desc": "Доктор.", "powers": "Ночью лечит игрока."},
        {"name": "Клетус Кесседи", "emoji": "🩸", "side": "neutral", "type": "maniac", "desc": "Маньяк.", "powers": "Ночью убивает игрока."},
    ]
    extra_pool = [
        {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
        {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
        {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
        {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
        {"name": "Капитан Америка", "emoji": "🦸", "side": "civilian", "type": "sergeant", "desc": "Сержант.", "powers": "Двойной голос днём."},
        {"name": "Сорвиголова", "emoji": "⚖️", "side": "civilian", "type": "lawyer", "desc": "Адвокат.", "powers": "Ночью спасает от казни."},
        {"name": "Булзай", "emoji": "🎯", "side": "civilian", "type": "sniper", "desc": "Снайпер.", "powers": "Один выстрел."},
        {"name": "Дэдпул", "emoji": "🃏", "side": "neutral", "type": "suicide", "desc": "Самоубийца.", "powers": "Забирает убийцу с собой."},
        {"name": "Домино", "emoji": "🍀", "side": "neutral", "type": "lucky", "desc": "Счастливчик.", "powers": "Выживает раз."},
        {"name": "Зелёный Гоблин", "emoji": "🎃", "side": "neutral", "type": "kamikaze", "desc": "Камикадзе.", "powers": "Взрывает."},
        {"name": "Бомж", "emoji": "🏚️", "side": "neutral", "type": "bum", "desc": "Бездомный.", "powers": "Не лечится."},
    ]
    needed = player_count - len(roles)
    if needed > 0:
        extra = random.sample(extra_pool, min(needed, len(extra_pool)))
        roles.extend(extra)
        while len(roles) < player_count:
            roles.append({"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."})
    random.shuffle(roles)
    return roles[:player_count]

# ---------- Клавиатуры ----------
def build_vote_keyboard(game_id: int, players: list) -> InlineKeyboardMarkup:
    buttons = []
    for p in players:
        display = f"@{p[2]}"
        cb = f"v_{game_id}_{p[1]}"
        buttons.append([InlineKeyboardButton(display, callback_data=cb)])
    buttons.append([InlineKeyboardButton("⏭️ Пропустить", callback_data=f"vs_{game_id}")])
    return InlineKeyboardMarkup(buttons)

def build_night_keyboard(game_id: int, players: list, role_type: str, for_user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for p in players:
        if p[0] == for_user_id:
            continue
        display = f"@{p[2]}"
        cb = f"n_{game_id}_{role_type}_{p[0]}"
        buttons.append([InlineKeyboardButton(display, callback_data=cb)])
    buttons.append([InlineKeyboardButton("⏭️ Никого", callback_data=f"ns_{game_id}_{role_type}")])
    return InlineKeyboardMarkup(buttons)

# ---------- Команда /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "🤖 <b>МАФИЯ MARVEL — ФИНАЛЬНАЯ</b>\n\n"
        "✅ Все действия работают\n"
        "✅ Ник Фьюри получает результаты\n"
        "✅ Голосование публичное\n\n"
        "Нажмите «🆕 Новая игра».",
        reply_markup=MAIN_KEYBOARD, parse_mode=ParseMode.HTML
    )

# ---------- Меню ----------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return

    # Если в режиме настройки фото
    if context.user_data.get("photo_setup") and update.message.photo:
        await handle_photo_upload(update, context)
        return

    text = update.message.text.strip()
    user_id = update.effective_user.id
    logger.info(f"Меню: '{text}' от {user_id}")

    if text == "🆕 Новая игра":
        context.user_data.clear()
        context.user_data["state"] = "awaiting_players"
        await update.message.reply_text(f"👥 Введите количество игроков (от {MIN_PLAYERS} до {MAX_PLAYERS}):")
        return

    if text == "📤 Опубликовать":
        await publish_game(update, context)
        return

    if text == "🚀 Запустить игру":
        await start_game(update, context)
        return

    if text == "🛑 Остановить":
        await stop_game(update, context)
        return

    if text == "🔍 Раскрыть роли":
        await reveal_roles(update, context)
        return

    if text == "📊 Статус":
        await game_status(update, context)
        return

    if text == "🖼 Настроить фото":
        await setup_photos_start(update, context)
        return

    if text == "❓ Помощь":
        await update.message.reply_text("🤖 <b>МАФИЯ</b>\n\nСоздайте игру, опубликуйте, запустите.", parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)
        return

    state = context.user_data.get("state")
    if state == "awaiting_players":
        try:
            num = int(text)
            if num < MIN_PLAYERS or num > MAX_PLAYERS:
                raise ValueError
            roles = generate_roles(num)
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute(
                    "INSERT INTO games (chat_id, status, phase, created_by, max_players) VALUES ('', 'lobby', 'lobby', ?, ?)",
                    (user_id, num)
                )
                game_id = cursor.lastrowid
                await db.commit()
            context.user_data["game_id"] = game_id
            context.user_data["roles_pool"] = json.dumps(roles)
            context.user_data["state"] = "ready"
            await update.message.reply_text(f"🦸 Игра #{game_id} создана!\n👥 Игроков: {num}\n\nНажмите «📤 Опубликовать».", reply_markup=MAIN_KEYBOARD)
        except ValueError:
            await update.message.reply_text(f"❌ Введите число от {MIN_PLAYERS} до {MAX_PLAYERS}.")
        return
    elif state == "awaiting_chat":
        await process_chat(update, context, text)
        return

# ---------- Настройка фото ----------
async def setup_photos_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Сначала создайте игру.", reply_markup=MAIN_KEYBOARD)
        return
    context.user_data["photo_setup"] = "awaiting_night"
    await update.message.reply_text(
        "🖼 Этап 1/4: Отправьте фото для НОЧИ (или нажмите Пропустить)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_night")]])
    )

async def handle_photo_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    state = context.user_data.get("photo_setup")
    game_id = context.user_data.get("game_id")
    if not state or not game_id or not update.message.photo:
        return

    photo_id = update.message.photo[-1].file_id
    async with aiosqlite.connect(DB_PATH) as db:
        if state == "awaiting_night":
            await db.execute("UPDATE games SET night_photo=? WHERE id=?", (photo_id, game_id))
            context.user_data["photo_setup"] = "awaiting_day"
            await update.message.reply_text("✅ Ночь сохранена! Этап 2/4: ДЕНЬ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_day")]]))
        elif state == "awaiting_day":
            await db.execute("UPDATE games SET day_photo=? WHERE id=?", (photo_id, game_id))
            context.user_data["photo_setup"] = "awaiting_vote"
            await update.message.reply_text("✅ День сохранён! Этап 3/4: ГОЛОСОВАНИЕ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_vote")]]))
        elif state == "awaiting_vote":
            await db.execute("UPDATE games SET vote_photo=? WHERE id=?", (photo_id, game_id))
            context.user_data["photo_setup"] = "awaiting_result"
            await update.message.reply_text("✅ Голосование сохранено! Этап 4/4: РЕЗУЛЬТАТЫ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_result")]]))
        elif state == "awaiting_result":
            await db.execute("UPDATE games SET result_photo=? WHERE id=?", (photo_id, game_id))
            context.user_data.pop("photo_setup", None)
            await update.message.reply_text("✅ Все фото настроены!", reply_markup=MAIN_KEYBOARD)
        await db.commit()

async def publish_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Сначала создайте игру.", reply_markup=MAIN_KEYBOARD)
        return
    context.user_data["state"] = "awaiting_chat"
    await update.message.reply_text("Введите @username чата:")

async def process_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, chat: str):
    game_id = context.user_data.get("game_id")
    context.user_data.pop("state", None)
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall("SELECT max_players FROM games WHERE id=?", (game_id,))
        if not game:
            await update.message.reply_text("❌ Игра не найдена.")
            return
        try:
            msg = await context.bot.send_message(
                chat_id=chat,
                text=f"🤖 <b>МАФИЯ MARVEL</b>\n\n👥 Игроки (0/{game[0][0]}):\n\nНажмите кнопку, чтобы присоединиться!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Присоединиться", callback_data=f"j_{game_id}")
                ]]),
                parse_mode=ParseMode.HTML
            )
            await db.execute("UPDATE games SET chat_id=? WHERE id=?", (chat, game_id))
            await db.commit()
            await update.message.reply_text(f"✅ Опубликовано в {chat}!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=MAIN_KEYBOARD)

# ---------- Запуск игры ----------
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        async with aiosqlite.connect(DB_PATH) as db:
            row = await db.execute_fetchall(
                "SELECT id FROM games WHERE created_by=? AND status='lobby' ORDER BY id DESC LIMIT 1",
                (update.effective_user.id,)
            )
            if not row:
                await update.message.reply_text("❌ Нет игры.", reply_markup=MAIN_KEYBOARD)
                return
            game_id = row[0][0]
            context.user_data["game_id"] = game_id

    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall("SELECT max_players, chat_id FROM games WHERE id=?", (game_id,))
        if not game:
            await update.message.reply_text("❌ Игра не найдена.")
            return
        chat_id = game[0][1]
        if not chat_id:
            await update.message.reply_text("❌ Игра не опубликована.", reply_markup=MAIN_KEYBOARD)
            return
        players = await db.execute_fetchall("SELECT user_id, username FROM players WHERE game_id=?", (game_id,))
        if len(players) < MIN_PLAYERS:
            await update.message.reply_text(f"❌ Минимум {MIN_PLAYERS} игроков.", reply_markup=MAIN_KEYBOARD)
            return
        roles = json.loads(context.user_data.get("roles_pool", "[]"))
        if not roles:
            await update.message.reply_text("❌ Ошибка ролей.", reply_markup=MAIN_KEYBOARD)
            return
        random.shuffle(roles)
        for i, p in enumerate(players):
            if i < len(roles):
                role = roles[i]
                await db.execute(
                    "UPDATE players SET role_name=?, role_emoji=?, role_side=?, role_type=? WHERE game_id=? AND user_id=?",
                    (role["name"], role["emoji"], role["side"], role["type"], game_id, p[0])
                )
                try:
                    await context.bot.send_message(
                        chat_id=p[0],
                        text=f"🎭 <b>Твоя роль:</b> {role['emoji']} {role['name']}\n\n📜 {role['desc']}\n⚡ {role['powers']}\n\n<i>🤫 Не показывай никому!</i>",
                        parse_mode=ParseMode.HTML
                    )
                except:
                    pass
        await db.execute("UPDATE games SET status='active' WHERE id=?", (game_id,))
        await db.commit()

    player_list = "\n".join([f"@{p[1]}" for p in players])
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚀 <b>ИГРА НАЧАЛАСЬ!</b>\n\n👥 Игроки:\n{player_list}\n\nРоли отправлены в личку.\nПервая ночь через 10 секунд...",
            parse_mode=ParseMode.HTML
        )
    except:
        pass

    await update.message.reply_text("🚀 Игра запущена!", reply_markup=MAIN_KEYBOARD)
    asyncio.create_task(game_loop(context, game_id, chat_id))

# ---------- Игровой цикл ----------
async def game_loop(context, game_id, chat_id):
    await asyncio.sleep(10)
    round_num = 1
    while True:
        async with aiosqlite.connect(DB_PATH) as db:
            status = await db.execute_fetchall("SELECT status FROM games WHERE id=?", (game_id,))
            if not status or status[0][0] != "active":
                logger.info(f"Игра #{game_id} неактивна, выход из цикла")
                break

        logger.info(f"=== РАУНД {round_num}: НОЧЬ ===")
        await run_night(context, game_id, chat_id, round_num)
        if await check_win(context, game_id, chat_id):
            break

        logger.info(f"=== РАУНД {round_num}: ДЕНЬ ===")
        await run_day(context, game_id, chat_id, round_num)
        if await check_win(context, game_id, chat_id):
            break

        round_num += 1

async def run_night(context, game_id, chat_id, round_num):
    async with aiosqlite.connect(DB_PATH) as db:
        status = await db.execute_fetchall("SELECT status, night_photo FROM games WHERE id=?", (game_id,))
        if not status or status[0][0] != "active":
            return
        night_photo = status[0][1]
        await db.execute("UPDATE games SET phase='night' WHERE id=?", (game_id,))
        await db.execute("UPDATE players SET night_target=NULL, has_voted=0 WHERE game_id=?", (game_id,))
        await db.commit()
        alive = await db.execute_fetchall(
            "SELECT id, user_id, username, role_type FROM players WHERE game_id=? AND is_alive=1",
            (game_id,)
        )

    # Отправляем кнопки
    mafia_count = 0
    comm_count = 0
    doc_count = 0
    maniac_count = 0
    for p in alive:
        role_type = p[3]
        text = None
        kb = None
        if role_type in ("don", "mistress"):
            text = f"🌙 <b>НОЧЬ #{round_num}</b>\nВы мафия. Выберите жертву:"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "mafia", p[1])
            mafia_count += 1
        elif role_type == "commissioner":
            text = f"🕵️ <b>НОЧЬ #{round_num}</b>\nКого проверить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "commissioner", p[1])
            comm_count += 1
        elif role_type == "doctor":
            text = f"💉 <b>НОЧЬ #{round_num}</b>\nКого лечить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "doctor", p[1])
            doc_count += 1
        elif role_type == "maniac":
            text = f"🩸 <b>НОЧЬ #{round_num}</b>\nКого убить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "maniac", p[1])
            maniac_count += 1
        
        if text and kb:
            try:
                await context.bot.send_message(chat_id=p[1], text=text, reply_markup=kb, parse_mode=ParseMode.HTML)
                logger.info(f"Кнопки отправлены: {p[2]} ({role_type})")
            except Exception as e:
                logger.error(f"Ошибка отправки кнопок {p[2]}: {e}")

    logger.info(f"Ночные кнопки: мафия={mafia_count}, комиссар={comm_count}, доктор={doc_count}, маньяк={maniac_count}")

    # Объявление в чат
    if night_photo:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=night_photo,
                caption=f"🌙 <b>НОЧЬ #{round_num}</b>\nМафия и спецроли выбирают цели...\nУ вас {NIGHT_DURATION} секунд.",
                parse_mode=ParseMode.HTML)
        except:
            await context.bot.send_message(chat_id=chat_id,
                text=f"🌙 <b>НОЧЬ #{round_num}</b>\nУ вас {NIGHT_DURATION} секунд.", parse_mode=ParseMode.HTML)
    else:
        try:
            await context.bot.send_message(chat_id=chat_id,
                text=f"🌙 <b>НОЧЬ #{round_num}</b>\nМафия и спецроли выбирают цели...\nУ вас {NIGHT_DURATION} секунд.",
                parse_mode=ParseMode.HTML)
        except:
            pass

    await asyncio.sleep(NIGHT_DURATION)

    # Обработка результатов
    async with aiosqlite.connect(DB_PATH) as db:
        status = await db.execute_fetchall("SELECT status FROM games WHERE id=?", (game_id,))
        if not status or status[0][0] != "active":
            return

        players = await db.execute_fetchall(
            "SELECT user_id, username, role_type, night_target FROM players WHERE game_id=? AND is_alive=1 AND night_target IS NOT NULL",
            (game_id,)
        )
        logger.info(f"Ночные цели: {[(p[1], p[2], p[3]) for p in players]}")

        mafia_votes = {}
        doctor_target = None
        maniac_target = None
        commissioner_checks = {}

        for p in players:
            target_id = p[3]
            if target_id == 0:
                continue
            if p[2] in ("don", "mistress"):
                votes = 2 if p[2] == "don" else 1
                mafia_votes[target_id] = mafia_votes.get(target_id, 0) + votes
                logger.info(f"Мафия {p[1]} голосует за {target_id}")
            elif p[2] == "doctor":
                doctor_target = target_id
                logger.info(f"Доктор лечит {target_id}")
            elif p[2] == "maniac":
                maniac_target = target_id
                logger.info(f"Маньяк убивает {target_id}")
            elif p[2] == "commissioner":
                target = await db.execute_fetchall(
                    "SELECT role_side, username FROM players WHERE game_id=? AND user_id=?",
                    (game_id, target_id)
                )
                if target:
                    commissioner_checks[p[0]] = (target_id, target[0][0], target[0][1])
                    logger.info(f"Комиссар {p[1]} проверяет {target[0][1]} ({target[0][0]})")

        # Отправляем результаты комиссару
        for comm_id, (target_id, side, target_name) in commissioner_checks.items():
            side_text = {"civilian": "🛡️ Мирный", "mafia": "👑 Мафия", "neutral": "⚡ Нейтрал"}.get(side, "Неизвестно")
            try:
                await context.bot.send_message(
                    chat_id=comm_id,
                    text=f"🕵️ <b>Результат проверки:</b>\n@{target_name} — {side_text}",
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Результат комиссару {comm_id}: {target_name} = {side_text}")
            except Exception as e:
                logger.error(f"Ошибка отправки комиссару: {e}")

        # Определяем жертву мафии
        mafia_victim = None
        if mafia_votes:
            max_votes = max(mafia_votes.values())
            candidates = [tid for tid, v in mafia_votes.items() if v == max_votes]
            mafia_victim = random.choice(candidates)
            logger.info(f"Жертва мафии: {mafia_victim}")

        # Применяем убийства
        killed = set()
        if mafia_victim and mafia_victim != doctor_target:
            await db.execute("UPDATE players SET is_alive=0 WHERE game_id=? AND user_id=?", (game_id, mafia_victim))
            killed.add(mafia_victim)
        if maniac_target and maniac_target != doctor_target:
            await db.execute("UPDATE players SET is_alive=0 WHERE game_id=? AND user_id=?", (game_id, maniac_target))
            killed.add(maniac_target)

        await db.commit()

        killed_names = []
        for kid in killed:
            p = await db.execute_fetchall("SELECT username FROM players WHERE game_id=? AND user_id=?", (game_id, kid))
            if p:
                killed_names.append(f"@{p[0][0]}")

    # Объявления в чат
    if mafia_votes:
        try:
            await context.bot.send_message(chat_id=chat_id, text="👑 <b>Мафия</b> выбрала цель...", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)
        except:
            pass
    if doctor_target:
        try:
            await context.bot.send_message(chat_id=chat_id, text="💉 <b>Доктор</b> посетил одного из жителей...", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)
        except:
            pass
    if commissioner_checks:
        try:
            await context.bot.send_message(chat_id=chat_id, text="🕵️ <b>Комиссар</b> провёл расследование...", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)
        except:
            pass
    if maniac_target:
        try:
            await context.bot.send_message(chat_id=chat_id, text="🩸 <b>Маньяк</b> вышел на охоту...", parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.5)
        except:
            pass

    # Утро
    result = f"🌅 <b>УТРО (раунд {round_num})</b>\n"
    result += f"💀 Убиты: {', '.join(killed_names)}\n" if killed_names else "✨ Никто не пострадал.\n"
    try:
        await context.bot.send_message(chat_id=chat_id, text=result, parse_mode=ParseMode.HTML)
    except:
        pass

async def run_day(context, game_id, chat_id, round_num):
    async with aiosqlite.connect(DB_PATH) as db:
        status = await db.execute_fetchall("SELECT status, day_photo, vote_photo, result_photo FROM games WHERE id=?", (game_id,))
        if not status or status[0][0] != "active":
            return
        day_photo = status[0][1]
        vote_photo = status[0][2]
        result_photo = status[0][3]
        await db.execute("UPDATE games SET phase='day' WHERE id=?", (game_id,))
        await db.execute("UPDATE players SET has_voted=0, vote_target=NULL WHERE game_id=?", (game_id,))
        await db.commit()
        alive = await db.execute_fetchall(
            "SELECT id, user_id, username, role_emoji FROM players WHERE game_id=? AND is_alive=1",
            (game_id,)
        )

    players_list = [(p[0], p[1], p[2], p[3]) for p in alive]
    vote_message = None
    try:
        vote_message = await context.bot.send_message(
            chat_id=chat_id,
            text=f"☀️ <b>ДЕНЬ #{round_num}</b>\n\nГолосуйте за казнь! У вас {DAY_DURATION} секунд.\n\n<b>Голоса (0):</b>\nПока никто не голосовал",
            reply_markup=build_vote_keyboard(game_id, players_list),
            parse_mode=ParseMode.HTML
        )
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE games SET message_id=? WHERE id=?", (vote_message.message_id, game_id))
            await db.commit()
    except:
        pass

    await asyncio.sleep(DAY_DURATION)

    # Подсчёт голосов
    async with aiosqlite.connect(DB_PATH) as db:
        status = await db.execute_fetchall("SELECT status FROM games WHERE id=?", (game_id,))
        if not status or status[0][0] != "active":
            return
        votes = await db.execute_fetchall(
            "SELECT p.username, t.username FROM players p LEFT JOIN players t ON p.vote_target = t.user_id AND p.game_id = t.game_id WHERE p.game_id=? AND p.is_alive=1 AND p.vote_target IS NOT NULL",
            (game_id,)
        )

    vote_details = {}
    vote_count = {}
    for v in votes:
        target = v[1] if v[1] else "неизвестный"
        if target not in vote_details:
            vote_details[target] = []
        vote_details[target].append(v[0])
        async with aiosqlite.connect(DB_PATH) as db:
            target_row = await db.execute_fetchall("SELECT user_id FROM players WHERE game_id=? AND username=?", (game_id, target))
            if target_row:
                vote_count[target_row[0][0]] = vote_count.get(target_row[0][0], 0) + 1

    # Публикация голосов
    vote_announcement = f"☀️ <b>ГОЛОСОВАНИЕ (раунд {round_num})</b>\n\n"
    if vote_details:
        for target, voters in vote_details.items():
            vote_announcement += f"<b>За @{target}:</b> {', @'.join(voters)}\n"
    else:
        vote_announcement += "😴 Никто не проголосовал.\n"

    if vote_photo:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=vote_photo, caption=vote_announcement, parse_mode=ParseMode.HTML)
        except:
            await context.bot.send_message(chat_id=chat_id, text=vote_announcement, parse_mode=ParseMode.HTML)
    else:
        try:
            await context.bot.send_message(chat_id=chat_id, text=vote_announcement, parse_mode=ParseMode.HTML)
        except:
            pass

    # Итог
    if vote_count:
        max_votes = max(vote_count.values())
        candidates = [tid for tid, v in vote_count.items() if v == max_votes]
        if len(candidates) == 1:
            victim = candidates[0]
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE players SET is_alive=0 WHERE game_id=? AND user_id=?", (game_id, victim))
                await db.commit()
                victim_name = await db.execute_fetchall("SELECT username, role_name, role_emoji FROM players WHERE game_id=? AND user_id=?", (game_id, victim))
            if victim_name:
                result = f"☀️ <b>ИТОГ</b>\n\n💀 Казнён игрок ({max_votes} голосов)\nРоль: {victim_name[0][2]} {victim_name[0][1]}"
            else:
                result = f"☀️ <b>ИТОГ</b>\n\n💀 Казнён игрок ({max_votes} голосов)"
        else:
            result = f"☀️ <b>ИТОГ</b>\n\n🤝 Ничья! Никто не казнён."
    else:
        result = "☀️ <b>ИТОГ</b>\n\n😴 Никто не проголосовал."

    if result_photo:
        try:
            await context.bot.send_photo(chat_id=chat_id, photo=result_photo, caption=result, parse_mode=ParseMode.HTML)
        except:
            await context.bot.send_message(chat_id=chat_id, text=result, parse_mode=ParseMode.HTML)
    else:
        try:
            await context.bot.send_message(chat_id=chat_id, text=result, parse_mode=ParseMode.HTML)
        except:
            pass

    if vote_message:
        try:
            await vote_message.edit_reply_markup(reply_markup=None)
        except:
            pass

async def check_win(context, game_id, chat_id) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        alive = await db.execute_fetchall("SELECT role_side FROM players WHERE game_id=? AND is_alive=1", (game_id,))
        civilians = sum(1 for a in alive if a[0] == "civilian")
        mafia = sum(1 for a in alive if a[0] == "mafia")
        logger.info(f"Проверка победы: мирные={civilians}, мафия={mafia}")
        if mafia >= civilians and mafia > 0:
            await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
            await db.commit()
            try:
                await context.bot.send_message(chat_id=chat_id, text="👑 <b>МАФИЯ ПОБЕДИЛА!</b>", parse_mode=ParseMode.HTML)
            except:
                pass
            return True
        elif mafia == 0:
            await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
            await db.commit()
            try:
                await context.bot.send_message(chat_id=chat_id, text="🛡️ <b>МИРНЫЕ ПОБЕДИЛИ!</b>", parse_mode=ParseMode.HTML)
            except:
                pass
            return True
        return False

# ---------- Callback-обработчик (ПОЛНОСТЬЮ ПЕРЕПИСАН) ----------
async def grid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    user_id = user.id
    username = user.username or user.first_name

    logger.info(f"CALLBACK: data='{data}', user={username} ({user_id})")

    # Пропуск фото
    if data.startswith("ps_"):
        game_id = context.user_data.get("game_id")
        phase = data.split("_")[1]
        if phase == "night":
            context.user_data["photo_setup"] = "awaiting_day"
            await query.edit_message_text("Этап 2/4: Отправьте фото для ДНЯ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_day")]]))
        elif phase == "day":
            context.user_data["photo_setup"] = "awaiting_vote"
            await query.edit_message_text("Этап 3/4: Отправьте фото для ГОЛОСОВАНИЯ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_vote")]]))
        elif phase == "vote":
            context.user_data["photo_setup"] = "awaiting_result"
            await query.edit_message_text("Этап 4/4: Отправьте фото для РЕЗУЛЬТАТОВ", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ Пропустить", callback_data="ps_result")]]))
        elif phase == "result":
            context.user_data.pop("photo_setup", None)
            await query.edit_message_text("✅ Настройка фото завершена!")
        await query.answer()
        return

    # Присоединение: j_123
    if data.startswith("j_"):
        game_id = int(data.split("_")[1])
        logger.info(f"Присоединение к игре {game_id}")
        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT status, max_players FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "lobby":
                await query.answer("❌ Набор закончен.", show_alert=True)
                return
            exists = await db.execute_fetchall("SELECT 1 FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if exists:
                await query.answer("❌ Вы уже в игре!", show_alert=True)
                return
            count = await db.execute_fetchall("SELECT COUNT(*) FROM players WHERE game_id=?", (game_id,))
            if count[0][0] >= game[0][1]:
                await query.answer("❌ Все места заняты!", show_alert=True)
                return
            await db.execute("INSERT INTO players (game_id, user_id, username) VALUES (?, ?, ?)", (game_id, user_id, username))
            await db.commit()
            players = await db.execute_fetchall("SELECT username FROM players WHERE game_id=?", (game_id,))
            player_list = "\n".join([f"@{p[0]}" for p in players])
            current = len(players)
            try:
                await query.edit_message_text(
                    f"🤖 <b>МАФИЯ MARVEL</b>\n\n👥 Игроки ({current}/{game[0][1]}):\n{player_list}\n\nНажмите кнопку, чтобы присоединиться!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Присоединиться", callback_data=f"j_{game_id}")
                    ]]) if current < game[0][1] else None,
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            await query.answer(f"✅ Вы в игре! ({current}/{game[0][1]})", show_alert=True)
        return

    # Дневное голосование: v_123_456
    if data.startswith("v_") and not data.startswith("vs_"):
        parts = data.split("_")
        if len(parts) != 3:
            await query.answer("❌ Ошибка данных.", show_alert=True)
            return
        game_id = int(parts[1])
        target_id = int(parts[2])
        logger.info(f"Голосование: {username} -> {target_id}")

        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT phase, message_id, chat_id FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "day":
                await query.answer("❌ Сейчас не день.", show_alert=True)
                return
            voter = await db.execute_fetchall("SELECT is_alive, role_type, has_voted FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if not voter or not voter[0][0]:
                await query.answer("❌ Вы мертвы.", show_alert=True)
                return
            if voter[0][2]:
                await query.answer("❌ Вы уже голосовали!", show_alert=True)
                return

            await db.execute("UPDATE players SET has_voted=1, vote_target=? WHERE game_id=? AND user_id=?", (target_id, game_id, user_id))
            if voter[0][1] == "sergeant":
                await db.execute(
                    "INSERT INTO players (game_id, user_id, username, role_name, role_emoji, role_side, role_type, is_alive, has_voted, vote_target) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)",
                    (game_id, user_id, username, "Сержант (доп)", "🦸", "civilian", "sergeant", target_id)
                )
            await db.commit()

            votes = await db.execute_fetchall(
                "SELECT p.username, t.username FROM players p LEFT JOIN players t ON p.vote_target = t.user_id AND p.game_id = t.game_id WHERE p.game_id=? AND p.is_alive=1 AND p.vote_target IS NOT NULL",
                (game_id,)
            )
            vote_details = {}
            for v in votes:
                target = v[1] if v[1] else "неизвестный"
                if target not in vote_details:
                    vote_details[target] = []
                vote_details[target].append(v[0])

            vote_text = f"☀️ <b>ДЕНЬ</b>\n\nГолосуйте за казнь!\n\n<b>Голоса ({len(votes)}):</b>\n"
            if vote_details:
                for target, voters in vote_details.items():
                    vote_text += f"• За @{target}: {', @'.join(voters)}\n"
            else:
                vote_text += "Пока никто не голосовал\n"

            message_id = game[0][1]
            chat_id = game[0][2]
            if message_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=vote_text,
                        reply_markup=build_vote_keyboard(game_id, [(p[0], p[1], p[2], p[3]) for p in await db.execute_fetchall(
                            "SELECT id, user_id, username, role_emoji FROM players WHERE game_id=? AND is_alive=1", (game_id,)
                        )]),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.error(f"Ошибка обновления голосования: {e}")

        await query.answer("✅ Голос учтён!", show_alert=True)
        return

    # Пропуск голосования: vs_123
    if data.startswith("vs_"):
        game_id = int(data.split("_")[1])
        async with aiosqlite.connect(DB_PATH) as db:
            voter = await db.execute_fetchall("SELECT has_voted FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if voter and voter[0][0]:
                await query.answer("❌ Вы уже голосовали!", show_alert=True)
                return
            await db.execute("UPDATE players SET has_voted=1 WHERE game_id=? AND user_id=?", (game_id, user_id))
            await db.commit()
        await query.answer("Вы пропустили голосование.", show_alert=True)
        return

    # Ночные действия: n_123_mafia_456
    if data.startswith("n_") and not data.startswith("ns_"):
        parts = data.split("_")
        if len(parts) != 4:
            await query.answer("❌ Ошибка данных.", show_alert=True)
            return
        game_id = int(parts[1])
        role_type = parts[2]
        target_id = int(parts[3])
        logger.info(f"Ночное действие: {username} ({role_type}) -> {target_id}")

        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT phase FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "night":
                await query.answer("❌ Сейчас не ночь.", show_alert=True)
                return
            player = await db.execute_fetchall("SELECT role_type, is_alive FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if not player:
                await query.answer("❌ Вы не в игре.", show_alert=True)
                return
            if not player[0][1]:
                await query.answer("❌ Вы мертвы.", show_alert=True)
                return

            actual_role = player[0][0]
            logger.info(f"Фактическая роль: {actual_role}, запрошенная: {role_type}")

            # Проверка роли
            valid = False
            if role_type == "mafia" and actual_role in ("don", "mistress"):
                valid = True
            elif role_type == "commissioner" and actual_role == "commissioner":
                valid = True
            elif role_type == "doctor" and actual_role == "doctor":
                valid = True
            elif role_type == "maniac" and actual_role == "maniac":
                valid = True

            if not valid:
                await query.answer(f"❌ Вы не {role_type}! Ваша роль: {actual_role}", show_alert=True)
                return

            await db.execute("UPDATE players SET night_target=? WHERE game_id=? AND user_id=?", (target_id, game_id, user_id))
            await db.commit()

        await query.answer("✅ Действие выполнено!", show_alert=True)
        return

    # Пропуск ночного действия: ns_123_mafia
    if data.startswith("ns_"):
        parts = data.split("_")
        game_id = int(parts[1])
        role_type = parts[2]
        async with aiosqlite.connect(DB_PATH) as db:
            player = await db.execute_fetchall("SELECT role_type FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if player:
                actual_role = player[0][0]
                if (role_type == "mafia" and actual_role in ("don", "mistress")) or actual_role == role_type:
                    await db.execute("UPDATE players SET night_target=0 WHERE game_id=? AND user_id=?", (game_id, user_id))
                    await db.commit()
        await query.answer("Пропущено.", show_alert=True)
        return

    await query.answer()

# ---------- Остановка и просмотр ----------
async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Нет игры.", reply_markup=MAIN_KEYBOARD)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
        await db.commit()
    await update.message.reply_text("🛑 Игра остановлена.", reply_markup=MAIN_KEYBOARD)

async def reveal_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Нет игры.", reply_markup=MAIN_KEYBOARD)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        players = await db.execute_fetchall(
            "SELECT username, role_name, role_emoji, role_side, is_alive FROM players WHERE game_id=?", (game_id,)
        )
    text = "🔍 <b>РАСКРЫТИЕ РОЛЕЙ</b>\n\n"
    for side, header in [("civilian", "🛡️ МИРНЫЕ"), ("mafia", "👑 МАФИЯ"), ("neutral", "⚡ НЕЙТРАЛЫ")]:
        text += f"<b>{header}:</b>\n"
        for p in players:
            if p[3] == side:
                status = "✅" if p[4] else "💀"
                text += f"  {status} {p[2]} {p[1]} — @{p[0]}\n"
        text += "\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)

async def game_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Нет игры.", reply_markup=MAIN_KEYBOARD)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall("SELECT phase, status, max_players, chat_id FROM games WHERE id=?", (game_id,))
        if not game:
            await update.message.reply_text("❌ Игра не найдена.")
            return
        phase, status, max_p, chat_id = game[0]
        alive = await db.execute_fetchall("SELECT COUNT(*) FROM players WHERE game_id=? AND is_alive=1", (game_id,))
        total = await db.execute_fetchall("SELECT COUNT(*) FROM players WHERE game_id=?", (game_id,))
    await update.message.reply_text(
        f"📊 <b>СТАТУС #{game_id}</b>\n\nФаза: {phase}\nСтатус: {status}\nЧат: {chat_id}\nИгроков: {total[0][0]}/{max_p}\nЖивых: {alive[0][0]}",
        parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
    )

# ---------- Запуск ----------
async def post_init(application: Application):
    await init_db()

def main():
    if not BOT_TOKEN:
        raise ValueError("Не задан BOT_TOKEN")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_menu))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, handle_photo_upload))
    app.add_handler(CallbackQueryHandler(grid_callback))
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
