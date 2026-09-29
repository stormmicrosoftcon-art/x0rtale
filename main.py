import sqlite3
import time
import uuid
import socket
import ssl
import urllib.error
from unixgram import Bot

# ================== КОНФИГУРАЦИЯ ==================
TOKEN = "2368706223:EAy3Kg3rAeDNdnFhh9qpgwS7ab8NiKvX"
bot = Bot(token=TOKEN)

CHECK_IMAGE_URL = "https://i.postimg.cc/7PNdsqNy/1000004716.jpg"
USDT_RUB_RATE = 92.50

ADMIN_USERNAME = "tael"
ADMIN_START_BALANCE = 10000.0
CHANNEL_URL = "https://unixgram.com/ch/xortale"
# ==================================================

# ----------------- БАЗА ДАННЫХ -----------------
conn = sqlite3.connect("xortale_v2.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
               CREATE TABLE IF NOT EXISTS users
               (
                   username
                   TEXT
                   PRIMARY
                   KEY,
                   last_chat_id
                   INTEGER,
                   balance_usdt
                   REAL
                   DEFAULT
                   0.0
               )
               """)

cursor.execute("""
               CREATE TABLE IF NOT EXISTS cheques
               (
                   code
                   TEXT
                   PRIMARY
                   KEY,
                   creator_username
                   TEXT,
                   target_username
                   TEXT,
                   total_amount
                   REAL,
                   amount_per_user
                   REAL,
                   total_activations
                   INTEGER,
                   activations_left
                   INTEGER,
                   is_cancelled
                   INTEGER
                   DEFAULT
                   0
               )
               """)

cursor.execute("""
               CREATE TABLE IF NOT EXISTS cheque_claims
               (
                   code
                   TEXT,
                   username
                   TEXT,
                   PRIMARY
                   KEY
               (
                   code,
                   username
               )
                   )
               """)
conn.commit()


def get_clean_username(user) -> str:
    if not user or not user.username:
        return ""
    return user.username.lstrip("@").strip().lower()


def get_or_register_user(user, chat_id: int):
    username = get_clean_username(user)
    if not username:
        return None

    cursor.execute("SELECT username, last_chat_id, balance_usdt FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()

    if not row:
        start_balance = ADMIN_START_BALANCE if username == ADMIN_USERNAME else 0.0
        cursor.execute(
            "INSERT INTO users (username, last_chat_id, balance_usdt) VALUES (?, ?, ?)",
            (username, chat_id, start_balance)
        )
        conn.commit()
        return {"username": username, "chat_id": chat_id, "balance_usdt": start_balance}

    current_balance = row[2]
    if row[1] != chat_id:
        cursor.execute("UPDATE users SET last_chat_id = ? WHERE username = ?", (chat_id, username))
        conn.commit()

    return {"username": row[0], "chat_id": chat_id, "balance_usdt": current_balance}


def build_main_menu(user_balance: float):
    return {
        "inline_keyboard": [
            [
                {"text": f"👛 Кошелёк • {user_balance:g} $", "callback_data": "menu_wallet"}
            ],
            [
                {"text": "📥 Пополнить", "callback_data": "menu_deposit"},
                {"text": "🧾 Чеки", "callback_data": "menu_cheques"}
            ],
            [
                {"text": "👥 P2P Маркет", "callback_data": "menu_p2p"}
            ],
            [
                {"text": "📢 Официальный канал", "callback_data": "menu_channel"}
            ]
        ]
    }


# ----------------- СТАРТ И МЕНЮ -----------------
@bot.message_handler(commands=["start"])
def start_cmd(message):
    username = get_clean_username(message.from_user)
    if not username:
        try:
            bot.send_message(
                message.chat.id,
                "⚠️ У вас не установлен публичный @username в профиле.\n"
                "Пожалуйста, установите username в настройках аккаунта, чтобы пользоваться xOrtale."
            )
        except Exception:
            pass
        return

    user = get_or_register_user(message.from_user, message.chat.id)
    text = (
        "⛵ xOrtale — это бот-кошелёк для получения, отправки, покупки и хранения криптовалюты в Unixgram.\n"
        "Обо всех возможностях читай в официальном канале"
    )
    try:
        bot.send_message(message.chat.id, text, reply_markup=build_main_menu(user["balance_usdt"]))
    except Exception as e:
        print(f"Ошибка в /start: {e}")


# ----------------- КОМАНДА /SEND -----------------
@bot.message_handler(commands=["send"])
def send_cheque_handler(message):
    username = get_clean_username(message.from_user)
    if not username:
        try:
            bot.send_message(message.chat.id, "❌ Для создания чеков установите @username в профиле.")
        except Exception:
            pass
        return

    user = get_or_register_user(message.from_user, message.chat.id)
    raw_text = message.text or getattr(message, "body", "") or ""
    parts = raw_text.strip().split()

    if len(parts) < 2:
        try:
            bot.send_message(
                message.chat.id,
                "⚠️ Примеры создания чеков:\n"
                "• /send 50 — обычный чек\n"
                "• /send 50 @username — персональный чек\n"
                "• /send 100 5 — мульти-чек на 5 человек"
            )
        except Exception:
            pass
        return

    try:
        total_amount = float(parts[1].replace(",", "."))
        if total_amount <= 0:
            raise ValueError()
    except ValueError:
        try:
            bot.send_message(message.chat.id, "❌ Неверно указана сумма. Пример: /send 50")
        except Exception:
            pass
        return

    target_username = None
    activations = 1

    if len(parts) >= 3:
        param = parts[2].strip()
        if param.startswith("@"):
            target_username = param.lstrip("@").lower()
            if target_username == username:
                try:
                    bot.send_message(message.chat.id, "❌ Нельзя выписать персональный чек самому себе.")
                except Exception:
                    pass
                return
        elif param.isdigit():
            activations = int(param)
            if activations < 1 or activations > 100:
                try:
                    bot.send_message(message.chat.id, "❌ Количество активаций должно быть от 1 до 100.")
                except Exception:
                    pass
                return

    if user["balance_usdt"] < total_amount:
        try:
            bot.send_message(
                message.chat.id,
                f"❌ Недостаточно средств на балансе!\nВаш баланс: {user['balance_usdt']:g} USDT"
            )
        except Exception:
            pass
        return

    amount_per_user = total_amount / activations
    rub_price = total_amount * USDT_RUB_RATE

    cursor.execute("UPDATE users SET balance_usdt = balance_usdt - ? WHERE username = ?", (total_amount, username))

    cheque_id = str(uuid.uuid4())[:8]
    cursor.execute("""
                   INSERT INTO cheques (code, creator_username, target_username, total_amount, amount_per_user,
                                        total_activations, activations_left)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   """, (cheque_id, username, target_username, total_amount, amount_per_user, activations, activations))
    conn.commit()

    btn_text = f"Получить ({activations}/{activations})" if activations > 1 else "Получить"
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": btn_text, "callback_data": f"claim_{cheque_id}"},
                {"text": "❌ Отменить", "callback_data": f"cancel_{cheque_id}"}
            ]
        ]
    }

    type_line = ""
    if target_username:
        type_line = f"\n🎯 Получатель: @{target_username}"
    elif activations > 1:
        type_line = f"\n👥 Мест: {activations} (по {amount_per_user:g}$)"

    caption = (
        f"🧾 Чек на {total_amount:g}$ — ({rub_price:,.2f} ₽)\n\n"
        f"👤 Отправитель: @{username}"
        f"{type_line}\n"
        f"Валюта: USDT"
    )

    try:
        bot.send_photo(
            chat_id=message.chat.id,
            photo=CHECK_IMAGE_URL,
            caption=caption,
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Ошибка отправки фото чека: {e}")
        cursor.execute("UPDATE users SET balance_usdt = balance_usdt + ? WHERE username = ?", (total_amount, username))
        cursor.execute("DELETE FROM cheques WHERE code = ?", (cheque_id,))
        conn.commit()


# ----------------- КЛИК «ПОЛУЧИТЬ» ПОД ФОТО -----------------
@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("claim_")))
def claim_cheque_handler(call):
    recipient_username = get_clean_username(call.from_user)
    if not recipient_username:
        try:
            bot.answer_callback_query(call.id, text="❌ Установите @username в настройках аккаунта!", show_alert=True)
        except Exception:
            pass
        return

    cheque_id = call.data.replace("claim_", "")

    cursor.execute("""
                   SELECT creator_username,
                          target_username,
                          total_amount,
                          amount_per_user,
                          total_activations,
                          activations_left,
                          is_cancelled
                   FROM cheques
                   WHERE code = ?
                   """, (cheque_id,))
    cheque = cursor.fetchone()

    if not cheque:
        try:
            bot.answer_callback_query(call.id, text="❌ Чек не найден!", show_alert=True)
        except Exception:
            pass
        return

    creator_username, target_username, total_amount, amount_per_user, total_activations, activations_left, is_cancelled = cheque

    if is_cancelled:
        try:
            bot.answer_callback_query(call.id, text="❌ Этот чек был отменен или удален создателем!", show_alert=True)
        except Exception:
            pass
        return

    if activations_left <= 0:
        try:
            bot.answer_callback_query(call.id, text="⚡ Все активации по этому чеку уже исчерпаны!", show_alert=True)
        except Exception:
            pass
        return

    if target_username and recipient_username != target_username:
        try:
            bot.answer_callback_query(call.id, text=f"🔒 Этот чек предназначен только для @{target_username}!",
                                      show_alert=True)
        except Exception:
            pass
        return

    cursor.execute("SELECT 1 FROM cheque_claims WHERE code = ? AND username = ?", (cheque_id, recipient_username))
    if cursor.fetchone():
        try:
            bot.answer_callback_query(call.id, text="⚠️ Вы уже забирали средства с этого чека!", show_alert=True)
        except Exception:
            pass
        return

    get_or_register_user(call.from_user, call.message.chat.id)
    cursor.execute("UPDATE users SET balance_usdt = balance_usdt + ? WHERE username = ?",
                   (amount_per_user, recipient_username))
    cursor.execute("INSERT INTO cheque_claims (code, username) VALUES (?, ?)", (cheque_id, recipient_username))

    new_left = activations_left - 1
    cursor.execute("UPDATE cheques SET activations_left = ? WHERE code = ?", (new_left, cheque_id))
    conn.commit()

    rub_claimed = amount_per_user * USDT_RUB_RATE
    try:
        bot.answer_callback_query(call.id, text=f"🎉 Получено: {amount_per_user:g}$ (~{rub_claimed:,.2f} ₽)!",
                                  show_alert=True)
    except Exception:
        pass

    if new_left > 0:
        updated_kb = {
            "inline_keyboard": [
                [
                    {"text": f"Получить ({new_left}/{total_activations})", "callback_data": f"claim_{cheque_id}"},
                    {"text": "❌ Отменить", "callback_data": f"cancel_{cheque_id}"}
                ]
            ]
        }
        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                          reply_markup=updated_kb)
        except Exception:
            pass
    else:
        if total_activations > 1:
            caption = (
                f"🧾 Раздача на {total_amount:g}$ завершена!\n\n"
                f"👤 Отправитель: @{creator_username}\n"
                f"👥 Все {total_activations} активаций были успешно разобраны.\n"
                f"Статус: Активирован"
            )
        else:
            caption = (
                f"🧾 Чек на {total_amount:g}$ — ({total_amount * USDT_RUB_RATE:,.2f} ₽)\n\n"
                f"👤 Отправитель: @{creator_username}\n"
                f"✅ Получил(а): @{recipient_username}\n"
                f"Статус: Активирован"
            )
        try:
            bot.edit_message_caption(
                caption=caption,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=None
            )
        except Exception:
            pass


# ----------------- КЛИК «ОТМЕНИТЬ» ПОД ФОТО -----------------
@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("cancel_")))
def cancel_cheque_handler(call):
    username = get_clean_username(call.from_user)
    cheque_id = call.data.replace("cancel_", "")

    cursor.execute("""
                   SELECT creator_username, amount_per_user, activations_left, is_cancelled
                   FROM cheques
                   WHERE code = ?
                   """, (cheque_id,))
    row = cursor.fetchone()

    if not row:
        try:
            bot.answer_callback_query(call.id, text="Чек не найден.", show_alert=True)
        except Exception:
            pass
        return

    creator_username, amount_per_user, activations_left, is_cancelled = row

    if is_cancelled or activations_left <= 0:
        try:
            bot.answer_callback_query(call.id, text="Чек уже неактивен.", show_alert=True)
        except Exception:
            pass
        return

    if username != creator_username:
        try:
            bot.answer_callback_query(call.id, text="Только создатель может отменить этот чек!", show_alert=True)
        except Exception:
            pass
        return

    refund_amount = amount_per_user * activations_left
    cursor.execute("UPDATE users SET balance_usdt = balance_usdt + ? WHERE username = ?", (refund_amount, username))
    cursor.execute("UPDATE cheques SET is_cancelled = 1, activations_left = 0 WHERE code = ?", (cheque_id,))
    conn.commit()

    try:
        bot.answer_callback_query(call.id, text=f"Чек отменен. Вам возвращено {refund_amount:g} USDT.", show_alert=True)
    except Exception:
        pass

    try:
        bot.edit_message_caption(
            caption=f"🧾 Чек отменен создателем.\nОстаток средств ({refund_amount:g} USDT) возвращен на баланс @{username}.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=None
        )
    except Exception:
        pass


# ----------------- ОБРАБОТКА МЕНЮ -----------------
@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("menu_")))
def menu_navigation_handler(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    username = get_clean_username(call.from_user)
    user = get_or_register_user(call.from_user, call.message.chat.id)

    if call.data == "menu_main":
        text = (
            "⛵ xOrtale — это бот-кошелёк для получения, отправки, покупки и хранения криптовалюты в Unixgram.\n"
            "Обо всех возможностях читай в официальном канале"
        )
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                  reply_markup=build_main_menu(user["balance_usdt"]))
        except Exception:
            pass

    elif call.data == "menu_wallet":
        rub = user["balance_usdt"] * USDT_RUB_RATE
        text = (
            f"👛 Кошелёк xOrtale\n\n"
            f"💵 Баланс: {user['balance_usdt']:g} USDT\n"
            f"🇷🇺 В рублях: ~{rub:,.2f} ₽\n"
            f"👤 Аккаунт: @{username}"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "◀️ Назад", "callback_data": "menu_main"}]
            ]
        }
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif call.data == "menu_cheques":
        text = (
            "🧾 Управление чеками\n\n"
            "Здесь вы можете создать новый чек для отправки в любой чат "
            "или просмотреть ваши активные чеки."
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "➕ Создать чек", "callback_data": "cheques_create_info"},
                    {"text": "📋 Активные чеки", "callback_data": "cheques_active_list"}
                ],
                [
                    {"text": "◀️ Назад в меню", "callback_data": "menu_main"}
                ]
            ]
        }
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif call.data == "menu_channel":
        text = (
            "📢 Официальный канал проекта:\n"
            f"{CHANNEL_URL}\n\n"
            "Перейдите по ссылке выше, чтобы читать свежие новости и обновления."
        )
        kb = {
            "inline_keyboard": [
                [{"text": "◀️ Назад", "callback_data": "menu_main"}]
            ]
        }
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif call.data == "menu_deposit":
        try:
            bot.answer_callback_query(call.id, text="Пополнение временно недоступно",
                                      show_alert=True)
        except Exception:
            pass

    elif call.data == "menu_p2p":
        try:
            bot.answer_callback_query(call.id, text="P2P Маркет находится в стадии разработки", show_alert=True)
        except Exception:
            pass


# ----------------- ПОДМЕНЮ: СОЗДАНИЕ И АКТИВНЫЕ ЧЕКИ -----------------
@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("cheques_")))
def cheques_sub_handler(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    username = get_clean_username(call.from_user)

    if call.data == "cheques_create_info":
        text = (
            "➕ Как создать чек:\n\n"
            "Отправьте команду в чат:\n"
            "• /send 10 — чек на 10$\n"
            "• /send 10 @username — персональный чек\n"
            "• /send 50 5 — мульти-раздача на 5 человек\n\n"
            "После отправки бот сгенерирует карточку с кнопкой получения!"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "◀️ Назад к чекам", "callback_data": "menu_cheques"}]
            ]
        }
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception:
            pass

    elif call.data == "cheques_active_list":
        cursor.execute("""
                       SELECT code, total_amount, activations_left, total_activations, target_username
                       FROM cheques
                       WHERE creator_username = ?
                         AND is_cancelled = 0
                         AND activations_left > 0
                       """, (username,))
        active_cheques = cursor.fetchall()

        if not active_cheques:
            text = "📋 У вас нет активных чеков."
            kb = {
                "inline_keyboard": [
                    [{"text": "◀️ Назад к чекам", "callback_data": "menu_cheques"}]
                ]
            }
            try:
                bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
            except Exception:
                pass
            return

        text = "📋 Ваши активные чеки:\nВыберите чек для управления (удалить / пересоздать):"
        buttons = []
        for code, total_amount, left, total, target in active_cheques:
            tag = f"для @{target}" if target else f"{left}/{total}"
            btn_title = f"🧾 {total_amount:g}$ ({tag})"
            buttons.append([{"text": btn_title, "callback_data": f"manage_chk_{code}"}])

        buttons.append([{"text": "◀️ Назад к чекам", "callback_data": "menu_cheques"}])
        try:
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                                  reply_markup={"inline_keyboard": buttons})
        except Exception:
            pass


# ----------------- УПРАВЛЕНИЕ КОНКРЕТНЫМ ЧЕКОМ -----------------
@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("manage_chk_")))
def manage_single_cheque(call):
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    username = get_clean_username(call.from_user)
    code = call.data.replace("manage_chk_", "")

    cursor.execute("""
                   SELECT total_amount, amount_per_user, total_activations, activations_left, target_username
                   FROM cheques
                   WHERE code = ?
                     AND creator_username = ?
                     AND is_cancelled = 0
                     AND activations_left > 0
                   """, (code, username))
    row = cursor.fetchone()

    if not row:
        try:
            bot.answer_callback_query(call.id, text="Чек не найден или уже не активен.", show_alert=True)
        except Exception:
            pass
        return

    total_amount, amount_per_user, total_activations, left, target = row
    target_str = f"@{target}" if target else "Любой"
    rem_amount = amount_per_user * left

    text = (
        f"🧾 Информация о чеке:\n\n"
        f"• Код: `{code}`\n"
        f"• Изначальная сумма: {total_amount:g} USDT\n"
        f"• Остаток к выплате: {rem_amount:g} USDT\n"
        f"• Осталось мест: {left} из {total_activations}\n"
        f"• Получатель: {target_str}"
    )

    kb = {
        "inline_keyboard": [
            [
                {"text": "🔄 Пересоздать", "callback_data": f"recreate_chk_{code}"},
                {"text": "🗑 Удалить", "callback_data": f"delete_chk_{code}"}
            ],
            [
                {"text": "◀️ К списку активных", "callback_data": "cheques_active_list"}
            ]
        ]
    }
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("delete_chk_")))
def delete_cheque_menu(call):
    username = get_clean_username(call.from_user)
    code = call.data.replace("delete_chk_", "")

    cursor.execute("""
                   SELECT amount_per_user, activations_left, is_cancelled
                   FROM cheques
                   WHERE code = ?
                     AND creator_username = ?
                   """, (code, username))
    row = cursor.fetchone()

    if not row or row[2] == 1 or row[1] <= 0:
        try:
            bot.answer_callback_query(call.id, text="Чек уже не активен.", show_alert=True)
        except Exception:
            pass
        return

    amount_per_user, left, _ = row
    refund = amount_per_user * left

    cursor.execute("UPDATE users SET balance_usdt = balance_usdt + ? WHERE username = ?", (refund, username))
    cursor.execute("UPDATE cheques SET is_cancelled = 1, activations_left = 0 WHERE code = ?", (code,))
    conn.commit()

    try:
        bot.answer_callback_query(call.id, text=f"Чек удален! Возвращено {refund:g} USDT.", show_alert=True)
    except Exception:
        pass
    cheques_sub_handler(call)


@bot.callback_query_handler(func=lambda call: bool(call.data and call.data.startswith("recreate_chk_")))
def recreate_cheque_menu(call):
    username = get_clean_username(call.from_user)
    old_code = call.data.replace("recreate_chk_", "")

    cursor.execute("""
                   SELECT amount_per_user, activations_left, total_activations, target_username, is_cancelled
                   FROM cheques
                   WHERE code = ?
                     AND creator_username = ?
                   """, (old_code, username))
    row = cursor.fetchone()

    if not row or row[4] == 1 or row[1] <= 0:
        try:
            bot.answer_callback_query(call.id, text="Не удалось пересоздать чек.", show_alert=True)
        except Exception:
            pass
        return

    amount_per_user, left, total_activations, target_username, _ = row
    rem_amount = amount_per_user * left

    cursor.execute("UPDATE cheques SET is_cancelled = 1, activations_left = 0 WHERE code = ?", (old_code,))

    new_code = str(uuid.uuid4())[:8]
    cursor.execute("""
                   INSERT INTO cheques (code, creator_username, target_username, total_amount, amount_per_user,
                                        total_activations, activations_left)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   """, (new_code, username, target_username, rem_amount, amount_per_user, left, left))
    conn.commit()

    try:
        bot.answer_callback_query(call.id, text="Чек успешно пересоздан!", show_alert=True)
    except Exception:
        pass

    rub_price = rem_amount * USDT_RUB_RATE
    btn_text = f"Получить ({left}/{left})" if left > 1 else "Получить"
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": btn_text, "callback_data": f"claim_{new_code}"},
                {"text": "❌ Отменить", "callback_data": f"cancel_{new_code}"}
            ]
        ]
    }

    type_line = f"\n🎯 Получатель: @{target_username}" if target_username else (f"\n👥 Мест: {left}" if left > 1 else "")
    caption = (
        f"🧾 Пересозданный чек на {rem_amount:g}$ — ({rub_price:,.2f} ₽)\n\n"
        f"👤 Отправитель: @{username}"
        f"{type_line}\n"
        f"Валюта: USDT"
    )

    try:
        bot.send_photo(
            chat_id=call.message.chat.id,
            photo=CHECK_IMAGE_URL,
            caption=caption,
            reply_markup=reply_markup
        )
    except Exception:
        pass


# ----------------- АДМИН-ВЫДАЧА (ТОЛЬКО ДЛЯ @TAEL) -----------------
@bot.message_handler(commands=["give"])
def admin_give_handler(message):
    username = get_clean_username(message.from_user)
    if username != ADMIN_USERNAME:
        return

    parts = (message.text or getattr(message, "body", "") or "").strip().split()
    if len(parts) < 3:
        try:
            bot.send_message(message.chat.id, "Использование: /give @username 100")
        except Exception:
            pass
        return

    target = parts[1].lstrip("@").lower()
    try:
        amount = float(parts[2].replace(",", "."))
        if amount <= 0:
            raise ValueError()
    except ValueError:
        try:
            bot.send_message(message.chat.id, "Неверная сумма.")
        except Exception:
            pass
        return

    cursor.execute("SELECT username FROM users WHERE username = ?", (target,))
    if not cursor.fetchone():
        try:
            bot.send_message(message.chat.id, f"❌ Пользователь @{target} еще ни разу не нажимал /start в боте.")
        except Exception:
            pass
        return

    cursor.execute("UPDATE users SET balance_usdt = balance_usdt + ? WHERE username = ?", (amount, target))
    conn.commit()
    try:
        bot.send_message(message.chat.id, f"✅ Вы успешно начислили {amount:g} USDT пользователю @{target}!")
    except Exception:
        pass


# ----------------- ЗАПУСК БОТА -----------------
if __name__ == "__main__":
    print("xOrtale bot is running...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=10)
        except (socket.timeout, TimeoutError, ssl.SSLError, urllib.error.URLError, ConnectionResetError):
            time.sleep(1)
        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(2)