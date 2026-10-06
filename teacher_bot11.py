import os
import asyncio
import sys
from google.genai.errors import APIError
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import asyncpg
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut, RetryAfter, TelegramError, Forbidden
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from google import genai
import uuid
from zoneinfo import ZoneInfo
import io
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes



# FastAPI & Uvicorn imports for Port Binding
from fastapi import FastAPI
import uvicorn
import threading

load_dotenv()

# Environment Variables
TEACHER_BOT_TOKEN = os.getenv("TEACHER_BOT_TOKEN")
STUDENT_BOT_TOKEN = os.getenv("STUDENT_BOT_TOKEN")
TEACHER_CHAT_ID = int(os.getenv("TEACHER_CHAT_ID", "0"))
HEAD_TEACHER_CHAT_ID = int(os.getenv("HEAD_TEACHER_CHAT_ID", "0"))
DEVELOPER_CHAT_ID = int(os.getenv("DEVELOPER_CHAT_ID", "0"))

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Admin IDs စာရင်းထဲသို့ ထည့်သွင်းခြင်း (0 မဟုတ်သူများကိုသာ ယူမည်)
ADMIN_IDS = [
    cid for cid in (TEACHER_CHAT_ID, DEVELOPER_CHAT_ID, HEAD_TEACHER_CHAT_ID) 
    if cid != 0
]

def is_admin(user_id: int) -> bool:
    """ဆရာ သို့မဟုတ် Developer ဟုတ်/မဟုတ် စစ်ဆေးပေးသည့် Helper"""
    return user_id in ADMIN_IDS

student_bot = Bot(token=STUDENT_BOT_TOKEN)

# Supabase Credentials
DB_USER = "postgres.vqcoaukndkspyddpnvhd"
DB_PASSWORD = "F2%e.b6ed4/96y!"
DB_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
DB_PORT = 5432
DB_NAME = "postgres"

MM_TZ = timezone(timedelta(hours=6, minutes=30))
db_pool = None
#scheduler = None
# Scheduler ကို ပုံမှန် တည်ဆောက်ပေးရပါမည် (None ဖြစ်မနေရပါ)
scheduler = AsyncIOScheduler(timezone=MM_TZ)

# Gemini Client စတင်ခြင်း
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


# ================= FastAPI Web Server (Port Binding) =================

app = FastAPI()

@app.get("/")
@app.head("/")  # <--- UptimeRobot ရဲ့ HEAD request ကိုပါ လက်ခံရန် ဤနေရာတွင် ထည့်ပေးပါ
def health_check():
    return {"status": "active", "bot": "Teacher Bot is running successfully!"}

def run_fastapi():
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

# ================= Database Helpers =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        min_size=1,
        max_size=5,
    )
    print("✅ Connected to Supabase Pooler.")

async def close_db():
    global db_pool
    if db_pool:
        await db_pool.close()

# ----------------- Gemini AI Analysis Function -----------------

async def analyze_weekly_data_with_gemini(student_records_text: str) -> str:
    """သင်တန်းသားများ၏ တစ်ပတ်တာ ဒေတာနှင့် မေးခွန်းများကို Gemini AI ဖြင့် သုံးသပ်ချက် ရေးသားခြင်း"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ Gemini API Key မရှိသဖြင့် AI သုံးသပ်ချက် မထုတ်နိုင်ပါ။"

    prompt = f"""
သင်သည် သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ရေး (Mindfulness Routine) သင်တန်းမှ ဆရာကြီးအတွက် အကူလက်ထောက် AI ဖြစ်ပါသည်။
အောက်ပါတို့သည် ပြီးခဲ့သည့် (၇) ရက်အတွင်း သင်တန်းသားများ၏ လေ့ကျင့်မှု ပြီးစီးမှု မှတ်တမ်းများနှင့် ၎င်းတို့ မေးမြန်း/ဆွေးနွေးထားသော အတွေ့အကြုံများ ဖြစ်ပါသည်:

{student_records_text}

ဆရာကြီး အလွယ်တကူ သုံးသပ်နိုင်ရန်အတွက် အောက်ပါအတိုင်း မြန်မာလို အနှစ်ချုပ် ရေးသားပေးပါ:
၁။ သင်တန်းသားတစ်ဦးချင်းစီ၏ လေ့ကျင့်မှု အားသာချက်/အားနည်းချက်၊ တရားထိုင်ခြင်း၊ ထိုင်ဖို့ပျက်ကွက်ခြင်း (ဥပမာ- မနက်ပိုင်း ပုံမှန်လုပ်နိုင်သော်လည်း ညနေပိုင်း အားနည်းခြင်း စသည်)
၂။ သင်တန်းသားများ၏ စိတ်ပိုင်းဆိုင်ရာ အတွေ့အကြုံ သို့မဟုတ် အခက်အခဲများအပေါ် ဆရာကြီး အဓိက သတိပြု လမ်းညွှန်ပေးသင့်သည့် အချက် (Actionable Insight)
၃။ စာဖတ်ရ လွယ်ကူစေရန် Bullet points များနှင့် သပ်ရပ်စွာ လိုတိုရှင်းရေးပေးပါ။
"""

    #candidate_models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

    client = genai.Client(api_key=api_key)

    for model_name in candidate_models:
        for attempt in range(3):
            try:
                def call_gemini():
                    return client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                    )

                response = await asyncio.to_thread(call_gemini)
                if response and response.text:
                    return response.text

            except (ConnectionResetError, OSError) as net_err:
                wait_sec = (attempt + 1) * 2
                print(f"⚠️ [Network] on {model_name}. Retrying in {wait_sec}s... ({net_err})")
                await asyncio.sleep(wait_sec)
                client = genai.Client(api_key=api_key)

            except APIError as api_err:
                if api_err.code in (503, 429):
                    wait_sec = (attempt + 1) * 3
                    print(f"⚠️ Gemini {model_name} busy (Code {api_err.code}). Retrying in {wait_sec}s...")
                    await asyncio.sleep(wait_sec)
                else:
                    print(f"❌ API Error on {model_name}: {api_err}")
                    break

            except Exception as e:
                print(f"❌ Unexpected error with {model_name}: {e}")
                break

    return "⚠️ AI သုံးသပ်ချက် ရယူရာတွင် Network ချိတ်ဆက်မှု ခေတ္တ အခက်အခဲရှိနေပါသဖြင့် နောက်တစ်ကြိမ် ပြန်လည်ကြိုးစားပေးပါခင်ဗျာ။"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ ဤ Bot သည် ဆရာနှင့် Admin သီးသန့် Bot ဖြစ်ပါသည်။")
        return

    text = (
        f"မင်္ဂလာပါ {update.effective_user.first_name} 🙏 (Admin/Teacher)\n\n"
        "သင်တန်းသားများ၏ တရားလေ့ကျင့်မှု စောင့်ကြည့်စစ်ဆေးနိုင်သော Command များ:\n\n"
        "• `/today` - ယနေ့ သင်တန်းသားများ၏ Routine ပြီးစီးမှု ရာခိုင်နှုန်း စစ်ဆေးရန်\n"
        "• `/questions` - သင်တန်းသားများ မေးထားသော မေးခွန်း/အတွေ့အကြုံများ ဖတ်ရှုရန်\n"
        "• `/reply <id> <text>` - သင်တန်းသားထံ တိုက်ရိုက် စာပြန်ရန်\n"
        "• `/weekly_report` - တစ်ပတ်တာ အစီရင်ခံစာနှင့် AI သုံးသပ်ချက် ထုတ်ယူရန်\n"
        "• `/send_routine <morning|afternoon|evening>` - သင်တန်းသားအားလုံးသို့ ပို့ရန်\n"
        "• `/detail_report <student_id>` - သင်တန်းသားတစ်ဦးချင်း အသေးစိတ် ဒေတာကြည့်ရန်\n"
        "• `/ai_report <student_id>` - သင်တန်းသားတစ်ဦးချင်း AI သုံးသပ်ချက် ထုတ်ရန်\n"
        "• `/jobs` - လက်ရှိ Schedule အချိန်ဇယားများ စစ်ဆေးရန်"
    )
    await update.message.reply_text(text)

async def today_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    today = datetime.now(MM_TZ).date()
    query = """
        SELECT s.student_name,
               COUNT(log.id) FILTER (WHERE log.is_done = TRUE) as completed_tasks,
               (SELECT COUNT(*) FROM routine_templates WHERE status = 'active') as total_tasks
        FROM students s
        LEFT JOIN student_daily_logs log 
            ON s.student_id = log.student_id AND log.log_date = $1
        GROUP BY s.student_id, s.student_name;
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, today)

    if not rows:
        await update.message.reply_text("ယနေ့အတွက် သင်တန်းသားများဘက်မှ မှတ်တမ်း မရှိသေးပါခင်ဗျာ။")
        return

    lines = [f"📊 *ယနေ့ ({today.strftime('%d/%m/%Y')}) သင်တန်းသားများ လေ့ကျင့်မှု အခြေအနေ -*\n"]
    for r in rows:
        done = r["completed_tasks"]
        total = r["total_tasks"]
        pct = int((done / total * 100)) if total > 0 else 0
        lines.append(f"• *{r['student_name']}*: `{done}/{total}` ပုဒ် ပြီးစီး ({pct}%)")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def view_questions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    query = """
        SELECT id, student_id, student_name, reflection_text, created_at, teacher_reply
        FROM student_reflections
        ORDER BY created_at DESC
        LIMIT 10;
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query)

    if not rows:
        await update.message.reply_text("မေးခွန်းနှင့် ဆွေးနွေးချက်များ မရှိသေးပါခင်ဗျာ။")
        return

    lines = ["📝 *နောက်ဆုံး လက်ခံရရှိထားသော မေးခွန်း/အတွေ့အကြုံများ -*\n"]
    for r in rows:
        created = r["created_at"].astimezone(MM_TZ).strftime("%d/%m %I:%M %p")
        status = "✅ ဖြေပြီး" if r["teacher_reply"] else "⏳ မဖြေရသေး"
        lines.append(
            f"👤 *{r['student_name']}* (ID: `{r['student_id']}`) - `{created}` [{status}]\n"
            f"❓ \"_{r['reflection_text']}_\"\n"
        )

    lines.append("အကြောင်းပြန်ရန်: `/reply <student_id> <စာသား>`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def reply_to_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sender_name = update.effective_user.first_name
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ ခွင့်ပြုချက်မရှိပါ။")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/reply <student_id> <စာသား>`\nဥပမာ: `/reply 8999991129 great! keep up`")
        return

    try:
        student_id = int(context.args[0])
        reply_message = " ".join(context.args[1:])

        async with Bot(token=STUDENT_BOT_TOKEN) as s_bot:
            await s_bot.send_message(
                chat_id=student_id,
                text=f"💌 *ဆရာ့ထံမှ အကြောင်းပြန်ကြားချက် ရောက်ရှိပါသည် -*\n\n{reply_message}",
            )

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE student_reflections 
                SET teacher_reply = $1 
                WHERE id = (
                    SELECT id FROM student_reflections 
                    WHERE student_id = $2 
                    ORDER BY created_at DESC LIMIT 1
                );
                """,
                reply_message, student_id
            )

        await update.message.reply_text(f"✅ သင်တန်းသား ID `{student_id}` ထံသို့ စာအောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။", parse_mode="Markdown")

        # အခြား Admin ဆီသို့လည်း အကြောင်းပြန်ကြားပြီးကြောင်း Sync ပေးခြင်း
        sync_text = f"ℹ️ *{sender_name}* က သင်တန်းသား (ID: `{student_id}`) ထံ စာပြန်လိုက်ပါသည်:\n\"{reply_message}\""
        for aid in ADMIN_IDS:
            if aid != user_id:
                try:
                    await context.bot.send_message(chat_id=aid, text=sync_text, parse_mode="Markdown")
                except Exception:
                    pass

    except Exception as e:
        await update.message.reply_text(f"❌ ပို့ဆောင်၍ မရပါ Error: `{e}`", parse_mode="Markdown")


async def handle_send_routine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Loading မလည်အောင် ချက်ချင်း ဖြေကြားပေးခြင်း

    data = query.data  # ပုံစံ: send_routine:morning:12345678
    parts = data.split(":")
    
    section = parts[1]  # morning, afternoon (သို့) evening
    target_student_id = int(parts[2]) if len(parts) > 2 else None

    # Section အလိုက် Header သတ်မှတ်ခြင်း
    header_titles = {
        "morning": "🌅 *မနက်ပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*",
        "afternoon": "☀️ *နေ့လည်ပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*",
        "evening": "🌙 *ညနေပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*"
    }
    header_text = header_titles.get(section, "📌 *နေ့စဉ် လေ့ကျင့်ခန်းများ*")

    try:
        # Student Bot Token ကို သုံးပြီး သက်ဆိုင်ရာ သင်တန်းသားထံ Routine ပို့ရန် Bot instance ဖန်တီးခြင်း
        student_bot = Bot(token=STUDENT_BOT_TOKEN)

        # Supabase DB မှ သက်ဆိုင်ရာ Section ရဲ့ Routine Tasks များကို ဆွဲထုတ်ရန်
        # (Teacher Bot ဘက်မှာ DB pool ရှိပြီးသားဖြစ်ရပါမည်)
        async with db_pool.acquire() as conn:
            tasks = await conn.fetch(
                "SELECT id, title, description FROM routine_templates WHERE section = $1 AND status = 'active' ORDER BY task_order ASC;",
                section
            )
            
            # သင်တန်းသား ဒီနေ့ ပြီးထားပြီးသား တွေ စစ်ဆေးရန်
            today = datetime.now(MM_TZ).date()
            done_rows = await conn.fetch(
                "SELECT template_id FROM student_daily_logs WHERE student_id = $1 AND log_date = $2 AND is_done = TRUE;",
                target_student_id, today
            )
        done_ids = {r["template_id"] for r in done_rows}

        if not tasks:
            await query.message.reply_text("❌ ဤအချိန်ပိုင်းအတွက် Routine Tasks များ မရှိသေးပါ။")
            return

        # သင်တန်းသားထံ Header ပို့ခြင်း
        await student_bot.send_message(
            chat_id=target_student_id,
            text=f"{header_text}\n\nအောက်ပါ လေ့ကျင့်မှုများကို ပြုလုပ်ပြီးပါက သက်ဆိုင်ရာခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ -",
            parse_mode="Markdown"
        )

        # Task တစ်ခုချင်းစီကို ခလုတ်များနှင့်တကွ ပို့ပေးခြင်း
        for idx, t in enumerate(tasks, start=1):
            t_id = t["id"]
            task_text = f"📌 *{idx}။ {t['title']}*"
            if t["description"]:
                task_text += f"\n_{t['description']}_"

            if t_id in done_ids:
                keyboard = [[InlineKeyboardButton("✅ ပြီးပါပြီ", callback_data="already_done")]]
            else:
                keyboard = [[InlineKeyboardButton("လုပ်ဆောင်ပြီး (Done)", callback_data=f"done:{t_id}")]]

            await student_bot.send_message(
                chat_id=target_student_id,
                text=task_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

        # ခလုတ်နှိပ်ပြီးပါက Admin မက်ဆေ့ချ်ကို အောင်မြင်ကြောင်း ပြောင်းလဲပေးခြင်း
        await query.edit_message_text(
            text=f"{query.message.text}\n\n✅ *[ဤသင်တန်းသားထံ Routine ပို့ပြီးပါပြီ]*",
            parse_mode="Markdown"
        )

    except Exception as e:
        await query.message.reply_text(f"❌ Routine ပို့ရာတွင် အမှားအယွင်းရှိပါသည်: {e}")




async def handle_telegram_direct_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sender_name = update.effective_user.first_name

    if not is_admin(user_id):
        return

    replied_msg = update.message.reply_to_message
    if not replied_msg or not replied_msg.text:
        return

    #match = re.search(r"\(ID:\s*`?(\d+)`?\)", replied_msg.text)
    # သင်တန်းသား Alert စာသားထဲမှ ID ကို ပုံစံမှန်ကန်စွာ ဖမ်းယူခြင်း (ဥပမာ: ID: `123456` သို့မဟုတ် (ID: 123456))
    match = re.search(r"ID:\s*`?(\d+)`?", replied_msg.text)
    if not match:
        await update.message.reply_text("❌ ဤစာသည် သင်တန်းသား ID ပါဝင်သော မက်ဆေ့ချ်မဟုတ်ပါ။ သင်တန်းသားဆီမှ ရောက်လာသော Alert ကို Reply လုပ်ပေးပါ။")
        return

    student_id = int(match.group(1))
    reply_text = update.message.text

    try:
        async with Bot(token=STUDENT_BOT_TOKEN) as s_bot:
            await s_bot.send_message(
                chat_id=student_id,
                text=f"💌 *ဆရာ့ထံမှ အကြောင်းပြန်ကြားချက် ရောက်ရှိပါသည် -*\n\n{reply_text}",
                parse_mode="Markdown"
            )

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE student_reflections 
                SET teacher_reply = $1 
                WHERE id = (
                    SELECT id FROM student_reflections 
                    WHERE student_id = $2 
                    ORDER BY created_at DESC LIMIT 1
                );
                """,
                reply_text, student_id
            )

        await update.message.reply_text(f"✅ သင်တန်းသား ID `{student_id}` ထံသို့ စာအောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။", parse_mode="Markdown")

        # ကျန် Admin ဆီသို့ အသိပေး Sync လုပ်ခြင်း
        sync_text = f"ℹ️ *{sender_name}* က သင်တန်းသား (ID: `{student_id}`) ထံ Reply ပြန်လိုက်ပါသည်:\n\"{reply_text}\""
        for aid in ADMIN_IDS:
            if aid != user_id:
                try:
                    await context.bot.send_message(chat_id=aid, text=sync_text, parse_mode="Markdown")
                except Exception:
                    pass

    except Exception as e:
        await update.message.reply_text(f"❌ ပို့ဆောင်၍ မရပါ: {e}")

async def send_safe_message(bot, chat_id: int, text: str):
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")
        except Exception:
            await bot.send_message(chat_id=chat_id, text=chunk)

async def get_student_task_breakdown(conn, student_id: int, start_date, end_date):
    query = """
        SELECT 
            t.title AS task_title,
            t.section,
            COUNT(l.id) FILTER (WHERE l.is_done = TRUE) AS completed_count,
            COUNT(DISTINCT d.date_val) AS total_days
        FROM routine_templates t
        CROSS JOIN (
            SELECT generate_series($2::date, $3::date, '1 day'::interval)::date AS date_val
        ) d
        LEFT JOIN student_daily_logs l 
            ON l.template_id = t.id 
            AND l.student_id = $1 
            AND l.log_date = d.date_val
        WHERE t.status = 'active'
        GROUP BY t.id, t.title, t.section
        ORDER BY t.section, t.task_order;
    """
    return await conn.fetch(query, student_id, start_date, end_date)

# ================= Excel Export Functionality =================


async def cmd_export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """သင်တန်းသားတစ်ဦးချင်းစီ၏ အသေးစိတ် မှတ်တမ်းကို နာမည်ဖြင့် ရှာဖွေပြီး Excel ဖိုင်ထုတ်ပေးခြင်း"""
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/export_excel `\n(ဥပမာ- `/export_excel wil` သို့မဟုတ် `/export_excel မိုး`)", parse_mode="Markdown")
        return

    query_text = " ".join(context.args).strip()
    search_pattern = f"%{query_text}%"

    async with db_pool.acquire() as conn:
        # နာမည်တစ်စိတ်တပိုင်းဖြင့် (ILIKE) ရှာဖွေခြင်း
        students = await conn.fetch("SELECT student_id, student_name FROM students WHERE student_name ILIKE $1;", search_pattern)

    if not students:
        await update.message.reply_text(f"❌ '{query_text}' အမည်ဖြင့် ကိုက်ညီသော သင်တန်းသားကို မတွေ့ရှိပါ။")
        return

    if len(students) == 1:
        # သင်တန်းသား ၁ ယောက်တည်း တွေ့လျှင် တန်းပြီး Excel ထုတ်မည်
        student_id = students[0]["student_id"]
        await generate_and_send_excel(update.message, student_id)
    else:
        # တွေ့ရှိသူ ၁ ယောက်ထက် ပိုပါက ရွေးချယ်စရာ ခလုတ်များ (Inline Buttons) ထုတ်ပေးမည်
        keyboard = []
        for s in students:
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"excl_{s['student_id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 '{query_text}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)} ယောက်) တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ လိုချင်သူကို ရွေးချယ်ပါ -",
            reply_markup=reply_markup
        )

async def export_excel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ သင်တန်းသားကို ရွေးချယ်လိုက်သည့်အခါ Excel ထုတ်ပေးမည့် Handler"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
        
    data = query.data
    if data.startswith("excl_"):
        student_id = int(data.split("_")[1])
        try:
            await query.message.delete()
        except Exception:
            pass
            
        await generate_and_send_excel(query.message, student_id)

async def generate_and_send_excel(message_obj, student_id: int):
    """သင်တန်းသား ID ဖြင့် ဒေတာဆွဲထုတ်ပြီး Excel ဖိုင်ဆောက်ကာ ပို့ပေးသည့် Helper Function"""
    status = await message_obj.reply_text("⏳ Excel ဖိုင် ထုတ်ယူနေပါသည် ခေတ္တစောင့်ဆိုင်းပေးပါ...")
    try:
        today = datetime.now(MM_TZ).date()
        start_date = today - timedelta(days=7)

        async with db_pool.acquire() as conn:
            student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
            if not student:
                await status.edit_text("❌ သင်တန်းသား အချက်အလက် ရှာမတွေ့ပါ။")
                return
            student_name = student["student_name"]

            detail_query = """
                SELECT 
                    d.date_val AS log_date, 
                    t.section, 
                    t.title AS task_title,
                    COALESCE(l.is_done, FALSE) AS is_done,
                    COALESCE(l.reflection, '') AS reflection
                FROM routine_templates t
                CROSS JOIN (
                    SELECT generate_series($2::date, $3::date, '1 day'::interval)::date AS date_val
                ) d
                LEFT JOIN student_daily_logs l 
                    ON l.template_id = t.id 
                    AND l.student_id = $1 
                    AND l.log_date = d.date_val
                WHERE t.status = 'active'
                ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
            """
            rows = await conn.fetch(detail_query, student_id, start_date, today)

        if not rows:
            await status.edit_text(f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။")
            return

        data = []
        for r in rows:
            status_text = "Done" if r["is_done"] else "Not Done"
            data.append({
                "Date": r["log_date"].strftime("%Y-%m-%d"),
                "Section": r["section"].capitalize() if r["section"] else "",
                "Task Title": r["task_title"],
                "Status": status_text,
                "Reflection": r["reflection"]
            })

        df = pd.DataFrame(data)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Weekly Report')
        output.seek(0)

        filename = f"{student_name.replace(' ', '_')}_Weekly_Report.xlsx"
        await message_obj.reply_document(
            document=output,
            filename=filename,
            caption=f"📊 *{student_name}* ၏ ပြီးခဲ့သည့် (၇) ရက်တာ အသေးစိတ် Excel အစီရင်ခံစာ။",
            parse_mode="Markdown"
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Error ဖြစ်ပေါ်နေပါသည်: {e}")


def format_plain_detail_report(student_name: str, task_summary: list, start_date, end_date) -> str:
    total_assigned = 0
    total_done = 0
    
    sections = {
        "morning": ("🌅 မနက်ပိုင်း လေ့ကျင့်မှုများ", []),
        "afternoon": ("☀️ နေ့လယ်ပိုင်း လေ့ကျင့်မှုများ", []),
        "evening": ("🌙 ညနေပိုင်း လေ့ကျင့်မှုများ", [])
    }
    
    for r in task_summary:
        sec = r.get("section", "morning")
        completed = r["completed_count"]
        total_days = r["total_days"]
        total_assigned += total_days
        total_done += completed
        
        if completed == total_days:
            icon = "🟢"
        elif completed > 0:
            icon = "🟡"
        else:
            icon = "🔴"
            
        line = f"{icon} *{r['task_title']}* — {completed}/{total_days} ရက်"
        if sec in sections:
            sections[sec][1].append(line)
        else:
            sections.setdefault("other", ("📌 အခြား လေ့ကျင့်မှုများ", [])).append(line)

    percent = (total_done / total_assigned * 100) if total_assigned > 0 else 0

    msg = f"📊 *အပတ်စဉ် လေ့ကျင့်မှု အသေးစိတ် မှတ်တမ်း*\n"
    msg += f"👤 သင်တန်းသား: *{student_name}*\n"
    msg += f"📅 ကာလ: `{start_date}` မှ `{end_date}` အထိ\n"
    msg += f"📈 စုစုပေါင်း ပြီးမြောက်မှု: *{percent:.1f}%* ({total_done}/{total_assigned} ကြိမ်)\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    for _, (title, tasks) in sections.items():
        if tasks:
            msg += f"*{title}*\n"
            for t in tasks:
                msg += f"  {t}\n"
            msg += "\n"

    msg += "💡 _မှတ်ချက်: 🟢 အကုန်ပြီး | 🟡 တချို့ပြီး | 🔴 မပြီးသေး_"
    return msg

async def generate_personalized_ai_report(student_name: str, task_summary: list) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    breakdown_text = ""
    for r in task_summary:
        breakdown_text += f"- {r['task_title']} ({r['section']}): {r['total_days']} ရက်မှာ {r['completed_count']} ကြိမ် ပြီးမြောက်ခဲ့သည်။\n"

    prompt = f"""
သင်သည် မေတ္တာနှင့် ဂရုစိုက်မှုအပြည့်ရှိသော ဗုဒ္ဓဘာသာ/Mindfulness လေ့ကျင့်ရေး သင်တန်းဆရာတစ်ဦး ဖြစ်သည်။
အောက်ပါ အချက်အလက်များသည် သင်တန်းသား "{student_name}" ၏ ပြီးခဲ့သော တစ်ပတ်တာ လေ့ကျင့်မှု အသေးစိတ် မှတ်တမ်းဖြစ်သည်:

{breakdown_text}

သင်တန်းသားအတွက် နွေးထွေးအားတက်ဖွယ် အပတ်စဉ် သုံးသပ်ချက် အစီရင်ခံစာ (Weekly Detail Report) ကို မြန်မာဘာသာဖြင့် ရေးသားပေးပါ:
၁။ ကောင်းမွန်စွာ ပြုလုပ်ထားသော အလုပ်များကို အသိအမှတ်ပြု ချီးကျူးပေးပါ။
၂။ အထူးသတိပြုရန်: အကယ်၍ သင်တန်းသားသည် "တရားထိုင်ခြင်း" (Meditation) သို့မဟုတ် သတိပဋ္ဌာန်နှင့် သက်ဆိုင်သော အလေ့အကျင့်များကို အကြိမ်ရေ နည်းပါးနေခြင်း သို့မဟုတ် လုံးဝ မလုပ်ဘဲ ကျော်သွားခြင်းရှိပါက -
   - အပြစ်မတင်ဘဲ နူးညံ့စွာ နားချပေးပါ။
   - နေ့စဉ် ၅ မိနစ် သို့မဟုတ် ၁၀ မိနစ်ခန့် စတင် တရားထိုင်ခြင်းသည် စိတ်ဖိစီးမှု လျော့ကျစေခြင်း၊ စိတ်တည်ငြိမ်ခြင်း၊ နေ့စဉ်ဘဝတွင် သတိကပ်နိုင်ခြင်း စသည့် အကျိုးကျေးဇူးများကို နားလည်လွယ်အောင် ရှင်းပြပြီး လာမည့်အပတ်တွင် စမ်းသပ်လုပ်ဆောင်ကြည့်ရန် တွန်းအားပေးပါ။
၃။ ဖတ်ရလွယ်ကူအောင် စာပိုဒ်တိုများ၊ Bullet ပုံစံများနှင့် သင့်တော်သော Emoji များကို သုံးပြီး လိုရင်းရောက်အောင် စာကြောင်းတိုတိုနဲ့ရှင်းရှင်းလေးရေးပေးပါ။
"""
    # 🟢 Google Gemini API ရဲ့ တရားဝင် Model နာမည်များဖြင့် စီစဉ်ထားခြင်း
    #candidate_models = ["gemini-2.5-flash", "gemini-1.5-flash"]
    candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

    for model_name in candidate_models:
        for attempt in range(3):
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response.text:
                    return response.text
            except APIError as e:
                if e.code in (503, 429):
                    wait_sec = (attempt + 1) * 3
                    print(f"⚠️ {model_name} busy (Code {e.code}). Retrying in {wait_sec}s...")
                    await asyncio.sleep(wait_sec)
                else:
                    raise e
            except Exception as e:
                print(f"Unexpected error with {model_name}: {e}")
                break

    return f"🙏 မင်္ဂလာပါ {student_name}ခင်ဗျာ၊ ယခုတစ်ပတ်အတွက် အသေးစိတ် အစီရင်ခံစာ ထုတ်ယူရာတွင် ခေတ္တ အခက်အခဲရှိနေပါသဖြင့် မကြာမီ ပြန်လည်ပို့ဆောင်ပေးပါမည်ခင်ဗျာ။"

async def send_detailed_weekly_report(application, student_id: int):
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        student = await conn.fetchrow(
            "SELECT student_name FROM students WHERE student_id = $1;", 
            student_id
        )
        student_name = student["student_name"] if (student and student["student_name"]) else "သင်တန်းသား"
        task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)

    ai_feedback = await generate_personalized_ai_report(student_name, task_summary)
    await application.bot.send_message(chat_id=student_id, text=ai_feedback)

# ================= Weekly Report (ဆရာနှင့် Developer နှစ်ဦးလုံးဆီ ပို့ခြင်း) =================

async def send_weekly_report2(app, manual_chat_id=None):
    """အပတ်စဉ် အစီရင်ခံစာနှင့် Gemini AI သုံးသပ်ချက် ပို့ဆောင်ခြင်း"""
    # Manual တောင်းပါက တောင်းသူထံသာ ပို့မည်၊ Scheduler အချိန်ကိုက်ဖြစ်ပါက ADMIN_IDS နှစ်ဦးလုံးဆီ ပို့မည်
    target_recipients = [manual_chat_id] if manual_chat_id else ADMIN_IDS

    perf_query = """
        SELECT s.student_name,
               COUNT(log.id) FILTER (WHERE log.is_done = TRUE) as total_done,
               COUNT(DISTINCT log.log_date) as active_days
        FROM students s
        LEFT JOIN student_daily_logs log 
            ON s.student_id = log.student_id 
            AND log.log_date >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY s.student_id, s.student_name;
    """

    qa_query = """
        SELECT student_name, reflection_text, log_date
        FROM student_reflections
        WHERE log_date >= CURRENT_DATE - INTERVAL '7 days'
        ORDER BY log_date ASC;
    """

    async with db_pool.acquire() as conn:
        perf_rows = await conn.fetch(perf_query)
        qa_rows = await conn.fetch(qa_query)

    if not perf_rows:
        if manual_chat_id:
            await app.bot.send_message(chat_id=manual_chat_id, text="ပြီးခဲ့သည့် ၇ ရက်အတွက် ဒေတာမှတ်တမ်း မရှိသေးပါခင်ဗျာ။")
        return

    raw_data_lines = []
    report_lines = ["📈 *အပတ်စဉ် သင်တန်းသားများ၏ လေ့ကျင့်မှု အစီရင်ခံစာ (Weekly Report)*\n"]
    for r in perf_rows:
        line = f"• *{r['student_name']}*: စုစုပေါင်း ({r['total_done']}) ကြိမ်ပြီးစီး၊ တက်ရောက်မှု ({r['active_days']}/7) ရက်"
        report_lines.append(line)
        raw_data_lines.append(line)

    raw_data_lines.append("\n[သင်တန်းသားများ၏ မေးခွန်းများနှင့် အတွေ့အကြုံများ]")
    if qa_rows:
        for q in qa_rows:
            raw_data_lines.append(f"- {q['student_name']} ({q['log_date']}): \"{q['reflection_text']}\"")
    else:
        raw_data_lines.append("- (မေးခွန်းများ မရှိပါ)")

    summary_text = "\n".join(report_lines)
    student_records_text = "\n".join(raw_data_lines)
    ai_summary = await analyze_weekly_data_with_gemini(student_records_text)
    final_ai_msg = f"🧠 *Gemini AI ၏ အပတ်စဉ် သုံးသပ်ချက် အနှစ်ချုပ် -*\n\n{ai_summary}"

    # ဆရာနှင့် Developer နှစ်ဦးလုံးဆီ ပို့ဆောင်ခြင်း
    for admin_id in target_recipients:
        try:
            await app.bot.send_message(chat_id=admin_id, text=summary_text, parse_mode="Markdown")
            await send_safe_message(app.bot, admin_id, final_ai_msg)
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"Failed to send weekly report to {admin_id}: {e}")

async def send_weekly_report(app, manual_chat_id=None):
    """သင်တန်းသား များ၏ စာရင်းကို Excel ဖြင့် ထုတ်ပေးပြီး AI Summary နှင့်အတူ Admin ထံ ပို့ခြင်း"""
    target_recipients = [manual_chat_id] if manual_chat_id else ADMIN_IDS

    # ၁။ Database မှ ဒေတာများ ဆွဲထုတ်ခြင်း (Performance & QA Queries)
    perf_query = """
        SELECT s.student_name,
               COUNT(log.id) FILTER (WHERE log.is_done = TRUE) as total_done,
               COUNT(DISTINCT log.log_date) as active_days
        FROM students s
        LEFT JOIN student_daily_logs log 
            ON s.student_id = log.student_id 
            AND log.log_date >= CURRENT_DATE - INTERVAL '7 days'
        GROUP BY s.student_id, s.student_name
        ORDER BY total_done DESC;
    """

    qa_query = """
        SELECT student_name, reflection_text, log_date
        FROM student_reflections
        WHERE log_date >= CURRENT_DATE - INTERVAL '7 days'
        ORDER BY log_date ASC;
    """

    async with db_pool.acquire() as conn:
        perf_rows = await conn.fetch(perf_query)
        qa_rows = await conn.fetch(qa_query)

    if not perf_rows:
        for admin_id in target_recipients:
            await app.bot.send_message(chat_id=admin_id, text="ပြီးခဲ့သည့် ၇ ရက်အတွက် ဒေတာမှတ်တမ်း မရှိသေးပါခင်ဗျာ။")
        return

    # ၂။ Pandas DataFrame သုံးပြီး Excel ဖိုင်ထဲသို့ ထည့်ရန် ပြင်ဆင်ခြင်း
    df_perf = pd.DataFrame([dict(r) for r in perf_rows])
    df_perf.columns = ["သင်တန်းသားနာမည်", "ပြီးမြောက်သည့် Task အရေအတွက်", "ဝင်ရောက်လုပ်ကိုင်သည့် ရက်ပေါင်း"]

    df_qa = pd.DataFrame([dict(r) for r in qa_rows]) if qa_rows else pd.DataFrame(columns=["student_name", "reflection_text", "log_date"])
    if not df_qa.empty:
        df_qa.columns = ["သင်တန်းသားနာမည်", "Reflection မှတ်တမ်း", "ရက်စွဲ"]

    # Excel ဖိုင်ကို Memory ထဲတွင် တည်ဆောက်ခြင်း (Disk ပေါ်တွင် ဖိုင်သိမ်းစရာမလိုပါ)
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df_perf.to_excel(writer, sheet_name='Weekly Performance', index=False)
        if not df_qa.empty:
            df_qa.to_excel(writer, sheet_name='Reflections & Q&A', index=False)
    excel_buffer.seek(0)

    # ၃။ AI ထံ ပို့ရန် Data များကို စုစည်းခြင်း
    raw_data_lines = []
    for r in perf_rows:
        raw_data_lines.append(f"- {r['student_name']}: စုစုပေါင်း ({r['total_done']}) ကြိမ်ပြီးစီး၊ တက်ရောက်မှု ({r['active_days']}/7) ရက်")
    
    if qa_rows:
        for q in qa_rows:
            raw_data_lines.append(f"  [Reflection] {q['student_name']} ({q['log_date']}): \"{q['reflection_text']}\"")

    student_records_text = "\n".join(raw_data_lines)
    
    # AI ဖြင့် ခြုံငုံသုံးသပ်ချက် (Summary) ထုတ်ယူခြင်း
    ai_summary = await analyze_weekly_data_with_gemini(student_records_text)
    final_ai_msg = f"📊 *[အပတ်စဉ် သင်တန်းသားများ၏ ခြုံငုံသုံးသပ်ချက် (AI Summary)]*\n\n{ai_summary}"

    # ၄။ Admin ဆီသို့ AI Text နှင့် Excel ဖိုင်ကို တွဲ၍ ပို့ဆောင်ခြင်း
    for admin_id in target_recipients:
        try:
            # ပထမဦးစွာ AI ၏ သုံးသပ်ချက် စာသားကို ပို့မည်
            await send_safe_message(app.bot, admin_id, final_ai_msg)
            
            # ထို့နောက် သင်တန်းသား များအားလုံး၏ စာရင်းပါ Excel ဖိုင်ကို ပို့မည်
            filename = f"Weekly_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
            await app.bot.send_document(
                chat_id=admin_id,
                document=excel_buffer,
                filename=filename,
                caption="📁 *သင်တန်းသား များအားလုံး၏ တစ်ပတ်တာ အသေးစိတ်စာရင်း Excel ဖိုင်* ဖြင့် ပူးတွဲတင်ပြအပ်ပါသည်။"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Failed to send weekly report to {admin_id}: {e}")

async def send_detailed_report_without_ai(application, student_id: int, recipient_chat_id: int = None):
    target_chat = recipient_chat_id or student_id
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
        student_name = student["student_name"] if (student and student["student_name"]) else "သင်တန်းသား"
        task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)

    report_text = format_plain_detail_report(student_name, task_summary, start_date, today)
    await application.bot.send_message(chat_id=target_chat, text=report_text, parse_mode="Markdown")

async def send_detailed_report_with_ai(application, student_id: int, recipient_chat_id: int = None):
    target_chat = recipient_chat_id or student_id
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
        student_name = student["student_name"] if (student and student["student_name"]) else "သင်တန်းသား"
        task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)

    plain_stats = format_plain_detail_report(student_name, task_summary, start_date, today)
    ai_feedback = await generate_personalized_ai_report(student_name, task_summary)
    full_message = f"{plain_stats}\n\n🤖 *ဆရာ့ထံမှ အကြံပြု သုံးသပ်ချက်*\n━━━━━━━━━━━━━━━━━━━━\n{ai_feedback}"
    await application.bot.send_message(chat_id=target_chat, text=full_message)

async def cmd_detail_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """သင်တန်းသားနာမည်ဖြင့် ရှာဖွေ၍ အသေးစိတ် အစီရင်ခံစာကို ထုတ်ပေးခြင်း"""
    if not is_admin(update.effective_user.id):
        return

    # Argument ပါမပါ စစ်ဆေးခြင်း
    if not context.args:
        await update.message.reply_text(
            "⚠️ **အသုံးပြုပုံ:** `/detail_report `\n"
            "ဥပမာ - `/detail_report မောင်မောင်`", 
            parse_mode="Markdown"
        )
        return

    search_name = " ".join(context.args).strip()
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        # သင်တန်းသားနာမည်ကို တစိတ်တပိုင်း (ILIKE) ဖြင့် ရှာဖွေခြင်း
        students = await conn.fetch(
            "SELECT student_id, student_name FROM students WHERE student_name ILIKE $1;", 
            f"%{search_name}%"
        )

        if not students:
            await update.message.reply_text(f"❌ '{search_name}' အမည်ဖြင့် သင်တန်းသားကို ရှာမတွေ့ပါ။")
            return

        # အကယ်၍ ရှာတွေ့တဲ့သင်တန်းသား 1 ယောက်တည်းဆိုရင် Report ကို တန်းထုတ်ပေးမည်
        if len(students) == 1:
            student = students[0]
            await generate_and_send_report(update, context, student["student_id"], student["student_name"], start_date, today)
            return

        # အကယ်၍ နာမည်တူသူ (သို့) အများအပြားတွေ့ရင် ခလုတ်များဖြင့် ရွေးချယ်ခိုင်းမည်
        keyboard = []
        for s in students:
            # ချက်တင်ခလုတ်တွင် data အဖြစ် student_id ကို ပေးပို့မည်
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"rep_{s['student_id']}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        # 🟢 ပြင်ဆင်ချက်: unefined ဖြစ်နေသော cols ကို ဖြုတ်ပြီး len(students) ကို တိုက်ရိုက်သုံးထားပါပြီ
        await update.message.reply_text(
            f"🔍 '{search_name}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)}) ဦး တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ ရွေးချယ်ပါ:",
            reply_markup=reply_markup
        )

async def cmd_detail_report2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: `/detail_report <student_id>`", parse_mode="Markdown")
        return
    try:
        student_id = int(context.args[0])
        await update.message.reply_text("⏳ Data စစ်ဆေးနေပါသည်...")
        await send_detailed_report_without_ai(context.application, student_id, recipient_chat_id=update.effective_chat.id)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        
async def generate_and_send_report(update_or_query, context, student_id, student_name, start_date, today):
    """သင်တန်းသားတစ်ဦးချင်းစီ၏ (၇) ရက်တာ အချက်အလက်များကို စုစည်းပြီး ပို့ပေးသည့် Helper Function"""
    async with db_pool.acquire() as conn:
        detail_query = """
            SELECT 
                d.date_val AS log_date, 
                t.section, 
                t.title AS task_title,
                COALESCE(l.is_done, FALSE) AS is_done,
                COALESCE(l.reflection, '') AS reflection
            FROM routine_templates t
            CROSS JOIN (
                SELECT generate_series($2::date, $3::date, '1 day'::interval)::date AS date_val
            ) d
            LEFT JOIN student_daily_logs l 
                ON l.template_id = t.id 
                AND l.student_id = $1 
                AND l.log_date = d.date_val
            WHERE t.status = 'active'
            ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
        """
        rows = await conn.fetch(detail_query, student_id, start_date, today)

    if not rows:
        msg = f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။"
        # CallbackQuery ဖြစ်နေလျှင် edit_message_text သုံးခြင်းက ပိုလုံခြုံပါတယ်
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(msg)
        elif hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    # စာသားပုံစံဖြင့် Report တည်ဆောက်ခြင်း (Markdown error မတက်အောင် ပုံစံရိုးရိုး သို့မဟုတ် ခွဲထုတ်သုံးပါ)
    report_text = f"📊 {student_name} ၏ (၇) ရက်တာ အစီရင်ခံစာ\n\n"
    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)
    report_text += f"✅ ပြီးစီးမှု: {done_count}/{total_count} Tasks\n"
    report_text += "-----------------------------------\n"
    
    for r in rows[:15]: 
        status_icon = "✅" if r["is_done"] else "❌"
        report_text += f"{r['log_date']} | {r['section']} | {r['task_title']} - {status_icon}\n"

    try:
        # CallbackQuery လား၊ Message လား အတိအကျ ခွဲခြားပေးခြင်း
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(report_text)
        elif hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(report_text)
        else:
            message = update_or_query.message or update_or_query.effective_message
            await message.reply_text(report_text)
    except Exception as e:
        print(f"❌ Send Report Error: {e}")
        # Error တက်ရင်တောင် markdown ကို ဖြုတ်ပြီး ပို့ကြည့်ရန်
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.message.reply_text(report_text)

async def generate_and_send_report2(update_or_query, context, student_id, student_name, start_date, today):
    """သင်တန်းသားတစ်ဦးချင်းစီ၏ (၇) ရက်တာ အချက်အလက်များကို စုစည်းပြီး ပို့ပေးသည့် Helper Function"""
    async with db_pool.acquire() as conn:
        detail_query = """
            SELECT 
                d.date_val AS log_date, 
                t.section, 
                t.title AS task_title,
                COALESCE(l.is_done, FALSE) AS is_done,
                COALESCE(l.reflection, '') AS reflection
            FROM routine_templates t
            CROSS JOIN (
                SELECT generate_series($2::date, $3::date, '1 day'::interval)::date AS date_val
            ) d
            LEFT JOIN student_daily_logs l 
                ON l.template_id = t.id 
                AND l.student_id = $1 
                AND l.log_date = d.date_val
            WHERE t.status = 'active'
            ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
        """
        rows = await conn.fetch(detail_query, student_id, start_date, today)

    if not rows:
        msg = f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။"
        if hasattr(update_or_query, "message") and update_or_query.message:
            await update_or_query.message.reply_text(msg)
        else:
            await update_or_query.edit_message_text(msg)
        return

    # စာသားပုံစံဖြင့် Report တည်ဆောက်ခြင်း
    report_text = f"📊 *{student_name}* ၏ (၇) ရက်တာ အစီရင်ခံစာ\n\n"
    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)
    report_text += f"✅ ပြီးစီးမှု: {done_count}/{total_count} Tasks\n"
    report_text += "-----------------------------------\n"
    
    # 🟢 ဤနေရာတွင် .append() အစား += ဖြင့် ပြင်ဆင်ထားပါသည်
    for r in rows[:15]: 
        status_icon = "✅" if r["is_done"] else "❌"
        report_text += f"{r['log_date']} | {r['section']} | {r['task_title']} - {status_icon}\n"

    if hasattr(update_or_query, "message") and update_or_query.message:
        await update_or_query.message.reply_text(report_text, parse_mode="Markdown")
    else:
        await update_or_query.edit_message_text(report_text, parse_mode="Markdown")


async def report_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ သင်တန်းသားကို ရွေးချယ်လိုက်သည့်အခါ လုပ်ဆောင်မည့် Handler"""
    query = update.callback_query
    
    try:
        await query.answer()
        
        if query.data.startswith("rep_"):
            student_id = int(query.data.split("_")[1])
            today = datetime.now(MM_TZ).date()
            start_date = today - timedelta(days=7)
            
            # db_pool ရှိမရှိ စစ်ဆေးခြင်း
            global db_pool
            if db_pool is None:
                await query.message.reply_text("❌ Database Connection မရှိသေးပါ။")
                return

            async with db_pool.acquire() as conn:
                student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
                student_name = student["student_name"] if student else "Unknown"

            await generate_and_send_report(query, context, student_id, student_name, start_date, today)

    except Exception as e:
        # ခလုတ်နှိပ်လိုက်လို့ Error တက်လာရင် ဘာကြောင့်လဲဆိုတာ Admin ဆီကို စာပို့ပြပါလိမ့်မည်
        print(f"❌ Callback Error: {e}")
        try:
            await query.message.reply_text(f"❌ Error ဖြစ်ပေါ်ပါသည်: {e}")
        except:
            pass

async def report_callback_handler3(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ သင်တန်းသားကို ရွေးချယ်လိုက်သည့်အခါ လုပ်ဆောင်မည့် Handler"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("rep_"):
        student_id = int(query.data.split("_")[1])
        today = datetime.now(MM_TZ).date()
        start_date = today - timedelta(days=7)
        
        async with db_pool.acquire() as conn:
            student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
            student_name = student["student_name"] if student else "Unknown"

        await generate_and_send_report(query, context, student_id, student_name, start_date, today)


async def cmd_ai_report2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: `/ai_report <student_id>`", parse_mode="Markdown")
        return
    try:
        student_id = int(context.args[0])
        status = await update.message.reply_text("⏳ Gemini AI ဖြင့် သုံးသပ်ချက် ထုတ်ယူနေပါသည်...")
        await send_detailed_report_with_ai(context.application, student_id, recipient_chat_id=update.effective_chat.id)
        await status.delete()
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_ai_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """နာမည်ဖြင့် ရှာဖွေပြီး သင်တန်းသားတစ်ဦးချင်းစီ၏ AI Report ထုတ်ပေးသည့် Command"""
    if not is_admin(update.effective_user.id):
        return
        
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: `/ai_report `\n(ဥပမာ- `/ai_report wil` သို့မဟုတ် `/ai_report kyaw`)", parse_mode="Markdown")
        return
        
    # ရိုက်လိုက်သော စာသားများကို ပေါင်းစပ်၍ Search Query ပြုလုပ်ခြင်း
    query_text = " ".join(context.args).strip()
    search_pattern = f"%{query_text}%"
    
    async with db_pool.acquire() as conn:
        # データベース တွင် ILIKE ဖြင့် ရှာဖွေခြင်း
        students = await conn.fetch("SELECT student_id, student_name FROM students WHERE student_name ILIKE $1;", search_pattern)
        
    if not students:
        await update.message.reply_text(f"❌ '{query_text}' အမည်ဖြင့် ကိုက်ညီသော သင်တန်းသားကို မတွေ့ရှိပါ။")
        return
        
    if len(students) == 1:
        # သင်တန်းသား ၁ ယောက်တည်း တွေ့လျှင် တန်းပြီး Report ထုတ်မည်
        student_id = students[0]["student_id"]
        await process_and_send_ai_report(update.message, student_id)
    else:
        # တွေ့ရှိသူ ၁ ယောက်ထက် ပိုပါက ရွေးချယ်စရာ ခလုတ်များ (Inline Buttons) ထုတ်ပေးမည်
        keyboard = []
        for s in students:
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"airep_{s['student_id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 '{query_text}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)} ယောက်) တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ လိုချင်သူကို ရွေးချယ်ပါ -",
            reply_markup=reply_markup
        )

async def ai_report_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ သင်တန်းသားတစ်ဦးကို ရွေးချယ်လိုက်သည့်အခါ အလုပ်လုပ်မည့် Handler"""
    query = update.callback_query
    await query.answer()
    
    if not is_admin(query.from_user.id):
        return
        
    data = query.data
    if data.startswith("airep_"):
        student_id = int(data.split("_")[1])
        # ရွေးချယ်စရာ ခလုတ်စာတိုကို ဖျောက်လိုက်ခြင်း
        try:
            await query.message.delete()
        except Exception:
            pass
            
        await process_and_send_ai_report(query.message, student_id)

async def process_and_send_ai_report(message_obj, student_id: int):
    """သင်တန်းသား ID ဖြင့် ဒေတာဆွဲထုတ်ပြီး AI Report တည်ဆောက်ကာ ပို့ပေးသည့် Helper Function"""
    status = await message_obj.reply_text("⏳ Gemini AI ဖြင့် သုံးသပ်ချက် ထုတ်ယူနေပါသည် ခေတ္တစောင့်ဆိုင်းပေးပါ...")
    try:
        today = datetime.now(MM_TZ).date()
        start_date = today - timedelta(days=7)
        
        async with db_pool.acquire() as conn:
            student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
            student_name = student["student_name"] if (student and student["student_name"]) else "သင်တန်းသား"
            task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)
            
        plain_stats = format_plain_detail_report(student_name, task_summary, start_date, today)
        ai_feedback = await generate_personalized_ai_report(student_name, task_summary)
        
        full_message = f"{plain_stats}\n\n🤖 *ဆရာ့ထံမှ အကြံပြု သုံးသပ်ချက်*\n━━━━━━━━━━━━━━━━━━━━\n{ai_feedback}"
        
        await message_obj.reply_text(full_message, parse_mode=None)
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Error ဖြစ်ပေါ်နေပါသည်: {e}")

async def broadcast_detailed_weekly_reports(application):
    print("🚀 Starting Weekly AI Detailed Reports Broadcast...")
    async with db_pool.acquire() as conn:
        students = await conn.fetch("SELECT student_id, student_name FROM students;")

    for stu in students:
        s_id = stu["student_id"]
        try:
            await send_detailed_weekly_report(application, s_id)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Failed to send detail report to {s_id}: {e}")

    print("✅ Weekly AI Detailed Reports Broadcast Completed.")

async def add_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    raw_text = update.message.text.partition(' ')[2].strip()
    if not raw_text or "|" not in raw_text:
        await update.message.reply_text(
            "⚠️ *အသုံးပြုပုံ မှားယွင်းနေပါသည်*\n\n"
            "ပုံစံ: `/add_task <အချိန်ပိုင်း> | <ခေါင်းစဉ်> | <ရှင်းလင်းချက်>`\n\n"
            "ဥပမာ:\n"
            "`/add_task morning | မနက်ခင်း အကြောလျှော့ခြင်း | ခန္ဓာကိုယ်ကို သတိဖြင့် ဖြေလျှော့ပါ`\n\n"
            "*(အချိန်ပိုင်း နေရာတွင် morning, afternoon သို့မဟုတ် evening ဟု ရေးပေးပါ)*",
            parse_mode="Markdown"
        )
        return

    parts = [p.strip() for p in raw_text.split("|")]
    section = parts[0].lower()
    title = parts[1]
    description = parts[2] if len(parts) > 2 else ""

    if section not in ["morning", "afternoon", "evening"]:
        await update.message.reply_text("❌ အချိန်ပိုင်းသည် `morning`, `afternoon` သို့မဟုတ် `evening` သာ ဖြစ်ရပါမည်။")
        return

    async with db_pool.acquire() as conn:
        last_order = await conn.fetchval(
            "SELECT COALESCE(MAX(task_order), 0) FROM routine_templates WHERE section = $1;",
            section
        )
        new_order = last_order + 1
        await conn.execute(
            """
            INSERT INTO routine_templates (section, task_order, title, description, status, is_active)
            VALUES ($1, $2, $3, $4, 'active', TRUE);
            """,
            section, new_order, title, description
        )

    await update.message.reply_text(
        f"✅ *Task အသစ် အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ!*\n\n"
        f"⏰ အချိန်ပိုင်း: *{section.capitalize()}* (အမှတ်စဉ်: {new_order})\n"
        f"📌 ခေါင်းစဉ်: *{title}*\n"
        f"📝 ရှင်းလင်းချက်: _{description}_",
        parse_mode="Markdown"
    )

async def delete_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/delete_task <task_id>`\n(ID ကို `/list_tasks` တွင် ကြည့်နိုင်ပါသည်)")
        return

    task_id = int(context.args[0])
    async with db_pool.acquire() as conn:
        result = await conn.execute("DELETE FROM routine_templates WHERE id = $1;", task_id)

    if "DELETE 1" in result:
        await update.message.reply_text(f"✅ Task ID `{task_id}` ကို အောင်မြင်စွာ ဖျက်ပြီးပါပြီ။", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Task ID `{task_id}` ကို ရှာမတွေ့ပါခင်ဗျာ။")




async def switch_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    async with db_pool.acquire() as conn:
        next_count = await conn.fetchval("SELECT COUNT(*) FROM routine_templates WHERE status = 'next';")

        if next_count == 0:
            await update.message.reply_text("⚠️ နောက်အပတ်အတွက် ကြိုတင်ထည့်ထားသော Task (Next) မရှိသေးပါခင်ဗျာ။\n`/add_next` ဖြင့် အရင်ထည့်သွင်းပေးပါ။")
            return

        async with conn.transaction():
            await conn.execute("UPDATE routine_templates SET status = 'archived', is_active = FALSE WHERE status = 'active';")
            await conn.execute("UPDATE routine_templates SET status = 'active', is_active = TRUE WHERE status = 'next';")

    await update.message.reply_text(
        f"🚀 *အပတ်သစ်သို့ အောင်မြင်စွာ ကူးပြောင်းပြီးပါပြီ!*\n\n"
        f"• Task အသစ် ({next_count}) ခုကို Active စာရင်းသို့ ထည့်သွင်းလိုက်ပါပြီ။\n"
        f"• ယခင်အပတ် Task အဟောင်းများကို သိမ်းဆည်း (Archived) ထားလိုက်ပါပြီ။",
        parse_mode="Markdown"
    )

async def toggle_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/toggle_task <Task_ID>`", parse_mode="Markdown")
        return

    task_id = int(context.args[0])
    async with db_pool.acquire() as conn:
        task = await conn.fetchrow("SELECT id, title, is_active FROM routine_templates WHERE id = $1;", task_id)
        if not task:
            await update.message.reply_text(f"❌ Task ID `{task_id}` ကို ရှာမတွေ့ပါခင်ဗျာ။", parse_mode="Markdown")
            return

        new_status = not task["is_active"]
        await conn.execute("UPDATE routine_templates SET is_active = $1 WHERE id = $2;", new_status, task_id)

    status_str = "🟢 ဖွင့်လှစ်ထားပါသည် (ACTIVE)" if new_status else "🔴 ပိတ်ထားပါသည် (INACTIVE)"
    await update.message.reply_text(
        f"✅ Task ID `{task_id}` (*{task['title']}*) ၏ အခြေအနေကို\n👉 *{status_str}* သို့ ပြောင်းလဲလိုက်ပါပြီ။",
        parse_mode="Markdown"
    )


async def list_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    async with db_pool.acquire() as conn:
        active_tasks = await conn.fetch(
            "SELECT id, section, task_order, title FROM routine_templates WHERE status = 'active' ORDER BY section, task_order ASC;"
        )
        next_tasks = await conn.fetch(
            "SELECT id, section, task_order, title FROM routine_templates WHERE status = 'next' ORDER BY section, task_order ASC;"
        )

    msg = ["📋 *Routine စီမံခန့်ခွဲမှု အခြေအနေ*\n"]
    msg.append("🟢 *လက်ရှိ သုံးနေသော Tasks (Active):*")
    if active_tasks:
        for t in active_tasks:
            clean_title = t['title'].replace("*", "").replace("_", " ")
            sec_code = t['section'][:1].upper()
            msg.append(f"  • [{sec_code}] {clean_title} (ID: `{t['id']}`)")
    else:
        msg.append("  _(မရှိသေးပါ)_")

    msg.append("\n🟡 *နောက်အပတ်အတွက် ကြိုထည့်ထားသော Tasks (Next):*")
    if next_tasks:
        for t in next_tasks:
            clean_title = t['title'].replace("*", "").replace("_", " ")
            sec_code = t['section'][:1].upper()
            msg.append(f"  • [{sec_code}] {clean_title} (ID: `{t['id']}`)")
        msg.append("\n👉 အပတ်သစ်သို့ ပြောင်းလဲအသုံးပြုရန်: `/switch_week`")
    else:
        msg.append("  _(ကြိုတင်ထည့်ထားသော Task မရှိပါ - ထည့်ရန်: `/add_next`)_")

    final_text = "\n".join(msg)
    try:
        await update.message.reply_text(final_text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(final_text)

async def broadcast_routine_to_students(section_name: str, header_text: str, target_student_id: int = None) -> int:
    today = datetime.now(MM_TZ).date()
    
    async with db_pool.acquire() as conn:
        if target_student_id:
            students = [{"student_id": target_student_id}]
        else:
            students = await conn.fetch("SELECT student_id FROM students;")

        tasks = await conn.fetch(
            "SELECT id, title, description FROM routine_templates WHERE section = $1 AND status = 'active' ORDER BY task_order ASC;",#AND is_active = TRUE
            section_name
        )

    if not tasks or not students:
        return 0

    count = 0
    async with Bot(token=STUDENT_BOT_TOKEN) as s_bot:
        for stu in students:
            chat_id = stu["student_id"]
            batch_id = str(uuid.uuid4())[:8]
            batch_message_ids = []

            try:
                header_keyboard = [
                    [InlineKeyboardButton("🗑️ ထပ်နေပါက ဤစာရင်းကို ဖျက်မည်", callback_data=f"dismiss_batch:{batch_id}")]
                ]
                
                h_msg = await s_bot.send_message(
                    chat_id=chat_id,
                    text=f"{header_text}\n\nအောက်ပါ လေ့ကျင့်မှုများကို ပြုလုပ်ပြီးပါက သက်ဆိုင်ရာခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ -",
                    reply_markup=InlineKeyboardMarkup(header_keyboard),
                    parse_mode="Markdown"
                )
                batch_message_ids.append(h_msg.message_id)
                await asyncio.sleep(0.05)

                for idx, t in enumerate(tasks, start=1):
                    t_id = t["id"]
                    task_text = f"📌 *{idx}။ {t['title']}*"
                    if t["description"]:
                        task_text += f"\n_{t['description']}_"

                    task_keyboard = [
                        [InlineKeyboardButton("လုပ်ဆောင်ပြီး (Done)", callback_data=f"done:{t_id}")]
                    ]

                    t_msg = await s_bot.send_message(
                        chat_id=chat_id,
                        text=task_text,
                        reply_markup=InlineKeyboardMarkup(task_keyboard),
                        parse_mode="Markdown"
                    )
                    batch_message_ids.append(t_msg.message_id)
                    await asyncio.sleep(0.05)

                async with db_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO routine_batch_logs (batch_id, student_id, message_ids)
                        VALUES ($1, $2, $3);
                        """,
                        batch_id, chat_id, batch_message_ids
                    )

                count += 1
            except Exception as e:
                print(f"Error sending to {chat_id}: {e}")

            await asyncio.sleep(0.05)

    return count

SECTION_HEADERS = {
    "morning": "🌅 *မင်္ဂလာနံနက်ခင်းပါ သင်တန်းသား/သူတို့ရေ*\nယနေ့ မနက်ပိုင်းအတွက် သတ်မှတ်ထားသော လေ့ကျင့်ခန်းများ ဖြစ်ပါသည် -",
    "afternoon": "☀️ *မင်္ဂလာနေ့လယ်ခင်းပါ သင်တန်းသား/သူတို့ရေ*\nယနေ့ နေ့လယ်ပိုင်းအတွက် သတ်မှတ်ထားသော လေ့ကျင့်ခန်းများ ဖြစ်ပါသည် -",
    "evening": "🌙 *မင်္ဂလာညနေခင်းပါ သင်တန်းသား/သူတို့ရေ*\nယနေ့ ညနေပိုင်းအတွက် သတ်မှတ်ထားသော လေ့ကျင့်ခန်းများ ဖြစ်ပါသည် -"
}

async def cmd_send_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ဤ Command ကို အသုံးပြုရန် ခွင့်ပြုချက်မရှိပါ။")
        return

    if not context.args:
        help_text = (
            "📌 *အသုံးပြုပုံ လမ်းညွှန်:*\n\n"
            "၁။ သင်တန်းသားအားလုံးထံ ပို့လိုပါက:\n"
            "   `/send_routine <morning|afternoon|evening>`\n\n"
            "၂။ သင်တန်းသားတစ်ဦးတည်းထံ ပို့လိုပါက:\n"
            "   `/send_routine <section> <student_id>`"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return

    section = context.args[0].lower().strip()
    if section not in SECTION_HEADERS:
        await update.message.reply_text("⚠️ Section အမည် မှားယွင်းနေပါသည်။ (`morning`, `afternoon`, `evening`)")
        return

    target_student_id = None
    if len(context.args) >= 2:
        try:
            target_student_id = int(context.args[1])
        except ValueError:
            await update.message.reply_text("⚠️ သင်တန်းသား ID သည် ဂဏန်းသီးသန့် ဖြစ်ရပါမည်။")
            return

    header_text = SECTION_HEADERS[section]
    target_label = f"သင်တန်းသား `{target_student_id}` ထံသို့" if target_student_id else "သင်တန်းသားအားလုံးထံသို့"
    status_msg = await update.message.reply_text(f"⏳ {target_label} *{section}* routine ပို့ဆောင်နေပါသည်...", parse_mode="Markdown")

    try:
        sent_count = await broadcast_routine_to_students(
            section_name=section,
            header_text=header_text,
            target_student_id=target_student_id
        )

        if sent_count > 0:
            await status_msg.edit_text(f"✅ *{section.capitalize()} Routine ပို့ဆောင်ပြီးပါပြီ!*\n\n👥 လက်ခံရရှိသူ: *{sent_count}* ဦး", parse_mode="Markdown")
        else:
            await status_msg.edit_text("⚠️ ပို့ဆောင်၍ မရပါ (Task မရှိသေးခြင်း သို့မဟုတ် သင်တန်းသား မရှိခြင်းဖြစ်နိုင်ပါသည်)။")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error ဖြစ်ပေါ်ပါသည်: `{e}`", parse_mode="Markdown")

async def handle_teacher_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("send_routine:"):
        return

    _, section, student_id_str = data.split(":")
    student_id = int(student_id_str)

    section_titles = {
        "morning": "🌅 *(က) မနက်ပိုင်း လေ့ကျင့်မှု (5:00 AM - 12:00 PM)*",
        "afternoon": "☀️ *(ခ) နေ့လယ်ပိုင်း လေ့ကျင့်မှု (12:00 PM - 5:00 PM)*",
        "evening": "🌙 *(ဂ) ညနေပိုင်း လေ့ကျင့်မှု (5:00 PM - 12:00 AM)*"
    }
    header_text = section_titles.get(section, "သတိပဋ္ဌာန် လေ့ကျင့်မှု")
    sent = await broadcast_routine_to_students(section, header_text, target_student_id=student_id)

    if sent > 0:
        await query.message.reply_text(f"✅ သင်တန်းသား (ID: `{student_id}`) ထံသို့ *{section.capitalize()}* Checklist ကို အောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။", parse_mode="Markdown")
    else:
        await query.message.reply_text(f"❌ ပို့ဆောင်၍ မရပါ (Database တွင် Routine မရှိသေးပါ သို့မဟုတ် သင်တန်းသားအား မတွေ့ပါ)။")

async def cmd_check_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
        
    global scheduler  # 👈 ဤနေရာတွင် global scheduler ကို သေချာခေါ်သုံးပေးပါ
    if scheduler is None:
        await update.message.reply_text("⚠️ Scheduler မရှိသေးပါ။")
        return
        
        
    jobs = scheduler.get_jobs()
    if not jobs:
        await update.message.reply_text("လောလောဆယ် Schedule လုပ်ထားသော Job မရှိသေးပါ။")
        return

    text = "⏰ *လက်ရှိ Schedule စာရင်းများ:*\n━━━━━━━━━━━━━━━━━━━━\n"
    for job in jobs:
        text += f"🔹 *ID:* `{job.id}`\n   *အမည်:* {job.name}\n   *နောက်တစ်ကြိမ် ပို့မည့်အချိန်:* `{job.next_run_time}`\n\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def notify_with_three_beeps(chat_id: int, reminder_text: str):
    for _ in range(2):
        try:
            beep_msg = await student_bot.send_message(
                chat_id=chat_id,
                text="🔔 *သတိပေးချက်*",
                parse_mode="Markdown",
                disable_notification=False
            )
            await asyncio.sleep(0.7)
            try:
                await student_bot.delete_message(chat_id=chat_id, message_id=beep_msg.message_id)
            except Exception:
                pass
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
            pass

    try:
        await student_bot.send_message(chat_id=chat_id, text=reminder_text, parse_mode="Markdown", disable_notification=False)
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await student_bot.send_message(chat_id=chat_id, text=reminder_text, parse_mode="Markdown", disable_notification=False)

async def send_section_pending_reminder(application, section_name: str, reminder_title: str):
    today = datetime.now(MM_TZ).date()

    async with db_pool.acquire() as conn:
        active_tasks = await conn.fetch(
            "SELECT id FROM routine_templates WHERE section = $1 AND status = 'active';",
            section_name
        )
        if not active_tasks:
            return

        total_active_count = len(active_tasks)

        query = """
        SELECT 
            s.student_id, 
            s.student_name,
            ARRAY_AGG(rt.title) FILTER (WHERE sdl.id IS NULL) AS pending_tasks
        FROM students s
        CROSS JOIN routine_templates rt
        LEFT JOIN student_daily_logs sdl 
            ON sdl.student_id = s.student_id 
            AND sdl.template_id = rt.id 
            AND sdl.log_date = $1
        WHERE rt.section = $2 AND rt.status = 'active'
        GROUP BY s.student_id, s.student_name
        HAVING COUNT(sdl.id) < $3;
        """
        pending_students = await conn.fetch(query, today, section_name, total_active_count)

    if not pending_students:
        return

    for idx, stu in enumerate(pending_students):
        s_id = stu["student_id"]
        s_name = stu["student_name"] or "သင်တန်းသား"
        tasks = stu["pending_tasks"] or []
        task_list_str = "\n".join([f"  ▫️ {t}" for t in tasks])

        msg = (
            f"🔔 *{reminder_title}*\n\n"
            f"မင်္ဂလာပါ *{s_name}* ခင်ဗျာ၊\n"
            f"ယနေ့ {section_name} အတွက် အောက်ပါ လေ့ကျင့်ခန်းများ လုပ်ဆောင်ရန် ကျန်ရှိနေပါသေးသည် -\n\n"
            f"{task_list_str}\n\n"
            f"အဆင်ပြေသည့်အခါ အထက်ရှိ စာရင်းမှတစ်ဆင့် ပြီးစီးကြောင်း မှတ်သားပေးပါခင်ဗျာ။ 🙏"
        )

        try:
            await notify_with_three_beeps(s_id, msg)
        except Forbidden:
            print(f"⚠️ Student {s_id} blocked the bot.")
        except Exception as e:
            print(f"❌ Failed to remind {s_id}: {e}")

        if (idx + 1) % 20 == 0:
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(0.1)

async def send_evening_all_pending_summary(application):
    today = datetime.now(MM_TZ).date()

    async with db_pool.acquire() as conn:
        active_tasks = await conn.fetch("SELECT id FROM routine_templates WHERE status = 'active';")
        if not active_tasks:
            return

        total_active_count = len(active_tasks)

        query = """
        SELECT 
            s.student_id, 
            s.student_name,
            COUNT(rt.id) FILTER (WHERE sdl.id IS NULL AND rt.section = 'morning') AS morning_pending,
            COUNT(rt.id) FILTER (WHERE sdl.id IS NULL AND rt.section = 'afternoon') AS afternoon_pending,
            COUNT(rt.id) FILTER (WHERE sdl.id IS NULL AND rt.section = 'evening') AS evening_pending,
            COUNT(rt.id) FILTER (WHERE sdl.id IS NULL) AS total_pending
        FROM students s
        CROSS JOIN routine_templates rt
        LEFT JOIN student_daily_logs sdl 
            ON sdl.student_id = s.student_id 
            AND sdl.template_id = rt.id 
            AND sdl.log_date = $1
        WHERE rt.status = 'active'
        GROUP BY s.student_id, s.student_name
        HAVING COUNT(sdl.id) < $2;
        """
        pending_students = await conn.fetch(query, today, total_active_count)

    if not pending_students:
        return

    for idx, stu in enumerate(pending_students):
        s_id = stu["student_id"]
        s_name = stu["student_name"] or "သင်တန်းသား"
        m_count = stu["morning_pending"]
        a_count = stu["afternoon_pending"]
        e_count = stu["evening_pending"]
        total_p = stu["total_pending"]

        msg = (
            f"🌙 *တစ်နေ့တာ လေ့ကျင့်မှု အကျဉ်းချုပ် သတိပေးချက်*\n\n"
            f"မင်္ဂလာပါ *{s_name}* ခင်ဗျာ၊\n"
            f"ယနေ့အတွက် မပြီးပြတ်သေးသော အလေ့အကျင့် စုစုပေါင်း (*{total_p}*) ခု ကျန်ရှိနေပါသည်:\n\n"
            f"  🌅 မနက်ပိုင်း အလုပ်ကြွေး: *{m_count}* ခု\n"
            f"  ☀️ နေ့လယ်ပိုင်း အလုပ်ကြွေး: *{a_count}* ခု\n"
            f"  🌙 ညနေပိုင်း အလုပ်ကြွေး: *{e_count}* ခု\n\n"
            f"အိပ်ရာမဝင်မီ စိတ်တည်ငြိမ်အေးချမ်းစွာဖြင့် ကျန်ရှိနေသည်များကို ပြီးမြောက်အောင် ဖြည့်စွက်ပေးပါခင်ဗျာ။ 🙏"
        )

        try:
            await notify_with_three_beeps(s_id, msg)
        except Forbidden:
            print(f"⚠️ Student {s_id} blocked the bot.")
        except Exception as e:
            print(f"❌ Failed evening summary to {s_id}: {e}")

        if (idx + 1) % 20 == 0:
            await asyncio.sleep(2.0)
        else:
            await asyncio.sleep(0.1)

async def cmd_remind_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ဤ Command ကို အသုံးပြုရန် ခွင့်ပြုချက်မရှိပါ။")
        return

    if not context.args:
        help_text = (
            "📌 *အသုံးပြုပုံ:*\n"
            "`/remind_pending morning` (မနက်ခင်း အလုပ်ကြွေး)\n"
            "`/remind_pending afternoon` (နေ့လယ်ခင်း အလုပ်ကြွေး)\n"
            "`/remind_pending evening` (ညနေ စုစုပေါင်း အရေအတွက်)"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return

    section = context.args[0].lower().strip()
    status_msg = await update.message.reply_text(f"⏳ {section} reminder စတင် ပို့ဆောင်နေပါသည်...")

    try:
        if section == "morning":
            await send_section_pending_reminder(context.application, "morning", "မနက်ခင်း လေ့ကျင့်မှု သတိပေးချက်")
        elif section == "afternoon":
            await send_section_pending_reminder(context.application, "afternoon", "နေ့လယ်ခင်း လေ့ကျင့်မှု သတိပေးချက်")
        elif section == "evening":
            await send_evening_all_pending_summary(context.application)
        else:
            await status_msg.edit_text("⚠️ Section အမည် မှားယွင်းနေပါသည်။ (`morning`, `afternoon`, `evening`)")
            return

        await status_msg.edit_text(f"✅ {section} reminder စမ်းသပ် ပို့ဆောင်ခြင်း ပြီးမြောက်ပါပြီ။")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error ဖြစ်ပေါ်ပါသည်: {e}")

# ================= Main Runner =================

async def post_init(application):
    print("-> post_init called!")  # 👈 ဒီဟာလေး ထည့်ပြီး စမ်းပါ
    global scheduler
    await init_db()

    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Yangon"))

    # Routine Checklists
    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=5, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["morning", "🌅 မင်္ဂလာနံနက်ခင်းပါခင်ဗျာ၊ ယနေ့ နံနက်ပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
        id="morning_routine",
        name="Morning Routine Checklist"
    )

    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=12, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["afternoon", "☀️ မင်္ဂလာနေ့လယ်ခင်းပါခင်ဗျာ၊ နေ့လယ်ပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
        id="afternoon_routine",
        name="Afternoon Routine Checklist"
    )

    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=17, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["evening", "🌙 မင်္ဂလာညချမ်းပါခင်ဗျာ၊ ညပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
        id="evening_routine",
        name="Evening Routine Checklist"
    )

    # Reminders
    scheduler.add_job(
        send_section_pending_reminder,
        CronTrigger(hour=11, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application, "morning", "မနက်ခင်း လေ့ကျင့်မှု သတိပေးချက်"],
        id="reminder_morning_11am",
        name="Morning Routine 11 AM Reminder",
        misfire_grace_time=300,
        coalesce=True
    )

    scheduler.add_job(
        send_section_pending_reminder,
        CronTrigger(hour=16, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application, "afternoon", "နေ့လယ်ခင်း လေ့ကျင့်မှု သတိပေးချက်"],
        id="reminder_afternoon_4pm",
        name="Afternoon Routine 4 PM Reminder",
        misfire_grace_time=300,
        coalesce=True
    )

    scheduler.add_job(
        send_evening_all_pending_summary,
        CronTrigger(hour=20, minute=15, timezone=ZoneInfo("Asia/Yangon")),
        args=[application],
        id="reminder_evening_815pm",
        name="Evening Routine 8:15 PM Pending Summary",
        misfire_grace_time=300,
        coalesce=True
    )

    # Weekly Reports
    scheduler.add_job(
        broadcast_detailed_weekly_reports,
        CronTrigger(day_of_week="sun", hour=20, minute=30, timezone=ZoneInfo("Asia/Yangon")),
        args=[application],
        id="weekly_students_broadcast_job",
        name="Weekly AI Detailed Broadcast for Students",
        misfire_grace_time=300,
        coalesce=True
    )

    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=21, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application],
        id="weekly_report",
        name="Weekly Report"
    )

    scheduler.start()
    print("⏰ Routine & Report Schedulers started successfully...")

async def manual_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_weekly_report(context.application, manual_chat_id=update.effective_user.id)

async def set_next_week_bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """နောက်အပတ်အတွက် မနက်/နေ့လယ်/ည Routine အားလုံးကို တစ်ကြိမ်တည်းဖြင့် အစုလိုက် ထည့်သွင်းခြင်း"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    # Message ထဲမှ စာကြောင်းများကို ယူခြင်း
    raw_text = update.message.text.partition('\n')[2].strip()
    if not raw_text:
        help_text = (
            "📌 *နောက်အပတ်အတွက် အလုပ်စာရင်း အစုလိုက် ထည့်သွင်းပုံ:*\n\n"
            "`/set_next_week` ဟု ရိုက်ပြီး အောက်ကြောင်းတွင် ပုံစံအတိုင်း ရေးပေးပါ -\n\n"
            "`/set_next_week`\n"
            "`morning | ခေါင်းစဉ် | ရှင်းလင်းချက်`\n"
            "`afternoon | ခေါင်းစဉ် | ရှင်းလင်းချက်`\n"
            "`evening | ခေါင်းစဉ် | ရှင်းလင်းချက်`\n\n"
            "💡 အချိန်ပိုင်းနေရာတွင် `morning`, `afternoon`, `evening` ဟု ရေးပေးရပါမည်။"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return

    lines = raw_text.split('\n')
    valid_tasks = []
    
    # စာကြောင်းတစ်ကြောင်းချင်းစီကို parse လုပ်ခြင်း
    for idx, line in enumerate(lines, start=1):
        line = line.strip()
        if not line or "|" not in line:
            continue
            
        parts = [p.strip() for p in line.split("|")]
        section = parts[0].lower()
        title = parts[1]
        desc = parts[2] if len(parts) > 2 else ""

        if section in ["morning", "afternoon", "evening"]:
            valid_tasks.append((section, title, desc))

    if not valid_tasks:
        await update.message.reply_text("⚠️ မှန်ကန်သော Task စာရင်းတစ်ခုမျှ မပါဝင်ပါ။ `section | title | description` ပုံစံ မှန်ကန်မှု ရှိ/မရှိ စစ်ဆေးပေးပါ။")
        return

    # Database ထဲသို့ ထည့်သွင်းခြင်း
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # ယခင်က next စာရင်းအဟောင်းများ ရှိနေပါက ရှင်းထုတ်ပစ်ခြင်း
            await conn.execute("DELETE FROM routine_templates WHERE status = 'next';")

            order_counters = {"morning": 1, "afternoon": 1, "evening": 1}
            for section, title, desc in valid_tasks:
                order = order_counters[section]
                await conn.execute(
                    """
                    INSERT INTO routine_templates (section, task_order, title, description, status, is_active)
                    VALUES ($1, $2, $3, $4, 'next', TRUE);
                    """,
                    section, order, title, desc
                )
                order_counters[section] += 1

    summary_msg = (
        f"✅ *နောက်အပတ်အတွက် Task စုစုပေါင်း ({len(valid_tasks)}) ခုကို Next စာရင်းသို့ ထည့်သွင်းပြီးပါပြီ!*\n\n"
        f"🌅 မနက်ပိုင်း: {order_counters['morning'] - 1} ခု\n"
        f"☀️ နေ့လယ်ပိုင်း: {order_counters['afternoon'] - 1} ခု\n"
        f"🌙 ညနေပိုင်း: {order_counters['evening'] - 1} ခု\n\n"
        f"👉 စာရင်းစစ်ဆေးရန်: `/list_tasks`\n"
        f"👉 အပတ်သစ်စတင်ရန်: `/switch_week`"
    )
    await update.message.reply_text(summary_msg, parse_mode="Markdown")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        print(f"⚠️ Network glitch ခေတ္တဖြစ်ပေါ်ပါသည်: {err}")
    else:
        print(f"❌ Error ဖြစ်ပေါ်ပါသည်: {err}")
async def cmd_view_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """သင်တန်းသားများထံ Notification မပို့ဘဲ လက်ရှိ အလုပ်ကြွေးစာရင်းကို ဆရာက ယာယီစစ်ဆေးကြည့်ရှုခြင်း"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    today = datetime.now(MM_TZ).date()

    async with db_pool.acquire() as conn:
        # လက်ရှိ active ဖြစ်နေသော tasks များအားလုံး
        active_tasks = await conn.fetch(
            "SELECT id, section, title FROM routine_templates WHERE status = 'active' AND is_active = TRUE ORDER BY section, task_order ASC;"
        )
        if not active_tasks:
            await update.message.reply_text("⚠️ လက်ရှိ သတ်မှတ်ထားသော Active Task များ မရှိသေးပါခင်ဗျာ။")
            return

        total_active_count = len(active_tasks)

        # အလုပ်မပြီးသေးသော သင်တန်းသားများနှင့် ၎င်းတို့၏ ကျန်ရှိသော task များကို ဆွဲထုတ်ခြင်း
        query = """
        SELECT 
            s.student_id, 
            s.student_name,
            ARRAY_AGG(rt.title || ' [' || rt.section || ']') FILTER (WHERE sdl.id IS NULL) AS pending_tasks,
            COUNT(rt.id) FILTER (WHERE sdl.id IS NULL) AS pending_count
        FROM students s
        CROSS JOIN routine_templates rt
        LEFT JOIN student_daily_logs sdl 
            ON sdl.student_id = s.student_id 
            AND sdl.template_id = rt.id 
            AND sdl.log_date = $1
            AND sdl.is_done = TRUE
        WHERE rt.status = 'active' AND rt.is_active = TRUE
        GROUP BY s.student_id, s.student_name
        HAVING COUNT(sdl.id) < $2
        ORDER BY pending_count DESC;
        """
        pending_students = await conn.fetch(query, today, total_active_count)

    if not pending_students:
        await update.message.reply_text(
            f"🎉 *ယနေ့ ({today.strftime('%d/%m/%Y')}) အခြေအနေ:*\n\nသင်တန်းသားအားလုံး သတ်မှတ်ထားသော Routine များကို အပြည့်အဝ ပြီးမြောက်ထားကြပါသည် (အလုပ်ကြွေး မရှိပါ)။",
            parse_mode="Markdown"
        )
        return

    msg = [f"📋 *ယနေ့ ({today.strftime('%d/%m/%Y')}) အလုပ်ကြွေး ကျန်ရှိသူများ စာရင်း*\n━━━━━━━━━━━━━━━━━━━━\n"]

    for stu in pending_students:
        s_name = stu["student_name"] or "အမည်မသိ"
        s_id = stu["student_id"]
        p_count = stu["pending_count"]
        tasks = stu["pending_tasks"] or []

        msg.append(f"👤 *{s_name}* (ID: `{s_id}`) — ကျန်ရှိ: *{p_count}* ခု")
        for t in tasks:
            msg.append(f"   ▫️ {t}")
        msg.append("")

    msg.append("💡 _မှတ်ချက်: ဤစာရင်းသည် ဆရာ ကြည့်ရှုရန် သီးသန့်ဖြစ်ပြီး သင်တန်းသားများထံ သတိပေးချက် မရောက်ပါ။_")

    await send_safe_message(context.bot, update.effective_chat.id, "\n".join(msg))
    
    # 👇 ဤနေရာတွင် ထည့်ပါ
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    # FastAPI Server ကို Background Thread ဖြင့် စတင်ခြင်း
    threading.Thread(target=run_fastapi, daemon=True).start()
    print("🌐 FastAPI server started on background thread.")
    
    #await init_db()
    # Timeout များကို တိုးမြှင့်ထားသော Request Config
    request_config = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30.0,
        write_timeout=30.0,
        connect_timeout=30.0,
        pool_timeout=30.0
    )
    app = (
        ApplicationBuilder()
        .token(TEACHER_BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    # Handlers များ
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("add_task", add_task_command))
    app.add_handler(CommandHandler("delete_task", delete_task_command))
    app.add_handler(CommandHandler("switch_week", switch_week_command))
    app.add_handler(CommandHandler("list_tasks", list_tasks_command))
    app.add_handler(CommandHandler("toggle_task", toggle_task_command))
    app.add_handler(CommandHandler("today", today_status))
    app.add_handler(CommandHandler("questions", view_questions))
    app.add_handler(CommandHandler("reply", reply_to_student))
    #app.add_handler(CommandHandler("weekly_report", manual_weekly_report)
    app.add_handler(CommandHandler("weekly_report", lambda u, c: send_weekly_report(c.application, manual_chat_id=u.effective_chat.id)))
    app.add_handler(CommandHandler("jobs", cmd_check_jobs))
    app.add_handler(CommandHandler("send_routine", cmd_send_routine))
    app.add_handler(CommandHandler("detail_report", cmd_detail_report))
    app.add_handler(CommandHandler("ai_report", cmd_ai_report))
    app.add_handler(CommandHandler("export_excel", cmd_export_excel))
    app.add_handler(CallbackQueryHandler(export_excel_callback_handler, pattern="^excl_"))
    app.add_handler(CommandHandler("remind_pending", cmd_remind_pending))
    app.add_handler(CommandHandler("set_next_week", set_next_week_bulk_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_telegram_direct_reply))
    app.add_handler(CommandHandler("pending", cmd_view_pending))
    app.add_handler(CallbackQueryHandler(handle_teacher_action))
    app.add_handler(CallbackQueryHandler(report_callback_handler, pattern=r"^rep_"))
    app.add_handler(CallbackQueryHandler(ai_report_callback_handler, pattern="^airep_"))
    app.add_handler(CallbackQueryHandler(handle_send_routine_callback, pattern=r"^send_routine:"))
    app.add_error_handler(error_handler)
    
    # 🌟 အရေးကြီးသည် - post_init ကို ဤနေရာတွင် တိုက်ရိုက် လှမ်းခေါ်ပေးခြင်း 🌟
    await post_init(app)
    
    print("🤖 Teacher Bot is up and running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Bot ရပ်မသွားစေရန် စောင့်ဆိုင်းခြင်း
    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot Stopped.")