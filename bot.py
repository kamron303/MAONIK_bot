import asyncio
import aiosqlite
import os
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # <- замените на свой токен или задайте BOT_TOKEN
CHANNEL = "@MAONIK_gift"
ADMIN_IDS = {7955777831, 1483826275}  # замените на своих админов
BOT_USERNAME = "Maonik_bot"  # используется в реферальных линках
DB_PATH = "users.db"

# === BOT & DISPATCHER ===
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# === FSM States ===
class CreateCheck(StatesGroup):
    waiting_amount = State()
    waiting_activations = State()

class AdminCreatePromo(StatesGroup):
    waiting_code = State()
    waiting_stars = State()
    waiting_activations = State()

# === DB INIT ===
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL DEFAULT 0,
            ref_id INTEGER,
            ref_bonus INTEGER DEFAULT 0,
            invited_count INTEGER DEFAULT 0
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS checks (
            check_id TEXT PRIMARY KEY,
            creator_id INTEGER,
            total_stars INTEGER,
            activations_left INTEGER,
            stars_per_activation INTEGER
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS check_activations (
            check_id TEXT,
            user_id INTEGER,
            PRIMARY KEY (check_id, user_id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            stars INTEGER,
            activations_left INTEGER
        )
        """)
        await db.commit()

# === Keyboards ===
def menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Мой профиль", callback_data="profile")],
        [InlineKeyboardButton(text="Заработать звезды", callback_data="earn")],
        [InlineKeyboardButton(text="Удвоить звёзды", url="https://t.me/LUDKA_1stars")]
    ])

def back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back")],
        [InlineKeyboardButton(text="Вывести звёзды", callback_data="withdraw")],
        [InlineKeyboardButton(text="Создать чек", callback_data="create_check")]
    ])

def withdraw_kb():
    return InlineKeyboardMarkup(inline_keyboard=[ 
        [
            InlineKeyboardButton(text="15⭐", callback_data="wd_15"),
            InlineKeyboardButton(text="25⭐", callback_data="wd_25")
        ],
        [
            InlineKeyboardButton(text="50⭐", callback_data="wd_50"),
            InlineKeyboardButton(text="100⭐", callback_data="wd_100")
        ],
        [InlineKeyboardButton(text="Назад", callback_data="profile")]
    ])

# === Helpers ===
async def is_subscribed(user_id):
    """Проверяет подписку на CHANNEL"""
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

async def ensure_user_in_db(user, ref_id=None):
    """
    Вставляет пользователя в БД если нет.
    Возвращает кортеж (created_new: bool)
    """
    user_id = user.id
    username = user.username or ""
    first_name = user.first_name or ""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        exists = await cur.fetchone()
        if exists:
            # Обновляем имя/юзернейм при необходимости
            await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (username, first_name, user_id))
            await db.commit()
            return False
        # Insert new
        if ref_id and ref_id != user_id:
            # проверим что рефер существет
            cur2 = await db.execute("SELECT user_id FROM users WHERE user_id=?", (ref_id,))
            if await cur2.fetchone():
                await db.execute(
                    "INSERT INTO users (user_id, username, first_name, ref_id) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, ref_id)
                )
                # увеличить invited_count и дать небольшой бонус реферу (опционально)
                await db.execute("UPDATE users SET invited_count = invited_count + 1 WHERE user_id=?", (ref_id,))
                # например, дать 1 звезду реферу
                await db.execute("UPDATE users SET balance = balance + 1 WHERE user_id=?", (ref_id,))
                await db.commit()
                return True
        # без реферала
        await db.execute("INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?)", (user_id, username, first_name))
        await db.commit()
        return True

# === Handlers ===

@dp.message(CommandStart())
async def cmd_start(message: Message):
    """
    /start [param]
    Возможные param:
      - claim_<check_id>
      - promo_<code>
      - <referrer_id>   (реферал по id)
    """
    user = message.from_user
    user_id = user.id
    args = message.get_args()  # aiogram 3: returns string after /start
    param = args.strip() if args else ""

    # handle referral by numeric id like /start 12345
    # or param starting with number
    ref_id = None
    if param and param.isdigit():
        try:
            ref_id = int(param)
        except Exception:
            ref_id = None

    # ensure user row exists and possibly register referral
    await ensure_user_in_db(user, ref_id=ref_id)

    # === handle claim link: claim_<check_id>
    if param and param.startswith("claim_"):
        check_id = param[6:]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT creator_id, activations_left, stars_per_activation FROM checks WHERE check_id=?", (check_id,))
            row = await cur.fetchone()
            if not row:
                await message.answer("❌ Чек не найден или недействителен.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            creator_id, activations_left, stars_per_activation = row
            if activations_left <= 0:
                await message.answer("❌ У этого чека закончились активации.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            # prevent duplicate activation
            cur2 = await db.execute("SELECT 1 FROM check_activations WHERE check_id=? AND user_id=?", (check_id, user_id))
            if await cur2.fetchone():
                await message.answer("❌ Вы уже активировали этот чек.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            # give stars, record activation, decrement activations_left
            await db.execute("INSERT INTO check_activations (check_id, user_id) VALUES (?, ?)", (check_id, user_id))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stars_per_activation, user_id))
            await db.execute("UPDATE checks SET activations_left = activations_left - 1 WHERE check_id=?", (check_id,))
            await db.commit()
            await message.answer(f"✅ Вы получили {stars_per_activation}⭐! Спасибо за активацию.")
            # notify creator if possible
            try:
                await bot.send_message(creator_id, f"🎉 Ваш чек {check_id} активирован пользователем @{user.username or user.first_name or user_id}. Осталось активаций: {max(0, activations_left-1)}")
            except Exception:
                pass
            await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
            return

    # handle promo code param: promo_<code>
    if param and param.startswith("promo_"):
        code = param[6:]
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT code, stars, activations_left FROM promo_codes WHERE code=?", (code,))
            row = await cur.fetchone()
            if not row:
                await message.answer("❌ Промокод не найден или недействителен.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            _, stars, activations_left = row
            if activations_left <= 0:
                await message.answer("❌ У этого промокода закончились активации.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            # prevent duplicate use: use check_activations table with key promo_<code>
            key = f"promo_{code}"
            cur2 = await db.execute("SELECT 1 FROM check_activations WHERE check_id=? AND user_id=?", (key, user_id))
            if await cur2.fetchone():
                await message.answer("❌ Вы уже активировали этот промокод.")
                await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
                return
            # give stars and decrement
            await db.execute("INSERT INTO check_activations (check_id, user_id) VALUES (?, ?)", (key, user_id))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stars, user_id))
            await db.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code=?", (code,))
            await db.commit()
            await message.answer(f"✅ Промокод применён — вы получили {stars}⭐!")
            await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
            return

    # обычный старт — проверка подписки
    if not await is_subscribed(user_id):
        await message.answer(
            "‼️ Вы не подписаны на канал ‼️\nПожалуйста, подпишитесь, чтобы продолжить.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Подписаться", url=f"https://t.me/{CHANNEL.lstrip('@')}")]
            ])
        )
        return

    await message.answer("Приветствуем вас в нашем боте!", reply_markup=menu_kb())

@dp.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await call.message.edit_text("Приветствуем вас в нашем боте!", reply_markup=menu_kb())
    await call.answer()

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    user_id = call.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT first_name, username, balance, invited_count FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            # should not happen, but ensure
            await ensure_user_in_db(call.from_user)
            first_name = call.from_user.first_name or ""
            username = call.from_user.username or ""
            balance = 0.0
            invited = 0
        else:
            first_name, username, balance, invited = row

    text = (
        f"👤 Профиль: {first_name} (@{username})\n"
        f"⭐ Баланс: {balance}\n"
        f"🤝 Пригласил: {invited}"
    )
    await call.message.edit_text(text, reply_markup=profile_kb())
    await call.answer()

@dp.callback_query(F.data == "earn")
async def earn(call: CallbackQuery):
    # Здесь можно добавить реальную логику — например задания/проверки
    text = "Здесь можно заработать звезды — пока что поделитесь ссылкой на бота или используйте промокоды."
    await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()

@dp.callback_query(F.data == "create_check")
async def cb_create_check(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Введите общую сумму звёзд для чека (целое число):")
    await state.set_state(CreateCheck.waiting_amount)
    await call.answer()

@dp.message(CreateCheck.waiting_amount)
async def create_check_amount(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("Введите целое положительное число для суммы.")
        return
    amount = int(txt)
    if amount <= 0:
        await message.answer("Значение должно быть больше нуля.")
        return
    await state.update_data(amount=amount)
    await message.answer("Сколько активаций будет у чека? Введите целое число (например: 3):")
    await state.set_state(CreateCheck.waiting_activations)

@dp.message(CreateCheck.waiting_activations)
async def create_check_activations(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("Введите целое число для активаций.")
        return
    activations = int(txt)
    data = await state.get_data()
    amount = data.get("amount", 0)
    if activations <= 0:
        await message.answer("Значение должно быть больше нуля.")
        return
    user_id = message.from_user.id
    # calculate stars per activation (integer division)
    stars_per_activation = amount // activations
    if stars_per_activation <= 0:
        # если слишком много активаций для малой суммы, все равно дадим 1 звезду и скорректируем: потребуем activations = amount
        stars_per_activation = 1
        activations = amount

    async with aiosqlite.connect(DB_PATH) as db:
        # check balance
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        balance = row[0] if row else 0
        if balance < amount:
            await message.answer("❌ У вас недостаточно звёзд для создания чека.")
            await state.clear()
            return
        # создаём чек
        check_id = uuid.uuid4().hex[:12]
        await db.execute(
            "INSERT INTO checks (check_id, creator_id, total_stars, activations_left, stars_per_activation) VALUES (?, ?, ?, ?, ?)",
            (check_id, user_id, amount, activations, stars_per_activation)
        )
        # списываем со счета творця
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        await db.commit()

    # формируем сообщение для создателя
    claim_link = f"https://t.me/{BOT_USERNAME}?start=claim_{check_id}"
    text = (
        "💳 Чек создан!\n"
        f"⭐ Звезд: {amount}\n\n"
        f"🔁 Доступных активаций: {activations}\n\n"
        f"🎁 За каждую активацию — {stars_per_activation} ⭐\n\n"
        "👇 Нажми кнопку ниже, чтобы поделиться чеком и чтобы другие забрали свои звёзды!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Забрать звёзды", url=claim_link)],
        [InlineKeyboardButton(text="Назад", callback_data="profile")]
    ])
    await message.answer(text, reply_markup=kb)
    await state.clear()

# === Withdraw callbacks (simple implementation) ===
@dp.callback_query(F.data.startswith("wd_"))
async def withdraw_amount(call: CallbackQuery):
    user_id = call.from_user.id
    pair = call.data.split("_")
    if len(pair) != 2:
        await call.answer()
        return
    try:
        amount = int(pair[1])
    except Exception:
        await call.answer()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        balance = row[0] if row else 0
        if balance < amount:
            await call.answer("У вас недостаточно звёзд.", show_alert=True)
            return
        # вычитаем и регистрируем (платежная логика не реализована)
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
        await db.commit()
    await call.message.answer(f"✅ Запрошен вывод {amount}⭐. Администратор свяжется с вами для подтверждения (реализуйте логику выплат).")
    await call.answer()

# === Admin panel ===
@dp.message(Command(commands=["admin"]))
async def admin_panel(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        # игнорируем (не отвечаем)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text="Отмена", callback_data="admin_cancel")]
    ])
    await message.answer("Админ-панель:", reply_markup=kb)

@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    await call.message.answer("Введите код промокода (например: SUPER2025):")
    await state.set_state(AdminCreatePromo.waiting_code)
    await call.answer()

@dp.message(AdminCreatePromo.waiting_code)
async def admin_create_promo_code(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code:
        await message.answer("Код не может быть пустым. Попробуйте ещё раз.")
        return
    await state.update_data(code=code)
    await message.answer("Сколько звёзд будет давать промокод? Введите целое число (например: 50).")
    await state.set_state(AdminCreatePromo.waiting_stars)

@dp.message(AdminCreatePromo.waiting_stars)
async def admin_create_promo_stars(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("Введите целое число для звёзд.")
        return
    stars = int(txt)
    if stars <= 0:
        await message.answer("Значение должно быть больше нуля.")
        return
    await state.update_data(stars=stars)
    await message.answer("Сколько активаций у промокода? Введите целое число (например: 100).")
    await state.set_state(AdminCreatePromo.waiting_activations)

@dp.message(AdminCreatePromo.waiting_activations)
async def admin_create_promo_activations(message: Message, state: FSMContext):
    txt = message.text.strip()
    if not txt.isdigit():
        await message.answer("Введите целое число для активаций.")
        return
    activations = int(txt)
    if activations <= 0:
        await message.answer("Значение должно быть больше нуля.")
        return
    data = await state.get_data()
    code = data.get("code")
    stars = data.get("stars")
    # insert promo code
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM promo_codes WHERE code=?", (code,))
        if await cur.fetchone():
            await message.answer("❌ Такой промокод уже существует. Операция отменена.")
            await state.clear()
            return
        await db.execute("INSERT INTO promo_codes (code, stars, activations_left) VALUES (?, ?, ?)", (code, stars, activations))
        await db.commit()

    link = f"https://t.me/{BOT_USERNAME}?start=promo_{code}"
    await message.answer(f"✅ Промокод создан:\nКод: {code}\nЗвёзд: {stars}\nАктиваций: {activations}\n\nСсылка: {link}")
    await state.clear()

@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        await call.answer()
        return
    await state.clear()
    await call.message.edit_text("Отменено.", reply_markup=menu_kb())
    await call.answer()

# === Fallback for text messages ===
@dp.message()
async def fallback(message: Message):
    await message.answer("Используйте меню — нажмите /start, чтобы открыть меню.", reply_markup=menu_kb())

# === Main ===
async def main():
    await init_db()
    # drop webhook if any and start polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass
    print("Bot started...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
