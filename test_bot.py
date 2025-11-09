# test_bot.py
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ایمپورت تنظیمات از فایل config
import config

# ========== پیکربندی از فایل config ==========
BOT_TOKEN = config.BOT_TOKEN
CHANNEL_USERNAME = config.CHANNEL_USERNAME
ADMIN_USER_ID = config.ADMIN_USER_ID
SPECIAL_TESTER_ID = config.SPECIAL_TESTER_ID
DB_PATH = config.DB_PATH

# ================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# دیتابیس: init + توابع
# =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        free_used INTEGER DEFAULT 0
    )
    """)
    # Listings table
    c.execute("""
    CREATE TABLE IF NOT EXISTS listings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        created_at TEXT,
        expire_at TEXT,
        data_json TEXT,
        receipt_file_id TEXT,
        status TEXT DEFAULT 'pending'
    )
    """)
    conn.commit()
    conn.close()

def get_user_row(user_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, free_used FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"user_id": row[0], "free_used": bool(row[1])}

def ensure_user(user_id: int):
    if get_user_row(user_id) is None:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        conn.commit()
        conn.close()

def set_user_free_used(user_id: int):
    ensure_user(user_id)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET free_used = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def record_listing(user_id: int, data_json: str, receipt_file_id: Optional[str]=None) -> int:
    """Insert a listing and return listing id."""
    now = datetime.utcnow()
    created_at = now.isoformat()
    expire_at = (now + timedelta(days=config.PRICE_CONFIG["listing_expiry_days"])).isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO listings (user_id, created_at, expire_at, data_json, receipt_file_id, status)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, created_at, expire_at, data_json, receipt_file_id, 'active'))
    lid = c.lastrowid
    conn.commit()
    conn.close()
    return lid

def mark_listing_rejected_by_admin(listing_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE listings SET status = 'rejected' WHERE id = ?", (listing_id,))
    conn.commit()
    conn.close()

def get_user_listings(user_id: int) -> list:
    """گرفتن لیست آگهی‌های کاربر برای ویرایش"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, data_json FROM listings WHERE user_id = ? AND status = 'active'", (user_id,))
    rows = c.fetchall()
    conn.close()
    return [{"id": row[0], "data_json": row[1]} for row in rows]

def update_listing(listing_id: int, data_json: str):
    """ویرایش آگهی"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE listings SET data_json = ? WHERE id = ?", (data_json, listing_id))
    conn.commit()
    conn.close()

# =========================
# حافظه موقت برای جریان فرم
# =========================
user_form_state: Dict[int, Dict] = {}
PLATFORM_STATES = {}
CHAR_COUNT_STATES = {}
NUMBER_VALIDATION_STATES = {}
DIVISION_VALIDATION_STATES = {}
PHOTO_UPLOAD_STATES = {}
PLAYER_VALUE_STATES = {}

# =========================
# منوها
# =========================
main_menu_buttons = [
    [KeyboardButton("🔄 استارت مجدد"), KeyboardButton("💰 فروش اکانت")],
    [KeyboardButton("📂 اکانت‌های من"), KeyboardButton("📖 راهنما")]
]
main_menu = ReplyKeyboardMarkup(main_menu_buttons, resize_keyboard=True, one_time_keyboard=False)

# =========================
# منوی جدید برای انتخاب روش فروش
# =========================
sale_method_selection_buttons = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 فرم دستی", callback_data="manual_form")],
    [InlineKeyboardButton("🤖 ربات", callback_data="bot_form")]
])

# =========================
# متن راهنما
# =========================
GUIDE_TEXT = config.TEXTS["guide"]

# دکمه‌های فروش (15 دکمه، سه‌تایی) - با دکمه جدید
sale_buttons = [
    [
        InlineKeyboardButton("🌐 وب اپ", callback_data="web_app"),
        InlineKeyboardButton("📧 نوع ایمیل", callback_data="email_type"),
        InlineKeyboardButton("🎮 انتخاب پلتفرم", callback_data="platform")
    ],
    [
        InlineKeyboardButton("💰 کوین اکانت", callback_data="coin_account"),
        InlineKeyboardButton("⚡ بازیکنان ترید", callback_data="trade_players"),
        InlineKeyboardButton("❌ بازیکنان آنترید", callback_data="non_trade_players")
    ],
    [
        InlineKeyboardButton("🏆 مچ ارنینگ", callback_data="match_earning"),
        InlineKeyboardButton("⭐ لول سیزن", callback_data="season_level"),
        InlineKeyboardButton("🏅 دیویژن رایوالز", callback_data="division_rivals")
    ],
    [
        InlineKeyboardButton("💵 قیمت اکانت", callback_data="price"),
        InlineKeyboardButton("💰 تخمین قیمت", callback_data="estimate_price"),
        InlineKeyboardButton("📝 نحوه فروش", callback_data="sale_method")
    ],
    [
        InlineKeyboardButton("✅ ثبت نهایی", callback_data="final_submit"),
        InlineKeyboardButton("📋 نمایش اطلاعات ثبت شده", callback_data="show_entered_data"),
        InlineKeyboardButton("📸 ثبت عکس تیم", callback_data="team_photo")
    ]
]
sale_menu = InlineKeyboardMarkup(sale_buttons)

# =========================
# منوهای جدید برای نحوه فروش
# =========================
sale_rules_buttons = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ می‌پذیرم", callback_data="accept_rules")],
    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
])

sale_method_choice_buttons = InlineKeyboardMarkup([
    [InlineKeyboardButton("📱 ثبت آیدی خودم", callback_data="sale_method_self")],
    [InlineKeyboardButton("🛒 فروش از طریق کانال", callback_data="sale_method_channel")],
    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_rules")]
])

# =========================
# دکمه‌های تأیید نهایی
# =========================
final_confirmation_buttons = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ مطمئن هستم و ثبت نهایی", callback_data="confirm_final_submit")],
    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
])

# =========================
# متن قوانین فروش
# =========================
SALE_RULES_TEXT = config.TEXTS["sale_rules"]

# منوی انتخاب نوع ایمیل
email_type_buttons = [
    [
        InlineKeyboardButton("Gmail", callback_data="email_gmail"),
        InlineKeyboardButton("Outlook", callback_data="email_outlook")
    ],
    [
        InlineKeyboardButton("Hotmail", callback_data="email_hotmail"),
        InlineKeyboardButton("Yahoo", callback_data="email_yahoo")
    ],
    [
        InlineKeyboardButton("سایر", callback_data="email_other"),
        InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")
    ]
]
email_type_menu = InlineKeyboardMarkup(email_type_buttons)

# منوی انتخاب نوع وب اپ
web_app_buttons = [
    [
        InlineKeyboardButton("وب باز", callback_data="web_open"),
        InlineKeyboardButton("وب بسته", callback_data="web_closed")
    ],
    [
        InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")
    ]
]
web_app_menu = InlineKeyboardMarkup(web_app_buttons)

# =========================
# Helper: check membership
# =========================
async def is_member_of_channel(bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return m.status in ("creator", "administrator", "member")
    except Exception as e:
        logger.warning("get_chat_member failed: %s", e)
        return False

# =========================
# تابع تخمین قیمت (ویرایش شده با استفاده از config)
# =========================
def estimate_price(form_data):
    """تابع تخمین قیمت بر اساس فرمول تعریف شده"""
    try:
        # مقداردهی اولیه
        coins = int(form_data.get('coin_account', 0))
        trade_players_value_input = int(form_data.get('trade_players_value', 0))
        nontrade_players_value_input = int(form_data.get('non_trade_players_value', 0))
       
        # محاسبه ارزش کوین (از config)
        coin_value = (coins / config.PRICE_CONFIG["coin_divider"]) * config.PRICE_CONFIG["coin_value_unit"]
       
        # محاسبه ارزش بازیکنان ترید (از config)
        trade_players_value = (trade_players_value_input * config.PRICE_CONFIG["trade_players_multiplier"]) / config.PRICE_CONFIG["trade_players_divider"]
       
        # محاسبه ارزش بازیکنان آنترید (از config)
        nontrade_players_value = (nontrade_players_value_input * config.PRICE_CONFIG["nontrade_players_multiplier"]) / config.PRICE_CONFIG["nontrade_players_divider"] * config.PRICE_CONFIG["nontrade_players_discount"]
       
        # وب اپ (از config)
        web_app = form_data.get('web_app')
        if web_app == 'وب باز':
            web_app_bonus = config.PRICE_CONFIG["web_app_open_bonus"]
        else:
            web_app_bonus = config.PRICE_CONFIG["web_app_closed_bonus"]
       
        # تاثیر مچ ارنینگ (از config)
        match_earning = int(form_data.get('match_earning', 0))
        match_bonus = 0
        for (min_val, max_val), bonus in config.PRICE_CONFIG["match_earning_bonuses"].items():
            if min_val <= match_earning < max_val:
                match_bonus = bonus
                break
       
        # تاثیر لول سیزن (از config)
        season_level = int(form_data.get('season_level', 0))
        season_bonus = 0
        for (min_val, max_val), bonus in config.PRICE_CONFIG["season_level_bonuses"].items():
            if min_val <= season_level < max_val:
                season_bonus = bonus
                break
       
        # تاثیر دیویژن رایوالز (از config)
        division = form_data.get('division_rivals', '')
        division_bonus = config.PRICE_CONFIG["division_bonuses"].get(str(division).lower(), 0)
       
        # جمع کل
        total = (coin_value + trade_players_value + nontrade_players_value +
                 web_app_bonus + match_bonus + season_bonus + division_bonus)
       
        # محاسبه بازه قیمتی (از config)
        range_percent = config.PRICE_CONFIG["price_range_percent"]
        lower_bound = total * (1 - range_percent)
        upper_bound = total * (1 + range_percent)
       
        return {
            'estimate': f"💰 تخمین قیمت: {int(lower_bound):,} - {int(upper_bound):,} تومان",
            'details': f"""
📊 جزئیات محاسبه:
• ارزش کوین: {int(coin_value):,} تومان
• بازیکنان ترید: {int(trade_players_value):,} تومان
• بازیکنان آنترید: {int(nontrade_players_value):,} تومان
• وب اپ: {web_app_bonus:,} تومان
• مچ ارنینگ: {match_bonus:,} تومان
• لول سیزن: {season_bonus:,} تومان
• دیویژن رایوالز: {division_bonus:,} تومان
            """,
            'success': True
        }
   
    except Exception as e:
        logger.error(f"Error in estimate_price: {e}")
        return {
            'success': False,
            'error': "خطا در محاسبه قیمت. لطفا از پر بودن فیلدهای ضروری اطمینان حاصل کنید."
        }

# =========================
# توابع جدید برای سیستم پلتفرم (ویرایش شده با config)
# =========================
async def handle_platform_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند انتخاب پلتفرم"""
    query = update.callback_query
    await query.answer()
   
    user_id = query.from_user.id
    PLATFORM_STATES[user_id] = {'step': 'select_platform'}
   
    keyboard = [
        [InlineKeyboardButton("پلی استیشن", callback_data="platform_ps")],
        [InlineKeyboardButton("ایکس باکس", callback_data="platform_xbox")],
        [InlineKeyboardButton("پی سی", callback_data="platform_pc")],
        [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await query.edit_message_text(
        "🎮 لطفاً پلتفرم خود را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def show_ps_options(query):
    """نمایش گزینه‌های پلی استیشن"""
    keyboard = [
        [InlineKeyboardButton("ظرفیت 3", callback_data="subplatform_ps3")],
        [InlineKeyboardButton("ظرفیت 2", callback_data="subplatform_ps2")],
        [InlineKeyboardButton("کامل", callback_data="subplatform_psfull")],
        [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_platform")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await query.edit_message_text(
        "🎯 ظرفیت پلی استیشن را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def show_xbox_options(query):
    """نمایش گزینه‌های ایکس باکس"""
    keyboard = [
        [InlineKeyboardButton("هوم", callback_data="subplatform_xboxhome")],
        [InlineKeyboardButton("سوویچ", callback_data="subplatform_xboxswitch")],
        [InlineKeyboardButton("کامل", callback_data="subplatform_xboxfull")],
        [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_platform")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await query.edit_message_text(
        "🎯 نوع ایکس باکس را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def show_pc_options(query):
    """نمایش گزینه‌های پی سی"""
    keyboard = [
        [InlineKeyboardButton("بازی به صورت کامل", callback_data="subplatform_pcfull")],
        [InlineKeyboardButton("اشتراک EA Play Pro", callback_data="subplatform_eaplay")],
        [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_platform")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await query.edit_message_text(
        "💻 نوع اکانت پی سی را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def finalize_platform_selection(query, user_id, platform, subplatform):
    """ثبت نهایی انتخاب پلتفرم و نمایش فرم موقت"""
    # ذخیره در state کاربر
    user_state = user_form_state.get(user_id, {})
   
    # ثبت اطلاعات پلتفرم
    platform_display = get_platform_display_name(platform, subplatform)
    user_state['form']['platform'] = platform_display
    user_state['form']['platform_details'] = {
        'main_platform': platform,
        'sub_platform': subplatform
    }
   
    # نمایش فرم موقت
    temp_form_text = generate_temp_form_text(user_state['form'])
   
    keyboard = [
        [InlineKeyboardButton("✅ تأیید و ادامه", callback_data="continue_to_form")],
        [InlineKeyboardButton("✏️ ویرایش پلتفرم", callback_data="platform")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await query.edit_message_text(
        f"✅ پلتفرم ثبت شد: {platform_display}\n\n"
        f"📋 فرم موقت شما:\n{temp_form_text}\n\n"
        f"آیا می‌خواهید ادامه دهید؟",
        reply_markup=reply_markup
    )

async def handle_eaplay_days_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش تعداد روزهای EA Play"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
   
    if text == "/back":
        # برگشت به انتخاب پلتفرم پی سی
        query = update
        await show_pc_options(query)
        return
   
    if not text.isdigit():
        await update.message.reply_text("❌ لطفاً فقط عدد وارد کنید (مثال: 110)")
        return
   
    days = int(text)
    if days <= 0 or days > 3650:
        await update.message.reply_text("❌ تعداد روز نامعتبر است. لطفاً عدد معتبر وارد کنید.")
        return
   
    # ثبت اطلاعات
    user_state = user_form_state.get(user_id, {})
    user_state['form']['platform'] = f"پی سی - EA Play Pro ({days} روز)"
    user_state['form']['platform_details'] = {
        'main_platform': 'pc',
        'sub_platform': 'eaplay',
        'eaplay_days': days
    }
   
    # نمایش فرم موقت
    temp_form_text = generate_temp_form_text(user_state['form'])
   
    keyboard = [
        [InlineKeyboardButton("✅ تأیید و ادامه", callback_data="continue_to_form")],
        [InlineKeyboardButton("✏️ ویرایش پلتفرم", callback_data="platform")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
   
    await update.message.reply_text(
        f"✅ اطلاعات EA Play Pro ثبت شد: {days} روز\n\n"
        f"📋 فرم موقت شما:\n{temp_form_text}\n\n"
        f"آیا می‌خواهید ادامه دهید؟",
        reply_markup=reply_markup
    )

def get_platform_display_name(main_platform, sub_platform):
    """تبدیل کد پلتفرم به نام نمایشی (از config)"""
    return config.PLATFORM_CONFIG.get(main_platform, {}).get(sub_platform, "نامشخص")

def generate_temp_form_text(form_data):
    """تولید متن فرم موقت"""
    text = "┌─── 📋 فرم موقت ───┐\n"
   
    if 'platform' in form_data:
        text += f"🎮 پلتفرم: {form_data['platform']}\n"
   
    fields_display = {
        'email_type': '📧 نوع ایمیل',
        'web_app': '🌐 وب اپ',
        'coin_account': '💰 کوین اکانت',
        'trade_players': '⚡ بازیکنان ترید',
        'trade_players_value': '💰 ارزش بازیکنان ترید',
        'non_trade_players': '❌ بازیکنان آنترید',
        'non_trade_players_value': '💰 ارزش بازیکنان آنترید',
        'match_earning': '🏆 مچ ارنینگ',
        'season_level': '⭐ لول سیزن',
        'division_rivals': '🏅 دیویژن رایوالز',
        'sale_method': '📝 نحوه فروش',
        'user_contact': '📱 آیدی ارتباط',
        'purchase_link': '🛒 لینک خرید',
        'price': '💵 قیمت اکانت'
    }
   
    for field, display_name in fields_display.items():
        if field in form_data:
            if field == 'purchase_link' and len(form_data[field]) > 30:
                text += f"{display_name}: {form_data[field][:30]}...\n"
            else:
                text += f"{display_name}: {form_data[field]}\n"
   
    text += "└────────────────┘"
    return text

# =========================
# تابع جدید برای نمایش اطلاعات کامل فرم
# =========================
def generate_complete_form_display(form_data):
    """تولید متن کامل فرم با وضعیت تکمیل هر فیلد"""
    text = "┌─── 📋 اطلاعات ثبت شده ───┐\n\n"
   
    fields_info = [
        ('🎮 پلتفرم', 'platform'),
        ('📧 نوع ایمیل', 'email_type'),
        ('🌐 وب اپ', 'web_app'),
        ('💰 کوین اکانت', 'coin_account'),
        ('⚡ بازیکنان ترید', 'trade_players'),
        ('💰 ارزش بازیکنان ترید', 'trade_players_value'),
        ('❌ بازیکنان آنترید', 'non_trade_players'),
        ('💰 ارزش بازیکنان آنترید', 'non_trade_players_value'),
        ('🏆 مچ ارنینگ', 'match_earning'),
        ('⭐ لول سیزن', 'season_level'),
        ('🏅 دیویژن رایوالز', 'division_rivals'),
        ('📝 نحوه فروش', 'sale_method'),
        ('📱 آیدی ارتباط', 'user_contact'),
        ('🛒 لینک خرید', 'purchase_link'),
        ('💵 قیمت اکانت', 'price'),
        ('📸 عکس‌های تیم', 'team_photos')
    ]
   
    completed_count = 0
    total_fields = len(fields_info)
   
    for display_name, field in fields_info:
        if field in form_data and form_data[field]:
            value = form_data[field]
            if field == 'team_photos':
                value = f"{len(form_data[field])} عکس"
            elif field == 'purchase_link' and len(value) > 25:
                value = f"{value[:25]}..."
           
            text += f"✅ {display_name}: {value}\n"
            completed_count += 1
        else:
            text += f"❌ {display_name}: ثبت نشده\n"
   
    text += f"\n📊 وضعیت تکمیل: {completed_count}/{total_fields}\n"
   
    if completed_count == total_fields:
        text += "🎉 تمام اطلاعات تکمیل شده است!\n"
    elif completed_count >= total_fields * 0.7:
        text += "⚠️ بیشتر اطلاعات تکمیل شده است\n"
    elif completed_count >= total_fields * 0.4:
        text += "🔶 نیمی از اطلاعات تکمیل شده است\n"
    else:
        text += "🔴 اطلاعات کمی تکمیل شده است\n"
   
    text += "└─────────────────────────┘"
    return text

# =========================
# تابع جدید برای ارسال فرم به ادمین
# =========================
async def send_form_to_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int, form_data: dict, photos: list = None):
    """ارسال اطلاعات فرم به ادمین برای تأیید"""
    try:
        form_text = generate_complete_form_display(form_data)
        user_info = f"👤 کاربر: {user_id}"
        if 'user_contact' in form_data:
            user_info += f" - {form_data['user_contact']}"
       
        full_message = f"{user_info}\n\n{form_text}"
       
        admin_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تأیید و انتشار", callback_data=f"admin_approve_free|{user_id}"),
                InlineKeyboardButton("❌ رد آگهی", callback_data=f"admin_reject_free|{user_id}")
            ]
        ])
       
        if photos and len(photos) > 0:
            await context.bot.send_photo(
                chat_id=ADMIN_USER_ID,
                photo=photos[0],
                caption=full_message,
                reply_markup=admin_buttons
            )
           
            for i in range(1, len(photos)):
                await context.bot.send_photo(
                    chat_id=ADMIN_USER_ID,
                    photo=photos[i]
                )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=full_message,
                reply_markup=admin_buttons
            )
           
        return True
       
    except Exception as e:
        logger.error(f"خطا در ارسال فرم به ادمین: {e}")
        return False

# =========================
# توابع جدید برای سیستم شمارنده کاراکتر (ویرایش شده با config)
# =========================
async def handle_char_count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های مربوط به شمارنده کاراکتر"""
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    char_state = CHAR_COUNT_STATES.get(user_id)
    if not char_state:
        return False
   
    field = char_state['field']
    max_chars = config.PRICE_CONFIG["char_limits"].get(field, 25)
    text_length = len(text)
   
    if text == "/back":
        CHAR_COUNT_STATES.pop(user_id, None)
        await update.message.reply_text("به فرم اصلی برگشتید.", reply_markup=sale_menu)
        return True
   
    if text_length > max_chars:
        await update.message.reply_text(
            f"❌ اسامی وارد شده بیشتر از حد مجاز است!\n"
            f"📝 تعداد کاراکترهای وارد شده: {text_length}\n"
            f"✅ حداکثر مجاز: {max_chars} کاراکتر\n\n"
            f"لطفا دوباره تلاش کنید:\n\n"
            f"📝 تعداد کاراکترهای باقیمانده: {max_chars}/{max_chars}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
    state['form'][field] = text
   
    CHAR_COUNT_STATES.pop(user_id, None)
   
    if field == 'trade_players':
        PLAYER_VALUE_STATES[user_id] = {
            'field': 'trade_players_value',
            'player_type': 'ترید'
        }
        await update.message.reply_text(
            "✅ نام بازیکنان ترید ثبت شد.\n\n"
            "💰 لطفا مجموع ارزش بازیکنان ترید خود را وارد کنید (به کوین):\n"
            "مثال: 400000\n\n"
            "ℹ️ این مقدار در محاسبه قیمت نهایی استفاده خواهد شد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    elif field == 'non_trade_players':
        PLAYER_VALUE_STATES[user_id] = {
            'field': 'non_trade_players_value',
            'player_type': 'آنترید'
        }
        await update.message.reply_text(
            "✅ نام بازیکنان آنترید ثبت شد.\n\n"
            "💰 لطفا مجموع ارزش بازیکنان آنترید خود را وارد کنید (به کوین):\n"
            "مثال: 100000\n\n"
            "ℹ️ این مقدار در محاسبه قیمت نهایی استفاده خواهد شد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    state['awaiting_field'] = None
    await update.message.reply_text(f"✅ اطلاعات ثبت شد:\n{text}", reply_markup=sale_menu)
    return True

# =========================
# تابع جدید برای مدیریت ارزش بازیکنان
# =========================
async def handle_player_value_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های مربوط به ارزش بازیکنان"""
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    value_state = PLAYER_VALUE_STATES.get(user_id)
    if not value_state:
        return False
   
    field = value_state['field']
    player_type = value_state['player_type']
   
    if text == "/back":
        PLAYER_VALUE_STATES.pop(user_id, None)
        await update.message.reply_text("به فرم اصلی برگشتید.", reply_markup=sale_menu)
        return True
   
    if not text:
        await update.message.reply_text(
            f"❌ لطفا یک عدد وارد کنید.\n\n"
            f"💰 لطفا مجموع ارزش بازیکنان {player_type} خود را وارد کنید (به کوین).\n"
            f"مثال: 400000\n\n"
            f"ℹ️ فقط عدد انگلیسی باشد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    if not text.isdigit():
        await update.message.reply_text(
            f"❌ فقط عدد انگلیسی مجاز است!\n"
            f"لطفا از حروف فارسی، انگلیسی یا کاراکترهای خاص استفاده نکنید.\n\n"
            f"💰 لطفا مجموع ارزش بازیکنان {player_type} خود را وارد کنید (به کوین).\n"
            f"مثال: 400000\n\n"
            f"ℹ️ فقط عدد انگلیسی باشد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    try:
        number_value = int(text)
    except ValueError:
        await update.message.reply_text(
            f"❌ خطا در پردازش عدد!\n"
            f"لطفا فقط عدد معتبر وارد کنید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
    state['form'][field] = text
   
    PLAYER_VALUE_STATES.pop(user_id, None)
   
    formatted_number = f"{number_value:,}".replace(",", ".")
   
    await update.message.reply_text(
        f"✅ ارزش بازیکنان {player_type} ثبت شد: {formatted_number} کوین",
        reply_markup=sale_menu
    )
    return True

# =========================
# توابع جدید برای سیستم اعتبارسنجی عددی (ویرایش شده با config)
# =========================
async def handle_number_validation_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های مربوط به اعتبارسنجی عددی"""
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    number_state = NUMBER_VALIDATION_STATES.get(user_id)
    if not number_state:
        return False
   
    field = number_state['field']
    max_digits = config.PRICE_CONFIG["digit_limits"].get(field, 8)
    only_numbers = number_state.get('only_numbers', True)
   
    if text == "/back":
        NUMBER_VALIDATION_STATES.pop(user_id, None)
        await update.message.reply_text("به فرم اصلی برگشتید.", reply_markup=sale_menu)
        return True
   
    if not text:
        error_message = get_error_message(field, max_digits)
        await update.message.reply_text(
            error_message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    if only_numbers and not text.isdigit():
        error_message = get_error_message(field, max_digits)
        await update.message.reply_text(
            f"❌ فقط عدد انگلیسی مجاز است!\n{error_message}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    if len(text) > max_digits:
        error_message = get_error_message(field, max_digits)
        await update.message.reply_text(
            f"❌ تعداد ارقام بیشتر از حد مجاز است!\n"
            f"📊 تعداد ارقام وارد شده: {len(text)}\n"
            f"✅ حداکثر مجاز: {max_digits} رقم\n\n{error_message}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    try:
        number_value = int(text)
    except ValueError:
        error_message = get_error_message(field, max_digits)
        await update.message.reply_text(
            f"❌ خطا در پردازش عدد!\n{error_message}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
    state['form'][field] = text
   
    NUMBER_VALIDATION_STATES.pop(user_id, None)
    state['awaiting_field'] = None
   
    formatted_number = f"{number_value:,}".replace(",", ".")
   
    await update.message.reply_text(
        f"✅ {get_success_message(field)}: {formatted_number}",
        reply_markup=sale_menu
    )
    return True

def get_error_message(field: str, max_digits: int) -> str:
    """پیام خطای مناسب برای هر فیلد"""
    messages = {
        'coin_account': f"💰 لطفا مقدار کوین اکانت را وارد کنید.\nمثال: 245000\n\nℹ️ فقط عدد انگلیسی باشد\n🔢 حداکثر {max_digits} رقم مجاز است",
        'match_earning': f"🏆 لطفا مچ ارنینگ را وارد کنید.\nمثال: 1200\n\nℹ️ فقط عدد انگلیسی باشد\n🔢 حداکثر {max_digits} رقم مجاز است",
        'season_level': f"⭐ لطفا لول سیزن را وارد کنید.\nمثال: 5\n\nℹ️ فقط عدد انگلیسی باشد\n🔢 حداکثر {max_digits} رقم مجاز است",
        'price': f"💵 قیمت اکانت خود را وارد کنید.\nمثال: 250000\n\nℹ️ فقط عدد انگلیسی باشد\n🔢 حداکثر {max_digits} رقم مجاز است"
    }
    return messages.get(field, f"لطفا یک عدد معتبر وارد کنید (حداکثر {max_digits} رقم)")

def get_success_message(field: str) -> str:
    """پیام موفقیت مناسب برای هر فیلد"""
    messages = {
        'coin_account': "مقدار کوین اکانت ثبت شد",
        'match_earning': "مچ ارنینگ ثبت شد",
        'season_level': "لول سیزن ثبت شد",
        'price': "قیمت اکانت ثبت شد"
    }
    return messages.get(field, "اطلاعات ثبت شد")

# =========================
# توابع جدید برای سیستم اعتبارسنجی دیویژن رایوالز
# =========================
async def handle_division_validation_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های مربوط به اعتبارسنجی دیویژن رایوالز"""
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    division_state = DIVISION_VALIDATION_STATES.get(user_id)
    if not division_state:
        return False
   
    field = division_state['field']
   
    if text == "/back":
        DIVISION_VALIDATION_STATES.pop(user_id, None)
        await update.message.reply_text("به فرم اصلی برگشتید.", reply_markup=sale_menu)
        return True
   
    if not text:
        await update.message.reply_text(
            "❌ لطفا یک مقدار وارد کنید.\n\n"
            "🏅 لطفا دیویژن رایوالز را وارد کنید:\n"
            "- یک کلمه 5 حرفی (مثلاً: Elite)\n"
            "- یا یک عدد از 1 تا 10",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True
   
    if text.isdigit():
        num = int(text)
        if 1 <= num <= 10:
            state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
            state['form'][field] = text
            DIVISION_VALIDATION_STATES.pop(user_id, None)
            state['awaiting_field'] = None
            await update.message.reply_text(f"✅ دیویژن رایوالز ثبت شد: {text}", reply_markup=sale_menu)
            return True
        else:
            await update.message.reply_text(
                "❌ عدد وارد شده باید بین 1 تا 10 باشد.\n\n"
                "🏅 لطفا دیویژن رایوالز را وارد کنید:\n"
                "- یک کلمه 5 حرفی (مثلاً: Elite)\n"
                "- یا یک عدد از 1 تا 10",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return True
   
    if text.isalpha() and len(text) == 5:
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
        state['form'][field] = text
        DIVISION_VALIDATION_STATES.pop(user_id, None)
        state['awaiting_field'] = None
        await update.message.reply_text(f"✅ دیویژن رایوالز ثبت شد: {text}", reply_markup=sale_menu)
        return True
    else:
        await update.message.reply_text(
            "❌ مقدار وارد شده معتبر نیست.\n\n"
            "🏅 لطفا دیویژن رایوالز را وارد کنید:\n"
            "- یک کلمه 5 حرفی (فقط حروف، دقیقاً 5 کاراکتر)\n"
            "- یا یک عدد از 1 تا 10",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return True

# =========================
# توابع جدید برای سیستم آپلود عکس (ویرایش شده با config)
# =========================
async def handle_photo_upload_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت پیام‌های مربوط به آپلود عکس"""
    user = update.effective_user
    user_id = user.id
   
    photo_state = PHOTO_UPLOAD_STATES.get(user_id)
    if not photo_state:
        return False
   
    text = (update.message.text or "").strip()
    if text == "/back":
        PHOTO_UPLOAD_STATES.pop(user_id, None)
        await update.message.reply_text("به فرم اصلی برگشتید.", reply_markup=sale_menu)
        return True
   
    PHOTO_UPLOAD_STATES.pop(user_id, None)
    await update.message.reply_text(
        "✅ از حالت آپلود عکس خارج شدید. به فرم اصلی برگشتید.",
        reply_markup=sale_menu
    )
    return True

# =========================
# تابع تولید لینک خرید منحصربه‌فرد
# =========================
def generate_purchase_link(user_id: int, listing_data: dict) -> str:
    """تولید لینک خرید منحصربه‌فرد برای کاربر"""
    import hashlib
    import time
   
    unique_string = f"{user_id}_{time.time()}_{listing_data.get('platform', '')}"
    link_hash = hashlib.md5(unique_string.encode()).hexdigest()[:8]
   
    base_url = "https://your-domain.com/purchase"
    return f"{base_url}/{link_hash}"

# =========================
# توابع جدید برای سیستم فرم دستی (ویرایش شده با config)
# =========================
async def handle_manual_form_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دریافت فرم دستی از کاربر"""
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    manual_state = context.user_data.get('manual_form')
    if not manual_state or manual_state.get('step') != 'awaiting_form':
        return False
   
    menu_commands = ["🔄 استارت مجدد", "💰 فروش اکانت", "📂 اکانت‌های من", "📖 راهنما"]
    if text in menu_commands:
        context.user_data.pop('manual_form', None)
        return False
   
    text_length = len(text)
   
    if text_length < 250:
        await update.message.reply_text(
            "❌ خطا. لطفا فرم کامل بفرستید"
        )
        return True
   
    if text_length > 800:
        excess_chars = text_length - 800
        await update.message.reply_text(
            f"❌ خطا شما ({excess_chars}) کاراکتر بیشتر از حد مجاز فرستاده اید"
        )
        return True
   
    manual_state['form_text'] = text
    manual_state['step'] = 'form_received'
   
    form_display = f"""
📋 فرم ارسالی شما:
{text}
اگر از صحت اطلاعات وارد شده اطمینان دارید کلید ثبت عکس را انتخاب کنید
"""
   
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 ثبت عکس", callback_data="manual_add_photos")]
    ])
   
    await update.message.reply_text(form_display, reply_markup=keyboard)
    return True

async def handle_manual_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دریافت عکس‌های فرم دستی"""
    user = update.effective_user
    user_id = user.id
   
    manual_state = context.user_data.get('manual_form')
    if not manual_state or manual_state.get('step') != 'awaiting_photos':
        return False
   
    if not update.message.photo:
        return False
   
    photo = update.message.photo[-1]
    file_id = photo.file_id
   
    if 'photos' not in manual_state:
        manual_state['photos'] = []
    manual_state['photos'].append(file_id)
   
    current_count = len(manual_state['photos'])
    max_photos = config.PRICE_CONFIG["max_photos"]
   
    if current_count >= max_photos:
        manual_state['step'] = 'photos_received'
       
        await update.message.reply_text(
            f"✅ {current_count} عکس با موفقیت ثبت شد.\n\n"
            f"با انتخاب کلید ثبت نهایی فرم و عکس های شما برای ادمین ارسال خواهند شد",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ ثبت نهایی", callback_data="manual_final_submit")]
            ])
        )
    else:
        remaining = max_photos - current_count
        await update.message.reply_text(
            f"✅ عکس {current_count} ثبت شد.\n\n"
            f"📸 می‌توانید {remaining} عکس دیگر ارسال کنید."
        )
    return True

async def submit_manual_form_to_admin(context: ContextTypes.DEFAULT_TYPE, user_id: int, form_text: str, photos: list = None):
    """ارسال فرم دستی به ادمین برای تأیید"""
    try:
        user_info = f"👤 کاربر: {user_id}"
        full_message = f"{user_info}\n\n📝 فرم دستی:\n{form_text}"
       
        admin_buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ تأیید و انتشار", callback_data=f"admin_approve_manual|{user_id}"),
                InlineKeyboardButton("❌ رد آگهی", callback_data=f"admin_reject_manual|{user_id}")
            ]
        ])
       
        if photos and len(photos) > 0:
            await context.bot.send_photo(
                chat_id=ADMIN_USER_ID,
                photo=photos[0],
                caption=full_message,
                reply_markup=admin_buttons
            )
           
            for i in range(1, len(photos)):
                await context.bot.send_photo(
                    chat_id=ADMIN_USER_ID,
                    photo=photos[i]
                )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=full_message,
                reply_markup=admin_buttons
            )
           
        return True
       
    except Exception as e:
        logger.error(f"خطا در ارسال فرم دستی به ادمین: {e}")
        return False

# =========================
# هندل کردن callbacks فرم دستی
# =========================
async def handle_manual_form_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر مخصوص callbacks فرم دستی"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
   
    if data == "manual_final_submit":
        manual_state = context.user_data.get('manual_form')
       
        if not manual_state:
            await query.edit_message_text(
                "❌ خطا: اطلاعات فرم یافت نشد. لطفاً فرآیند را از ابتدا شروع کنید.",
                reply_markup=main_menu
            )
            return
       
        if not manual_state.get('form_text'):
            await query.edit_message_text(
                "❌ خطا: متن فرم یافت نشد. لطفاً فرآیند را از ابتدا شروع کنید.",
                reply_markup=main_menu
            )
            return
       
        if str(user_id) not in [ADMIN_USER_ID, SPECIAL_TESTER_ID]:
            u = get_user_row(user_id)
            if u and u.get("free_used"):
                await query.edit_message_text(
                    "❌ شما قبلاً یک اکانت رایگان ثبت کرده‌اید.",
                    reply_markup=main_menu
                )
                context.user_data.pop('manual_form', None)
                return
       
        photos = manual_state.get('photos', [])
       
        logger.info(f"ارسال فرم دستی به ادمین - کاربر: {user_id}, طول متن: {len(manual_state['form_text'])}, تعداد عکس: {len(photos)}")
       
        success = await submit_manual_form_to_admin(context, user_id, manual_state['form_text'], photos)
       
        if success:
            import json
            set_user_free_used(user_id)
           
            form_data = {
                'form_text': manual_state['form_text'],
                'photos_count': len(photos),
                'submission_type': 'manual'
            }
            record_listing(user_id=user_id, data_json=json.dumps(form_data, ensure_ascii=False))
           
            context.user_data.pop('manual_form', None)
           
            await query.edit_message_text(
                "✅ فرم و عکس های شما با موفقیت ثبت شدند.\n\n"
                "📋 پس از تأیید ادمین، آگهی شما در کانال نمایش داده خواهد شد.\n"
                "⏳ زمان بررسی: حداکثر 24 ساعت\n\n"
                "با تشکر از اعتماد شما! 🙏"
            )
        else:
            await query.edit_message_text(
                "❌ خطا در ارسال اطلاعات به ادمین. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید."
            )
        return
   
    elif data == "manual_add_photos":
        manual_state = context.user_data.get('manual_form')
        if manual_state and manual_state.get('step') == 'form_received':
            manual_state['step'] = 'awaiting_photos'
            await query.edit_message_text(
                "📸 لطفا حداکثر ۳ عکس از اکانت/تیم خود آپلود کنید"
            )
        return

# =========================
# هندل کردن callbacks اصلی منوی فروش
# =========================
async def handle_main_sale_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هندلر مخصوص callbacks منوی اصلی فروش"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    data = query.data
   
    if data == "manual_form":
        if str(user_id) not in [ADMIN_USER_ID, SPECIAL_TESTER_ID]:
            u = get_user_row(user_id)
            if u and u.get("free_used"):
                await query.edit_message_text(
                    "❌ شما قبلاً یک آگهی رایگان ثبت کرده‌اید.",
                    reply_markup=main_menu
                )
                return
       
        manual_form_template = config.TEXTS["manual_form_template"]
       
        context.user_data['manual_form'] = {
            'step': 'awaiting_form',
            'form_text': '',
            'photos': []
        }
       
        await query.edit_message_text(manual_form_template)
        return
   
    elif data == "bot_form":
        if str(user_id) not in [ADMIN_USER_ID, SPECIAL_TESTER_ID]:
            u = get_user_row(user_id)
            if u and u.get("free_used"):
                await query.edit_message_text(
                    "❌ شما قبلاً یک اکانت رایگان ثبت کرده‌اید.",
                    reply_markup=main_menu
                )
                return
       
        user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
       
        special_msg = "👑 [حالت ویژه - بدون محدودیت]" if str(user_id) in [ADMIN_USER_ID, SPECIAL_TESTER_ID] else ""
       
        msg = (
            f"🤖 **ربات فرم پیشرفته**\n\n"
            f"{special_msg}\n\n"
            f"💎 کاربر گرامی، شما می‌توانید **یک اکانت رایگان** برای آگهی در کانال ثبت کنید.\n\n"
            "⚠️ دقت کنید بعد از زدن **ثبت نهایی** امکان ویرایش اطلاعات اکانت رایگان وجود ندارد.\n\n"
            "🟢 لطفا گزینه مورد نظر خود را انتخاب کنید:"
        )
        await query.edit_message_text(msg, reply_markup=sale_menu, parse_mode="Markdown")
        return

# =========================
# Handlers
# =========================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
   
    member = await is_member_of_channel(context.bot, user.id)
    if not member:
        join_button = InlineKeyboardButton(text="پیوستن به کانال رنک1", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")
        i_joined_button = InlineKeyboardButton(text="✅ من عضو شدم (بررسی مجدد)", callback_data="check_join")
        keyboard_not_member = InlineKeyboardMarkup([[join_button], [i_joined_button]])
        await context.bot.send_message(chat_id=chat_id, text="لطفاً ابتدا در کانال رنک 1 عضو شوید.", reply_markup=keyboard_not_member)
        return
   
    welcome_message = config.TEXTS["welcome"].format(user.first_name)
    await context.bot.send_message(chat_id=chat_id, text=welcome_message, reply_markup=main_menu)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    data = query.data
   
    if data == "back_to_menu":
        context.user_data.pop('manual_form', None)
        user_form_state.pop(user_id, None)
        PLATFORM_STATES.pop(user_id, None)
        CHAR_COUNT_STATES.pop(user_id, None)
        NUMBER_VALIDATION_STATES.pop(user_id, None)
        DIVISION_VALIDATION_STATES.pop(user_id, None)
        PHOTO_UPLOAD_STATES.pop(user_id, None)
        PLAYER_VALUE_STATES.pop(user_id, None)
       
        await query.edit_message_text("🏠 به منوی اصلی برگشتید.", reply_markup=main_menu)
        return
   
    if data == "check_join":
        member = await is_member_of_channel(context.bot, user_id)
        if member:
            await query.edit_message_text("ممنون! شما عضو هستید. از منوی اصلی استفاده کنید.")
            try:
                await context.bot.send_message(chat_id=user_id, text="منوی اصلی:", reply_markup=main_menu)
            except:
                pass
        else:
            await query.edit_message_text("هنوز عضو کانال نیستید. لطفا عضو شوید.")
        return
   
    if data == "continue_to_form":
        await query.edit_message_text(
            "به فرم اصلی برگشتید. لطفاً سایر فیلدها را تکمیل کنید:",
            reply_markup=sale_menu
        )
        PLATFORM_STATES.pop(user_id, None)
        return
   
    if data == "show_entered_data":
        state = user_form_state.get(user_id)
        if not state or not state.get("form"):
            await query.edit_message_text(
                "❌ هنوز هیچ اطلاعاتی ثبت نکرده‌اید.\n\n"
                "لطفا ابتدا اطلاعات فرم را تکمیل کنید.",
                reply_markup=sale_menu
            )
            return
       
        form_display = generate_complete_form_display(state['form'])
       
        back_button = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ برگشت و ویرایش", callback_data="back_to_form")]
        ])
       
        await query.edit_message_text(
            form_display,
            reply_markup=back_button
        )
        return
   
    if data == "sale_method":
        await query.edit_message_text(
            SALE_RULES_TEXT,
            reply_markup=sale_rules_buttons,
            parse_mode="Markdown"
        )
        return
   
    if data == "accept_rules":
        await query.edit_message_text(
            "✅ با تشکر از پذیرش قوانین\n\n"
            "لطفا نحوه فروش خود را انتخاب کنید:",
            reply_markup=sale_method_choice_buttons
        )
        return
   
    if data == "back_to_rules":
        await query.edit_message_text(
            SALE_RULES_TEXT,
            reply_markup=sale_rules_buttons,
            parse_mode="Markdown"
        )
        return
   
    if data == "sale_method_self":
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
       
        if 'purchase_link' in state['form']:
            del state['form']['purchase_link']
       
        user_contact = f"@{user.username}" if user.username else f"{user.first_name} {user.last_name or ''}".strip()
        if not user_contact or user_contact == "@":
            user_contact = f"UserID: {user_id}"
       
        state['form']['sale_method'] = "ثبت آیدی خودم"
        state['form']['user_contact'] = user_contact
       
        await query.edit_message_text(
            f"✅ روش فروش ثبت شد: **ثبت آیدی خودم**\n"
            f"📱 آیدی شما: `{user_contact}`\n\n"
            f"این اطلاعات در فرم شما ذخیره شد.",
            parse_mode="Markdown",
            reply_markup=sale_menu
        )
        return
   
    if data == "sale_method_channel":
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
       
        if 'user_contact' in state['form']:
            del state['form']['user_contact']
       
        purchase_link = generate_purchase_link(user_id, state.get('form', {}))
       
        state['form']['sale_method'] = "فروش از طریق کانال"
        state['form']['purchase_link'] = purchase_link
       
        await query.edit_message_text(
            f"✅ روش فروش ثبت شد: **فروش از طریق کانال**\n"
            f"🛒 لینک خرید مخصوص شما:\n`{purchase_link}`\n\n"
            f"این لینک پس از تایید نهایی در کانال قرار خواهد گرفت.",
            parse_mode="Markdown",
            reply_markup=sale_menu
        )
        return
   
    if data == "email_type":
        await query.edit_message_text(
            "📧 نوع ایمیل اکانت خود را انتخاب کنید:",
            reply_markup=email_type_menu
        )
        return
   
    if data == "web_app":
        await query.edit_message_text(
            "🌐 نوع ترنسفر وب اپ اکانت را انتخاب کنید:",
            reply_markup=web_app_menu
        )
        return
   
    if data.startswith("email_"):
        email_type = data.split("_")[1]
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
        state['form']['email_type'] = config.EMAIL_TYPES.get(email_type, "سایر")
       
        await query.edit_message_text(
            f"✅ نوع ایمیل ثبت شد: {config.EMAIL_TYPES.get(email_type, 'سایر')}",
            reply_markup=sale_menu
        )
        return
   
    if data.startswith("web_"):
        web_type = data.split("_")[1]
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
        state['form']['web_app'] = config.WEB_APP_TYPES.get(web_type, "وب بسته")
       
        await query.edit_message_text(
            f"✅ نوع وب اپ ثبت شد: {config.WEB_APP_TYPES.get(web_type, 'وب بسته')}",
            reply_markup=sale_menu
        )
        return
   
    if data == "platform":
        await handle_platform_selection(update, context)
        return
   
    if data == "back_to_form":
        await query.edit_message_text(
            "به فرم اصلی برگشتید. لطفا فیلدها را تکمیل کنید:",
            reply_markup=sale_menu
        )
        PLATFORM_STATES.pop(user_id, None)
        CHAR_COUNT_STATES.pop(user_id, None)
        NUMBER_VALIDATION_STATES.pop(user_id, None)
        DIVISION_VALIDATION_STATES.pop(user_id, None)
        PHOTO_UPLOAD_STATES.pop(user_id, None)
        PLAYER_VALUE_STATES.pop(user_id, None)
        return
   
    if data == "back_to_platform":
        await handle_platform_selection(update, context)
        return
   
    if data.startswith("platform_"):
        platform_type = data.split("_")[1]
        PLATFORM_STATES[user_id] = {
            'step': 'select_subplatform',
            'platform': platform_type
        }
       
        if platform_type == "ps":
            await show_ps_options(query)
        elif platform_type == "xbox":
            await show_xbox_options(query)
        elif platform_type == "pc":
            await show_pc_options(query)
        return
   
    if data.startswith("subplatform_"):
        sub_type = data.split("_")[1]
        state = PLATFORM_STATES.get(user_id, {})
       
        if state.get('platform') == 'pc' and sub_type == 'eaplay':
            PLATFORM_STATES[user_id]['step'] = 'enter_eaplay_days'
            PLATFORM_STATES[user_id]['subplatform'] = sub_type
           
            await query.edit_message_text(
                "📅 چند روز از اعتبار EA Play Pro اکانت شما باقی مانده؟\n"
                "لطفاً عدد تعداد روزهای باقیمانده را ارسال کنید (مثال: 110)\n\n"
                "↩️ /back برای برگشت"
            )
        else:
            await finalize_platform_selection(query, user_id, state['platform'], sub_type)
        return
   
    if data == "estimate_price":
        state = user_form_state.get(user_id)
        if not state or not state.get("form"):
            await query.edit_message_text(
                "❌ هنوز اطلاعات کافی برای تخمین قیمت وارد نکرده‌اید.\n\n"
                "لطفا حداقل فیلدهای زیر را پر کنید:\n"
                "• کوین اکانت\n• بازیکنان ترید/آنترید\n• وب اپ",
                reply_markup=sale_menu
            )
            return
       
        form_data = state['form']
        required_fields = ['coin_account']
       
        missing_fields = []
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                missing_fields.append(field)
       
        if missing_fields:
            await query.edit_message_text(
                f"❌ برای تخمین قیمت نیاز به پر کردن فیلدهای زیر دارید:\n"
                f"• {', '.join(missing_fields)}\n\n"
                f"لطفا ابتدا این فیلدها را پر کنید.",
                reply_markup=sale_menu
            )
            return
       
        result = estimate_price(form_data)
       
        if result['success']:
            message = f"{result['estimate']}\n\n{result['details']}\n\n"
            message += "⚠️ کاربر محترم قیمت ربات حدودی است و ممکن است اطلاعات ربات به روز نباشد"
           
            await query.edit_message_text(
                message,
                reply_markup=sale_menu
            )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}",
                reply_markup=sale_menu
            )
        return
   
    if data == "price":
        NUMBER_VALIDATION_STATES[user_id] = {
            'field': 'price',
            'max_digits': config.PRICE_CONFIG["digit_limits"]["price"],
            'only_numbers': True
        }
        await query.edit_message_text(
            "💵 قیمت اکانت خود را وارد کنید.\n"
            "مثال: 250000\n\n"
            "ℹ️ فقط عدد انگلیسی باشد\n"
            f"🔢 حداکثر {config.PRICE_CONFIG['digit_limits']['price']} رقم مجاز است",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return
   
    if data == "final_submit":
        state = user_form_state.get(user_id)
        if not state or not state.get("form"):
            await query.edit_message_text("فرم خالی است. لطفا فیلدها را تکمیل کنید قبل از ثبت نهایی.")
            return
       
        confirmation_text = """
⚠️ کاربر محترم با زدن ثبت نهایی، اطلاعات اکانت شما برای درج در کانال به ادمین ارسال خواهد شد.
🔍 خواهشمند است قبل از ثبت نهایی از صحت اطلاعات زیر اطمینان حاصل کنید:
{form_display}
آیا از ثبت نهایی اطلاعات مطمئن هستید؟
""".format(form_display=generate_complete_form_display(state['form']))
       
        await query.edit_message_text(
            confirmation_text,
            reply_markup=final_confirmation_buttons
        )
        return
   
    if data == "confirm_final_submit":
        state = user_form_state.get(user_id)
        if not state or not state.get("form"):
            await query.edit_message_text("خطا: اطلاعات فرم یافت نشد.")
            return
       
        photos = state['form'].get('team_photos', [])
        success = await send_form_to_admin(context, user_id, state['form'], photos)
       
        if success:
            import json
            set_user_free_used(user_id)
            record_listing(user_id=user_id, data_json=json.dumps(state["form"], ensure_ascii=False))
           
            user_form_state.pop(user_id, None)
            PLATFORM_STATES.pop(user_id, None)
            CHAR_COUNT_STATES.pop(user_id, None)
            NUMBER_VALIDATION_STATES.pop(user_id, None)
            DIVISION_VALIDATION_STATES.pop(user_id, None)
            PHOTO_UPLOAD_STATES.pop(user_id, None)
            PLAYER_VALUE_STATES.pop(user_id, None)
           
            await query.edit_message_text(
                "✅ اطلاعات اکانت شما با موفقیت ثبت شد و برای بررسی به ادمین ارسال گردید.\n\n"
                "📋 پس از تأیید ادمین، آگهی شما در کانال نمایش داده خواهد شد.\n"
                "⏳ زمان بررسی: حداکثر 24 ساعت\n\n"
                "با تشکر از اعتماد شما! 🙏"
            )
        else:
            await query.edit_message_text(
                "❌ خطا در ارسال اطلاعات به ادمین. لطفاً مجدداً تلاش کنید یا با پشتیبانی تماس بگیرید."
            )
        return
   
    if data in {
        "coin_account", "trade_players", "non_trade_players",
        "match_earning", "season_level", "division_rivals", "team_photo"
    }:
        state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
       
        field_name = data
       
        if data == "non_trade_players":
            CHAR_COUNT_STATES[user_id] = {
                'field': 'non_trade_players',
                'max_chars': config.PRICE_CONFIG["char_limits"]["non_trade_players"],
                'remaining': config.PRICE_CONFIG["char_limits"]["non_trade_players"]
            }
            await query.edit_message_text(
                "❌ لطفا نام برترین بازیکنان آنترید خود را وارد کنید.\n"
                "مثال: امباپه دیونگ پدری\n\n"
                f"📝 تعداد کاراکترهای باقیمانده: {config.PRICE_CONFIG['char_limits']['non_trade_players']}/{config.PRICE_CONFIG['char_limits']['non_trade_players']}\n"
                f"⚠️ حداکثر {config.PRICE_CONFIG['char_limits']['non_trade_players']} کاراکتر مجاز است",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
       
        if data == "trade_players":
            CHAR_COUNT_STATES[user_id] = {
                'field': 'trade_players',
                'max_chars': config.PRICE_CONFIG["char_limits"]["trade_players"],
                'remaining': config.PRICE_CONFIG["char_limits"]["trade_players"]
            }
            await query.edit_message_text(
                "❌ لطفا نام برترین بازیکنان ترید خود را وارد کنید.\n"
                "مثال: امباپه دیونگ پدری\n\n"
                f"📝 تعداد کاراکترهای باقیمانده: {config.PRICE_CONFIG['char_limits']['trade_players']}/{config.PRICE_CONFIG['char_limits']['trade_players']}\n"
                f"⚠️ حداکثر {config.PRICE_CONFIG['char_limits']['trade_players']} کاراکتر مجاز است",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
       
        if data == "coin_account":
            NUMBER_VALIDATION_STATES[user_id] = {
                'field': 'coin_account',
                'max_digits': config.PRICE_CONFIG["digit_limits"]["coin_account"],
                'only_numbers': True
            }
            await query.edit_message_text(
                "💰 لطفا مقدار کوین اکانت را وارد کنید.\n"
                "مثال: 245000\n\n"
                "ℹ️ فقط عدد انگلیسی باشد\n"
                f"🔢 حداکثر {config.PRICE_CONFIG['digit_limits']['coin_account']} رقم مجاز است",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
        
        if data == "match_earning":
            NUMBER_VALIDATION_STATES[user_id] = {
                'field': 'match_earning',
                'max_digits': config.PRICE_CONFIG["digit_limits"]["match_earning"],
                'only_numbers': True
            }
            await query.edit_message_text(
                "🏆 لطفا مچ ارنینگ را وارد کنید.\n"
                "مثال: 1200\n\n"
                "ℹ️ فقط عدد انگلیسی باشد\n"
                f"🔢 حداکثر {config.PRICE_CONFIG['digit_limits']['match_earning']} رقم مجاز است",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
        
        if data == "season_level":
            NUMBER_VALIDATION_STATES[user_id] = {
                'field': 'season_level',
                'max_digits': config.PRICE_CONFIG["digit_limits"]["season_level"],
                'only_numbers': True
            }
            await query.edit_message_text(
                "⭐ لطفا لول سیزن را وارد کنید.\n"
                "مثال: 5\n\n"
                "ℹ️ فقط عدد انگلیسی باشد\n"
                f"🔢 حداکثر {config.PRICE_CONFIG['digit_limits']['season_level']} رقم مجاز است",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
        
        if data == "division_rivals":
            DIVISION_VALIDATION_STATES[user_id] = {
                'field': 'division_rivals'
            }
            await query.edit_message_text(
                "🏅 لطفا دیویژن رایوالز را وارد کنید:\n"
                "- یک کلمه 5 حرفی (مثلاً: Elite)\n"
                "- یا یک عدد از 1 تا 10",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
        
        if data == "team_photo":
            PHOTO_UPLOAD_STATES[user_id] = {
                'field': 'team_photos',
                'max_photos': config.PRICE_CONFIG["max_photos"],
                'photos': []
            }
            await query.edit_message_text(
                "📸 لطفا حداکثر 3 عکس از اکانت خود ارسال کنید.\n\n"
                "📌 محدودیت‌ها:\n"
                "• فقط فایل‌های عکس مجاز هستند\n"
                "• فرمت‌های قابل قبول: JPG, JPEG, PNG, WEBP\n"
                "• فایل‌های غیر عکس بلاک و حذف می‌شوند\n\n"
                "↩️ /back برای برگشت به فرم",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                ])
            )
            return
       
        state['awaiting_field'] = field_name
        prompts = {
            "sale_method": "📝 نحوه فروش را توضیح دهید (مثلاً ارسال آنی / پس از واریز)."
        }
        prompt_text = prompts.get(field_name, "لطفا مقدار را وارد کنید:")
        await query.edit_message_text(prompt_text)
        return
   
    await query.edit_message_text("دکمه شناخته نشد.")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = (update.message.text or "").strip()
   
    if await handle_manual_form_text(update, context):
        return
   
    if await handle_player_value_message(update, context):
        return
   
    if await handle_number_validation_message(update, context):
        return
   
    if await handle_char_count_message(update, context):
        return
    
    if await handle_division_validation_message(update, context):
        return
    
    if await handle_photo_upload_message(update, context):
        return
   
    platform_state = PLATFORM_STATES.get(user_id, {})
    if platform_state.get('step') == 'enter_eaplay_days':
        await handle_eaplay_days_input(update, context)
        return
   
    state = user_form_state.get(user_id)
    if state and state.get("awaiting_field"):
        field = state['awaiting_field']
        state['form'][field] = text
        state['awaiting_field'] = None
        await update.message.reply_text(f"✅ مقدار '{field}' ثبت شد.", reply_markup=sale_menu)
        return
   
    if text == "/start" or text == "🔄 استارت مجدد":
        await start_command(update, context)
        return
    
    if text == "📖 راهنما":
        await update.message.reply_text(GUIDE_TEXT, reply_markup=main_menu)
        return
   
    if text == "💰 فروش اکانت":
        member = await is_member_of_channel(context.bot, user_id)
        if not member:
            join_button = InlineKeyboardButton(text="پیوستن به کانال رنک1", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")
            i_joined_button = InlineKeyboardButton(text="✅ من عضو شدم (بررسی مجدد)", callback_data="check_join")
            keyboard_not_member = InlineKeyboardMarkup([[join_button], [i_joined_button]])
            await update.message.reply_text("لطفاً ابتدا در کانال رنک 1 عضو شوید.", reply_markup=keyboard_not_member)
            return
       
        if str(user_id) not in [ADMIN_USER_ID, SPECIAL_TESTER_ID]:
            u = get_user_row(user_id)
            if u and u.get("free_used"):
                await update.message.reply_text(
                    "❌ شما قبلاً یک آگهی رایگان ثبت کرده‌اید.",
                    reply_markup=main_menu
                )
                return
       
        msg = (
            "💎 کاربر گرامی، لطفاً روش فروش خود را انتخاب کنید:\n\n"
            "📝 **فرم دستی**: پر کردن فرم متنی ساده\n"
            "🤖 **ربات**: پر کردن فرم پیشرفته با راهنمای گام به گام"
        )
        await update.message.reply_text(msg, reply_markup=sale_method_selection_buttons, parse_mode="Markdown")
        return
   
    if text == "📂 اکانت‌های من":
        u = get_user_row(user_id)
        txt = "📁 وضعیت شما:\n"
        txt += f"آگهی رایگان ثبت کرده‌اید: {'✅' if u and u.get('free_used') else '❌'}\n\n"
        
        listings = get_user_listings(user_id)
        if listings:
            txt += "📂 آگهی‌های فعال شما:\n"
            keyboard = []
            for listing in listings:
                txt += f"آگهی ID {listing['id']}\n"
                keyboard.append([InlineKeyboardButton(f"✏️ ویرایش آگهی {listing['id']}", callback_data=f"edit_listing|{listing['id']}")])
            reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else main_menu
        else:
            txt += "هیچ آگهی فعالی ندارید."
            reply_markup = main_menu
        
        await update.message.reply_text(txt, reply_markup=reply_markup)
        return
   
    await update.message.reply_text("لطفا از منو انتخاب کنید.", reply_markup=main_menu)

# =========================
# هندلر عکس‌ها
# =========================
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
   
    if await handle_manual_photos(update, context):
        return
   
    photo_state = PHOTO_UPLOAD_STATES.get(user_id)
    if photo_state:
        if not update.message.photo:
            if update.message.document:
                await update.message.reply_text(
                    "❌ فقط فایل‌های عکس مجاز هستند!\n\n"
                    "لطفا فقط عکس ارسال کنید (فرمت‌های مجاز: JPG, JPEG, PNG, WEBP)",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
                    ])
                )
            return
       
        photo = update.message.photo[-1]
        file_id = photo.file_id
       
        photo_state['photos'].append(file_id)
       
        current_count = len(photo_state['photos'])
        max_photos = photo_state['max_photos']
       
        if current_count >= max_photos:
            state = user_form_state.setdefault(user_id, {"awaiting_field": None, "form": {}, "pending_listing_id": None})
            state['form']['team_photos'] = photo_state['photos']
           
            PHOTO_UPLOAD_STATES.pop(user_id, None)
           
            await update.message.reply_text(
                f"✅ {current_count} عکس با موفقیت ثبت شد.\n\n"
                f"به فرم اصلی برگشتید.",
                reply_markup=sale_menu
            )
        else:
            remaining = max_photos - current_count
            await update.message.reply_text(
                f"✅ عکس {current_count} ثبت شد.\n\n"
                f"📸 می‌توانید {remaining} عکس دیگر ارسال کنید.\n"
                f"یا برای بازگشت به فرم از دکمه زیر استفاده کنید:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ برگشت به فرم", callback_data="back_to_form")]
                ])
            )
        return

# =========================
# هندلر فایل‌های مستند
# =========================
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
   
    photo_state = PHOTO_UPLOAD_STATES.get(user_id)
    if photo_state:
        await update.message.reply_text(
            "❌ فقط فایل‌های عکس مجاز هستند!\n\n"
            "لطفا فقط عکس ارسال کنید (فرمت‌های مجاز: JPG, JPEG, PNG, WEBP)\n\n"
            "فایل‌های دیگر مانند PDF, ZIP, MP4 و... پذیرفته نمی‌شوند.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ برگشت", callback_data="back_to_form")]
            ])
        )
        return
   
    await update.message.reply_text(
        "❌ این نوع فایل پشتیبانی نمی‌شود.\n\n"
        "لطفا فقط از گزینه‌های منو استفاده کنید.",
        reply_markup=main_menu
    )

# =========================
# Callback برای دکمه‌های ادمین
# =========================
async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    parts = data.split("|")
    action = parts[0] if parts else None
   
    if action == "admin_approve_free" and len(parts) == 2:
        user_id = int(parts[1])
       
        try:
            await context.bot.send_message(
                user_id,
                "🎉 آگهی رایگان شما توسط ادمین تأیید شد!\n\n"
                "✅ اکنون آگهی شما در کانال فعال شده و به مدت ۱۰ روز نمایش داده خواهد شد.\n"
                "با تشکر از انتخاب شما! 💎"
            )
        except Exception:
            logger.warning("ارسال پیام تأیید به کاربر با خطا مواجه شد.")
       
        await query.edit_message_text("✅ آگهی رایگان تأیید و در کانال منتشر شد.")
        return
    
    if action == "admin_reject_free" and len(parts) == 2:
        user_id = int(parts[1])
       
        try:
            await context.bot.send_message(
                user_id,
                "❌ متأسفانه آگهی رایگان شما توسط ادمین رد شد.\n\n"
                "📋 دلایل احتمالی:\n"
                "• اطلاعات ناقص یا نادرست\n"
                "• عکس‌های نامناسب\n"
                "• مغایرت با قوانین کانال\n\n"
                "🔧 لطفاً اطلاعات را بررسی کرده و مجدداً ثبت کنید.\n"
                "📞 برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
            )
        except Exception:
            logger.warning("ارسال پیام رد به کاربر با خطا مواجه شد.")
       
        await query.edit_message_text("❌ آگهی رایگان رد شد و کاربر مطلع گردید.")
        return
   
    if action == "admin_approve_manual" and len(parts) == 2:
        user_id = int(parts[1])
       
        try:
            await context.bot.send_message(
                user_id,
                "🎉 آگهی فرم دستی شما توسط ادمین تأیید شد!\n\n"
                "✅ اکنون آگهی شما در کانال فعال شده و به مدت ۱۰ روز نمایش داده خواهد شد.\n"
                "با تشکر از انتخاب شما! 💎"
            )
        except Exception:
            logger.warning("ارسال پیام تأیید به کاربر با خطا مواجه شد.")
       
        await query.edit_message_text("✅ آگهی فرم دستی تأیید و در کانال منتشر شد.")
        return
    
    if action == "admin_reject_manual" and len(parts) == 2:
        user_id = int(parts[1])
       
        try:
            await context.bot.send_message(
                user_id,
                "❌ متأسفانه آگهی فرم دستی شما توسط ادمین رد شد.\n\n"
                "📋 دلانی احتمالی:\n"
                "• اطلاعات ناقص یا نادرست\n"
                "• عکس‌های نامناسب\n"
                "• مغایرت با قوانین کانال\n\n"
                "🔧 لطفاً اطلاعات را بررسی کرده و مجدداً ثبت کنید.\n"
                "📞 برای اطلاعات بیشتر با پشتیبانی تماس بگیرید."
            )
        except Exception:
            logger.warning("ارسال پیام رد به کاربر با خطا مواجه شد.")
       
        await query.edit_message_text("❌ آگهی فرم دستی رد شد و کاربر مطلع گردید.")
        return
   
    await query.edit_message_text("دستور ناشناخته برای ادمین.")

# =========================
# اجرای بات
# =========================
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
   
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_"))
   
    app.add_handler(CallbackQueryHandler(handle_main_sale_callbacks, pattern=r"^(manual_form|bot_form)$"))
   
    app.add_handler(CallbackQueryHandler(handle_manual_form_callbacks, pattern=r"^manual_"))
   
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
   
    logger.info("Bot started (polling).")
    app.run_polling()

if __name__ == "__main__":
    main()
