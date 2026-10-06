"""
SKxListBot — Catalog list + channel votes (no MongoDB)
- Admin adds items: type, poster URL, title, screenshot URLs
- Private preview + post to channel
- Inline ❤️ votes (in-memory, for a few hours while bot is up)
- Render free web service ready
"""

from __future__ import annotations

import os
import re
import json
import logging
import asyncio
from threading import Thread
from typing import Dict, List, Optional, Set

from flask import Flask
from telegram import (
    Update,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

# ================== CONFIG ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MAIN_OWNER_ID = int(os.environ.get("OWNER_ID", "8723278238"))
PORT = int(os.environ.get("PORT", 8080))
MAX_ITEMS = 10
MAX_SS_PER_ITEM = 15

CHANNEL_FILE = "channel.json"
ADMINS_FILE = "admins.json"

(
    WAIT_TYPE,
    WAIT_POSTER,
    WAIT_TITLE,
    WAIT_SS,
    WAIT_NEXT,
    WAIT_CONFIRM,
) = range(6)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory votes: post_key -> { "votes": {item_idx: count}, "voters": {item_idx: set(user_id)} }
# post_key = f"{chat_id}:{message_id}"
VOTE_STORE: Dict[str, dict] = {}
# post_key -> list of item dicts (for button labels / rebuild)
POST_ITEMS: Dict[str, List[dict]] = {}

URL_RE = re.compile(r"^https?://\S+", re.I)


# ================== ADMIN / CHANNEL ==================
def load_admins() -> set:
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f).get("admins", []))
        except Exception:
            pass
    return set()


def save_admins(admins: set) -> None:
    with open(ADMINS_FILE, "w", encoding="utf-8") as f:
        json.dump({"admins": list(admins)}, f, indent=2)


ADMINS = load_admins()
ADMINS.add(MAIN_OWNER_ID)


def is_admin(uid: int) -> bool:
    return uid in ADMINS or uid == MAIN_OWNER_ID


def load_channel():
    if os.path.exists(CHANNEL_FILE):
        try:
            with open(CHANNEL_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("channel_id")
        except Exception:
            pass
    return None


def save_channel(channel_id) -> None:
    with open(CHANNEL_FILE, "w", encoding="utf-8") as f:
        json.dump({"channel_id": channel_id}, f, indent=2)


def clear_channel() -> None:
    if os.path.exists(CHANNEL_FILE):
        try:
            os.remove(CHANNEL_FILE)
        except Exception:
            pass


# ================== HELPERS ==================
def esc(s: str) -> str:
    if not s:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def is_url(text: str) -> bool:
    return bool(URL_RE.match((text or "").strip()))


def type_emoji(t: str) -> str:
    return {"movie": "🎬", "webseries": "📺", "show": "🎞"}.get(t, "🎬")


def type_label(t: str) -> str:
    return {"movie": "Movie", "webseries": "Web Series", "show": "Show"}.get(t, "Item")


def session_items(context: ContextTypes.DEFAULT_TYPE) -> List[dict]:
    return context.user_data.setdefault("items", [])


def current_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "draft" not in context.user_data:
        context.user_data["draft"] = {
            "type": None,
            "poster": None,
            "title": None,
            "screenshots": [],
        }
    return context.user_data["draft"]


def reset_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("items", None)
    context.user_data.pop("draft", None)
    context.user_data.pop("to_delete", None)


def track(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    if message and getattr(message, "message_id", None):
        context.user_data.setdefault("to_delete", []).append(message.message_id)


async def cleanup_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, extra: int = None):
    ids = list(context.user_data.get("to_delete") or [])
    if extra:
        ids.append(extra)
    for mid in ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    context.user_data["to_delete"] = []


def build_list_caption(items: List[dict], with_hint: bool = True) -> str:
    lines = ["📋 <b>TODAY'S LIST</b>", ""]
    for i, it in enumerate(items, 1):
        em = type_emoji(it.get("type") or "movie")
        title = esc(it.get("title") or "Untitled")
        poster = it.get("poster") or ""
        ss = it.get("screenshots") or []
        lines.append(f"<b>{i}.</b> {em} <b>{title}</b>")
        row_bits = []
        if poster:
            row_bits.append(f'🖼 <a href="{esc(poster)}">Poster</a>')
        if ss:
            # first few SS links + count
            shown = ss[:5]
            ss_parts = [
                f'<a href="{esc(u)}">SS{j}</a>' for j, u in enumerate(shown, 1)
            ]
            extra = f" +{len(ss) - 5}" if len(ss) > 5 else ""
            row_bits.append(f"📸 {' · '.join(ss_parts)}{extra}")
        if row_bits:
            lines.append("   " + "  |  ".join(row_bits))
        lines.append("")
    if with_hint:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append("❤️ <i>Tap a button below for what you want next</i>")
    text = "\n".join(lines).strip()
    if len(text) > 4000:
        text = text[:3980] + "\n\n… <i>(trimmed)</i>"
    return text


def vote_keyboard(items: List[dict], votes: Optional[Dict[int, int]] = None) -> InlineKeyboardMarkup:
    votes = votes or {}
    rows = []
    row = []
    for i in range(len(items)):
        c = int(votes.get(i, 0))
        label = f"❤️ {i + 1}" if c == 0 else f"❤️ {i + 1} ({c})"
        row.append(InlineKeyboardButton(label, callback_data=f"v:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def post_key(chat_id, message_id) -> str:
    return f"{chat_id}:{message_id}"


# ================== START / HELP ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text(
            "👋 This bot is for admin list posts.\n"
            "Vote on the channel post with ❤️ buttons."
        )
        return
    await update.message.reply_text(
        "👋 *SKxListBot*\n\n"
        "Build a short list (movie / series / show) with poster + screenshot links,\n"
        "preview privately, post to channel, collect ❤️ votes.\n\n"
        "*Commands*\n"
        "`/new` — start a list (max 10 items)\n"
        "`/setchannel @Channel` — set post channel\n"
        "`/getchannel` — show channel\n"
        "`/cancel` — cancel current list\n"
        "`/help`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 *Help*\n\n"
        "1. `/new`\n"
        "2. Choose type → poster URL → title → screenshot URLs\n"
        "3. Add more items or Finish\n"
        "4. Private preview → Post to channel\n\n"
        "Votes are in-memory (best for a few hours while bot is online).\n"
        "Forwarded copies of the post usually *won’t* keep live vote buttons — "
        "vote on the original bot post in the channel.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/setchannel @Channel` or `-100...`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    raw = context.args[0].strip()
    if raw.startswith("@"):
        ch = raw
    else:
        try:
            ch = int(raw)
        except ValueError:
            await update.message.reply_text("Invalid channel.")
            return
    save_channel(ch)
    await update.message.reply_text(
        f"✅ Channel set: `{ch}`\nMake the bot *admin* in that channel.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def get_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    ch = load_channel()
    await update.message.reply_text(
        f"📢 `{ch}`" if ch else "No channel. `/setchannel @Channel`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    clear_channel()
    await update.message.reply_text("✅ Channel cleared.")


async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /addadmin <user_id>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID")
        return
    ADMINS.add(new_id)
    save_admins(ADMINS)
    await update.message.reply_text(f"✅ Admin `{new_id}`", parse_mode=ParseMode.MARKDOWN)


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != MAIN_OWNER_ID:
        await update.message.reply_text("⛔ Owner only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /removeadmin <user_id>")
        return
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID")
        return
    if rid == MAIN_OWNER_ID:
        await update.message.reply_text("Cannot remove owner.")
        return
    ADMINS.discard(rid)
    save_admins(ADMINS)
    await update.message.reply_text(f"✅ Removed `{rid}`", parse_mode=ParseMode.MARKDOWN)


async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    lines = ["👑 *Admins*", ""]
    for a in sorted(ADMINS):
        lines.append(f"`{a}`" + (" (Owner)" if a == MAIN_OWNER_ID else ""))
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if update.callback_query:
        await update.callback_query.answer()
        if chat_id:
            await cleanup_chat(context, chat_id, update.callback_query.message.message_id)
        try:
            await update.callback_query.edit_message_text("❌ Cancelled.")
        except Exception:
            pass
    else:
        if chat_id:
            await cleanup_chat(context, chat_id)
        await update.message.reply_text("❌ Cancelled. /new to start again.")
    reset_session(context)
    return ConversationHandler.END


# ================== CREATE LIST ==================
async def new_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only.")
        return ConversationHandler.END
    reset_session(context)
    track(context, update.message)
    kb = [
        [
            InlineKeyboardButton("🎬 Movie", callback_data="t_movie"),
            InlineKeyboardButton("📺 Web Series", callback_data="t_webseries"),
        ],
        [InlineKeyboardButton("🎞 Show / Other", callback_data="t_show")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    msg = await update.message.reply_text(
        f"📋 *New list* (max {MAX_ITEMS} items)\n\nSelect type for item #1:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )
    track(context, msg)
    return WAIT_TYPE


async def type_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    mapping = {
        "t_movie": "movie",
        "t_webseries": "webseries",
        "t_show": "show",
    }
    t = mapping.get(query.data)
    if not t:
        return WAIT_TYPE
    draft = current_draft(context)
    draft.clear()
    draft["type"] = t
    draft["screenshots"] = []
    n = len(session_items(context)) + 1
    await query.edit_message_text(
        f"Item #{n} · {type_label(t)}\n\n"
        "🖼 Send *poster image URL*:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAIT_POSTER


async def poster_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track(context, update.message)
    text = (update.message.text or "").strip()
    if not is_url(text):
        msg = await update.message.reply_text("⚠️ Send a valid URL (https://...)")
        track(context, msg)
        return WAIT_POSTER
    current_draft(context)["poster"] = text
    msg = await update.message.reply_text("📝 Send *title*:", parse_mode=ParseMode.MARKDOWN)
    track(context, msg)
    return WAIT_TITLE


async def title_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track(context, update.message)
    title = (update.message.text or "").strip()
    if len(title) < 1:
        msg = await update.message.reply_text("⚠️ Title empty. Send again.")
        track(context, msg)
        return WAIT_TITLE
    current_draft(context)["title"] = title[:200]
    kb = [
        [InlineKeyboardButton("✅ Done screenshots", callback_data="ss_done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    msg = await update.message.reply_text(
        "📸 Send screenshot *URLs* one by one.\n"
        "When finished, tap *Done screenshots*.\n"
        f"(max {MAX_SS_PER_ITEM})",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )
    track(context, msg)
    return WAIT_SS


async def ss_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track(context, update.message)
    text = (update.message.text or "").strip()
    draft = current_draft(context)
    if not is_url(text):
        msg = await update.message.reply_text("⚠️ URL required, or tap Done.")
        track(context, msg)
        return WAIT_SS
    ss = draft.setdefault("screenshots", [])
    if len(ss) >= MAX_SS_PER_ITEM:
        msg = await update.message.reply_text(f"⚠️ Max {MAX_SS_PER_ITEM} screenshots.")
        track(context, msg)
        return WAIT_SS
    ss.append(text)
    kb = [
        [InlineKeyboardButton("✅ Done screenshots", callback_data="ss_done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    msg = await update.message.reply_text(
        f"✅ SS {len(ss)} saved. More URL or *Done*.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )
    track(context, msg)
    return WAIT_SS


async def ss_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    draft = current_draft(context)
    items = session_items(context)
    items.append(
        {
            "type": draft.get("type") or "movie",
            "poster": draft.get("poster"),
            "title": draft.get("title") or "Untitled",
            "screenshots": list(draft.get("screenshots") or []),
        }
    )
    context.user_data["draft"] = {
        "type": None,
        "poster": None,
        "title": None,
        "screenshots": [],
    }
    n = len(items)
    kb = []
    if n < MAX_ITEMS:
        kb.append([InlineKeyboardButton("➕ Add another item", callback_data="add_more")])
    kb.append([InlineKeyboardButton("✅ Finish list", callback_data="finish_list")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    await query.edit_message_text(
        f"✅ Item #{n} added: *{items[-1]['title']}*\n"
        f"Total: {n}/{MAX_ITEMS}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return WAIT_NEXT


async def next_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel":
        return await cancel(update, context)
    if data == "add_more":
        items = session_items(context)
        if len(items) >= MAX_ITEMS:
            await query.answer(f"Max {MAX_ITEMS} items", show_alert=True)
            return WAIT_NEXT
        kb = [
            [
                InlineKeyboardButton("🎬 Movie", callback_data="t_movie"),
                InlineKeyboardButton("📺 Web Series", callback_data="t_webseries"),
            ],
            [InlineKeyboardButton("🎞 Show / Other", callback_data="t_show")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
        ]
        await query.edit_message_text(
            f"Select type for item #{len(items) + 1}:",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        return WAIT_TYPE
    if data == "finish_list":
        items = session_items(context)
        if not items:
            await query.answer("No items yet", show_alert=True)
            return WAIT_NEXT
        return await show_preview(update, context)
    return WAIT_NEXT


async def show_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    items = session_items(context)
    caption = build_list_caption(items, with_hint=True)
    ch = load_channel()
    try:
        await query.edit_message_text(
            "👀 *Private preview*\n\n" + caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest:
        # message too long / parse — send new
        await query.message.reply_text(
            caption, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

    kb = []
    if ch:
        kb.append(
            [InlineKeyboardButton("📢 Post to channel", callback_data="post_channel")]
        )
    kb.append(
        [InlineKeyboardButton("👤 Send preview to me again", callback_data="post_me")]
    )
    if not ch:
        kb.append(
            [
                InlineKeyboardButton(
                    "ℹ️ Set channel first: /setchannel", callback_data="noop"
                )
            ]
        )
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])
    msg = await query.message.reply_text(
        "Choose:",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    track(context, msg)
    return WAIT_CONFIRM


async def confirm_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel":
        return await cancel(update, context)
    if data == "noop":
        await query.answer("Use /setchannel @Channel", show_alert=True)
        return WAIT_CONFIRM

    items = session_items(context)
    if not items:
        await query.edit_message_text("No items.")
        reset_session(context)
        return ConversationHandler.END

    chat_id = query.message.chat_id
    caption = build_list_caption(items, with_hint=True)

    if data == "post_me":
        await query.edit_message_text("⏳ Sending preview…")
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=vote_keyboard(items),
        )
        await cleanup_chat(context, chat_id, query.message.message_id)
        # Keep session so they can still post to channel? Clear for simplicity
        reset_session(context)
        await context.bot.send_message(
            chat_id, "✅ Preview sent.\n/new for a new list."
        )
        return ConversationHandler.END

    if data == "post_channel":
        ch = load_channel()
        if not ch:
            await query.answer("Set channel: /setchannel @Channel", show_alert=True)
            return WAIT_CONFIRM
        await query.edit_message_text("⏳ Posting to channel…")
        try:
            msg = await context.bot.send_message(
                chat_id=ch,
                text=caption,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=vote_keyboard(items),
            )
            key = post_key(msg.chat_id, msg.message_id)
            VOTE_STORE[key] = {
                "votes": {i: 0 for i in range(len(items))},
                "voters": {i: set() for i in range(len(items))},
            }
            POST_ITEMS[key] = list(items)
            await cleanup_chat(context, chat_id, query.message.message_id)
            reset_session(context)
            await context.bot.send_message(
                chat_id,
                f"✅ Posted to channel.\n"
                f"Votes work on *this* channel message (not on forwards).\n"
                f"/new for next list.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except TelegramError as e:
            await context.bot.send_message(
                chat_id,
                f"⚠️ Channel post failed: {e}\n"
                "Is the bot admin in the channel?",
            )
        return ConversationHandler.END

    return WAIT_CONFIRM


# ================== VOTES ==================
async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("v:"):
        await query.answer()
        return
    try:
        idx = int(data.split(":", 1)[1])
    except ValueError:
        await query.answer()
        return

    user = update.effective_user
    msg = query.message
    if not msg:
        await query.answer()
        return

    key = post_key(msg.chat_id, msg.message_id)
    store = VOTE_STORE.get(key)
    items = POST_ITEMS.get(key)

    # If bot restarted, store empty — still allow optimistic UI without persist
    if store is None:
        # Try to recover item count from button count
        store = {"votes": {}, "voters": {}}
        VOTE_STORE[key] = store
    if items is None:
        # cannot rebuild caption; only update buttons from current markup length
        items = POST_ITEMS.get(key) or []

    votes: Dict[int, int] = store.setdefault("votes", {})
    voters: Dict[int, Set[int]] = store.setdefault("voters", {})
    if idx not in voters:
        voters[idx] = set()
    if idx not in votes:
        votes[idx] = 0

    uid = user.id
    if uid in voters[idx]:
        await query.answer("You already voted for this one.", show_alert=False)
        return

    voters[idx].add(uid)
    votes[idx] = int(votes.get(idx, 0)) + 1

    # Rebuild keyboard
    n_items = len(items) if items else max(votes.keys(), default=idx) + 1
    if not items:
        # minimal fake items for keyboard width
        items = [{"title": str(i + 1)} for i in range(n_items)]
        if idx >= len(items):
            items.extend({"title": str(i + 1)} for i in range(len(items), idx + 1))

    try:
        await query.edit_message_reply_markup(
            reply_markup=vote_keyboard(items, votes)
        )
    except BadRequest as e:
        # message not modified / too old
        logger.info(f"vote edit: {e}")
    await query.answer(f"❤️ #{idx + 1} · {votes[idx]}")


# ================== FLASK ==================
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "SKxListBot is Alive ✅", 200


@flask_app.route("/health")
def health():
    return "OK", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ================== MAIN ==================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Set BOT_TOKEN env")
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    Thread(target=run_flask, daemon=True).start()
    print(f"✅ Flask on port {PORT}")

    async def post_init(application: Application):
        await application.bot.set_my_commands(
            [
                BotCommand("start", "Start"),
                BotCommand("new", "Create a list"),
                BotCommand("setchannel", "Set channel"),
                BotCommand("getchannel", "Show channel"),
                BotCommand("cancel", "Cancel"),
                BotCommand("help", "Help"),
            ]
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error("Error: %s", context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ Error. /cancel and try /new again."
                )
            except Exception:
                pass

    app.add_error_handler(on_error)

    conv = ConversationHandler(
        entry_points=[CommandHandler("new", new_cmd)],
        states={
            WAIT_TYPE: [
                CallbackQueryHandler(type_chosen, pattern="^t_"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAIT_POSTER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, poster_received),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAIT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, title_received),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAIT_SS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, ss_received),
                CallbackQueryHandler(ss_done, pattern="^ss_done$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAIT_NEXT: [
                CallbackQueryHandler(next_choice, pattern="^(add_more|finish_list)$"),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
            WAIT_CONFIRM: [
                CallbackQueryHandler(
                    confirm_post, pattern="^(post_channel|post_me|noop)$"
                ),
                CallbackQueryHandler(cancel, pattern="^cancel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
        ],
        allow_reentry=True,
        per_message=False,
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CommandHandler("getchannel", get_channel))
    app.add_handler(CommandHandler("removechannel", remove_channel))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("admins", list_admins))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(vote_callback, pattern="^v:"))
    app.add_handler(CommandHandler("cancel", cancel))

    print("✅ SKxListBot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
