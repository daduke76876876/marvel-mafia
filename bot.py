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
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "./data"), "mafia_vote.db")

NIGHT_DURATION = 30
DAY_DURATION = 60
MIN_PLAYERS = 5
MAX_PLAYERS = 15

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🆕 Новая игра", "📤 Опубликовать"],
        ["🚀 Запустить игру", "🛑 Остановить"],
        ["🔍 Раскрыть роли", "📊 Статус"],
        ["❓ Помощь"]
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
                status TEXT DEFAULT 'lobby',
                phase TEXT DEFAULT 'lobby',
                created_by INTEGER NOT NULL,
                max_players INTEGER DEFAULT 10,
                phase_end REAL DEFAULT 0,
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
        # Миграции
        for col in ["night_target", "has_voted"]:
            try:
                await db.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
        await db.commit()

# ---------- Роли ----------
ROLES_POOL = [
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
    {"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует днём."},
    {"name": "Капитан Америка", "emoji": "🦸", "side": "civilian", "type": "sergeant", "desc": "Сержант.", "powers": "Двойной голос."},
    {"name": "Сорвиголова", "emoji": "⚖️", "side": "civilian", "type": "lawyer", "desc": "Адвокат.", "powers": "Спасает от казни."},
    {"name": "Ник Фьюри", "emoji": "🕵️", "side": "civilian", "type": "commissioner", "desc": "Комиссар.", "powers": "Проверяет игрока."},
    {"name": "Доктор Стрэндж", "emoji": "💉", "side": "civilian", "type": "doctor", "desc": "Доктор.", "powers": "Лечит игрока."},
    {"name": "Булзай", "emoji": "🎯", "side": "civilian", "type": "sniper", "desc": "Снайпер.", "powers": "Один выстрел."},
    {"name": "Кингпин", "emoji": "👑", "side": "mafia", "type": "don", "desc": "Дон мафии.", "powers": "Решающий голос."},
    {"name": "Чёрная Вдова", "emoji": "🕷️", "side": "mafia", "type": "mistress", "desc": "Любовница.", "powers": "Блокирует роль."},
    {"name": "Дэдпул", "emoji": "🃏", "side": "neutral", "type": "suicide", "desc": "Самоубийца.", "powers": "Забирает с собой."},
    {"name": "Домино", "emoji": "🍀", "side": "neutral", "type": "lucky", "desc": "Счастливчик.", "powers": "Выживает раз."},
    {"name": "Клетус Кесседи", "emoji": "🩸", "side": "neutral", "type": "maniac", "desc": "Маньяк.", "powers": "Убивает ночью."},
    {"name": "Зелёный Гоблин", "emoji": "🎃", "side": "neutral", "type": "kamikaze", "desc": "Камикадзе.", "powers": "Взрывает."},
    {"name": "Бомж", "emoji": "🏚️", "side": "neutral", "type": "bum", "desc": "Бездомный.", "powers": "Не лечится."},
]

# ---------- Клавиатуры ----------
def build_vote_keyboard(game_id: int, players: list) -> InlineKeyboardMarkup:
    buttons = []
    for p in players:
        display = f"@{p[2]}"
        cb = f"vote:{game_id}:{p[0]}:{p[1]}"
        buttons.append([InlineKeyboardButton(display, callback_data=cb)])
    buttons.append([InlineKeyboardButton("⏭️ Пропустить", callback_data=f"vote_skip:{game_id}")])
    return InlineKeyboardMarkup(buttons)

def build_night_keyboard(game_id: int, players: list, role_type: str, for_user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for p in players:
        if p[0] == for_user_id:
            continue
        display = f"@{p[2]}"
        cb = f"night:{game_id}:{role_type}:{p[0]}"
        buttons.append([InlineKeyboardButton(display, callback_data=cb)])
    buttons.append([InlineKeyboardButton("⏭️ Никого", callback_data=f"night_skip:{game_id}:{role_type}")])
    return InlineKeyboardMarkup(buttons)

# ---------- Команда /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    await update.message.reply_text(
        "🤖 <b>МАФИЯ MARVEL — ГОЛОСОВАНИЕ В ЧАТЕ</b>\n\n"
        "Бот управляет фазами автоматически.\n"
        "Голосование происходит прямо в чате!\n\n"
        "Нажмите «🆕 Новая игра».",
        reply_markup=MAIN_KEYBOARD, parse_mode=ParseMode.HTML
    )

# ---------- Меню ----------
async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    text = update.message.text.strip()
    user_id = update.effective_user.id

    logger.info(f"Меню: {text} от {user_id}")

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

    if text == "❓ Помощь":
        await update.message.reply_text(
            "🤖 <b>МАФИЯ — ГОЛОСОВАНИЕ В ЧАТЕ</b>\n\n"
            "1️⃣ Создайте игру (количество игроков)\n"
            "2️⃣ Опубликуйте в чат (@название)\n"
            "3️⃣ Игроки нажимают «Присоединиться»\n"
            "4️⃣ Нажмите «🚀 Запустить игру»\n\n"
            f"🌙 Ночь ({NIGHT_DURATION}с) — мафия голосует в личке\n"
            f"☀️ День ({DAY_DURATION}с) — все голосуют в чате\n"
            "🏆 Бот автоматически определяет победителя!",
            parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD
        )
        return

    state = context.user_data.get("state")
    if state == "awaiting_players":
        try:
            num = int(text)
            if num < MIN_PLAYERS or num > MAX_PLAYERS:
                raise ValueError
            roles = ROLES_POOL[:num]
            while len(roles) < num:
                roles.append({"name": "Мирный житель", "emoji": "👤", "side": "civilian", "type": "citizen", "desc": "Простой житель.", "powers": "Голосует."})
            roles = roles[:num]

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
            logger.info(f"Игра #{game_id} создана ведущим {user_id}, мест: {num}")
            await update.message.reply_text(
                f"🦸 Игра #{game_id} создана!\n👥 Мест: {num}\n\nНажмите «📤 Опубликовать» и укажите @чат.",
                reply_markup=MAIN_KEYBOARD
            )
        except ValueError:
            await update.message.reply_text(f"❌ Введите число от {MIN_PLAYERS} до {MAX_PLAYERS}.")
        return

    elif state == "awaiting_chat":
        await process_chat(update, context, text)
        return

# ---------- Публикация ----------
async def publish_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Сначала создайте игру.", reply_markup=MAIN_KEYBOARD)
        return
    context.user_data["state"] = "awaiting_chat"
    await update.message.reply_text("Введите @username чата (например, @mygroup):")

async def process_chat(update: Update, context: ContextTypes.DEFAULT_TYPE, chat: str):
    game_id = context.user_data.get("game_id")
    context.user_data.pop("state", None)

    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall("SELECT max_players FROM games WHERE id=?", (game_id,))
        if not game:
            await update.message.reply_text("❌ Игра не найдена.")
            return
        max_players = game[0][0]

        try:
            msg = await context.bot.send_message(
                chat_id=chat,
                text=f"🤖 <b>МАФИЯ MARVEL</b>\n\n👥 Игроки (0/{max_players}):\n\nНажмите кнопку, чтобы присоединиться!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Присоединиться", callback_data=f"join:{game_id}")
                ]]),
                parse_mode=ParseMode.HTML
            )
            # СОХРАНЯЕМ chat_id в БД
            await db.execute("UPDATE games SET chat_id=? WHERE id=?", (chat, game_id))
            await db.commit()
            logger.info(f"Игра #{game_id} опубликована в чат {chat}")
            await update.message.reply_text(f"✅ Игра опубликована в {chat}!", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"Ошибка публикации: {e}")
            await update.message.reply_text(f"❌ Ошибка: {e}\n\nУбедитесь, что бот добавлен в чат и является администратором.", reply_markup=MAIN_KEYBOARD)

# ---------- Запуск игры ----------
async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        # Попробуем найти последнюю игру в лобби
        async with aiosqlite.connect(DB_PATH) as db:
            row = await db.execute_fetchall(
                "SELECT id FROM games WHERE created_by=? AND status='lobby' ORDER BY id DESC LIMIT 1",
                (update.effective_user.id,)
            )
            if not row:
                await update.message.reply_text("❌ Нет игры. Создайте новую.", reply_markup=MAIN_KEYBOARD)
                return
            game_id = row[0][0]
            context.user_data["game_id"] = game_id

    logger.info(f"Запуск игры #{game_id}")

    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute_fetchall("SELECT max_players, chat_id FROM games WHERE id=?", (game_id,))
        if not game:
            await update.message.reply_text("❌ Игра не найдена.")
            return
        max_players, chat_id = game[0]

        if not chat_id:
            await update.message.reply_text("❌ Игра не опубликована. Сначала нажмите «📤 Опубликовать».", reply_markup=MAIN_KEYBOARD)
            return

        players = await db.execute_fetchall("SELECT user_id, username FROM players WHERE game_id=?", (game_id,))
        if len(players) < MIN_PLAYERS:
            await update.message.reply_text(f"❌ Минимум {MIN_PLAYERS} игроков. Сейчас: {len(players)}. Ждите ещё игроков.", reply_markup=MAIN_KEYBOARD)
            return

        # Распределяем роли
        roles = json.loads(context.user_data.get("roles_pool", "[]"))
        if not roles:
            await update.message.reply_text("❌ Ошибка ролей. Создайте игру заново.", reply_markup=MAIN_KEYBOARD)
            return

        random.shuffle(roles)
        for i, p in enumerate(players):
            if i < len(roles):
                role = roles[i]
                await db.execute(
                    "UPDATE players SET role_name=?, role_emoji=?, role_side=?, role_type=? WHERE game_id=? AND user_id=?",
                    (role["name"], role["emoji"], role["side"], role["type"], game_id, p[0])
                )
                # Отправляем роль в личку
                try:
                    await context.bot.send_message(
                        chat_id=p[0],
                        text=f"🎭 <b>Твоя роль:</b> {role['emoji']} {role['name']}\n\n📜 {role['desc']}\n⚡ {role['powers']}\n\n<i>🤫 Не показывай никому!</i>",
                        parse_mode=ParseMode.HTML
                    )
                    logger.info(f"Роль {role['name']} отправлена игроку {p[1]}")
                except Exception as e:
                    logger.error(f"Не удалось отправить роль {p[1]}: {e}")

        await db.execute("UPDATE games SET status='active' WHERE id=?", (game_id,))
        await db.commit()

    # Уведомление в чат
    player_list = "\n".join([f"@{p[1]}" for p in players])
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚀 <b>ИГРА НАЧАЛАСЬ!</b>\n\n👥 Игроки:\n{player_list}\n\nРоли отправлены в личку.\nПервая ночь через 10 секунд...",
            parse_mode=ParseMode.HTML
        )
        logger.info(f"Уведомление о старте отправлено в чат {chat_id}")
    except Exception as e:
        logger.error(f"Ошибка отправки в чат: {e}")
        await update.message.reply_text(f"❌ Не удалось отправить сообщение в чат {chat_id}. Проверьте права бота.", reply_markup=MAIN_KEYBOARD)
        return

    await update.message.reply_text("🚀 Игра запущена! Фазы начнутся автоматически.", reply_markup=MAIN_KEYBOARD)

    # Запускаем игровой цикл
    asyncio.create_task(game_loop(context, game_id, chat_id))

# ---------- Игровой цикл ----------
async def game_loop(context, game_id, chat_id):
    logger.info(f"Игровой цикл запущен для игры #{game_id} в чате {chat_id}")
    await asyncio.sleep(10)

    round_num = 1
    while True:
        async with aiosqlite.connect(DB_PATH) as db:
            status = await db.execute_fetchall("SELECT status FROM games WHERE id=?", (game_id,))
            if not status or status[0][0] != "active":
                logger.info(f"Игра #{game_id} завершена, цикл остановлен")
                break

        logger.info(f"Раунд {round_num}: Ночь")
        await run_night(context, game_id, chat_id)
        if await check_win(context, game_id, chat_id):
            break

        logger.info(f"Раунд {round_num}: День")
        await run_day(context, game_id, chat_id)
        if await check_win(context, game_id, chat_id):
            break

        round_num += 1

async def run_night(context, game_id, chat_id):
    logger.info(f"Начало ночи для игры #{game_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET phase='night', phase_end=? WHERE id=?", (time.time() + NIGHT_DURATION, game_id))
        await db.execute("UPDATE players SET has_voted=0, vote_target=NULL, night_target=NULL WHERE game_id=?", (game_id,))
        await db.commit()

        alive = await db.execute_fetchall(
            "SELECT id, user_id, username, role_type FROM players WHERE game_id=? AND is_alive=1",
            (game_id,)
        )

    # Отправляем сообщения для спецролей в личку
    for p in alive:
        role_type = p[3]
        if role_type in ("don", "mistress"):
            text = "🌙 <b>НОЧЬ</b>\nВы мафия. Выберите жертву:"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "mafia", p[1])
        elif role_type == "commissioner":
            text = "🕵️ <b>НОЧЬ</b>\nКого проверить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "commissioner", p[1])
        elif role_type == "doctor":
            text = "💉 <b>НОЧЬ</b>\nКого лечить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "doctor", p[1])
        elif role_type == "maniac":
            text = "🩸 <b>НОЧЬ</b>\nКого убить?"
            kb = build_night_keyboard(game_id, [(a[0], a[1], a[2]) for a in alive], "maniac", p[1])
        else:
            continue

        try:
            await context.bot.send_message(
                chat_id=p[1],
                text=text,
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка отправки ночного сообщения {p[2]}: {e}")

    # Уведомление в чат
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🌙 <b>НОЧЬ ({NIGHT_DURATION}с)</b>\nГород засыпает. Мафия и спецроли выбирают цели.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления в чат: {e}")

    await asyncio.sleep(NIGHT_DURATION)

    # Обработка результатов ночи
    async with aiosqlite.connect(DB_PATH) as db:
        players = await db.execute_fetchall(
            "SELECT id, user_id, username, role_type, night_target FROM players WHERE game_id=? AND is_alive=1",
            (game_id,)
        )

        mafia_votes = {}
        doctor_target = None
        maniac_target = None
        commissioner_targets = {}

        for p in players:
            if p[4]:  # night_target
                target_id = p[4]
                if p[3] in ("don", "mistress"):
                    votes = 2 if p[3] == "don" else 1
                    mafia_votes[target_id] = mafia_votes.get(target_id, 0) + votes
                elif p[3] == "doctor":
                    doctor_target = target_id
                elif p[3] == "maniac":
                    maniac_target = target_id
                elif p[3] == "commissioner":
                    commissioner_targets[p[1]] = target_id

        mafia_victim = None
        if mafia_votes:
            max_votes = max(mafia_votes.values())
            candidates = [tid for tid, v in mafia_votes.items() if v == max_votes]
            mafia_victim = random.choice(candidates)

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

    # Результаты ночи в чат
    result = "🌅 <b>УТРО</b>\n"
    result += f"💀 Убиты: {', '.join(killed_names)}\n" if killed_names else "✨ Никто не пострадал.\n"
    try:
        await context.bot.send_message(chat_id=chat_id, text=result, parse_mode=ParseMode.HTML)
    except:
        pass

    # Проверки комиссара
    for comm_id, target_id in commissioner_targets.items():
        async with aiosqlite.connect(DB_PATH) as db:
            target = await db.execute_fetchall("SELECT role_side, username FROM players WHERE game_id=? AND user_id=?", (game_id, target_id))
        if target:
            side = target[0][0]
            side_text = {"civilian": "🛡️ Мирный", "mafia": "👑 Мафия", "neutral": "⚡ Нейтрал"}.get(side, "Неизвестно")
            try:
                await context.bot.send_message(chat_id=comm_id, text=f"🕵️ Проверка: @{target[0][1]} — {side_text}", parse_mode=ParseMode.HTML)
            except:
                pass

async def run_day(context, game_id, chat_id):
    logger.info(f"Начало дня для игры #{game_id}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET phase='day', phase_end=? WHERE id=?", (time.time() + DAY_DURATION, game_id))
        await db.execute("UPDATE players SET has_voted=0, vote_target=NULL WHERE game_id=?", (game_id,))
        await db.commit()

        alive = await db.execute_fetchall(
            "SELECT id, user_id, username, role_emoji FROM players WHERE game_id=? AND is_alive=1",
            (game_id,)
        )

    players_list = [(p[0], p[1], p[2], p[3]) for p in alive]
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"☀️ <b>ДЕНЬ ({DAY_DURATION}с)</b>\n\nГолосуйте за казнь!",
            reply_markup=build_vote_keyboard(game_id, players_list),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка отправки голосования: {e}")

    await asyncio.sleep(DAY_DURATION)

    # Подсчёт голосов
    async with aiosqlite.connect(DB_PATH) as db:
        votes = await db.execute_fetchall(
            "SELECT vote_target FROM players WHERE game_id=? AND is_alive=1 AND vote_target IS NOT NULL",
            (game_id,)
        )

    vote_count = {}
    for v in votes:
        vote_count[v[0]] = vote_count.get(v[0], 0) + 1

    if vote_count:
        max_votes = max(vote_count.values())
        candidates = [tid for tid, v in vote_count.items() if v == max_votes]
        if len(candidates) == 1:
            victim = candidates[0]
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE players SET is_alive=0 WHERE game_id=? AND user_id=?", (game_id, victim))
                await db.commit()
                victim_name = await db.execute_fetchall("SELECT username FROM players WHERE game_id=? AND user_id=?", (game_id, victim))
            result = f"☀️ <b>ГОЛОСОВАНИЕ</b>\n\n💀 Казнён: @{victim_name[0][0]} ({max_votes} голосов)"
        else:
            result = f"☀️ <b>ГОЛОСОВАНИЕ</b>\n\n🤝 Ничья! Никто не казнён."
    else:
        result = "☀️ <b>ГОЛОСОВАНИЕ</b>\n\n😴 Никто не проголосовал."

    try:
        await context.bot.send_message(chat_id=chat_id, text=result, parse_mode=ParseMode.HTML)
    except:
        pass

async def check_win(context, game_id, chat_id) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        alive = await db.execute_fetchall("SELECT role_side FROM players WHERE game_id=? AND is_alive=1", (game_id,))
        civilians = sum(1 for a in alive if a[0] == "civilian")
        mafia = sum(1 for a in alive if a[0] == "mafia")

        if mafia >= civilians and mafia > 0:
            await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
            await db.commit()
            try:
                await context.bot.send_message(chat_id=chat_id, text="👑 <b>МАФИЯ ПОБЕДИЛА!</b>\nКингпин захватил город!", parse_mode=ParseMode.HTML)
            except:
                pass
            logger.info(f"Игра #{game_id}: Победила мафия")
            return True
        elif mafia == 0:
            await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
            await db.commit()
            try:
                await context.bot.send_message(chat_id=chat_id, text="🛡️ <b>МИРНЫЕ ПОБЕДИЛИ!</b>\nМстители спасли город!", parse_mode=ParseMode.HTML)
            except:
                pass
            logger.info(f"Игра #{game_id}: Победили мирные")
            return True
        return False

# ---------- Callback-обработчик ----------
async def grid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    user_id = user.id
    username = user.username or user.first_name

    logger.info(f"Callback: {data} от {username}")

    # Присоединение к игре
    if data.startswith("join:"):
        game_id = int(data.split(":")[1])
        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT status, max_players FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "lobby":
                await query.answer("❌ Набор закончен или игра уже началась.", show_alert=True)
                return

            exists = await db.execute_fetchall("SELECT 1 FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if exists:
                await query.answer("❌ Вы уже в игре!", show_alert=True)
                return

            count = await db.execute_fetchall("SELECT COUNT(*) FROM players WHERE game_id=?", (game_id,))
            if count[0][0] >= game[0][1]:
                await query.answer("❌ Все места заняты!", show_alert=True)
                return

            await db.execute(
                "INSERT INTO players (game_id, user_id, username) VALUES (?, ?, ?)",
                (game_id, user_id, username)
            )
            await db.commit()

            players = await db.execute_fetchall("SELECT username FROM players WHERE game_id=?", (game_id,))
            player_list = "\n".join([f"@{p[0]}" for p in players])
            current = len(players)
            max_p = game[0][1]

            try:
                await query.edit_message_text(
                    f"🤖 <b>МАФИЯ MARVEL</b>\n\n👥 Игроки ({current}/{max_p}):\n{player_list}\n\nНажмите кнопку, чтобы присоединиться!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Присоединиться", callback_data=f"join:{game_id}")
                    ]]) if current < max_p else None,
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Ошибка обновления сообщения: {e}")

            await query.answer(f"✅ Вы в игре! ({current}/{max_p})", show_alert=True)
            logger.info(f"Игрок {username} присоединился к игре #{game_id} ({current}/{max_p})")
        return

    # Голосование днём
    if data.startswith("vote:") and "vote_skip" not in data:
        parts = data.split(":")
        game_id = int(parts[1])
        target_id = int(parts[3])

        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT phase FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "day":
                await query.answer("❌ Сейчас не день.", show_alert=True)
                return

            voter = await db.execute_fetchall("SELECT is_alive, role_type FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if not voter or not voter[0][0]:
                await query.answer("❌ Вы мертвы.", show_alert=True)
                return

            await db.execute(
                "UPDATE players SET has_voted=1, vote_target=? WHERE game_id=? AND user_id=?",
                (target_id, game_id, user_id)
            )
            # Сержант = двойной голос
            if voter[0][1] == "sergeant":
                # Добавляем фиктивный голос
                await db.execute(
                    "INSERT INTO players (game_id, user_id, username, role_name, role_emoji, role_side, role_type, is_alive, has_voted, vote_target) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?)",
                    (game_id, user_id, username, "Сержант (доп.голос)", "🦸", "civilian", "sergeant", target_id)
                )
            await db.commit()

        await query.answer("✅ Голос учтён!", show_alert=True)
        logger.info(f"Игрок {username} проголосовал за {target_id}")
        return

    if data.startswith("vote_skip:"):
        await query.answer("Вы пропустили голосование.", show_alert=True)
        return

    # Ночные действия
    if data.startswith("night:") and "night_skip" not in data:
        parts = data.split(":")
        game_id = int(parts[1])
        role_type = parts[2]
        target_id = int(parts[3])

        async with aiosqlite.connect(DB_PATH) as db:
            game = await db.execute_fetchall("SELECT phase FROM games WHERE id=?", (game_id,))
            if not game or game[0][0] != "night":
                await query.answer("❌ Сейчас не ночь.", show_alert=True)
                return

            player = await db.execute_fetchall("SELECT role_type, is_alive FROM players WHERE game_id=? AND user_id=?", (game_id, user_id))
            if not player or not player[0][1] or player[0][0] != role_type:
                await query.answer("❌ Вы не можете этого сделать.", show_alert=True)
                return

            await db.execute(
                "UPDATE players SET night_target=? WHERE game_id=? AND user_id=?",
                (target_id, game_id, user_id)
            )
            await db.commit()

        await query.answer("✅ Действие выполнено!", show_alert=True)
        logger.info(f"Игрок {username} ({role_type}) выбрал цель {target_id}")
        return

    if data.startswith("night_skip:"):
        await query.answer("Пропущено.", show_alert=True)
        return

    await query.answer()

# ---------- Остановка и просмотр ----------
async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Нет активной игры.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE games SET status='finished' WHERE id=?", (game_id,))
        await db.commit()
    await update.message.reply_text("🛑 Игра остановлена.", reply_markup=MAIN_KEYBOARD)

async def reveal_roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    game_id = context.user_data.get("game_id")
    if not game_id:
        await update.message.reply_text("❌ Нет игры.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        players = await db.execute_fetchall(
            "SELECT username, role_name, role_emoji, role_side, is_alive FROM players WHERE game_id=?",
            (game_id,)
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
        await update.message.reply_text("❌ Нет игры.")
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
        f"📊 <b>СТАТУС #{game_id}</b>\n\n"
        f"Фаза: {phase}\nСтатус: {status}\n"
        f"Чат: {chat_id}\n"
        f"Игроков: {total[0][0]}/{max_p}\nЖивых: {alive[0][0]}",
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
    app.add_handler(CallbackQueryHandler(grid_callback))
    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
