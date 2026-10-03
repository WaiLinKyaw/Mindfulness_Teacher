import os
import asyncio
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
scheduler = None

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
    """တပည့်များ၏ တစ်ပတ်တာ ဒေတာနှင့် မေးခွန်းများကို Gemini AI ဖြင့် သုံးသပ်ချက် ရေးသားခြင်း"""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "⚠️ Gemini API Key မရှိသဖြင့် AI သုံးသပ်ချက် မထုတ်နိုင်ပါ။"

    prompt = f"""
သင်သည် သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်ရေး (Mindfulness Routine) သင်တန်းမှ ဆရာကြီးအတွက် အကူလက်ထောက် AI ဖြစ်ပါသည်။
အောက်ပါတို့သည် ပြီးခဲ့သည့် (၇) ရက်အတွင်း တပည့်များ၏ လေ့ကျင့်မှု ပြီးစီးမှု မှတ်တမ်းများနှင့် ၎င်းတို့ မေးမြန်း/ဆွေးနွေးထားသော အတွေ့အကြုံများ ဖြစ်ပါသည်:

{student_records_text}

ဆရာကြီး အလွယ်တကူ သုံးသပ်နိုင်ရန်အတွက် အောက်ပါအတိုင်း မြန်မာလို အနှစ်ချုပ် ရေးသားပေးပါ:
၁။ တပည့်တစ်ဦးချင်းစီ၏ လေ့ကျင့်မှု အားသာချက်/အားနည်းချက်၊ တရားထိုင်ခြင်း၊ ထိုင်ဖို့ပျက်ကွက်ခြင်း (ဥပမာ- မနက်ပိုင်း ပုံမှန်လုပ်နိုင်သော်လည်း ညနေပိုင်း အားနည်းခြင်း စသည်)
၂။ တပည့်များ၏ စိတ်ပိုင်းဆိုင်ရာ အတွေ့အကြုံ သို့မဟုတ် အခက်အခဲများအပေါ် ဆရာကြီး အဓိက သတိပြု လမ်းညွှန်ပေးသင့်သည့် အချက် (Actionable Insight)
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

    return "⚠️ AI သုံးသပ်ချက် ရယူရာတွင် Network ချိတ်ဆက်မှု ခေတ္တ အခက်အခဲရှိနေပါသဖြင့် နောက်တစ်ကြိမ် ပြန်လည်ကြိုးစားပေးပါခင်ဗျာ。"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ ဤ Bot သည် ဆရာနှင့် Admin သီးသန့် Bot ဖြစ်ပါသည်။")
        return

    text = (
        f"မင်္ဂလာပါ {update.effective_user.first_name} 🙏 (Admin/Teacher)\n\n"
        "တပည့်များ၏ တရားလေ့ကျင့်မှု စောင့်ကြည့်စစ်ဆေးနိုင်သော Command များ:\n\n"
        "• `/today` - ယနေ့ တပည့်များ၏ Routine ပြီးစီးမှု ရာခိုင်နှုန်း စစ်ဆေးရန်\n"
        "• `/questions` - တပည့်များ မေးထားသော မေးခွန်း/အတွေ့အကြုံများ ဖတ်ရှုရန်\n"
        "• `/reply  ` - တပည့်ထံ တိုက်ရိုက် စာပြန်ရန်\n"
        "• `/weekly_report` - တစ်ပတ်တာ အစီရင်ခံစာနှင့် AI သုံးသပ်ချက် ထုတ်ယူရန်\n"
        "• `/detail_report ` - ကျောင်းသားတစ်ဦးချင်း အသေးစိတ် ဒေတာကြည့်ရန်\n"
        "• `/ai_report ` - ကျောင်းသားတစ်ဦးချင်း AI သုံးသပ်ချက် ထုတ်ရန်\n"
        "• `/export_excel ` - ကျောင်းသားတစ်ဦးချင်း ၏ အသေးစိတ် Excel Report ထုတ်ယူရန်\n"
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
        await update.message.reply_text("ယနေ့အတွက် တပည့်များဘက်မှ မှတ်တမ်း မရှိသေးပါခင်ဗျာ။")
        return

    lines = [f"📊 *ယနေ့ ({today.strftime('%d/%m/%Y')}) တပည့်များ လေ့ကျင့်မှု အခြေအနေ -*\n"]
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

    lines.append("အကြောင်းပြန်ရန်: `/reply  `")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def reply_to_student(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sender_name = update.effective_user.first_name
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ ခွင့်ပြုချက်မရှိပါ။")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ အသုံးပြုပုံ: `/reply  `\nဥပမာ: `/reply 8999991129 great! keep up`")
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

        await update.message.reply_text(f"✅ တပည့် ID `{student_id}` ထံသို့ စာအောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။", parse_mode="Markdown")

        sync_text = f"ℹ️ *{sender_name}* က တပည့် (ID: `{student_id}`) ထံ စာပြန်လိုက်ပါသည်:\n\"{reply_message}\""
        for aid in ADMIN_IDS:
            if aid != user_id:
                try:
                    await context.bot.send_message(chat_id=aid, text=sync_text, parse_mode="Markdown")
                except Exception:
                    pass

    except Exception as e:
        await update.message.reply_text(f"❌ ပို့ဆောင်၍ မရပါ Error: `{e}`", parse_mode="Markdown")

async def handle_telegram_direct_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    sender_name = update.effective_user.first_name

    if not is_admin(user_id):
        return

    replied_msg = update.message.reply_to_message
    if not replied_msg or not replied_msg.text:
        return

    match = re.search(r"\(ID:\s*`?(\d+)`?\)", replied_msg.text)
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

        await update.message.reply_text(f"✅ တပည့် ID `{student_id}` ထံသို့ စာအောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။", parse_mode="Markdown")

        sync_text = f"ℹ️ *{sender_name}* က တပည့် (ID: `{student_id}`) ထံ Reply ပြန်လိုက်ပါသည်:\n\"{reply_text}\""
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import pandas as pd
import io

async def cmd_export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ကျောင်းသားတစ်ဦးချင်းစီ၏ အသေးစိတ် မှတ်တမ်းကို နာမည်ဖြင့် ရှာဖွေပြီး Excel ဖိုင်ထုတ်ပေးခြင်း"""
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
        await update.message.reply_text(f"❌ '{query_text}' အမည်ဖြင့် ကိုက်ညီသော ကျောင်းသားကို မတွေ့ရှိပါ။")
        return

    if len(students) == 1:
        # ကျောင်းသား ၁ ယောက်တည်း တွေ့လျှင် တန်းပြီး Excel ထုတ်မည်
        student_id = students[0]["student_id"]
        await generate_and_send_excel(update.message, student_id)
    else:
        # တွေ့ရှိသူ ၁ ယောက်ထက် ပိုပါက ရွေးချယ်စရာ ခလုတ်များ (Inline Buttons) ထုတ်ပေးမည်
        keyboard = []
        for s in students:
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"excl_{s['student_id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 '{query_text}' နှင့် ကိုက်ညီသော ကျောင်းသား ({len(students)} ယောက်) တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ လိုချင်သူကို ရွေးချယ်ပါ -",
            reply_markup=reply_markup
        )

async def export_excel_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ ကျောင်းသားကို ရွေးချယ်လိုက်သည့်အခါ Excel ထုတ်ပေးမည့် Handler"""
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
    """ကျောင်းသား ID ဖြင့် ဒေတာဆွဲထုတ်ပြီး Excel ဖိုင်ဆောက်ကာ ပို့ပေးသည့် Helper Function"""
    status = await message_obj.reply_text("⏳ Excel ဖိုင် ထုတ်ယူနေပါသည် ခေတ္တစောင့်ဆိုင်းပေးပါ...")
    try:
        today = datetime.now(MM_TZ).date()
        start_date = today - timedelta(days=7)

        async with db_pool.acquire() as conn:
            student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
            if not student:
                await status.edit_text("❌ ကျောင်းသား အချက်အလက် ရှာမတွေ့ပါ။")
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
    msg += f"👤 ကျောင်းသား: *{student_name}*\n"
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


import os
import asyncio
from google import genai
from google.genai.errors import APIError

async def generate_personalized_ai_report(student_name: str, task_summary: list) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    breakdown_text = ""
    for r in task_summary:
        breakdown_text += f"- {r['task_title']} ({r['section']}): {r['total_days']} ရက်မှာ {r['completed_count']} ကြိမ် ပြီးမြောက်ခဲ့သည်။\n"

    prompt = f"""
သင်သည် မေတ္တာနှင့် ဂရုစိုက်မှုအပြည့်ရှိသော ဗုဒ္ဓဘာသာ/Mindfulness လေ့ကျင့်ရေး သင်တန်းဆရာတစ်ဦး ဖြစ်သည်။
အောက်ပါ အချက်အလက်များသည် ကျောင်းသား "{student_name}" ၏ ပြီးခဲ့သော တစ်ပတ်တာ လေ့ကျင့်မှု အသေးစိတ် မှတ်တမ်းဖြစ်သည်:

{breakdown_text}

ကျောင်းသားအတွက် နွေးထွေးအားတက်ဖွယ် အပတ်စဉ် သုံးသပ်ချက် အစီရင်ခံစာ (Weekly Detail Report) ကို မြန်မာဘာသာဖြင့် ရေးသားပေးပါ:
၁။ ကောင်းမွန်စွာ ပြုလုပ်ထားသော အလုပ်များကို အသိအမှတ်ပြု ချီးကျူးပေးပါ။
၂။ အထူးသတိပြုရန်: အကယ်၍ ကျောင်းသားသည် "တရားထိုင်ခြင်း" (Meditation) သို့မဟုတ် သတိပဋ္ဌာန်နှင့် သက်ဆိုင်သော အလေ့အကျင့်များကို အကြိမ်ရေ နည်းပါးနေခြင်း သို့မဟုတ် လုံးဝ မလုပ်ဘဲ ကျော်သွားခြင်းရှိပါက -
   - အပြစ်မတင်ဘဲ နူးညံ့စွာ နားချပေးပါ။
   - နေ့စဉ် ၅ မိနစ် သို့မဟုတ် ၁၀ မိနစ်ခန့် စတင် တရားထိုင်ခြင်းသည် စိတ်ဖိစီးမှု လျော့ကျစေခြင်း၊ စိတ်တည်ငြိမ်ခြင်း၊ နေ့စဉ်ဘဝတွင် သတိကပ်နိုင်ခြင်း စသည့် အကျိုးကျေးဇူးများကို နားလည်လွယ်အောင် ရှင်းပြပြီး လာမည့်အပတ်တွင် စမ်းသပ်လုပ်ဆောင်ကြည့်ရန် တွန်းအားပေးပါ။
၃။ ဖတ်ရလွယ်ကူအောင် စာပိုဒ်တိုများ၊ Bullet ပုံစံများနှင့် သင့်တော်သော Emoji များကို သုံးပြီး လိုရင်းရောက်အောင် စာကြောင်းတိုတိုနဲ့ရှင်းရှင်းလေးရေးပေးပါ။
"""

    # 🟢 Google Gemini API ရဲ့ တရားဝင် Model နာမည်များဖြင့် စီစဉ်ထားခြင်း
    candidate_models = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

    for model_name in candidate_models:
        for attempt in range(3):  # တစ်ခုချင်းစီကို ၃ ကြိမ်အထိ ထပ်မံကြိုးစားမည် (Retry)
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    return response.text
            except APIError as e:
                # 503 (Service Unavailable) သို့မဟုတ် 429 (Rate Limit / Too Many Requests) ဖြစ်မှသာ ဆက်တိုက် Retry မည်
                if e.code in (503, 429):
                    wait_sec = (attempt + 1) * 3
                    print(f"⚠️ Model {model_name} busy (Code {e.code}). Retrying in {wait_sec}s...")
                    await asyncio.sleep(wait_sec)
                else:
                    # အခြား Error များ (ဥပမာ- API Key မှားခြင်း၊ Invalid Argument စသည်) တက်လျှင် ချက်ချင်း Exception ပစ်မည်
                    raise e
            except Exception as e:
                print(f"Unexpected error with {model_name}: {e}")
                break  # တခြား မမျှော်လင့်ထားသော Error တက်လျှင် ဤ Model ကို ကျော်ပြီး နောက် Model တစ်ခုသို့ ပြောင်းမည်

    # 🔴 Model အားလုံး သုံးမရတော့မှသာ (All Fallbacks Failed) ဤ Fallback စာသားကို ပြန်ပေးမည်
    return f"🙏 မင်္ဂလာပါ {student_name}ခင်ဗျာ၊ ယခုတစ်ပတ်အတွက် အသေးစိတ် အစီရင်ခံစာ ထုတ်ယူရာတွင် အေအိုင် (AI) ဝန်ဆောင်မှု ခေတ္တအလုပ်ရှုပ်နေပါသဖြင့် မကြာမီ ပြန်လည်ပို့ဆောင်ပေးပါမည်ခင်ဗျာ။"
    

async def send_detailed_weekly_report(application, student_id: int):
    today = datetime.now(MM_TZ).date()
    start_date = today - timedelta(days=7)

    async with db_pool.acquire() as conn:
        student = await conn.fetchrow(
            "SELECT student_name FROM students WHERE student_id = $1;", 
            student_id
        )
        student_name = student["student_name"] if (student and student["student_name"]) else "တပည့်"
        task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)

    ai_feedback = await generate_personalized_ai_report(student_name, task_summary)
    await application.bot.send_message(chat_id=student_id, text=ai_feedback)

async def send_weekly_report(app, manual_chat_id=None):
    """ကျောင်းသား များ၏ စာရင်းကို Excel ဖြင့် ထုတ်ပေးပြီး AI Summary နှင့်အတူ Admin ထံ ပို့ခြင်း"""
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
    df_perf.columns = ["ကျောင်းသားနာမည်", "ပြီးမြောက်သည့် Task အရေအတွက်", "ဝင်ရောက်လုပ်ကိုင်သည့် ရက်ပေါင်း"]

    df_qa = pd.DataFrame([dict(r) for r in qa_rows]) if qa_rows else pd.DataFrame(columns=["student_name", "reflection_text", "log_date"])
    if not df_qa.empty:
        df_qa.columns = ["ကျောင်းသားနာမည်", "Reflection မှတ်တမ်း", "ရက်စွဲ"]

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
    final_ai_msg = f"📊 *[အပတ်စဉ် တပည့်များ၏ ခြုံငုံသုံးသပ်ချက် (AI Summary)]*\n\n{ai_summary}"

    # ၄။ Admin ဆီသို့ AI Text နှင့် Excel ဖိုင်ကို တွဲ၍ ပို့ဆောင်ခြင်း
    for admin_id in target_recipients:
        try:
            # ပထမဦးစွာ AI ၏ သုံးသပ်ချက် စာသားကို ပို့မည်
            await send_safe_message(app.bot, admin_id, final_ai_msg)
            
            # ထို့နောက် ကျောင်းသား ၂၀၀ စာရင်းပါ Excel ဖိုင်ကို ပို့မည်
            filename = f"Weekly_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
            await app.bot.send_document(
                chat_id=admin_id,
                document=excel_buffer,
                filename=filename,
                caption="📁 *ကျောင်းသား ၂၀၀ လုံး၏ တစ်ပတ်တာ အသေးစိတ်စာရင်း Excel ဖိုင်* ဖြင့် ပူးတွဲတင်ပြအပ်ပါသည်။"
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Failed to send weekly report to {admin_id}: {e}")

async def cmd_detail_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ကျောင်းသားနာမည်ဖြင့် ရှာဖွေ၍ အသေးစိတ် အစီရင်ခံစာကို ထုတ်ပေးခြင်း"""
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
        # ကျောင်းသားနာမည်ကို တစိတ်တပိုင်း (ILIKE) ဖြင့် ရှာဖွေခြင်း
        students = await conn.fetch(
            "SELECT student_id, student_name FROM students WHERE student_name ILIKE $1;", 
            f"%{search_name}%"
        )

        if not students:
            await update.message.reply_text(f"❌ '{search_name}' အမည်ဖြင့် ကျောင်းသားကို ရှာမတွေ့ပါ။")
            return

        # အကယ်၍ ရှာတွေ့တဲ့ကျောင်းသား 1 ယောက်တည်းဆိုရင် Report ကို တန်းထုတ်ပေးမည်
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
            f"🔍 '{search_name}' နှင့် ကိုက်ညီသော ကျောင်းသား ({len(students)}) ဦး တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ ရွေးချယ်ပါ:",
            reply_markup=reply_markup
        )

async def generate_and_send_report(update_or_query, context, student_id, student_name, start_date, today):
    """ကျောင်းသားတစ်ဦးချင်းစီ၏ (၇) ရက်တာ အချက်အလက်များကို စုစည်းပြီး ပို့ပေးသည့် Helper Function"""
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
    """Inline Button မှ ကျောင်းသားကို ရွေးချယ်လိုက်သည့်အခါ လုပ်ဆောင်မည့် Handler"""
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


async def cmd_ai_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """နာမည်ဖြင့် ရှာဖွေပြီး ကျောင်းသားတစ်ဦးချင်းစီ၏ AI Report ထုတ်ပေးသည့် Command"""
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
        await update.message.reply_text(f"❌ '{query_text}' အမည်ဖြင့် ကိုက်ညီသော ကျောင်းသားကို မတွေ့ရှိပါ။")
        return
        
    if len(students) == 1:
        # ကျောင်းသား ၁ ယောက်တည်း တွေ့လျှင် တန်းပြီး Report ထုတ်မည်
        student_id = students[0]["student_id"]
        await process_and_send_ai_report(update.message, student_id)
    else:
        # တွေ့ရှိသူ ၁ ယောက်ထက် ပိုပါက ရွေးချယ်စရာ ခလုတ်များ (Inline Buttons) ထုတ်ပေးမည်
        keyboard = []
        for s in students:
            keyboard.append([InlineKeyboardButton(s["student_name"], callback_data=f"airep_{s['student_id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🔍 '{query_text}' နှင့် ကိုက်ညီသော ကျောင်းသား ({len(students)} ယောက်) တွေ့ရှိပါသည်။ ကျေးဇူးပြု၍ လိုချင်သူကို ရွေးချယ်ပါ -",
            reply_markup=reply_markup
        )

async def ai_report_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline Button မှ ကျောင်းသားတစ်ဦးကို ရွေးချယ်လိုက်သည့်အခါ အလုပ်လုပ်မည့် Handler"""
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
    """ကျောင်းသား ID ဖြင့် ဒေတာဆွဲထုတ်ပြီး AI Report တည်ဆောက်ကာ ပို့ပေးသည့် Helper Function"""
    status = await message_obj.reply_text("⏳ Gemini AI ဖြင့် သုံးသပ်ချက် ထုတ်ယူနေပါသည် ခေတ္တစောင့်ဆိုင်းပေးပါ...")
    try:
        today = datetime.now(MM_TZ).date()
        start_date = today - timedelta(days=7)
        
        async with db_pool.acquire() as conn:
            student = await conn.fetchrow("SELECT student_name FROM students WHERE student_id = $1;", student_id)
            student_name = student["student_name"] if (student and student["student_name"]) else "တပည့်"
            task_summary = await get_student_task_breakdown(conn, student_id, start_date, today)
            
        plain_stats = format_plain_detail_report(student_name, task_summary, start_date, today)
        ai_feedback = await generate_personalized_ai_report(student_name, task_summary)
        
        full_message = f"{plain_stats}\n\n🤖 *ဆရာ့ထံမှ အကြံပြု သုံးသပ်ချက်*\n━━━━━━━━━━━━━━━━━━━━\n{ai_feedback}"
        
        await message_obj.reply_text(full_message, parse_mode=None)
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Error ဖြစ်ပေါ်နေပါသည်: {e}")

async def main():
    # FastAPI Server ကို Background Thread ဖြင့် စတင်ခြင်း
    threading.Thread(target=run_fastapi, daemon=True).start()
    print("🌐 FastAPI server started on background thread.")

    await init_db()
    # Timeout များကို တိုးမြှင့်ထားသော Request Config
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )

    # application တည်ဆောက်ရာတွင် request ကို ထည့်သုံးပါ
    application = ApplicationBuilder().token(TEACHER_BOT_TOKEN).request(request).build()
    #application = ApplicationBuilder().token(TEACHER_BOT_TOKEN).build()

    # Handlers များကို ထည့်သွင်းခြင်း
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("today", today_status))
    application.add_handler(CommandHandler("questions", view_questions))
    application.add_handler(CommandHandler("reply", reply_to_student))
    application.add_handler(CommandHandler("weekly_report", lambda u, c: send_weekly_report(c.application, manual_chat_id=u.effective_chat.id)))
    application.add_handler(CommandHandler("detail_report", cmd_detail_report))
    application.add_handler(CommandHandler("ai_report", cmd_ai_report))
    application.add_handler(CommandHandler("export_excel", cmd_export_excel))
    application.add_handler(CallbackQueryHandler(export_excel_callback_handler, pattern="^excl_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_telegram_direct_reply))
    application.add_handler(CallbackQueryHandler(report_callback_handler, pattern="^rep_"))
    application.add_handler(CallbackQueryHandler(ai_report_callback_handler, pattern="^airep_"))

    print("🤖 Teacher Bot is up and running...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Bot ရပ်မသွားစေရန် စောင့်ဆိုင်းခြင်း
    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Bot Stopped.")