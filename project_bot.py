#!/usr/bin/env python3
"""
Telegram Project Management Bot
Features: Create/list tasks, update status, deadlines, assign, reports, predecessor/successor
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Storage ──────────────────────────────────────────────────────────────────
DATA_FILE = "tasks.json"

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"tasks": {}, "next_id": 1}

def save_data(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

def next_id(data: dict) -> str:
    tid = f"T{data['next_id']:04d}"
    data["next_id"] += 1
    return tid

# ── Conversation states ───────────────────────────────────────────────────────
(
    WAIT_TITLE, WAIT_DESC, WAIT_ASSIGNEE, WAIT_DEADLINE,
    WAIT_PRED, WAIT_SUCC,
    WAIT_STATUS_ID, WAIT_NEW_STATUS,
    WAIT_ASSIGN_ID, WAIT_ASSIGN_NAME,
    WAIT_DEADLINE_ID, WAIT_DEADLINE_DATE,
    WAIT_PRED_TASK_ID, WAIT_PRED_VALUE,
    WAIT_SUCC_TASK_ID, WAIT_SUCC_VALUE,
) = range(16)

STATUSES = ["Todo", "In Progress", "Review", "Done", "Blocked"]

# ── Helpers ───────────────────────────────────────────────────────────────────
def task_summary(task: dict, short=False) -> str:
    status_emoji = {"Todo": "📋", "In Progress": "🔄", "Review": "🔍", "Done": "✅", "Blocked": "🚫"}.get(task["status"], "❓")
    deadline = task.get("deadline", "—")
    assignee = task.get("assignee", "Unassigned")
    pred = ", ".join(task.get("predecessors", [])) or "—"
    succ = ", ".join(task.get("successors", [])) or "—"

    if short:
        return f"{status_emoji} *{task['id']}* — {task['title']}\n   👤 {assignee}  📅 {deadline}"

    overdue = ""
    if deadline != "—":
        try:
            if date.fromisoformat(deadline) < date.today() and task["status"] != "Done":
                overdue = " ⚠️ OVERDUE"
        except Exception:
            pass

    return (
        f"{status_emoji} *{task['id']}: {task['title']}*\n"
        f"📝 {task.get('description', '—')}\n"
        f"👤 Assignee: {assignee}\n"
        f"📅 Deadline: {deadline}{overdue}\n"
        f"🔗 Predecessors: {pred}\n"
        f"🔗 Successors: {succ}\n"
        f"🕐 Created: {task.get('created', '—')}"
    )

def build_status_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(s, callback_data=f"setstatus_{s}")] for s in STATUSES
    ])

# ── /start ────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Project Management Bot*\n\n"
        "Commands:\n"
        "➕ /addtask — Create a new task\n"
        "📋 /list — List all tasks\n"
        "🔍 /task `<ID>` — View task details\n"
        "🔄 /setstatus — Update task status\n"
        "👤 /assign — Assign task to someone\n"
        "📅 /setdeadline — Set/update deadline\n"
        "🔗 /setpred — Set predecessor task(s)\n"
        "🔗 /setsucc — Set successor task(s)\n"
        "📊 /report — Project report\n"
        "❌ /cancel — Cancel current operation\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ── Add Task ──────────────────────────────────────────────────────────────────
async def addtask_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 *New Task*\nEnter the task *title*:", parse_mode="Markdown")
    return WAIT_TITLE

async def addtask_title(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_task"] = {"title": update.message.text.strip()}
    await update.message.reply_text("✏️ Enter a short *description* (or /skip):", parse_mode="Markdown")
    return WAIT_DESC

async def addtask_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_task"]["description"] = update.message.text.strip()
    await update.message.reply_text("👤 Who should be *assigned* to this task? (or /skip):", parse_mode="Markdown")
    return WAIT_ASSIGNEE

async def addtask_assignee(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_task"]["assignee"] = update.message.text.strip()
    await update.message.reply_text("📅 Enter *deadline* (YYYY-MM-DD) or /skip:", parse_mode="Markdown")
    return WAIT_DEADLINE

async def addtask_deadline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    try:
        date.fromisoformat(val)
        ctx.user_data["new_task"]["deadline"] = val
    except ValueError:
        await update.message.reply_text("⚠️ Invalid date format. Use YYYY-MM-DD. Try again or /skip:")
        return WAIT_DEADLINE
    await update.message.reply_text("🔗 Enter *predecessor* task IDs (e.g. T0001,T0002) or /skip:", parse_mode="Markdown")
    return WAIT_PRED

async def addtask_pred(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_task"]["predecessors"] = [x.strip().upper() for x in update.message.text.split(",") if x.strip()]
    await update.message.reply_text("🔗 Enter *successor* task IDs (e.g. T0003) or /skip:", parse_mode="Markdown")
    return WAIT_SUCC

async def addtask_succ(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_task"]["successors"] = [x.strip().upper() for x in update.message.text.split(",") if x.strip()]
    return await _save_new_task(update, ctx)

async def skip_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Generic skip — figures out which step we're on by checking what's missing."""
    task = ctx.user_data.get("new_task", {})
    if "description" not in task:
        task["description"] = ""
        await update.message.reply_text("👤 Who should be *assigned* to this task? (or /skip):", parse_mode="Markdown")
        return WAIT_ASSIGNEE
    if "assignee" not in task:
        task["assignee"] = "Unassigned"
        await update.message.reply_text("📅 Enter *deadline* (YYYY-MM-DD) or /skip:", parse_mode="Markdown")
        return WAIT_DEADLINE
    if "deadline" not in task:
        task["deadline"] = "—"
        await update.message.reply_text("🔗 Enter *predecessor* task IDs or /skip:", parse_mode="Markdown")
        return WAIT_PRED
    if "predecessors" not in task:
        task["predecessors"] = []
        await update.message.reply_text("🔗 Enter *successor* task IDs or /skip:", parse_mode="Markdown")
        return WAIT_SUCC
    if "successors" not in task:
        task["successors"] = []
        return await _save_new_task(update, ctx)
    return ConversationHandler.END

async def _save_new_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    task = ctx.user_data.pop("new_task", {})
    tid = next_id(data)
    data["tasks"][tid] = {
        "id": tid,
        "title": task.get("title", "Untitled"),
        "description": task.get("description", ""),
        "assignee": task.get("assignee", "Unassigned"),
        "deadline": task.get("deadline", "—"),
        "status": "Todo",
        "predecessors": task.get("predecessors", []),
        "successors": task.get("successors", []),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_data(data)
    await update.message.reply_text(f"✅ Task *{tid}* created!\n\n{task_summary(data['tasks'][tid])}", parse_mode="Markdown")
    return ConversationHandler.END

# ── List Tasks ────────────────────────────────────────────────────────────────
async def list_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    if not data["tasks"]:
        await update.message.reply_text("📭 No tasks yet. Use /addtask to create one.")
        return
    lines = ["📋 *All Tasks:*\n"]
    for task in data["tasks"].values():
        lines.append(task_summary(task, short=True))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── View Task ─────────────────────────────────────────────────────────────────
async def view_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args:
        await update.message.reply_text("Usage: /task T0001")
        return
    tid = args[0].upper()
    data = load_data()
    task = data["tasks"].get(tid)
    if not task:
        await update.message.reply_text(f"❌ Task {tid} not found.")
        return
    await update.message.reply_text(task_summary(task), parse_mode="Markdown")

# ── Set Status ────────────────────────────────────────────────────────────────
async def setstatus_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Enter the *Task ID* to update status (e.g. T0001):", parse_mode="Markdown")
    return WAIT_STATUS_ID

async def setstatus_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.message.text.strip().upper()
    data = load_data()
    if tid not in data["tasks"]:
        await update.message.reply_text(f"❌ Task {tid} not found. Try again:")
        return WAIT_STATUS_ID
    ctx.user_data["status_task_id"] = tid
    await update.message.reply_text(
        f"Current status: *{data['tasks'][tid]['status']}*\nChoose new status:",
        reply_markup=build_status_keyboard(), parse_mode="Markdown"
    )
    return WAIT_NEW_STATUS

async def setstatus_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    new_status = query.data.replace("setstatus_", "")
    tid = ctx.user_data.pop("status_task_id", None)
    if not tid:
        await query.edit_message_text("⚠️ Session expired. Use /setstatus again.")
        return ConversationHandler.END
    data = load_data()
    data["tasks"][tid]["status"] = new_status
    save_data(data)
    await query.edit_message_text(f"✅ Task *{tid}* status → *{new_status}*", parse_mode="Markdown")
    return ConversationHandler.END

# ── Assign ────────────────────────────────────────────────────────────────────
async def assign_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👤 Enter the *Task ID* to assign:", parse_mode="Markdown")
    return WAIT_ASSIGN_ID

async def assign_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.message.text.strip().upper()
    data = load_data()
    if tid not in data["tasks"]:
        await update.message.reply_text(f"❌ Task {tid} not found. Try again:")
        return WAIT_ASSIGN_ID
    ctx.user_data["assign_task_id"] = tid
    await update.message.reply_text(f"Enter *assignee name* for task {tid}:", parse_mode="Markdown")
    return WAIT_ASSIGN_NAME

async def assign_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    tid = ctx.user_data.pop("assign_task_id")
    data = load_data()
    data["tasks"][tid]["assignee"] = name
    save_data(data)
    await update.message.reply_text(f"✅ Task *{tid}* assigned to *{name}*", parse_mode="Markdown")
    return ConversationHandler.END

# ── Set Deadline ──────────────────────────────────────────────────────────────
async def setdeadline_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📅 Enter the *Task ID* to set deadline:", parse_mode="Markdown")
    return WAIT_DEADLINE_ID

async def setdeadline_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.message.text.strip().upper()
    data = load_data()
    if tid not in data["tasks"]:
        await update.message.reply_text(f"❌ Task {tid} not found. Try again:")
        return WAIT_DEADLINE_ID
    ctx.user_data["deadline_task_id"] = tid
    await update.message.reply_text(f"Enter *deadline* for task {tid} (YYYY-MM-DD):", parse_mode="Markdown")
    return WAIT_DEADLINE_DATE

async def setdeadline_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    try:
        date.fromisoformat(val)
    except ValueError:
        await update.message.reply_text("⚠️ Invalid format. Use YYYY-MM-DD:")
        return WAIT_DEADLINE_DATE
    tid = ctx.user_data.pop("deadline_task_id")
    data = load_data()
    data["tasks"][tid]["deadline"] = val
    save_data(data)
    await update.message.reply_text(f"✅ Deadline for *{tid}* set to *{val}*", parse_mode="Markdown")
    return ConversationHandler.END

# ── Set Predecessor ───────────────────────────────────────────────────────────
async def setpred_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Enter the *Task ID* to set predecessors for:", parse_mode="Markdown")
    return WAIT_PRED_TASK_ID

async def setpred_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.message.text.strip().upper()
    data = load_data()
    if tid not in data["tasks"]:
        await update.message.reply_text(f"❌ Task {tid} not found. Try again:")
        return WAIT_PRED_TASK_ID
    ctx.user_data["pred_task_id"] = tid
    current = ", ".join(data["tasks"][tid].get("predecessors", [])) or "none"
    await update.message.reply_text(
        f"Current predecessors: *{current}*\nEnter new predecessor IDs (comma-separated, e.g. T0001,T0002):",
        parse_mode="Markdown"
    )
    return WAIT_PRED_VALUE

async def setpred_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    preds = [x.strip().upper() for x in update.message.text.split(",") if x.strip()]
    tid = ctx.user_data.pop("pred_task_id")
    data = load_data()
    data["tasks"][tid]["predecessors"] = preds
    save_data(data)
    await update.message.reply_text(f"✅ Predecessors for *{tid}*: {', '.join(preds) or 'cleared'}", parse_mode="Markdown")
    return ConversationHandler.END

# ── Set Successor ─────────────────────────────────────────────────────────────
async def setsucc_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔗 Enter the *Task ID* to set successors for:", parse_mode="Markdown")
    return WAIT_SUCC_TASK_ID

async def setsucc_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tid = update.message.text.strip().upper()
    data = load_data()
    if tid not in data["tasks"]:
        await update.message.reply_text(f"❌ Task {tid} not found. Try again:")
        return WAIT_SUCC_TASK_ID
    ctx.user_data["succ_task_id"] = tid
    current = ", ".join(data["tasks"][tid].get("successors", [])) or "none"
    await update.message.reply_text(
        f"Current successors: *{current}*\nEnter new successor IDs (comma-separated):",
        parse_mode="Markdown"
    )
    return WAIT_SUCC_VALUE

async def setsucc_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    succs = [x.strip().upper() for x in update.message.text.split(",") if x.strip()]
    tid = ctx.user_data.pop("succ_task_id")
    data = load_data()
    data["tasks"][tid]["successors"] = succs
    save_data(data)
    await update.message.reply_text(f"✅ Successors for *{tid}*: {', '.join(succs) or 'cleared'}", parse_mode="Markdown")
    return ConversationHandler.END

# ── Report ────────────────────────────────────────────────────────────────────
async def report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    tasks = list(data["tasks"].values())
    if not tasks:
        await update.message.reply_text("📭 No tasks found.")
        return

    total = len(tasks)
    by_status = {s: 0 for s in STATUSES}
    overdue = []
    unassigned = []
    today = date.today()

    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        if t.get("assignee", "Unassigned") in ("Unassigned", "", None):
            unassigned.append(t["id"])
        dl = t.get("deadline", "—")
        if dl and dl != "—":
            try:
                if date.fromisoformat(dl) < today and t["status"] != "Done":
                    overdue.append(t["id"])
            except Exception:
                pass

    done_pct = round(by_status.get("Done", 0) / total * 100) if total else 0

    lines = [
        "📊 *Project Report*",
        f"──────────────────",
        f"📦 Total Tasks: {total}",
        f"✅ Done: {by_status.get('Done', 0)} ({done_pct}%)",
        f"🔄 In Progress: {by_status.get('In Progress', 0)}",
        f"📋 Todo: {by_status.get('Todo', 0)}",
        f"🔍 In Review: {by_status.get('Review', 0)}",
        f"🚫 Blocked: {by_status.get('Blocked', 0)}",
        f"──────────────────",
        f"⚠️ Overdue: {', '.join(overdue) if overdue else 'None'}",
        f"👤 Unassigned: {', '.join(unassigned) if unassigned else 'None'}",
        f"──────────────────",
        f"📅 Report date: {today}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── Cancel ────────────────────────────────────────────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("Set the TELEGRAM_BOT_TOKEN environment variable!")

    app = Application.builder().token(BOT_TOKEN).build()

    # Add task conversation
    addtask_conv = ConversationHandler(
        entry_points=[CommandHandler("addtask", addtask_start)],
        states={
            WAIT_TITLE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_title)],
            WAIT_DESC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_desc),
                            CommandHandler("skip", skip_handler)],
            WAIT_ASSIGNEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_assignee),
                            CommandHandler("skip", skip_handler)],
            WAIT_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_deadline),
                            CommandHandler("skip", skip_handler)],
            WAIT_PRED:     [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_pred),
                            CommandHandler("skip", skip_handler)],
            WAIT_SUCC:     [MessageHandler(filters.TEXT & ~filters.COMMAND, addtask_succ),
                            CommandHandler("skip", skip_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    setstatus_conv = ConversationHandler(
        entry_points=[CommandHandler("setstatus", setstatus_start)],
        states={
            WAIT_STATUS_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, setstatus_id)],
            WAIT_NEW_STATUS: [CallbackQueryHandler(setstatus_callback, pattern="^setstatus_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    assign_conv = ConversationHandler(
        entry_points=[CommandHandler("assign", assign_start)],
        states={
            WAIT_ASSIGN_ID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, assign_id)],
            WAIT_ASSIGN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, assign_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    deadline_conv = ConversationHandler(
        entry_points=[CommandHandler("setdeadline", setdeadline_start)],
        states={
            WAIT_DEADLINE_ID:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setdeadline_id)],
            WAIT_DEADLINE_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setdeadline_date)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    pred_conv = ConversationHandler(
        entry_points=[CommandHandler("setpred", setpred_start)],
        states={
            WAIT_PRED_TASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, setpred_id)],
            WAIT_PRED_VALUE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setpred_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    succ_conv = ConversationHandler(
        entry_points=[CommandHandler("setsucc", setsucc_start)],
        states={
            WAIT_SUCC_TASK_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, setsucc_id)],
            WAIT_SUCC_VALUE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setsucc_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("task", view_task))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(addtask_conv)
    app.add_handler(setstatus_conv)
    app.add_handler(assign_conv)
    app.add_handler(deadline_conv)
    app.add_handler(pred_conv)
    app.add_handler(succ_conv)

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
