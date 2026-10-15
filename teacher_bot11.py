import os
import asyncio
import sys
from google.genai.errors import APIError
import re
from datetime import datetime, timezone, timedelta
import openpyxl
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
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, Reference

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

# 🟢 Batch ID သတ်မှတ်ခြင်း (၁၆ ယောက်အုပ်စုအတွက်)
BATCH_ID = os.getenv("BATCH_ID", "batch_Youth")

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
#DB_USER = "postgres"
#DB_PASSWORD = "jD7Zx3QP0qRBVyjW"
#DB_HOST = "db.hsexqfwveqfsyjdbjlee.supabase.co"
#DB_PORT = 5432
#DB_NAME = "postgres"
# Supabase Credentials (.env မှတဆင့် ခေါ်ယူခြင်း)
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "postgres")

MM_TZ = timezone(timedelta(hours=6, minutes=30))
db_pool = None
scheduler = AsyncIOScheduler(timezone=MM_TZ)

ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# ================= FastAPI Web Server (Port Binding) =================

app = FastAPI()

@app.get("/")
@app.head("/")
def health_check():
    #return {"status": "active", "bot": "Teacher Bot (Batch "BATCH_ID ") is running successfully!"}
    return {"status": "active", "bot": f"Teacher Bot ({BATCH_ID}) is running successfully!"}

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
        statement_cache_size=0
    )
    print("✅ Connected to Supabase Pooler.")

async def close_db():
    global db_pool
    if db_pool:
        await db_pool.close()

# ----------------- Gemini AI Analysis Function -----------------

async def analyze_weekly_data_with_gemini(student_records_text: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ Gemini API Key မရှိသဖြင့် AI သုံးသပ်ချက် မထုတ်နိုင်ပါ။"

    prompt = f"""
သင်သည် သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ရေး (Mindfulness Routine) သင်တန်းမှ ဆရာကြီးအတွက် အကူလက်ထောက် AI ဖြစ်ပါသည်။
အောက်ပါတို့သည် ပြီးခဲ့သည့် (၇) ရက်အတွင်း သင်တန်းသားများ၏ လေ့ကျင့်မှု ပြီးစီးမှု မှတ်တမ်းများနှင့် ၎င်းတို့ မေးမြန်း/ဆွေးနွေးထားသော အတွေ့အကြုံများ ဖြစ်ပါသည်:

{student_records_text}

ဆရာကြီး အလွယ်တကူ သုံးသပ်နိုင်ရန်အတွက် အောက်ပါအတိုင်း မြန်မာလို အနှစ်ချုပ် ရေးသားပေးပါ:
၁။ သင်တန်းသားတစ်ဦးချင်းစီ၏ လေ့ကျင့်မှု အားသာချက်/အားနည်းချက်၊ တရားထိုင်ခြင်း၊ ထိုင်ဖို့ပျက်ကွက်ခြင်း 
၂။ သင်တန်းသားများ၏ စိတ်ပိုင်းဆိုင်ရာ အတွေ့အကြုံ သို့မဟုတ် အခက်အခဲများအပေါ် ဆရာကြီး အဓိက သတိပြု လမ်းညွှန်ပေးသင့်သည့် အချက် (Actionable Insight)
၃။ စာဖတ်ရ လွယ်ကူစေရန် Bullet points များနှင့် သပ်ရပ်စွာ လိုတိုရှင်းရေးပေးပါ။
"""

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
            except Exception as e:
                await asyncio.sleep(2)
                
    return "⚠️ AI သုံးသပ်ချက် ရယူရာတွင် Network ချိတ်ဆက်မှု ခေတ္တ အခက်အခဲရှိနေပါသဖြင့် နောက်တစ်ကြိမ် ပြန်လည်ကြိုးစားပေးပါခင်ဗျာ။"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ ဤ Bot သည် ဆရာနှင့် Admin သီးသန့် Bot ဖြစ်ပါသည်။")
        return

    text = (
        f"မင်္ဂလာပါ {update.effective_user.first_name} 🙏 (Admin/Teacher - {BATCH_ID})\n\n"
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
        WHERE s.batch_id = $2
        GROUP BY s.student_id, s.student_name;
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, today, BATCH_ID)

    if not rows:
        await update.message.reply_text("ယနေ့အတွက် သင်တန်းသားများဘက်မှ မှတ်တမ်း မရှိသေးပါခင်ဗျာ။")
        return

    lines = [f"📊 *ယနေ့ ({today.strftime('%d/%m/%Y')}) သင်တန်းသားများ လေ့ကျင့်မှု အခြေအနေ ({BATCH_ID}) -*\n"]
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
        SELECT r.id, r.student_id, r.student_name, r.reflection_text, r.created_at, r.teacher_reply
        FROM student_reflections r
        JOIN students s ON r.student_id = s.student_id
        WHERE s.batch_id = $1
        ORDER BY r.created_at DESC
        LIMIT 10;
    """
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, BATCH_ID)

    if not rows:
        await update.message.reply_text("မေးခွန်းနှင့် ဆွေးနွေးချက်များ မရှိသေးပါခင်ဗျာ။")
        return

    lines = ["📝 *နောက်ဆုံး လက်ခံရရှိထားသော မေးခွန်း/အတွေ့အကြုံများ ({BATCH_ID})-*\n"]
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
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/reply <student_id> <စာသား>`")
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
    except Exception as e:
        await update.message.reply_text(f"❌ ပို့ဆောင်၍ မရပါ Error: `{e}`", parse_mode="Markdown")

async def handle_send_routine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split(":")
    section = parts[1]
    target_student_id = int(parts[2]) if len(parts) > 2 else None

    header_titles = {
        "morning": "🌅 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3)*မနက်ပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*",
        "afternoon": "☀️ သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3)*နေ့လည်ပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*",
        "evening": "🌙 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) *ညနေပိုင်း သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ခန်းများ*"
    }
    header_text = header_titles.get(section, "📌 *နေ့စဉ် လေ့ကျင့်ခန်းများ*")

    try:
        student_bot = Bot(token=STUDENT_BOT_TOKEN)
        async with db_pool.acquire() as conn:
            tasks = await conn.fetch(
                "SELECT id, title, description FROM routine_templates WHERE section = $1 AND status = 'active' ORDER BY task_order ASC;",
                section
            )
            today = datetime.now(MM_TZ).date()
            done_rows = await conn.fetch(
                "SELECT template_id FROM student_daily_logs WHERE student_id = $1 AND log_date = $2 AND is_done = TRUE;",
                target_student_id, today
            )
        done_ids = {r["template_id"] for r in done_rows}

        if not tasks:
            await query.message.reply_text("❌ ဤအချိန်ပိုင်းအတွက် Routine Tasks များ မရှိသေးပါ။")
            return

        await student_bot.send_message(
            chat_id=target_student_id,
            text=f"{header_text}\n\nအောက်ပါ လေ့ကျင့်မှုများကို ပြုလုပ်ပြီးပါက သက်ဆိုင်ရာခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ -",
            parse_mode="Markdown"
        )

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

        await query.edit_message_text(
            text=f"{query.message.text}\n\n✅ *[ဤသင်တန်းသားထံ Routine ပို့ပြီးပါပြီ]*",
            parse_mode="Markdown"
        )
    except Exception as e:
        await query.message.reply_text(f"❌ Routine ပို့ရာတွင် အမှားအယွင်းရှိပါသည်: {e}")

async def handle_telegram_direct_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    replied_msg = update.message.reply_to_message
    if not replied_msg or not replied_msg.text:
        return

    match = re.search(r"ID:\s*`?(\d+)`?", replied_msg.text)
    if not match:
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
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/export_excel <နာမည်>`", parse_mode="Markdown")
        return

    query_text = " ".join(context.args).strip()
    search_pattern = f"%{query_text}%"

    async with db_pool.acquire() as conn:
        students = await conn.fetch(
            "SELECT student_id, student_name FROM students WHERE student_name ILIKE $1 AND batch_id = $2;", 
            search_pattern, BATCH_ID
        )

    if not students:
        await update.message.reply_text(f"❌ '{query_text}' အမည်ဖြင့် ကိုက်ညီသော သင်တန်းသားကို မတွေ့ရှိပါ။")
        return

    if len(students) == 1:
        student_id = students[0]["student_id"]
        await generate_and_send_excel(update.message, student_id)
    else:
        keyboard = []
        for s in students:
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"excl_{s['student_id']}")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 '{query_text}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)} ယောက်) တွေ့ရှိပါသည်။",
            reply_markup=reply_markup
        )

async def export_excel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    percent = (total_done / total_assigned * 100) if total_assigned > 0 else 0

    msg = f"📊 *အပတ်စဉ် လေ့ကျင့်မှု အသေးစိတ် မှတ်တမ်း ({BATCH_ID})*\n"
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

    return msg

async def generate_personalized_ai_report(student_name: str, task_summary: list) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    breakdown_text = ""
    for r in task_summary:
        breakdown_text += f"- {r['task_title']} ({r['section']}): {r['total_days']} ရက်မှာ {r['completed_count']} ကြိမ် ပြီးမြောက်ခဲ့သည်။\n"

    prompt = f"""
သင်သည် မေတ္တာနှင့် ဂရုစိုက်မှုအပြည့်ရှိသော ဗုဒ္ဓဘာသာ/Mindfulness လေ့ကျင့်ရေး သင်တန်းဆရာတစ်ဦး ဖြစ်သည်။
သင်တန်းသား "{student_name}" ၏ ပြီးခဲ့သော တစ်ပတ်တာ လေ့ကျင့်မှု အသေးစိတ် မှတ်တမ်းအပေါ် အခြေခံ၍ အပတ်စဉ် သုံးသပ်ချက် အစီရင်ခံစာကို မြန်မာဘာသာဖြင့် ရေးသားပေးပါ:
၁။ ကောင်းမွန်စွာ ပြုလုပ်ထားသော အလုပ်များကို အသိအမှတ်ပြု ချီးကျူးပေးပါ။
၂။ လေ့ကျင့်မှု အားနည်းချက်များရှိပါက နူးညံ့စွာ နားချပြီး လာမည့်အပတ်တွင် စမ်းသပ်လုပ်ဆောင်ရန် တွန်းအားပေးပါ။
"""
    candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash"]
    for model_name in candidate_models:
        try:
            response = await client.aio.models.generate_content(model=model_name, contents=prompt)
            if response.text:
                return response.text
        except Exception:
            continue
    return f"🙏 မင်္ဂလာပါ {student_name}ခင်ဗျာ၊ ယခုတစ်ပတ်အတွက် အစီရင်ခံစာ အဆင်ပြေပါသည်။"

async def cmd_detail_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ **အသုံးပြုပုံ:** `/detail_report နာမည်`", parse_mode="Markdown")
        return

    search_name = " ".join(context.args).strip()
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        students = await conn.fetch(
            "SELECT student_id, student_name FROM students WHERE student_name ILIKE $1 AND batch_id = $2;", 
            f"%{search_name}%", BATCH_ID
        )

        if not students:
            await update.message.reply_text(f"❌ '{search_name}' အမည်ဖြင့် သင်တန်းသားကို ရှာမတွေ့ပါ။")
            return

        if len(students) == 1:
            student = students[0]
            await generate_and_send_report(update, context, student["student_id"], student["student_name"], start_date, today)
            return

        msg_lines = [
            f"🔍 '{search_name}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)}) ဦး တွေ့ရှိပါသည်။",
            "ကျေးဇူးပြု၍ အောက်ပါစာသားများကို Copy ကူး၍ အသုံးပြုပါ:\n"
        ]
        for s in students:
            cmd_text = f"/detail_report {s['student_name']}"
            msg_lines.append(f"• `{cmd_text}`")
        
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")

async def generate_and_send_report(update_or_query, context, student_id, student_name, start_date, today):
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
            WHERE t.status = 'active' AND t.batch_id = $4
            ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
        """
        rows = await conn.fetch(detail_query, student_id, start_date, today, BATCH_ID)

    if not rows:
        msg = f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။"
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    # ==========================================
    # 1. TELEGRAM တွင် ပြသရန် Text Report & Graph တည်ဆောက်ခြင်း
    # ==========================================
    daily_stats = {}
    for r in rows:
        d_val = r["log_date"]
        if d_val not in daily_stats:
            daily_stats[d_val] = {"done": 0, "total": 0}
        daily_stats[d_val]["total"] += 1
        if r["is_done"]:
            daily_stats[d_val]["done"] += 1

    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)

    report_text = f"📊 *{student_name} ၏ (၇) ရက်တာ အစီရင်ခံစာ*\n"
    report_text += f"━━━━━━━━━━━━━━━━━━━━━\n"
    report_text += f"🎯 စုစုပေါင်း ပြီးစီးမှု: *{done_count} / {total_count}* Tasks\n\n"
    
    report_text += f"📉 *နေ့အလိုက် ပြီးစီးမှု ဇယား (Progress Graph):*\n"
    for d_val in sorted(daily_stats.keys()):
        stats = daily_stats[d_val]
        d_done = stats["done"]
        d_total = stats["total"]
        
        if d_total > 0:
            percentage = d_done / d_total
            filled_blocks = round(percentage * 5)
            empty_blocks = 5 - filled_blocks
            bar = "🟩" * filled_blocks + "⬜" * empty_blocks
        else:
            bar = "⬜⬜⬜⬜⬜"
            
        date_str = d_val.strftime("%m-%d (%a)")
        report_text += f"`{date_str}` | {bar} *{d_done}/{d_total}*\n"

    report_text += f"━━━━━━━━━━━━━━━━━━━━━\n"
    report_text += f"📌 *နောက်ဆုံး မှတ်တမ်းအသေးစိတ်:*\n"
    for r in rows[:10]: 
        status_icon = "✅" if r["is_done"] else "❌"
        report_text += f"• `{r['log_date']}` | {r['section'][:4]} | {r['task_title']} {status_icon}\n"

    # ==========================================
    # 2. EXCEL ဖိုင် (.xlsx) တည်ဆောက်ခြင်း
    # ==========================================
    wb = Workbook()
    ws = wb.active
    ws.title = "Progress Report"

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Myanmar Text", size=11, bold=True, color="FFFFFF")
    bold_font = Font(name="Myanmar Text", size=10, bold=True)
    normal_font = Font(name="Myanmar Text", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    border_thin = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = f"Student Progress Report: {student_name} ({start_date} to {today})"
    title_cell.font = Font(name="Myanmar Text", size=12, bold=True, color="1F4E78")
    title_cell.alignment = center_align

    ws.merge_cells("A2:E2")
    summary_cell = ws["A2"]
    summary_cell.value = f"စုစုပေါင်း ပြီးစီးမှု: {done_count} / {total_count} Tasks"
    summary_cell.font = bold_font
    summary_cell.alignment = left_align

    ws.append([]) 

    headers = ["ရက်စွဲ (Date)", "အပိုင်း (Section)", "လေ့ကျင့်ခန်း ခေါင်းစဉ် (Task Title)", "အခြေအနေ (Status)", "ဆင်ခြင်သုံးသပ်ချက် (Reflection)"]
    ws.append(headers)
    
    for col_num in range(1, 6):
        cell = ws.cell(row=4, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align

    for r in rows:
        status_str = "Done (ပြီးပြီ)" if r["is_done"] else "Pending (မပြီးသေး)"
        row_data = [
            str(r["log_date"]),
            r["section"],
            r["task_title"],
            status_str,
            r["reflection"]
        ]
        ws.append(row_data)

    for row in ws.iter_rows(min_row=5, max_row=4 + len(rows), min_col=1, max_col=5):
        for cell in row:
            cell.font = normal_font
            cell.border = border_thin
            if cell.column in [1, 2, 4]:
                cell.alignment = center_align
            else:
                cell.alignment = left_align

    for col in range(1, 6):
        max_len = 0
        col_letter = openpyxl.utils.get_column_letter(col)
        for row in range(4, 5 + len(rows)):
            cell_value = ws.cell(row=row, column=col).value
            if cell_value:
                max_len = max(max_len, len(str(cell_value)))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 18)

    excel_file = io.BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    excel_file.name = f"{student_name}_report.xlsx"

    # ==========================================
    # 3. TELEGRAM သို့ စာသားရော ဖိုင်ပါ ပို့ဆောင်ခြင်း
    # ==========================================
    try:
        target_message = update_or_query.message if isinstance(update_or_query, Update) else update_or_query
        
        # 1. ပထမဦးစွာ Text Report (Summary & Graph) ကို ပို့မည်
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(report_text, parse_mode="Markdown")
            # ပြီးလျှင် Excel ဖိုင်ကိုပါ နောက်ထပ် မက်ဆေ့ချ်အနေဖြင့် ဆက်တိုက် ပို့မည်
            await target_message.reply_document(
                document=excel_file,
                caption=f"📁 *{student_name}* ၏ အသေးစိတ် Excel အစီရင်ခံစာ",
                parse_mode="Markdown"
            )
            await update_or_query.callback_query.answer()
        else:
            await target_message.reply_text(report_text, parse_mode="Markdown")
            await target_message.reply_document(
                document=excel_file,
                caption=f"📁 *{student_name}* ၏ အသေးစိတ် Excel အစီရင်ခံစာ",
                parse_mode="Markdown"
            )
            
    except Exception as e:
        print(f"❌ Send Report Error: {e}")
        
async def generate_and_send_reportwithexcel(update_or_query, context, student_id, student_name, start_date, today):
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
            WHERE t.status = 'active' AND t.batch_id = $4
            ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
        """
        rows = await conn.fetch(detail_query, student_id, start_date, today, BATCH_ID)

    if not rows:
        msg = f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။"
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    # 📊 Excel Workbook ဖန်တီးခြင်း
    wb = Workbook()
    ws = wb.active
    ws.title = "Progress Report"

    # Excel Styling များ သတ်မှတ်ခြင်း
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Myanmar Text", size=11, bold=True, color="FFFFFF")
    bold_font = Font(name="Myanmar Text", size=10, bold=True)
    normal_font = Font(name="Myanmar Text", size=10)
    center_align = Alignment(horizontal="center", vertical="center")
    left_align = Alignment(horizontal="left", vertical="center")
    border_thin = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )

    # 1. ခေါင်းစဉ်ထည့်ခြင်း
    ws.merge_cells("A1:E1")
    title_cell = ws["A1"]
    title_cell.value = f"Student Progress Report: {student_name} ({start_date} to {today})"
    title_cell.font = Font(name="Myanmar Text", size=12, bold=True, color="1F4E78")
    title_cell.alignment = center_align

    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)
    
    ws.merge_cells("A2:E2")
    summary_cell = ws["A2"]
    summary_cell.value = f"စုစုပေါင်း ပြီးစီးမှု: {done_count} / {total_count} Tasks"
    summary_cell.font = bold_font
    summary_cell.alignment = left_align

    ws.append([]) # Row 3 (Empty)

    # 2. Table Headers
    headers = ["ရက်စွဲ (Date)", "အပိုင်း (Section)", "လေ့ကျင့်ခန်း ခေါင်းစဉ် (Task Title)", "အခြေအနေ (Status)", "ဆင်ခြင်သုံးသပ်ချက် (Reflection)"]
    ws.append(headers)
    
    for col_num in range(1, 6):
        cell = ws.cell(row=4, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align

    # 3. Data ထည့်သွင်းခြင်း
    for r in rows:
        status_str = "Done (ပြီးပြီ)" if r["is_done"] else "Pending (မပြီးသေး)"
        row_data = [
            str(r["log_date"]),
            r["section"],
            r["task_title"],
            status_str,
            r["reflection"]
        ]
        ws.append(row_data)

    # Data row များကို Border နှင့် Font ထည့်ခြင်း
    for row in ws.iter_rows(min_row=5, max_row=4 + len(rows), min_col=1, max_col=5):
        for cell in row:
            cell.font = normal_font
            cell.border = border_thin
            if cell.column in [1, 2, 4]:
                cell.alignment = center_align
            else:
                cell.alignment = left_align

    # Column အကျယ်များကို အလိုအလျောက် သင့်လျော်အောင် ချိန်ညှိခြင်း
    #for col in ws.columns:
        #max_len = max(len(str(cell.value or '')) for cell in col)
        #col_letter = col[0].column_letter
        #ws.column_dimensions[col_letter].width = max(max_len + 4, 15)
    
    # 🟢 ကော်လံ အကျယ်များကို အလိုအလျောက် သင့်လျော်အောင် ချိန်ညှိခြင်း (MergedCell Error ကင်းဝေးစေရန်)
    for col in range(1, 6):
        max_len = 0
        col_letter = openpyxl.utils.get_column_letter(col)
        
        # Row 4 (Header) ကနေစပြီး Data တွေအထိ အရှည်ဆုံး စာသားကို ရှာခြင်း
        for row in range(4, 5 + len(rows)):
            cell_value = ws.cell(row=row, column=col).value
            if cell_value:
                max_len = max(max_len, len(str(cell_value)))
                
        ws.column_dimensions[col_letter].width = max(max_len + 4, 18)

    # Memory ထဲသို့ သိမ်းဆည်းခြင်း (BytesIO)
    excel_file = io.BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    excel_file.name = f"{student_name}_report.xlsx"

    # 📤 Telegram သို့ Document အဖြစ် ပို့ဆောင်ခြင်း
    try:
        # Status မက်ဆေ့ချ်ကို ဖယ်ရှားရန် သို့မဟုတ် Update လုပ်ရန်
        target_message = update_or_query.message if isinstance(update_or_query, Update) else update_or_query
        
        await target_message.reply_document(
            document=excel_file,
            caption=f"📊 *{student_name}* ၏ (၇) ရက်တာ အစီရင်ခံစာ (Excel ဖိုင်)\n✅ ပြီးစီးမှု: *{done_count}/{total_count} Tasks*",
            parse_mode="Markdown"
        )
        
        # အကယ်၍ Callback Query ဖြစ်နေပါက မူလ စာသားကို ရှင်းလင်းပေးရန်
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.answer()
            
    except Exception as e:
        print(f"❌ Send Excel Report Error: {e}")         
async def generate_and_send_report3(update_or_query, context, student_id, student_name, start_date, today):
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
            WHERE t.status = 'active' AND t.batch_id = $4
            ORDER BY d.date_val DESC, t.section ASC, t.task_order ASC;
        """
        # 🟢 BATCH_ID ဖြင့် စစ်ထုတ်ခြင်း ($4)
        rows = await conn.fetch(detail_query, student_id, start_date, today, BATCH_ID)

    if not rows:
        msg = f"📌 {student_name} အတွက် ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။"
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    # 📊 ရက်စွဲအလိုက် စုစည်းတွက်ချက်ခြင်း (Graph/Progress Bar ဖန်တီးရန်)
    daily_stats = {}
    for r in rows:
        d_val = r["log_date"]
        if d_val not in daily_stats:
            daily_stats[d_val] = {"done": 0, "total": 0}
        daily_stats[d_val]["total"] += 1
        if r["is_done"]:
            daily_stats[d_val]["done"] += 1

    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)

    # 📝 Report Header
    report_text = f"📊 *{student_name} ၏ (၇) ရက်တာ အစီရင်ခံစာ*\n"
    report_text += f"━━━━━━━━━━━━━━━━━━━━━\n"
    report_text += f"🎯 စုစုပေါင်း ပြီးစီးမှု: *{done_count} / {total_count}* Tasks\n\n"
    
    # 📈 Visual Progress Bar / Graph ပုံစံ ဖန်တီးခြင်း
    report_text += f"📉 *နေ့အလိုက် ပြီးစီးမှု ဇယား (Progress Graph):*\n"
    
    for d_val in sorted(daily_stats.keys()):
        stats = daily_stats[d_val]
        d_done = stats["done"]
        d_total = stats["total"]
        
        # 🟩 အလုပ်ပြီးပါက အစိမ်းကွက်၊ မပြီးသေးပါက အဖြူ/အမည်းကွက်ပြခြင်း
        if d_total > 0:
            percentage = d_done / d_total
            filled_blocks = round(percentage * 5) # ၅ ကွက်ပုံစံ graph
            empty_blocks = 5 - filled_blocks
            bar = "🟩" * filled_blocks + "⬜" * empty_blocks
        else:
            bar = "⬜⬜⬜⬜⬜"
            
        date_str = d_val.strftime("%m-%d (%a)")
        report_text += f"`{date_str}` | {bar} *{d_done}/{d_total}*\n"

    report_text += f"━━━━━━━━━━━━━━━━━━━━━\n"
    report_text += f"📌 *နောက်ဆုံး မှတ်တမ်းအသေးစိတ်:*\n"
    
    for r in rows[:10]: # စာသားအရှည်မလွန်စေရန် ၁၀ ခုပြခြင်း
        status_icon = "✅" if r["is_done"] else "❌"
        report_text += f"• `{r['log_date']}` | {r['section'][:4]} | {r['task_title']} {status_icon}\n"

    try:
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(report_text, parse_mode="Markdown")
        else:
            message = update_or_query.message or update_or_query.effective_message
            await message.reply_text(report_text, parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Send Report Error: {e}")

async def generate_and_send_report2(update_or_query, context, student_id, student_name, start_date, today):
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
        if isinstance(update_or_query, Update) and update_or_query.callback_query:
            await update_or_query.callback_query.edit_message_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        return

    report_text = f"📊 {student_name} ၏ (၇) ရက်တာ အစီရင်ခံစာ\n\n"
    done_count = sum(1 for r in rows if r["is_done"])
    total_count = len(rows)
    report_text += f"✅ ပြီးစီးမှု: {done_count}/{total_count} Tasks\n"
    
    for r in rows[:15]: 
        status_icon = "✅" if r["is_done"] else "❌"
        report_text += f"{r['log_date']} | {r['section']} | {r['task_title']} - {status_icon}\n"

    if isinstance(update_or_query, Update) and update_or_query.callback_query:
        await update_or_query.callback_query.edit_message_text(report_text)
    else:
        message = update_or_query.message or update_or_query.effective_message
        await message.reply_text(report_text)

async def cmd_ai_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("⚠️ **အသုံးပြုပုံ:** `/ai_report နာမည်`", parse_mode="Markdown")
        return

    search_name = " ".join(context.args).strip()
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        students = await conn.fetch(
            "SELECT student_id, student_name FROM students WHERE student_name ILIKE $1 AND batch_id = $2;", 
            f"%{search_name}%", BATCH_ID
        )

        if not students:
            await update.message.reply_text(f"❌ '{search_name}' အမည်ဖြင့် သင်တန်းသားကို ရှာမတွေ့ပါ။")
            return

        if len(students) == 1:
            student = students[0]
            await process_and_send_ai_report(update.message, student["student_id"])
            return

        msg_lines = [
            f"🔍 '{search_name}' နှင့် ကိုက်ညီသော သင်တန်းသား ({len(students)}) ဦး တွေ့ရှိပါသည်။",
            "ကျေးဇူးပြု၍ အောက်ပါစာသားများကို Copy ကူး၍ အသုံးပြုပါ:\n"
        ]
        for s in students:
            cmd_text = f"/ai_report {s['student_name']}"
            msg_lines.append(f"• `{cmd_text}`")
        
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
        
        
async def process_and_send_ai_report(message_obj, student_id: int):
    status = await message_obj.reply_text("⏳ Gemini AI ဖြင့် သုံးသပ်ချက် ထုတ်ယူနေပါသည်...")
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
        
        # 🟢 Telegram ၏ 4096 character ကန့်သတ်ချက်ကို ကျော်လွန်ပါက အပိုင်းခွဲပို့ရန်
        MAX_LENGTH = 4000
        if len(full_message) <= MAX_LENGTH:
            await message_obj.reply_text(full_message)
        else:
            # စာသားရှည်လျှင် အပိုင်းလိုက် ခွဲထုတ်၍ ပို့ခြင်း
            chunks = [full_message[i:i + MAX_LENGTH] for i in range(0, len(full_message), MAX_LENGTH)]
            for chunk in chunks:
                await message_obj.reply_text(chunk)
                await asyncio.sleep(0.3) # ပို့ဆောင်မှု တိုက်မိခြင်းမှ ကာကွယ်ရန် ခဏစောင့်ခြင်း

        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Error ဖြစ်ပေါ်နေပါသည်: {e}")
        
        

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
            "`/add_task morning | မနက်ခင်း အကြောလျှော့ခြင်း | ခန္ဓာကိုယ်ကို သတိဖြင့် ဖြေလျှော့ပါ`",
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
        # 🟢 ၁။ ဤ Batch အတွက်သာ လက်ရှိ max order ကို ရှာခြင်း
        last_order = await conn.fetchval(
            "SELECT COALESCE(MAX(task_order), 0) FROM routine_templates WHERE section = $1 AND batch_id = $2;",
            section, BATCH_ID
        )
        new_order = last_order + 1
        
        # 🟢 ၂။ SQL တွင် batch_id ကော်လံနှင့် $5 parameter ထည့်သွင်းခြင်း
        await conn.execute(
            """
            INSERT INTO routine_templates (section, task_order, title, description, status, is_active, batch_id)
            VALUES ($1, $2, $3, $4, 'active', TRUE, $5);
            """,
            section, new_order, title, description, BATCH_ID
        )

    await update.message.reply_text(
        f"✅ *({BATCH_ID}) အတွက် Task အသစ် အောင်မြင်စွာ ထည့်သွင်းပြီးပါပြီ!*\n\n"
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

    # Command ထဲတွင် batch နာမည် ပါမပါ စစ်ဆေးခြင်း (ဥပမာ: /list_tasks batch_Youth)
    args = context.args
    target_batch = args[0] if args else BATCH_ID

    async with db_pool.acquire() as conn:
        # 🟢 active_tasks တွင် batch_id ဖြင့် စစ်ထုတ်ခြင်း
        active_tasks = await conn.fetch(
            "SELECT id, section, task_order, title FROM routine_templates WHERE status = 'active' AND batch_id = $1 ORDER BY section, task_order ASC;",
            target_batch
        )
        # 🟢 next_tasks တွင် batch_id ဖြင့် စစ်ထုတ်ခြင်း
        next_tasks = await conn.fetch(
            "SELECT id, section, task_order, title FROM routine_templates WHERE status = 'next' AND batch_id = $1 ORDER BY section, task_order ASC;",
            target_batch
        )

    msg = [f"📋 *Routine စီမံခန့်ခွဲမှု အခြေအနေ ({target_batch})*\n"]
    
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
        msg.append("  _(ကြိုတင်ထည့်ထားသော Task မရှိပါ)_")

    final_text = "\n".join(msg)
    try:
        await update.message.reply_text(final_text, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(final_text)
        
      



async def cmd_send_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 🟢 Command အမှန်တကယ် ဝင်လာ/မလာ စစ်ဆေးရန် Print ထည့်ခြင်း
    print("DEBUG -> cmd_send_routine called!")

    if not is_admin(update.effective_user.id):
        print("DEBUG -> Admin check failed!")
        return
        
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: `/send_routine <morning|afternoon|evening>`", parse_mode="Markdown")
        return

    section = context.args[0].lower().strip()
    print(f"DEBUG -> Requested section: {section}")

    if section not in SECTION_HEADERS:
        print(f"DEBUG -> Section not in SECTION_HEADERS: {section}")
        await update.message.reply_text("⚠️ Section အမည် မှားယွင်းနေပါသည်။")
        return

    status_msg = await update.message.reply_text(f"⏳ Batch BATCH_ID အတွက် *{section}* routine ပို့ဆောင်နေပါသည်...", parse_mode="Markdown")
    
    # broadcast ဖန်ရှင်ကို ခေါ်ခြင်း
    sent_count = await broadcast_routine_to_students(section_name=section, header_text=SECTION_HEADERS[section])
    print(f"DEBUG -> broadcast finished. Sent count: {sent_count}")

    await status_msg.edit_text(f"✅ *{section.capitalize()} Routine ပို့ဆောင်ပြီးပါပြီ!*\n👥 လက်ခံရရှိသူ: *{sent_count}* ဦး", parse_mode="Markdown")
    

    
async def broadcast_routine_to_students(section_name: str, header_text: str, target_student_id: int = None) -> int:
    async with db_pool.acquire() as conn:
        target_batch = BATCH_ID

        if target_student_id:
            stu_batch = await conn.fetchval(
                "SELECT batch_id FROM students WHERE student_id = $1;", 
                target_student_id
            )
            if stu_batch:
                target_batch = stu_batch
            students = [{"student_id": target_student_id}]
        else:
            students = await conn.fetch(
                "SELECT student_id FROM students WHERE batch_id = $1;", 
                target_batch
            )

        tasks = await conn.fetch(
            """
            SELECT id, title, description 
            FROM routine_templates 
            WHERE section = $1 AND status = 'active' AND batch_id = $2 
            ORDER BY task_order ASC;
            """,
            section_name, target_batch
        )

        # 🔍 Debug Print များ
        print(f"DEBUG -> Target Batch: {target_batch}, Section: {section_name}")
        print(f"DEBUG -> Found Students Count: {len(students)}")
        print(f"DEBUG -> Found Tasks Count: {len(tasks)}")

        if not tasks or not students:
            print(f"⚠️ Routine ပို့ရန် မတွေ့ပါ (Tasks: {len(tasks)}, Students: {len(students)}, Batch: {target_batch})")
            return 0

        count = 0
        async with Bot(token=STUDENT_BOT_TOKEN) as s_bot:
            for stu in students:
                chat_id = stu["student_id"]
                routine_batch_id = str(uuid.uuid4())[:8]  
                batch_message_ids = []

                try:
                    # 🗑️ Header Message & Dismiss Button
                    header_keyboard = [
                        [InlineKeyboardButton("🗑️ ထပ်နေပါက ဤစာရင်းကို ဖျက်မည်", callback_data=f"dismiss_batch:{routine_batch_id}")]
                    ]
                    
                    h_msg = await s_bot.send_message(
                        chat_id=chat_id,
                        text=f"{header_text}\n\nအောက်ပါ လေ့ကျင့်မှုများကို ပြုလုပ်ပြီးပါက သက်ဆိုင်ရာခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ -",
                        reply_markup=InlineKeyboardMarkup(header_keyboard),
                        #parse_mode="Markdown"
                    )
                    batch_message_ids.append(h_msg.message_id)

                    # 📌 Task Messages
                    for idx, t in enumerate(tasks, start=1):
                        t_id = t["id"]
                        task_text = f"📌 *{idx}။ {t['title']}*"
                        if t["description"]:
                            task_text += f"\n_{t['description']}_"

                        task_keyboard = [[InlineKeyboardButton("လုပ်ဆောင်ပြီး (Done)", callback_data=f"done:{t_id}")]
                        ]
                        
                        t_msg = await s_bot.send_message(
                            chat_id=chat_id,
                            text=task_text,
                            reply_markup=InlineKeyboardMarkup(task_keyboard),
                            #parse_mode="Markdown"
                        )
                        batch_message_ids.append(t_msg.message_id)

                    # 🟢 Database Log သိမ်းဆည်းခြင်း
                    await conn.execute(
                        """
                        INSERT INTO routine_batch_logs (routine_batch_id, student_id, message_ids, batch_id) 
                        VALUES ($1, $2, $3, $4);
                        """,
                        routine_batch_id, chat_id, batch_message_ids, target_batch
                    )
                    count += 1

                except Exception as e:
                    print(f"❌ Error sending routine to {chat_id}: {e}")
                
                await asyncio.sleep(0.05)

    return count   
    

async def send_weekly_report(app, manual_chat_id=None):
    target_recipients = [manual_chat_id] if manual_chat_id else ADMIN_IDS

    async with db_pool.acquire() as conn:
        # ၁။ ခြုံငုံသုံးသပ်ချက် (Summary Performance) ယူရန်
        perf_query = """
            SELECT s.student_name,
                   COUNT(log.id) FILTER (WHERE log.is_done = TRUE) as total_done,
                   COUNT(DISTINCT log.log_date) as active_days
            FROM students s
            LEFT JOIN student_daily_logs log 
                ON s.student_id = log.student_id 
                AND log.log_date >= CURRENT_DATE - INTERVAL '7 days'
            WHERE s.batch_id = $1
            GROUP BY s.student_id, s.student_name
            ORDER BY total_done DESC;
        """
        perf_rows = await conn.fetch(perf_query, BATCH_ID)

        # ၂။ အသေးစိတ် မှတ်တမ်းများ (Detailed Logs for the past 7 days) ယူရန်
        detail_query = """
            SELECT s.student_name,
                   log.log_date,
                   t.section,
                   t.title AS task_title,
                   CASE WHEN log.is_done THEN 'Done (ပြီးပြီ)' ELSE 'Pending (မပြီးသေး)' END as status,
                   COALESCE(log.reflection, '') as reflection
            FROM students s
            CROSS JOIN routine_templates t
            LEFT JOIN student_daily_logs log
                ON s.student_id = log.student_id
                AND t.id = log.template_id
                AND log.log_date >= CURRENT_DATE - INTERVAL '7 days'
            WHERE s.batch_id = $1 AND t.batch_id = $1 AND t.status = 'active'
            ORDER BY s.student_name ASC, log.log_date DESC, t.section ASC;
        """
        detail_rows = await conn.fetch(detail_query, BATCH_ID)

    if not perf_rows:
        for admin_id in target_recipients:
            await app.bot.send_message(chat_id=admin_id, text="📌 ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။")
        return

    # Pandas DataFrames များ တည်ဆောက်ခြင်း
    df_perf = pd.DataFrame([dict(r) for r in perf_rows])
    df_perf.columns = ["သင်တန်းသား အမည်", "ပြီးမြောက်သည့် Task အရေအတွက်", "ဝင်ရောက်လုပ်ကိုင်သည့် ရက်ပေါင်း"]

    if detail_rows:
        df_detail = pd.DataFrame([dict(r) for r in detail_rows])
        df_detail.columns = ["သင်တန်းသား အမည်", "ရက်စွဲ", "အပိုင်း", "လေ့ကျင့်ခန်း ခေါင်းစဉ်", "အခြေအနေ", "ဆင်ခြင်သုံးသပ်ချက်"]
    else:
        df_detail = pd.DataFrame(columns=["သင်တန်းသား အမည်", "ရက်စွဲ", "အပိုင်း", "လေ့ကျင့်ခန်း ခေါင်းစဉ်", "အခြေအနေ", "ဆင်ခြင်သုံးသပ်ချက်"])

    # Excel ဖိုင်ထဲသို့ Tab နှစ်ခု (Summary နဲ့ Detail) ဖြင့် ထည့်သွင်းခြင်း
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df_perf.to_excel(writer, sheet_name='Performance Summary', index=False)
        df_detail.to_excel(writer, sheet_name='Detailed Logs', index=False)
    excel_buffer.seek(0)
    excel_buffer.name = f"Weekly_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx"

    # Telegram တွင် Admin ထံသို့ စာသားအကျဉ်းချုပ်နှင့် Excel ဖိုင် ပို့ဆောင်ခြင်း
    summary_text = (
        f"📊 *တစ်ပတ်တာ အကျဉ်းချုပ် အစီရင်ခံစာ*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 စုစုပေါင်း သင်တန်းသားဦးရေ: *{len(perf_rows)}* ဦး\n"
        f"📅 ကာလ: လွန်ခဲ့သော ၇ ရက်အတွင်း\n\n"
        f"📁 အသေးစိတ် အချက်အလက်များ (Performance Summary နှင့် Detailed Logs များကို Excel တွင် Tab ခွဲ၍) ပူးတွဲတင်ပြအပ်ပါသည်။"
    )

    for admin_id in target_recipients:
        try:
            # ၁။ Text အကျဉ်းချုပ် ပို့မည်
            await app.bot.send_message(chat_id=admin_id, text=summary_text, parse_mode="Markdown")
            
            # ၂။ Excel ဖိုင် ပို့မည်
            excel_buffer.seek(0) # Buffer pointer ကို အစသို့ ပြန်ပို့ရန်
            await app.bot.send_document(
                chat_id=admin_id,
                document=excel_buffer,
                filename=excel_buffer.name,
                caption="📈 *တစ်ပတ်တာ အသေးစိတ် Excel အစီရင်ခံစာ*"
            )
        except Exception as e:
            print(f"Failed to send weekly report to {admin_id}: {e}")

async def send_weekly_report2(app, manual_chat_id=None):
    target_recipients = [manual_chat_id] if manual_chat_id else ADMIN_IDS

    perf_query = """
        SELECT s.student_name,
               COUNT(log.id) FILTER (WHERE log.is_done = TRUE) as total_done,
               COUNT(DISTINCT log.log_date) as active_days
        FROM students s
        LEFT JOIN student_daily_logs log 
            ON s.student_id = log.student_id 
            AND log.log_date >= CURRENT_DATE - INTERVAL '7 days'
        WHERE s.batch_id = $1
        GROUP BY s.student_id, s.student_name
        ORDER BY total_done DESC;
    """
    async with db_pool.acquire() as conn:
        perf_rows = await conn.fetch(perf_query, BATCH_ID)

    if not perf_rows:
        for admin_id in target_recipients:
            await app.bot.send_message(chat_id=admin_id, text="ပြီးခဲ့သည့် ၇ ရက်အတွင်း ဒေတာ မရှိသေးပါ။")
        return

    df_perf = pd.DataFrame([dict(r) for r in perf_rows])
    df_perf.columns = [BATCH_ID, "ပြီးမြောက်သည့် Task အရေအတွက်", "ဝင်ရောက်လုပ်ကိုင်သည့် ရက်ပေါင်း"]

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
        df_perf.to_excel(writer, sheet_name='Performance', index=False)
    excel_buffer.seek(0)

    for admin_id in target_recipients:
        try:
            filename = f"Weekly_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
            await app.bot.send_document(
                chat_id=admin_id,
                document=excel_buffer,
                filename=filename,
                caption="📁 *သင်တန်းသားများ၏ တစ်ပတ်တာ အသေးစိတ်စာရင်း Excel ဖိုင်*"
            )
        except Exception as e:
            print(f"Failed to send weekly report: {e}")

async def handle_teacher_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("send_routine:"):
        return

    _, section, student_id_str = data.split(":")
    student_id = int(student_id_str)

    section_titles = {
        "morning": "🌅 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) *(က) မနက်ပိုင်း လေ့ကျင့်မှု (5:00 AM - 12:00 PM)*",
        "afternoon": "☀️ သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) *(ခ) နေ့လယ်ပိုင်း လေ့ကျင့်မှု (12:00 PM - 5:00 PM)*",
        "evening": "🌙 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) *(ဂ) ညနေပိုင်း လေ့ကျင့်မှု (5:00 PM - 12:00 AM)*"
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
        # 🟢 1. သက်ဆိုင်ရာ batch_id ၏ active tasks များကိုသာ အရင်ယူခြင်း
        active_tasks = await conn.fetch(
            "SELECT id FROM routine_templates WHERE section = $1 AND status = 'active' AND batch_id = $2;",
            section_name, BATCH_ID
        )
        if not active_tasks:
            return

        total_active_count = len(active_tasks)

        # 🟢 2. SQL Query တွင် batch_id ကို တိကျစွာ ထည့်သွင်းခြင်း
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
        WHERE rt.section = $2 AND rt.status = 'active' AND rt.batch_id = $3 AND s.batch_id = $3
        GROUP BY s.student_id, s.student_name
        HAVING COUNT(sdl.id) < $4;
        """
        pending_students = await conn.fetch(query, today, section_name, BATCH_ID, total_active_count)

    if not pending_students:
        return

    for idx, stu in enumerate(pending_students):
        s_id = stu["student_id"]
        s_name = stu["student_name"] or "သင်တန်းသား"
        tasks = stu["pending_tasks"] or []
        
        # 🟢 လုံခြုံရေးအရ Python ဘက်တွင်လည်း Duplicate ဖြစ်နေသော Task များကို ဖြုတ်ထုတ်ရန် (Set ကိုသုံး၍ Unique ဖြစ်စေခြင်း)
        unique_tasks = list(dict.fromkeys(tasks))
        task_list_str = "\n".join([f"  ▫️ {t}" for t in unique_tasks])

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
        # 🟢 1. သက်ဆိုင်ရာ batch_id အတွက် active tasks များကိုသာ ယူခြင်း
        active_tasks = await conn.fetch(
            "SELECT id FROM routine_templates WHERE status = 'active' AND batch_id = $1;",
            BATCH_ID
        )
        if not active_tasks:
            return

        total_active_count = len(active_tasks)

        # 🟢 2. SQL Query တွင် batch_id ကို တိကျစွာ ကန့်သတ်ပေးခြင်း
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
        WHERE rt.status = 'active' AND rt.batch_id = $2 AND s.batch_id = $2
        GROUP BY s.student_id, s.student_name
        HAVING COUNT(sdl.id) < $3;
        """
        pending_students = await conn.fetch(query, today, BATCH_ID, total_active_count)

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
            f"  🌅 မနက်ပိုင်း အလုပ် *{m_count}* ခု\n"
            f"  ☀️ နေ့လယ်ပိုင်း အလုပ် *{a_count}* ခု\n"
            f"  🌙 ညနေပိုင်း အလုပ် *{e_count}* ခု\n\n"
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
            "`/remind_pending morning` (မနက်ခင်း အလုပ်)\n"
            "`/remind_pending afternoon` (နေ့လယ်ခင်း အလုပ်)\n"
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
        
        
async def broadcast_detailed_weekly_reports(application):
    print("🚀 Starting Weekly AI Detailed Reports Broadcast...")
    async with db_pool.acquire() as conn:
        students = await conn.fetch("SELECT student_id, student_name FROM students WHERE batch_id = $1;", BATCH_ID)

    for stu in students:
        s_id = stu["student_id"]
        try:
            await send_detailed_weekly_report(application, s_id)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"Failed to send detail report to {s_id}: {e}")

    print("✅ Weekly AI Detailed Reports Broadcast Completed.")       
    
async def post_init(application):
    global scheduler
    await init_db()
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Asia/Yangon"))
     # Routine Checklists
    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=5, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["morning", "🌅 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) မင်္ဂလာနံနက်ခင်းပါခင်ဗျာ၊ ယနေ့ နံနက်ပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
        id="morning_routine",
        name="Morning Routine Checklist"
    )

    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=12, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["afternoon", "☀️ သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) မင်္ဂလာနေ့လယ်ခင်းပါခင်ဗျာ၊ နေ့လယ်ပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
        id="afternoon_routine",
        name="Afternoon Routine Checklist"
    )

    scheduler.add_job(
        broadcast_routine_to_students,
        CronTrigger(hour=17, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=["evening", "🌙 သတိပညာ -နေ့စဉ်ကမ္ဘာ(Level-3) မင်္ဂလာညချမ်းပါခင်ဗျာ၊ ညပိုင်း လေ့ကျင့်မှုများ ဖြစ်ပါသည် -"],
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
    print(f"⏰ Routine & Report Schedulers started successfully ({BATCH_ID})...")


async def manual_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await send_weekly_report(context.application, manual_chat_id=update.effective_user.id)

async def set_next_week_bulk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """နောက်အပတ်အတွက် Routine များကို Batch အမည်သတ်မှတ်၍ အစုလိုက် ထည့်သွင်းခြင်း"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ ခွင့်ပြုချက်မရှိပါ။")
        return

    # ပထမစာကြောင်းမှ Batch အမည်ကို ဖမ်းယူရန် (ဥပမာ: /set_next_week batch_Youth)
    full_text = update.message.text
    first_line = full_text.split('\n')[0].strip()
    parts_first = first_line.split()
    
    # အကယ်၍ Batch အမည်ပါ မပါလာလျှင် default အနေဖြင့် BATCH_ID ကို သုံးမည်
    target_batch = parts_first[1] if len(parts_first) > 1 else BATCH_ID

    raw_text = full_text.partition('\n')[2].strip()
    if not raw_text:
        help_text = (
            f"📌 *Batch အလိုက် Routine အစုလိုက် ထည့်သွင်းပုံ:*\n\n"
            f"`/set_next_week batch_Youth` (သို့မဟုတ် `batch_TharThar`)\n"
            f"`morning | ခေါင်းစဉ် | ရှင်းလင်းချက်`\n"
            f"`evening | ခေါင်းစဉ် | ရှင်းလင်းချက်`"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
        return

    lines = raw_text.split('\n')
    valid_tasks = []
    
    for line in lines:
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
        await update.message.reply_text("⚠️ မှန်ကန်သော Task စာရင်းတစ်ခုမျှ မပါဝင်ပါ။ `section | title | description` ပုံစံ စစ်ဆေးပါ။")
        return

    # Database ထဲသို့ သက်ဆိုင်ရာ target_batch ဖြင့် ထည့်သွင်းခြင်း
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            # ထို Batch အတွက် ယခင် next စာရင်းအဟောင်းများကိုသာ ရှင်းထုတ်ခြင်း
            await conn.execute("DELETE FROM routine_templates WHERE status = 'next' AND batch_id = $1;", target_batch)

            order_counters = {"morning": 1, "afternoon": 1, "evening": 1}
            for section, title, desc in valid_tasks:
                order = order_counters[section]
                await conn.execute(
                    """
                    INSERT INTO routine_templates (section, task_order, title, description, status, is_active, batch_id)
                    VALUES ($1, $2, $3, $4, 'next', TRUE, $5);
                    """,
                    section, order, title, desc, target_batch
                )
                order_counters[section] += 1

    summary_msg = (
        f"✅ *{target_batch}* အတွက် Task စုစုပေါင်း ({len(valid_tasks)}) ခုကို Next စာရင်းသို့ ထည့်သွင်းပြီးပါပြီ!\n\n"
        f"🌅 မနက်ပိုင်း: {order_counters['morning'] - 1} ခု\n"
        f"☀️ နေ့လယ်ပိုင်း: {order_counters['afternoon'] - 1} ခု\n"
        f"🌙 ညနေပိုင်း: {order_counters['evening'] - 1} ခု"
    )
    await update.message.reply_text(summary_msg, parse_mode="Markdown")


async def set_next_week_bulk_command2(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            await conn.execute("DELETE FROM routine_templates WHERE status = 'next' AND batch_id = $1;", BATCH_ID)

            order_counters = {"morning": 1, "afternoon": 1, "evening": 1}
            for section, title, desc in valid_tasks:
                order = order_counters[section]
                await conn.execute(
                    """
                    INSERT INTO routine_templates (section, task_order, title, description, status, is_active, batch_id)
                    VALUES ($1, $2, $3, $4, 'next', TRUE, $5);
                    """,
                    #section, title, desc, BATCH_ID
                    section, order, title, desc, BATCH_ID
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
            f"🎉 *ယနေ့ ({today.strftime('%d/%m/%Y')}) အခြေအနေ:*\n\nသင်တန်းသားအားလုံး သတ်မှတ်ထားသော Routine များကို အပြည့်အဝ ပြီးမြောက်ထားကြပါသည်။",
            parse_mode="Markdown"
        )
        return

    msg = [f"📋 *ယနေ့ ({today.strftime('%d/%m/%Y')}) အလုပ်လုပ်ရန် ကျန်ရှိသူများ စာရင်း*\n━━━━━━━━━━━━━━━━━━━━\n"]

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
    threading.Thread(target=run_fastapi, daemon=True).start()
    print("🌐 FastAPI server started on background thread.")
    
    request_config = HTTPXRequest(connection_pool_size=8, read_timeout=30.0, write_timeout=30.0, connect_timeout=30.0)
    app = ApplicationBuilder().token(TEACHER_BOT_TOKEN).request(request_config).post_init(post_init).build()
    await post_init(app)
  # Handlers များ
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("detail_report", cmd_detail_report))
    #app.add_handler(CallbackQueryHandler(report_callback_handler, pattern="^rep_"))
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
    app.add_handler(CommandHandler("export_excel", cmd_export_excel))
    app.add_handler(CallbackQueryHandler(export_excel_callback_handler, pattern="^excl_"))
    app.add_handler(CommandHandler("remind_pending", cmd_remind_pending))
    app.add_handler(CommandHandler("set_next_week", set_next_week_bulk_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_telegram_direct_reply))
    app.add_handler(CommandHandler("pending", cmd_view_pending))
    app.add_handler(CallbackQueryHandler(handle_teacher_action))
   
   
    # pattern ကို ခဏဖြုတ်ပြီး Callback အားလုံးကို ဖမ်းကြည့်ပါ
    #app.add_handler(CallbackQueryHandler(report_callback_handler))
    app.add_handler(CommandHandler("ai_report", cmd_ai_report))
    #app.add_handler(CallbackQueryHandler(ai_report_callback_handler, pattern="^airep_"))
    app.add_handler(CallbackQueryHandler(handle_send_routine_callback, pattern=r"^send_routine:"))
    app.add_error_handler(error_handler)

    print(f"🤖 Teacher Bot ({BATCH_ID}) is up and running...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot Stopped.")
