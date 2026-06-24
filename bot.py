import json
import logging
import os
import random
import string
import sys
import atexit
import msvcrt
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler

# ─── تنظیمات ───────────────────────────────────────────────
BOT_TOKEN    = "8767855704:AAEir-uPTLu3w1wdtKteDSBSeoT_XPStz4I"
CHANNEL_ID   = "@melkpelk1"
MINI_APP_URL = "https://melk-liard.vercel.app"
ADMIN_URL    = "https://melk-liard.vercel.app/admin.html"
ADVISOR_URL  = "https://melk-liard.vercel.app/advisor.html"
OWNER_URL    = "https://melk-liard.vercel.app/owner.html"
SUPABASE_URL = "https://vwbyxjhyrrclmpcbkpgj.supabase.co"
SUPABASE_KEY = "sb_publishable_0xEgmVLQ_fm_XrYMaiF8_Q_z4OyxWpC"
ADMIN_PASSWORD = "admin@1234"
ACCESS_PRICE = 0  # فعلاً رایگان

WAITING_PHONE = 1
WAITING_ADMIN_PASS = 2
WAITING_TICKET_SUBJECT = 3
WAITING_TICKET_MSG = 4
WAITING_REPORT_ID = 5
WAITING_REPORT_REASON = 6
WAITING_ADD_ADVISOR = 10
WAITING_REMOVE_ADVISOR = 11

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def html_esc(value):
    return str(value or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

_lock_file = None

def ensure_single_instance():
    """فقط یک نمونه از ربات اجازه اجرا دارد."""
    global _lock_file
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")
    _lock_file = open(lock_path, "w")
    try:
        msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("❌ یک نمونه از ربات هم‌اکنون در حال اجراست. ابتدا ترمینال‌های دیگر را ببندید.")
        sys.exit(1)
    _lock_file.write(str(os.getpid()))
    _lock_file.flush()
    atexit.register(_release_instance_lock)

def _release_instance_lock():
    global _lock_file
    if _lock_file:
        try:
            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            _lock_file.close()
        except OSError:
            pass
        _lock_file = None

# ─── Supabase ──────────────────────────────────────────────
def sb(method, path, data=None, params=""):
    url = f"{SUPABASE_URL}/rest/v1/{path}{params}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    body = json.dumps(data).encode() if data else None
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"Supabase error {method} {path}: {e}")
        return None

def upsert_user(user, phone=None):
    existing = sb("GET", "users", params=f"?id=eq.{user.id}&select=id")
    if existing:
        data = {"username": user.username or "", "full_name": user.full_name or ""}
        if phone:
            data["phone"] = phone
        sb("PATCH", "users", data, f"?id=eq.{user.id}")
    else:
        data = {"id": user.id, "username": user.username or "", "full_name": user.full_name or ""}
        if phone:
            data["phone"] = phone
        sb("POST", "users", data)

def update_phone(user_id, phone):
    sb("PATCH", "users", {"phone": phone}, f"?id=eq.{user_id}")

def get_user(user_id):
    res = sb("GET", "users", params=f"?id=eq.{user_id}&select=*")
    return res[0] if res else None

def get_wallet(user_id):
    u = get_user(user_id)
    return u.get("wallet", 0) if u else 0

def has_phone(user_id):
    u = get_user(user_id)
    return bool(u and u.get("phone"))

def is_banned(user_id):
    res = sb("GET", "banned_users", params=f"?user_id=eq.{user_id}&select=id")
    return bool(res)

def save_request(data, user_id, code, msg_id=None):
    sb("POST", "requests", {
        "unique_code": code, "user_id": user_id,
        "location": data.get("location"), "property_type": data.get("property_type"),
        "purpose": data.get("purpose"), "budget": data.get("budget"),
        "area": data.get("area"), "rooms": data.get("rooms"),
        "floor": data.get("floor"),
        "requirements": ", ".join(data.get("requirements", [])),
        "description": data.get("description", ""),
        "channel_message_id": msg_id,
        "status": "active",
    })

def get_request(code):
    res = sb("GET", "requests", params=f"?unique_code=eq.{code}&select=*")
    return res[0] if res else None

def mark_request_closed(code):
    sb("PATCH", "requests", {"status": "closed"}, f"?unique_code=eq.{code}")

def deduct_wallet(user_id, amount):
    if amount == 0:
        return True
    wallet = get_wallet(user_id)
    if wallet < amount:
        return False
    sb("PATCH", "users", {"wallet": wallet - amount}, f"?id=eq.{user_id}")
    sb("POST", "transactions", {
        "user_id": user_id, "amount": -amount,
        "type": "deduct", "description": "مشاهده اطلاعات درخواست"
    })
    return True

def get_advisor(user_id):
    """بررسی می‌کند آیا کاربر مشاور فعال است"""
    res = sb("GET", "advisors", params=f"?user_id=eq.{user_id}&select=*")
    return res[0] if res else None

def add_advisor(user_id):
    """یک کاربر را مشاور می‌کند"""
    existing = get_advisor(user_id)
    if existing:
        sb("PATCH", "advisors", {"is_active": True}, f"?user_id=eq.{user_id}")
    else:
        sb("POST", "advisors", {"user_id": user_id, "is_active": True})

def remove_advisor(user_id):
    """مشاور را غیرفعال می‌کند"""
    sb("PATCH", "advisors", {"is_active": False}, f"?user_id=eq.{user_id}")

def get_pending_otp(user_id):
    """آخرین OTP فعال کاربر را برمی‌گرداند"""
    now_iso = utc_now_iso()
    res = sb("GET", "otp_requests", params=f"?user_id=eq.{user_id}&expires_at=gt.{now_iso}&select=code&order=created_at.desc&limit=1")
    return res[0]["code"] if res else None

def save_advisor_request(advisor_id, request_code):
    """ثبت می‌کند که مشاور به درخواست دسترسی داشته"""
    sb("POST", "advisor_requests", {"advisor_id": advisor_id, "request_code": request_code})

def get_or_create_conversation(request_code, advisor_id, owner_id):
    """یک مکالمه می‌سازد یا مکالمه موجود را برمی‌گرداند"""
    existing = sb("GET", "conversations", params=f"?request_code=eq.{request_code}&advisor_id=eq.{advisor_id}&select=id")
    if existing:
        return existing[0]["id"]
    res = sb("POST", "conversations", {
        "request_code": request_code,
        "advisor_id": advisor_id,
        "owner_id": owner_id,
        "status": "active"
    })
    return res[0]["id"] if res else None

def save_message(conv_id, sender_id, content, msg_type="text"):
    """یک پیام ذخیره می‌کند"""
    sb("POST", "messages", {
        "conversation_id": conv_id,
        "sender_id": sender_id,
        "content": content,
        "type": msg_type,
        "is_read": False
    })

# ─── کیبوردها ─────────────────────────────────────────────
def kbd_main():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 ثبت درخواست ملک", web_app=WebAppInfo(url=MINI_APP_URL))],
        [KeyboardButton("👤 پروفایل من")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("🚨 ثبت شکایت")],
        [KeyboardButton("🎫 تیکت پشتیبانی"), KeyboardButton("ℹ️ راهنما")],
    ], resize_keyboard=True)

def kbd_phone():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📱 اشتراک‌گذاری شماره تلفن", request_contact=True)],
        [KeyboardButton("✏️ تایپ شماره تلفن")],
    ], resize_keyboard=True, one_time_keyboard=True)

def kbd_cancel():
    return ReplyKeyboardMarkup([[KeyboardButton("❌ انصراف")]], resize_keyboard=True, one_time_keyboard=True)

def kbd_for_user(context):
    return kbd_admin() if context.user_data.get("is_admin") else kbd_main()

def kbd_admin():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 پنل مدیریت", web_app=WebAppInfo(url=ADMIN_URL))],
        [KeyboardButton("📋 ثبت درخواست ملک", web_app=WebAppInfo(url=MINI_APP_URL))],
        [KeyboardButton("👤 پروفایل من")],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("🚨 ثبت شکایت")],
        [KeyboardButton("🎫 تیکت پشتیبانی"), KeyboardButton("ℹ️ راهنما")],
    ], resize_keyboard=True)

# ─── انصراف / بازگشت به منو ───────────────────────────────
async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_for_user(context))
    return ConversationHandler.END

# ─── /start ───────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)

    if is_banned(user.id):
        await update.message.reply_text("🚫 حساب شما مسدود شده است.")
        return ConversationHandler.END

    is_admin = context.user_data.get("is_admin", False)

    if has_phone(user.id):
        wallet = get_wallet(user.id)
        await update.message.reply_text(
            f"سلام {user.first_name} عزیز\n\n"
            f"به ملک‌یاب خوش آمدی\n\n"
            f"موجودی: {wallet:,} تومان",
            reply_markup=kbd_admin() if is_admin else kbd_main()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"سلام {user.first_name} عزیز\n\nبه ملک‌یاب خوش آمدی\n\n"
        "برای شروع، شماره تلفن خود را ثبت کنید:",
        reply_markup=kbd_phone()
    )
    return WAITING_PHONE

# ─── ثبت شماره ────────────────────────────────────────────
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    phone = update.message.contact.phone_number
    if not phone.startswith("+"): phone = "+" + phone
    update_phone(user.id, phone)
    await update.message.reply_text(
        f"شماره {phone} ثبت شد!\n\nحالا می‌توانید از پلتفرم استفاده کنید.",
        reply_markup=kbd_main()
    )
    return ConversationHandler.END

async def handle_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if text == "✏️ تایپ شماره تلفن":
        await update.message.reply_text(
            "شماره تلفن را وارد کنید:\nمثال: 09123456789",
            reply_markup=ReplyKeyboardRemove()
        )
        return WAITING_PHONE

    if text.startswith("+98"): digits = "0" + text[3:].replace(" ","")
    elif text.startswith("98") and len(text)==12: digits = "0" + text[2:]
    else: digits = text.replace(" ","").replace("-","")

    if not (digits.startswith("09") and len(digits)==11 and digits.isdigit()):
        await update.message.reply_text("شماره معتبر نیست. دوباره وارد کنید:\nمثال: 09123456789")
        return WAITING_PHONE

    update_phone(user.id, digits)
    await update.message.reply_text(f"شماره {digits} ثبت شد!", reply_markup=kbd_main())
    return ConversationHandler.END

# ─── /admin ───────────────────────────────────────────────
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("رمز عبور ادمین را وارد کنید:", reply_markup=kbd_cancel())
    return WAITING_ADMIN_PASS

async def check_admin_pass(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_main())
        return ConversationHandler.END
    if text == ADMIN_PASSWORD:
        context.user_data["is_admin"] = True
        await update.message.reply_text(
            "ورود موفق!\n\nروی پنل مدیریت بزنید.",
            reply_markup=kbd_admin()
        )
        return ConversationHandler.END
    await update.message.reply_text("رمز اشتباه است. دوباره تلاش کنید:")
    return WAITING_ADMIN_PASS

# ─── تیکت پشتیبانی ───────────────────────────────────────
async def ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("موضوع تیکت را بنویسید:", reply_markup=kbd_cancel())
    return WAITING_TICKET_SUBJECT

async def ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_main())
        return ConversationHandler.END
    context.user_data["ticket_subject"] = update.message.text
    await update.message.reply_text("متن پیام خود را بنویسید:", reply_markup=kbd_cancel())
    return WAITING_TICKET_MSG

async def ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_main())
        return ConversationHandler.END
    user = update.effective_user
    sb("POST", "tickets", {
        "user_id": user.id,
        "subject": context.user_data.get("ticket_subject", "—"),
        "message": update.message.text,
        "status": "open"
    })
    await update.message.reply_text("تیکت شما ثبت شد!\n\nتیم پشتیبانی پاسخ می‌دهد.", reply_markup=kbd_main())
    return ConversationHandler.END

# ─── ثبت شکایت ────────────────────────────────────────────
async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "کد یونیک آگهی مربوطه را وارد کنید (فرمت: MLK-XXXXX):",
        reply_markup=kbd_cancel()
    )
    return WAITING_REPORT_ID

async def report_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_main())
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    req = get_request(code)
    if not req:
        await update.message.reply_text("کد یافت نشد. دوباره وارد کنید یا انصراف دهید:")
        return WAITING_REPORT_ID
    context.user_data["report_code"] = code
    context.user_data["reported_user_id"] = req["user_id"]
    await update.message.reply_text("دلیل شکایت خود را بنویسید:", reply_markup=kbd_cancel())
    return WAITING_REPORT_REASON

async def report_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_main())
        return ConversationHandler.END
    user = update.effective_user
    sb("POST", "reports", {
        "reporter_id": user.id,
        "reported_id": context.user_data.get("reported_user_id"),
        "unique_code": context.user_data.get("report_code"),
        "reason": update.message.text,
        "status": "pending"
    })
    await update.message.reply_text("شکایت شما ثبت شد!\n\nتیم مدیریت بررسی خواهد کرد.", reply_markup=kbd_main())
    return ConversationHandler.END

# ─── OTP و مشاور ──────────────────────────────────────────
async def otp_poller(context):
    """
    هر ۵ ثانیه اجرا می‌شود و OTP های جدید را می‌فرستد
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = sb("GET", "otp_requests", params=f"?created_at=gt.{cutoff}&sent=is.null&select=*")
    if not rows:
        return
    for row in rows:
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    f"🔐 کد ورود به پنل:\n\n"
                    f"<b>{row['code']}</b>\n\n"
                    f"این کد ۲ دقیقه معتبر است.\n"
                    f"به هیچ‌کس ندهید."
                ),
                parse_mode="HTML",
            )
            sb("PATCH", "otp_requests", {"sent": True}, f"?id=eq.{row['id']}")
        except Exception as e:
            logger.error(f"OTP send error: {e}")

async def advisor_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور /advisor — لینک پنل مشاور را ارسال می‌کند"""
    user = update.effective_user
    advisor = get_advisor(user.id)

    if not advisor or not advisor.get("is_active"):
        await update.message.reply_text(
            "⛔ دسترسی مشاور ندارید.\n\n"
            "برای فعال‌سازی با ادمین تماس بگیرید."
        )
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🏛 ورود به پنل مشاور", web_app=WebAppInfo(url=ADVISOR_URL))
    ]])
    await update.message.reply_text(
        f"سلام {user.first_name} عزیز\n\n"
        "پنل مشاور آماده است.\n"
        "برای ورود روی دکمه زیر بزنید:",
        reply_markup=keyboard
    )

async def add_advisor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ادمین می‌تواند با /add_advisor یک کاربر را مشاور کند"""
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    await update.message.reply_text(
        "آی‌دی تلگرام کاربر را وارد کنید:\n"
        "(عدد — مثلاً: 123456789)",
        reply_markup=kbd_cancel()
    )
    return WAITING_ADD_ADVISOR

async def do_add_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ انصراف":
        await update.message.reply_text("انصراف.", reply_markup=kbd_admin())
        return ConversationHandler.END
    try:
        uid = int(text)
    except ValueError:
        await update.message.reply_text("عدد معتبر وارد کنید:")
        return WAITING_ADD_ADVISOR

    user = get_user(uid)
    if not user:
        await update.message.reply_text(f"کاربر {uid} در سیستم نیست. اول باید ربات را استارت زده باشد.")
        return WAITING_ADD_ADVISOR

    add_advisor(uid)
    name = user.get("full_name") or user.get("username") or str(uid)
    await update.message.reply_text(
        f"✅ {name} به عنوان مشاور فعال شد.\n\n"
        f"این کاربر می‌تواند با دستور /advisor وارد پنل شود.",
        reply_markup=kbd_admin()
    )
    return ConversationHandler.END

async def remove_advisor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("is_admin"):
        await update.message.reply_text("❌ دسترسی ندارید.")
        return
    await update.message.reply_text("آی‌دی تلگرام مشاور را وارد کنید:", reply_markup=kbd_cancel())
    return WAITING_REMOVE_ADVISOR

async def do_remove_advisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ انصراف":
        await update.message.reply_text("انصراف.", reply_markup=kbd_admin())
        return ConversationHandler.END
    try:
        uid = int(text)
    except ValueError:
        await update.message.reply_text("عدد معتبر وارد کنید:")
        return WAITING_REMOVE_ADVISOR
    remove_advisor(uid)
    await update.message.reply_text(f"✅ دسترسی مشاور {uid} غیرفعال شد.", reply_markup=kbd_admin())
    return ConversationHandler.END

async def notify_owner_new_message(context, conv_id, advisor_id):
    """
    بعد از ذخیره پیام توسط مشاور در پنل وب، اطلاع به صاحب آگهی
    """
    conv = sb("GET", "conversations", params=f"?id=eq.{conv_id}&select=*")
    if not conv:
        return
    c = conv[0]
    owner_id = c["owner_id"]
    code = c["request_code"]

    advisor = get_user(advisor_id)
    adv_name = (advisor.get("full_name") or advisor.get("username") or "مشاور") if advisor else "مشاور"

    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                f"💬 پیام جدید از مشاور\n\n"
                f"مشاور {adv_name} برای آگهی {code} پیام داده است.\n\n"
                "برای پاسخ از پنل مشاور یا ربات استفاده کنید."
            )
        )
    except Exception as e:
        logger.error(f"Notify owner error: {e}")

# ─── دریافت فرم Mini App ──────────────────────────────────
async def handle_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("🚫 حساب شما مسدود شده است.")
        return
    if not has_phone(user.id):
        await update.message.reply_text("ابتدا شماره تلفن خود را ثبت کنید.", reply_markup=kbd_phone())
        return

    data = json.loads(update.message.web_app_data.data)

    if data.get("type") == "admin_action":
        await handle_admin_action(update, context, data)
        return

    code = "MLK-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

    reqs = data.get("requirements", [])
    reqs_txt = "\n".join([f"  • {html_esc(r)}" for r in reqs]) if reqs else "  • —"
    desc = (data.get("description") or "").strip()
    desc_block = f"\n\n📝 <b>توضیحات</b>\n{html_esc(desc)}" if desc else ""

    channel_msg = (
        "🏛 <b>درخواست جدید</b>  ·  ملک‌یاب\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📋 <b>مشخصات ملک</b>\n"
        f"  📍 موقعیت: <b>{html_esc(data.get('location'))}</b>\n"
        f"  🏗 نوع: {html_esc(data.get('property_type'))}\n"
        f"  🎯 هدف: {html_esc(data.get('purpose'))}\n\n"
        "💰 <b>بودجه و متراژ</b>\n"
        f"  💵 بودجه: {html_esc(data.get('budget'))}\n"
        f"  📐 متراژ: {html_esc(data.get('area'))}\n"
        f"  🛏 اتاق: {html_esc(data.get('rooms'))}\n"
        f"  🏢 طبقه: {html_esc(data.get('floor'))}\n\n"
        f"✅ <b>الزامات</b>\n{reqs_txt}"
        f"{desc_block}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔖 کد درخواست: <code>{code}</code>"
    )

    try:
        bot_username = (await context.bot.get_me()).username
        inline_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📲 مشاهده اطلاعات متقاضی",
                url=f"https://t.me/{bot_username}?start=code_{code}"
            )
        ]])

        sent = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=channel_msg,
            parse_mode="HTML",
            reply_markup=inline_kb
        )

        save_request(data, user.id, code, sent.message_id)

        await update.message.reply_text(
            f"درخواست شما ثبت و در کانال منتشر شد!\n\n"
            f"کد یونیک: {code}\n\n"
            "هر بار که مشاوری اطلاعات شما را مشاهده کند، به شما اطلاع داده می‌شود.",
            reply_markup=kbd_admin() if context.user_data.get("is_admin") else kbd_main()
        )
    except Exception as e:
        logger.error(f"Channel error: {e}")
        await update.message.reply_text(f"خطا در انتشار: {e}")

# ─── پردازش اکشن ادمین ────────────────────────────────────
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE, data: dict):
    action = data.get("action")
    if action == "ban_user":
        sb("POST", "banned_users", {
            "user_id": data.get("user_id"),
            "reason": data.get("reason","بن توسط ادمین"),
            "banned_by": update.effective_user.id
        })
        await update.message.reply_text(f"کاربر {data.get('user_id')} بن شد.")
    elif action == "unban_user":
        sb("DELETE", "banned_users", params=f"?user_id=eq.{data.get('user_id')}")
        await update.message.reply_text(f"بن کاربر {data.get('user_id')} رفع شد.")
    elif action == "charge_wallet":
        uid = data.get("user_id")
        amount = data.get("amount", 0)
        u = get_user(uid)
        cur = u.get("wallet", 0) if u else 0
        sb("PATCH", "users", {"wallet": cur + amount}, f"?id=eq.{uid}")
        sb("POST", "transactions", {"user_id": uid, "amount": amount, "type": "charge", "description": "شارژ توسط ادمین"})
        await update.message.reply_text(f"{amount:,} تومان به کاربر شارژ شد.")
    elif action == "reply_ticket":
        ticket_id = data.get("ticket_id")
        uid = data.get("user_id")
        reply = data.get("reply","")
        sb("PATCH", "tickets", {"admin_reply": reply, "status": "closed"}, f"?id=eq.{ticket_id}")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"پاسخ پشتیبانی ملک‌یاب:\n\n{reply}"
            )
        except: pass
        await update.message.reply_text("پاسخ ارسال شد.")

# ─── /start با کد (از دکمه کانال) ────────────────────────
async def start_with_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)

    args = context.args
    if args and args[0].startswith("code_"):
        code = args[0][5:]
        await ask_request_validity(update, context, user, code)
        return ConversationHandler.END

    return await start(update, context)

# ─── پرسیدن از درخواست‌دهنده که آگهی معتبره یا نه ────────
async def ask_request_validity(update, context, advisor_user, code):
    req = get_request(code)
    if not req:
        await update.message.reply_text(f"کد {code} یافت نشد.")
        return

    if req.get("status") == "closed":
        await update.message.reply_text(
            f"این آگهی منقضی شده و دیگر معتبر نیست.\n\n"
            f"کد: {code}"
        )
        return

    if req["user_id"] == advisor_user.id:
        await update.message.reply_text(
            f"این درخواست متعلق به شماست.\n"
            f"موقعیت: {req.get('location','—')} | نوع: {req.get('property_type','—')}"
        )
        return

    pending = context.bot_data.get(f"pending_{code}")
    if pending:
        await update.message.reply_text(
            f"درخواست قبلی برای این کد هنوز در حال بررسی است.\n"
            f"لطفاً منتظر پاسخ متقاضی باشید."
        )
        return

    context.bot_data[f"pending_{code}"] = {
        "advisor_id": advisor_user.id,
        "advisor_name": advisor_user.full_name or advisor_user.username or str(advisor_user.id),
        "code": code,
        "req": req
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، هنوز نیاز دارم", callback_data=f"valid_{code}"),
            InlineKeyboardButton("❌ خیر، معامله کردم", callback_data=f"closed_{code}"),
        ]
    ])

    try:
        await context.bot.send_message(
            chat_id=req["user_id"],
            text=(
                f"یک مشاور می‌خواهد با شما در مورد آگهی زیر تماس بگیرد:\n\n"
                f"کد: {code}\n"
                f"موقعیت: {req.get('location','—')}\n"
                f"نوع: {req.get('property_type','—')}\n\n"
                f"آیا این آگهی هنوز معتبر است؟"
            ),
            reply_markup=keyboard
        )
        await update.message.reply_text(
            f"درخواست شما ارسال شد.\n\n"
            f"منتظر تأیید درخواست‌دهنده هستیم...\n"
            f"اگر آگهی معتبر باشد، اطلاعات تماس برای شما ارسال می‌شود."
        )
    except Exception as e:
        logger.error(f"Error notifying owner: {e}")
        await update.message.reply_text("خطا در ارسال پیام به درخواست‌دهنده.")

# ─── callback دکمه‌های تأیید/رد آگهی ─────────────────────
async def handle_validity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("valid_"):
        code = data[6:]
        pending = context.bot_data.get(f"pending_{code}")
        if not pending:
            await query.edit_message_text("این درخواست منقضی شده یا قبلاً پردازش شده است.")
            return

        advisor_id = pending["advisor_id"]
        req = pending["req"]

        wallet = get_wallet(advisor_id)
        if ACCESS_PRICE > 0 and wallet < ACCESS_PRICE:
            await context.bot.send_message(
                chat_id=advisor_id,
                text=f"موجودی ناکافی!\nنیاز: {ACCESS_PRICE:,} تومان | موجودی: {wallet:,} تومان"
            )
            await query.edit_message_text("مشاور موجودی کافی ندارد.")
            return

        deduct_wallet(advisor_id, ACCESS_PRICE)

        # ذخیره درخواست مشاور و ایجاد مکالمه
        save_advisor_request(advisor_id, code)
        conv_id = get_or_create_conversation(code, advisor_id, req["user_id"])

        owner = get_user(req["user_id"])
        owner_name = (owner.get("full_name") or owner.get("username") or "متقاضی") if owner else "متقاضی"
        owner_phone = owner.get("phone", "—") if owner else "—"
        owner_username = owner.get("username", "") if owner else ""

        await context.bot.send_message(
            chat_id=advisor_id,
            text=(
                f"آگهی تأیید شد — اطلاعات تماس:\n\n"
                f"کد: {code}\n"
                f"━━━━━━━━━━━━━━\n"
                f"موقعیت: {req.get('location','—')}\n"
                f"نوع: {req.get('property_type','—')}\n"
                f"بودجه: {req.get('budget','—')}\n"
                f"متراژ: {req.get('area','—')}\n"
                f"الزامات: {req.get('requirements','—')}\n"
                f"━━━━━━━━━━━━━━\n"
                f"نام: {owner_name}\n"
                f"یوزرنیم: {'@'+owner_username if owner_username else '—'}\n"
                f"شماره: {owner_phone}\n"
                f"━━━━━━━━━━━━━━\n"
                + (f"{ACCESS_PRICE:,} تومان از کیف پول کسر شد." if ACCESS_PRICE > 0 else "مشاهده رایگان — حالت تست")
            )
        )

        advisor = get_user(advisor_id)
        advisor_name = (advisor.get("full_name") or advisor.get("username") or "مشاور") if advisor else "مشاور"
        advisor_phone = advisor.get("phone", "—") if advisor else "—"
        advisor_username = advisor.get("username", "") if advisor else ""

        await query.edit_message_text(
            f"اطلاعات شما به مشاور ارسال شد.\n\n"
            f"مشاور: {advisor_name}\n"
            f"یوزرنیم: {'@'+advisor_username if advisor_username else '—'}\n"
            f"شماره: {advisor_phone}\n\n"
            f"منتظر تماس باشید."
        )

        context.bot_data.pop(f"pending_{code}", None)

    elif data.startswith("closed_"):
        code = data[7:]
        pending = context.bot_data.get(f"pending_{code}")

        mark_request_closed(code)

        req = get_request(code)
        if req and req.get("channel_message_id"):
            try:
                await context.bot.delete_message(
                    chat_id=CHANNEL_ID,
                    message_id=req["channel_message_id"]
                )
            except Exception as e:
                logger.warning(f"Could not delete channel message: {e}")

        if pending:
            await context.bot.send_message(
                chat_id=pending["advisor_id"],
                text=(
                    f"این آگهی منقضی شده است.\n\n"
                    f"کد: {code}\n"
                    f"درخواست‌دهنده اعلام کرد که معامله انجام شده.\n\n"
                    f"وجهی از کیف پول شما کسر نشد."
                )
            )
            context.bot_data.pop(f"pending_{code}", None)

        await query.edit_message_text(
            f"آگهی {code} منقضی شد و از کانال حذف شد.\n\n"
            f"ممنون از اطلاع‌رسانی شما!"
        )

# ─── پیام‌های متنی ────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if text == "❌ انصراف":
        await update.message.reply_text("انصراف داده شد.", reply_markup=kbd_for_user(context))
        return

    if is_banned(user.id):
        await update.message.reply_text("🚫 حساب شما مسدود شده است.")
        return

    if text == "💰 کیف پول":
        wallet = get_wallet(user.id)
        await update.message.reply_text(
            f"کیف پول شما\n\n"
            f"موجودی: {wallet:,} تومان\n\n"
            f"هزینه مشاهده: {ACCESS_PRICE:,} تومان\n\n"
            "شارژ به زودی فعال می‌شود."
        )
        return

    if text == "👤 پروفایل من":
        upsert_user(user)
        advisor = get_advisor(user.id)
        buttons = []
        if advisor and advisor.get("is_active"):
            buttons.append([InlineKeyboardButton("🏛 پنل مشاور", web_app=WebAppInfo(url=ADVISOR_URL))])
        buttons.append([InlineKeyboardButton("🏠 پنل متقاضی", web_app=WebAppInfo(url=OWNER_URL))])
        await update.message.reply_text(
            "کدام پنل را می‌خواهید باز کنید؟",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if text == "ℹ️ راهنما":
        await update.message.reply_text(
            "راهنمای ملک‌یاب\n\n"
            "متقاضی ملک:\n"
            "  ۱. ثبت درخواست را بزنید\n"
            "  ۲. فرم را پر کنید\n"
            "  ۳. آگهی در کانال منتشر می‌شود\n"
            "  ۴. مشاور کد را ارسال می‌کند\n"
            "  ۵. شما تأیید می‌کنید — اتصال برقرار می‌شود\n\n"
            "مشاور/فروشنده:\n"
            "  ۱. کانال را دنبال کنید\n"
            "  ۲. روی دکمه آگهی بزنید\n"
            "  ۳. منتظر تأیید درخواست‌دهنده باشید\n"
            "  ۴. اطلاعات تماس دریافت کنید\n\n"
            f"کانال: {CHANNEL_ID}"
        )
        return

    code = text.upper()
    if code.startswith("MLK-") and len(code) == 9:
        await ask_request_validity(update, context, user, code)
        return

    await update.message.reply_text("از منوی پایین استفاده کنید.")

# ─── اجرا ─────────────────────────────────────────────────
def main():
    ensure_single_instance()
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start_with_code),
            CommandHandler("admin", admin_cmd),
            CommandHandler("add_advisor", add_advisor_cmd),
            CommandHandler("remove_advisor", remove_advisor_cmd),
            MessageHandler(filters.Regex("^🎫 تیکت پشتیبانی$"), ticket_start),
            MessageHandler(filters.Regex("^🚨 ثبت شکایت$"), report_start),
        ],
        states={
            WAITING_PHONE: [
                MessageHandler(filters.CONTACT, handle_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_text),
            ],
            WAITING_ADMIN_PASS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_admin_pass),
            ],
            WAITING_TICKET_SUBJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_subject),
            ],
            WAITING_TICKET_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_message),
            ],
            WAITING_REPORT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_code),
            ],
            WAITING_REPORT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason),
            ],
            WAITING_ADD_ADVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_add_advisor),
            ],
            WAITING_REMOVE_ADVISOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_remove_advisor),
            ],
        },
        fallbacks=[
            CommandHandler("start", start_with_code),
            CommandHandler("cancel", cancel_conv),
            MessageHandler(filters.Regex("^❌ انصراف$"), cancel_conv),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("advisor", advisor_panel))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(CallbackQueryHandler(handle_validity_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # OTP Poller
    if app.job_queue:
        app.job_queue.run_repeating(otp_poller, interval=5, first=5)
    else:
        logger.warning("JobQueue not available. OTP polling disabled.")

    logger.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()