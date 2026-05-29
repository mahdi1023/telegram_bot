import json
import logging
import random
import string
import urllib.request
import urllib.error
from telegram import Update, WebAppInfo, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ─── تنظیمات ───────────────────────────────────────────────
BOT_TOKEN    = "8767855704:AAEir-uPTLu3w1wdtKteDSBSeoT_XPStz4I"
CHANNEL_ID   = "@melkpelk1"
MINI_APP_URL = "https://melk-liard.vercel.app"
SUPABASE_URL = "https://vwbyxjhyrrclmpcbkpgj.supabase.co"
SUPABASE_KEY = "sb_publishable_0xEgmVLQ_fm_XrYMaiF8_Q_z4OyxWpC"
ACCESS_PRICE = 0  # تومان برای هر مشاهده

# Conversation states
WAITING_PHONE = 1

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

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
    data = {
        "id": user.id,
        "username": user.username or "",
        "full_name": user.full_name or "",
    }
    if phone:
        data["phone"] = phone
    sb("POST", "users", data, "?on_conflict=id")

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

def save_request(data, user_id, code):
    sb("POST", "requests", {
        "unique_code": code,
        "user_id": user_id,
        "location": data.get("location"),
        "property_type": data.get("property_type"),
        "purpose": data.get("purpose"),
        "budget": data.get("budget"),
        "area": data.get("area"),
        "rooms": data.get("rooms"),
        "floor": data.get("floor"),
        "requirements": ", ".join(data.get("requirements", [])),
        "description": data.get("description", ""),
    })

def get_request(code):
    res = sb("GET", "requests", params=f"?unique_code=eq.{code}&select=*")
    return res[0] if res else None

def deduct_wallet(user_id, amount):
    if amount == 0:
        return True
    wallet = get_wallet(user_id)
    if wallet < amount:
        return False
    sb("PATCH", "users", {"wallet": wallet - amount}, f"?id=eq.{user_id}")
    sb("POST", "transactions", {
        "user_id": user_id,
        "amount": -amount,
        "type": "deduct",
        "description": "مشاهده اطلاعات درخواست"
    })
    return True

# ─── کیبوردها ─────────────────────────────────────────────
def kbd_main():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📋 ثبت درخواست ملک", web_app=WebAppInfo(url=MINI_APP_URL))],
        [KeyboardButton("💰 کیف پول"), KeyboardButton("📜 درخواست‌های من")],
        [KeyboardButton("ℹ️ راهنما")],
    ], resize_keyboard=True)

def kbd_phone():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📱 اشتراک‌گذاری شماره تلفن", request_contact=True)],
        [KeyboardButton("✏️ تایپ شماره تلفن")],
    ], resize_keyboard=True, one_time_keyboard=True)

def kbd_skip():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✏️ تایپ شماره تلفن")],
    ], resize_keyboard=True, one_time_keyboard=True)

# ─── /start ───────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)

    # اگه شماره داره مستقیم منوی اصلی
    if has_phone(user.id):
        wallet = get_wallet(user.id)
        await update.message.reply_text(
            f"✦ سلام *{user.first_name}* عزیز\n\n"
            "به *ملک‌یاب* خوش آمدی 🏛\n\n"
            "〉 *متقاضی ملک هستید؟*\n"
            "  روی «ثبت درخواست» بزنید.\n\n"
            "〉 *مشاور یا فروشنده هستید؟*\n"
            "  کد آگهی را از کانال بگیرید و اینجا ارسال کنید.\n\n"
            f"💰 موجودی: *{wallet:,} تومان*",
            reply_markup=kbd_main(),
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # اگه شماره نداره درخواست بده
    await update.message.reply_text(
        f"✦ سلام *{user.first_name}* عزیز\n\n"
        "به *ملک‌یاب* خوش آمدی 🏛\n\n"
        "برای استفاده از پلتفرم، ابتدا شماره تلفن خود را ثبت کنید.\n"
        "این شماره فقط پس از پرداخت هزینه به مشاوران نشان داده می‌شود.",
        reply_markup=kbd_phone(),
        parse_mode="Markdown"
    )
    return WAITING_PHONE

# ─── دریافت شماره از Contact ──────────────────────────────
async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    contact = update.message.contact
    phone = contact.phone_number

    # پاک‌سازی شماره
    if not phone.startswith("+"):
        phone = "+" + phone

    update_phone(user.id, phone)
    wallet = get_wallet(user.id)

    await update.message.reply_text(
        f"✅ شماره *{phone}* ثبت شد!\n\n"
        f"💰 موجودی: *{wallet:,} تومان*\n\n"
        "حالا می‌توانید از پلتفرم استفاده کنید.",
        reply_markup=kbd_main(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── دریافت شماره تایپ‌شده ───────────────────────────────
async def handle_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # اگه دکمه "تایپ شماره" زد
    if text == "✏️ تایپ شماره تلفن":
        await update.message.reply_text(
            "📱 لطفاً شماره تلفن خود را وارد کنید:\n"
            "مثال: `09123456789`",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return WAITING_PHONE

    # اعتبارسنجی شماره ایرانی
    digits = text.replace(" ", "").replace("-", "").replace("+98", "0").replace("98", "0" if text.startswith("98") else text[:2])
    if text.startswith("+98"):
        digits = "0" + text[3:].replace(" ","")
    elif text.startswith("98") and len(text) == 12:
        digits = "0" + text[2:]
    else:
        digits = text.replace(" ","").replace("-","")

    if not (digits.startswith("09") and len(digits) == 11 and digits.isdigit()):
        await update.message.reply_text(
            "❌ شماره وارد شده معتبر نیست.\n\n"
            "لطفاً دوباره وارد کنید:\n"
            "مثال: `09123456789`",
            parse_mode="Markdown"
        )
        return WAITING_PHONE

    update_phone(user.id, digits)
    wallet = get_wallet(user.id)

    await update.message.reply_text(
        f"✅ شماره *{digits}* ثبت شد!\n\n"
        f"💰 موجودی: *{wallet:,} تومان*\n\n"
        "حالا می‌توانید از پلتفرم استفاده کنید.",
        reply_markup=kbd_main(),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─── دریافت فرم Mini App ──────────────────────────────────
async def handle_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # اگه شماره نداره اول ثبت کنه
    if not has_phone(user.id):
        await update.message.reply_text(
            "⚠️ ابتدا شماره تلفن خود را ثبت کنید.\n"
            "از دستور /start استفاده کنید.",
            reply_markup=kbd_phone()
        )
        return

    data = json.loads(update.message.web_app_data.data)
    code = "MLK-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    save_request(data, user.id, code)

    reqs = data.get("requirements", [])
    reqs_txt = "\n".join(["• " + r for r in reqs]) if reqs else "• —"

    channel_msg = (
        "🏛 *درخواست جدید — ملک‌یاب*\n"
        "━━━━━━━━━━━━━━━━\n"
        f"📍 *موقعیت:* {data.get('location','—')}\n"
        f"🏗 *نوع:* {data.get('property_type','—')}\n"
        f"🎯 *هدف:* {data.get('purpose','—')}\n"
        f"💰 *بودجه:* {data.get('budget','—')}\n"
        f"📐 *متراژ:* {data.get('area','—')}\n"
        f"🛏 *اتاق:* {data.get('rooms','—')}\n"
        f"🏢 *طبقه:* {data.get('floor','—')}\n\n"
        f"✅ *الزامات:*\n{reqs_txt}\n\n"
        f"🔑 *کد درخواست:* `{code}`\n"
        "━━━━━━━━━━━━━━━━\n"
        f"_کد را در ربات ارسال کنید_"
    )

    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=channel_msg, parse_mode="Markdown")
        await update.message.reply_text(
            "✅ *درخواست شما ثبت و در کانال منتشر شد!*\n\n"
            f"🔑 کد یونیک: `{code}`\n\n"
            "هر بار که مشاوری اطلاعات شما را مشاهده کند، به شما اطلاع داده می‌شود.",
            parse_mode="Markdown",
            reply_markup=kbd_main()
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در انتشار: {e}")

# ─── پیام‌های متنی ────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if text == "💰 کیف پول":
        wallet = get_wallet(user.id)
        await update.message.reply_text(
            "💰 *کیف پول شما*\n\n"
            f"موجودی: *{wallet:,} تومان*\n\n"
            f"هزینه مشاهده هر درخواست: *{ACCESS_PRICE:,} تومان*\n\n"
            "〉 شارژ کیف پول به زودی فعال می‌شود.",
            parse_mode="Markdown"
        )
        return

    if text == "📜 درخواست‌های من":
        upsert_user(user)
        res = sb("GET", "requests", params=f"?user_id=eq.{user.id}&select=unique_code,location,property_type&order=created_at.desc&limit=5")
        if not res:
            await update.message.reply_text("📜 هنوز درخواستی ثبت نکرده‌اید.")
            return
        lines = ["📜 *درخواست‌های اخیر شما:*\n"]
        for r in res:
            lines.append(f"• `{r['unique_code']}` — {r.get('location','—')} — {r.get('property_type','—')}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if text == "ℹ️ راهنما":
        await update.message.reply_text(
            "ℹ️ *راهنمای ملک‌یاب*\n\n"
            "👤 *اگر متقاضی ملک هستید:*\n"
            "  ۱. روی «ثبت درخواست ملک» بزنید\n"
            "  ۲. فرم را کامل پر کنید\n"
            "  ۳. آگهی در کانال منتشر می‌شود\n"
            "  ۴. وقتی مشاوری اطلاعات شما را ببیند، خبر می‌گیرید\n\n"
            "🏢 *اگر مشاور یا فروشنده هستید:*\n"
            "  ۱. کانال ما را دنبال کنید\n"
            "  ۲. کد آگهی مورد نظر را کپی کنید\n"
            "  ۳. کد را اینجا ارسال کنید\n"
            f"  ۴. {ACCESS_PRICE:,} تومان از کیف پول کسر می‌شود\n"
            "  ۵. شماره تلفن و اطلاعات تماس متقاضی نمایش داده می‌شود\n\n"
            f"📢 کانال: {CHANNEL_ID}",
            parse_mode="Markdown"
        )
        return

    # ── کد یونیک ──
    code = text.upper()
    if code.startswith("MLK-") and len(code) == 9:

        # اگه شماره نداره
        if not has_phone(user.id):
            await update.message.reply_text(
                "⚠️ ابتدا شماره تلفن خود را ثبت کنید.",
                reply_markup=kbd_phone()
            )
            return

        req = get_request(code)
        if not req:
            await update.message.reply_text(
                "❌ این کد یافت نشد.\n\nکد را از کانال کپی کنید (فرمت: `MLK-XXXXX`)",
                parse_mode="Markdown"
            )
            return

        # اگه خودش صاحب درخواسته
        if req["user_id"] == user.id:
            await update.message.reply_text(
                "ℹ️ این درخواست متعلق به شماست.\n\n"
                f"📍 موقعیت: {req.get('location','—')}\n"
                f"🏗 نوع: {req.get('property_type','—')}\n"
                f"💰 بودجه: {req.get('budget','—')}",
            )
            return

        # بررسی موجودی
        wallet = get_wallet(user.id)
        if ACCESS_PRICE > 0 and wallet < ACCESS_PRICE:
            await update.message.reply_text(
                "💳 *موجودی ناکافی*\n\n"
                f"برای مشاهده این درخواست *{ACCESS_PRICE:,} تومان* نیاز دارید.\n"
                f"موجودی فعلی: *{wallet:,} تومان*",
                parse_mode="Markdown"
            )
            return

        if not deduct_wallet(user.id, ACCESS_PRICE):
            await update.message.reply_text("❌ خطا در پردازش پرداخت.")
            return

        # اطلاعات متقاضی
        owner = get_user(req["user_id"])
        owner_name = (owner.get("full_name") or owner.get("username") or "متقاضی") if owner else "متقاضی"
        owner_phone = owner.get("phone", "—") if owner else "—"
        owner_username = owner.get("username", "") if owner else ""

        contact_line = f"[{owner_name}](tg://user?id={req['user_id']})"
        username_line = f"@{owner_username}" if owner_username else "—"

        # پیام به مشاور
        await update.message.reply_text(
            f"✅ *اطلاعات درخواست {code}*\n"
            "━━━━━━━━━━━━━━\n"
            f"📍 موقعیت: {req.get('location','—')}\n"
            f"🏗 نوع: {req.get('property_type','—')}\n"
            f"💰 بودجه: {req.get('budget','—')}\n"
            f"📐 متراژ: {req.get('area','—')}\n"
            f"🛏 اتاق: {req.get('rooms','—')}\n"
            f"✅ الزامات: {req.get('requirements','—')}\n"
            f"📝 توضیحات: {req.get('description') or '—'}\n"
            "━━━━━━━━━━━━━━\n"
            f"👤 *متقاضی:* {contact_line}\n"
            f"📲 *یوزرنیم:* {username_line}\n"
            f"📞 *شماره تلفن:* `{owner_phone}`\n"
            "━━━━━━━━━━━━━━\n"
            + (f"💰 *{ACCESS_PRICE:,} تومان از کیف پول کسر شد.*" if ACCESS_PRICE > 0 else "✦ _مشاهده رایگان (حالت تست)_"),
            parse_mode="Markdown"
        )

        # اطلاع‌رسانی به متقاضی
        try:
            advisor = get_user(user.id)
            advisor_name = (advisor.get("full_name") or advisor.get("username") or "یک مشاور") if advisor else "یک مشاور"
            advisor_phone = advisor.get("phone", "—") if advisor else "—"
            advisor_username = advisor.get("username", "") if advisor else ""

            notif = (
                "🔔 *اطلاعیه ملک‌یاب*\n\n"
                f"یک مشاور درخواست شما `{code}` را مشاهده کرد.\n\n"
                f"👤 مشاور: [{advisor_name}](tg://user?id={user.id})\n"
                f"📲 یوزرنیم: {'@'+advisor_username if advisor_username else '—'}\n"
                f"📞 شماره تماس: `{advisor_phone}`\n\n"
                "_منتظر تماس باشید._"
            )
            await context.bot.send_message(
                chat_id=req["user_id"],
                text=notif,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Could not notify buyer: {e}")
        return

    await update.message.reply_text(
        "از منوی پایین استفاده کنید.\n"
        "برای مشاهده درخواست، کد آگهی را ارسال کنید (فرمت: `MLK-XXXXX`)",
        parse_mode="Markdown"
    )

# ─── اجرا ─────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler برای ثبت شماره
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_PHONE: [
                MessageHandler(filters.CONTACT, handle_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_text),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
