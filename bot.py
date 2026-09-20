import os
import sys
import re
import html
import logging
import asyncio
import httpx
from dotenv import load_dotenv

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_BASE_URL = os.getenv("API_BASE_URL", "https://gmail-fetch-api-by-xeon.vercel.app/api/profile").rstrip("/")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Regular expression to extract email addresses
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
AUTO_DELETE_DELAY = 300  # 5 minutes in seconds

# Prevent background asyncio tasks from being garbage collected in Python 3.12+
BACKGROUND_TASKS: set[asyncio.Task] = set()


def run_background_task(coro) -> asyncio.Task:
    """Run an async coroutine in background while maintaining a strong reference."""
    task = asyncio.create_task(coro)
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return task


async def schedule_auto_delete(chat_id: int, message_ids: list[int], delay: int, bot) -> None:
    """Delete specified messages after delay seconds."""
    try:
        logger.info(f"Scheduled auto-deletion of messages {message_ids} in chat {chat_id} in {delay}s")
        await asyncio.sleep(delay)
        for msg_id in message_ids:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                logger.info(f"Auto-deleted message {msg_id} in chat {chat_id}")
            except Exception as e:
                logger.warning(f"Could not auto-delete message {msg_id} in chat {chat_id}: {e}")
    except asyncio.CancelledError:
        logger.info(f"Auto-delete task for chat {chat_id} was cancelled.")
    except Exception as e:
        logger.error(f"Error in auto_delete for chat {chat_id}: {e}", exc_info=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message on /start."""
    user = update.effective_user
    user_name = user.first_name if user and user.first_name else "User"
    welcome_text = f"👋 <b>Hello {html.escape(user_name)}!</b>"
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help message on /help."""
    help_text = (
        "📖 <b>How to Use:</b>\n\n"
        "1. Send a Gmail address directly: <code>example@gmail.com</code>\n"
        "2. The bot queries Google Profile API and returns:\n"
        "   • 👤 Full Name & First/Last Names\n"
        "   • 🆔 Google Person ID\n"
        "   • 🏷️ User Type & Entity Type\n"
        "   • 📱 Reachable Google Apps (Photos, Maps, Meet, etc.)\n"
        "   • 🕒 Last Updated Timestamp\n"
        "   • 🖼️ High-Res Profile Photo\n"
        "   • ⭐ Google Maps Reviews\n\n"
        "⏳ <i>All results auto-delete in 5 minutes.</i>"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


def extract_names(profile: dict, email_query: str, raw_name: str | None = None) -> tuple[str, str, str, str]:
    """Extract full name, first name, last name, and an intelligent handle fallback."""
    names_dict = profile.get("names") or {}
    
    full_name = (raw_name or "").strip()
    first_name = ""
    last_name = ""

    # Search through all name sources (PROFILE, CONTACT, etc.) if available
    for source, n_data in names_dict.items():
        if isinstance(n_data, dict):
            if not full_name:
                full_name = (n_data.get("fullname") or n_data.get("displayName") or "").strip()
            if not first_name:
                first_name = (n_data.get("firstName") or "").strip()
            if not last_name:
                last_name = (n_data.get("lastName") or "").strip()

    if not full_name and (first_name or last_name):
        full_name = f"{first_name} {last_name}".strip()

    # Generate a readable guess/handle from the email username
    email_user = email_query.split("@")[0] if "@" in email_query else email_query
    # Clean handle (remove trailing digits or split by . _ -)
    cleaned_parts = [part.capitalize() for part in re.split(r"[._\-+]+", re.sub(r"\d+$", "", email_user)) if part]
    inferred_name = " ".join(cleaned_parts) if cleaned_parts else email_user

    return full_name, first_name, last_name, inferred_name


def format_profile_data(data: dict, email_query: str) -> tuple[str, str | None, InlineKeyboardMarkup | None]:
    """Parse JSON response and format into human-readable message matching exact user specification."""
    if not isinstance(data, dict):
        return (
            f"❌ <b>Invalid response received for:</b> <code>{html.escape(email_query)}</code>",
            None,
            None,
        )

    # Check if API returned an error or account not found
    if "error" in data:
        err_msg = data.get("error", "")
        return (
            f"❌ <b>Target Account Not Found:</b> <code>{html.escape(email_query)}</code>\n"
            f"<i>{html.escape(str(err_msg)) if err_msg else 'This Google account does not exist or has strict privacy settings enabled.'}</i>",
            None,
            None,
        )

    if "detail" in data:
        detail_msg = str(data.get("detail", ""))
        if "wasn't found" in detail_msg.lower() or "not found" in detail_msg.lower():
            return (
                f"❌ <b>Target Account Not Found:</b> <code>{html.escape(email_query)}</code>\n"
                "<i>This Google account does not exist or has strict privacy settings enabled.</i>",
                None,
                None,
            )

    summary = data.get("summary") or {}
    profile_container = data.get("PROFILE_CONTAINER") or {}
    profile = profile_container.get("profile") or {}

    # Extract fields with fallback across flat keys, summary, and nested schema
    email_val = (
        data.get("email")
        or summary.get("email")
        or ((profile.get("emails") or {}).get("PROFILE") or {}).get("value")
        or email_query
    )

    raw_name = data.get("name") or summary.get("name")
    full_name, first_name, last_name, inferred_name = extract_names(profile, email_query, raw_name=raw_name)
    final_name = full_name if full_name else inferred_name

    person_id = str(
        data.get("person_id")
        or data.get("gaia_id")
        or summary.get("person_id")
        or summary.get("gaia_id")
        or profile.get("personId")
        or ""
    ).strip()

    # User Type
    user_types = (
        summary.get("user_type")
        or ((profile.get("profileInfos") or {}).get("PROFILE") or {}).get("userTypes")
        or data.get("user_type")
    )
    if isinstance(user_types, list):
        user_type_str = ", ".join(user_types)
    else:
        user_type_str = str(user_types) if user_types else "GOOGLE_USER"

    # Apps
    apps_list = (
        summary.get("apps")
        or ((profile.get("inAppReachability") or {}).get("PROFILE") or {}).get("apps")
        or data.get("apps")
        or []
    )
    apps_str = ", ".join(apps_list) if isinstance(apps_list, list) and apps_list else (str(apps_list) if apps_list else "None")

    # Presence & Enterprise
    ext_data = profile.get("extendedData") or {}
    dynamite = ext_data.get("dynamiteData") or {}
    presence = summary.get("presence") or dynamite.get("presence") or "UNKNOWN"

    gplus = ext_data.get("gplusData") or {}
    is_enterprise = summary.get("enterprise")
    if is_enterprise is None:
        is_enterprise = "Yes" if gplus.get("isEntrepriseUser", False) else "No"

    # Last Updated
    source_ids = (profile.get("sourceIds") or {}).get("PROFILE") or {}
    last_updated = (
        summary.get("last_updated")
        or data.get("last_updated")
        or source_ids.get("lastUpdated")
        or ""
    )

    # Photo URL & Reviews URL
    photo_url = (
        data.get("profile_photo")
        or data.get("profile_pic_url")
        or summary.get("profile_photo")
        or summary.get("profile_pic_url")
        or ((profile.get("profilePhotos") or {}).get("PROFILE") or {}).get("url")
    )

    reviews_url = (
        summary.get("google_reviews")
        or data.get("google_reviews")
        or (f"https://www.google.com/maps/contrib/{person_id}" if person_id else "")
    )

    if not person_id and not photo_url and not profile and not raw_name:
        return (
            f"❌ <b>No profile information found for:</b> <code>{html.escape(email_query)}</code>",
            None,
            None,
        )

    # Construct exact layout requested
    lines = [
        "<b>GOOGLE ACCOUNT INFO</b>",
        f"<b>Name:</b> {html.escape(final_name)}",
        f"<b>Email:</b> {html.escape(email_val)}",
    ]
    if person_id:
        lines.append(f"<b>Person ID:</b> <code>{html.escape(person_id)}</code>")
    lines.append(f"<b>User Type:</b> {html.escape(user_type_str)}")
    lines.append(f"<b>Apps:</b> {html.escape(apps_str)}")
    lines.append(f"<b>Presence:</b> {html.escape(presence)}")
    lines.append(f"<b>Enterprise:</b> {html.escape(str(is_enterprise))}")
    if last_updated:
        lines.append(f"<b>Last Updated:</b> <code>{html.escape(last_updated)}</code>")

    lines.append("\n⏳ <i>This message will auto-delete in 5 minutes.</i>")

    # Inline Buttons for Profile Photo and Google Reviews
    buttons = []
    button_row = []
    if photo_url and photo_url.startswith("http"):
        button_row.append(InlineKeyboardButton("🖼️ Profile Photo", url=photo_url))
    if reviews_url:
        button_row.append(InlineKeyboardButton("🗺️ Google Reviews", url=reviews_url))
    
    if button_row:
        buttons.append(button_row)

    markup = InlineKeyboardMarkup(buttons) if buttons else None
    final_caption = "\n".join(lines)

    return final_caption, photo_url, markup



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages and extract emails."""
    text = (update.message.text or "").strip()
    if not text:
        return

    # Extract email
    match = EMAIL_REGEX.search(text)
    if not match:
        await update.message.reply_text(
            "⚠️ Please provide a valid email address (e.g. <code>example@gmail.com</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    target_email = match.group(0)
    user_msg_id = update.message.message_id
    status_msg = await update.message.reply_text(
        f"🔎 Fetching information for <code>{html.escape(target_email)}</code>...",
        parse_mode=ParseMode.HTML,
    )

    try:
        target_url = f"{API_BASE_URL}/{target_email}"
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(target_url)
            
            if resp.status_code == 404:
                err_msg = await status_msg.edit_text(
                    f"❌ <b>Target Account Not Found:</b> <code>{html.escape(target_email)}</code>\n"
                    "<i>This Google account does not exist or has strict privacy settings enabled.</i>",
                    parse_mode=ParseMode.HTML,
                )
                run_background_task(schedule_auto_delete(
                    chat_id=update.effective_chat.id,
                    message_ids=[user_msg_id, err_msg.message_id],
                    delay=AUTO_DELETE_DELAY,
                    bot=context.bot,
                ))
                return
            elif resp.status_code != 200:
                err_msg = await status_msg.edit_text(
                    f"⚠️ <b>API Error ({resp.status_code}):</b> Unable to retrieve data for <code>{html.escape(target_email)}</code>.",
                    parse_mode=ParseMode.HTML,
                )
                run_background_task(schedule_auto_delete(
                    chat_id=update.effective_chat.id,
                    message_ids=[user_msg_id, err_msg.message_id],
                    delay=AUTO_DELETE_DELAY,
                    bot=context.bot,
                ))
                return

            data = resp.json()

        text_content, photo_url, markup = format_profile_data(data, target_email)

        # Delete status message
        await status_msg.delete()

        sent_msg_ids = []

        # Attempt to send as photo if available
        if photo_url and photo_url.startswith("http"):
            try:
                # Telegram caption length limit is 1024 characters
                if len(text_content) <= 1024:
                    photo_msg = await update.message.reply_photo(
                        photo=photo_url,
                        caption=text_content,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                    )
                    sent_msg_ids.append(photo_msg.message_id)
                else:
                    m1 = await update.message.reply_photo(photo=photo_url)
                    m2 = await update.message.reply_text(
                        text_content,
                        parse_mode=ParseMode.HTML,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                    sent_msg_ids.extend([m1.message_id, m2.message_id])
            except Exception as pe:
                logger.warning(f"Could not send photo directly ({pe}), falling back to text.")
                fallback_msg = await update.message.reply_text(
                    text_content,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
                sent_msg_ids.append(fallback_msg.message_id)
        else:
            # Fallback to standard text message
            txt_msg = await update.message.reply_text(
                text_content,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            sent_msg_ids.append(txt_msg.message_id)

        # Schedule automatic deletion of query and response after 5 minutes (300s)
        if sent_msg_ids:
            run_background_task(schedule_auto_delete(
                chat_id=update.effective_chat.id,
                message_ids=[user_msg_id] + sent_msg_ids,
                delay=AUTO_DELETE_DELAY,
                bot=context.bot,
            ))

    except httpx.TimeoutException:
        err_msg = await status_msg.edit_text(
            "⏱️ <b>Request Timeout:</b> The API server took too long to respond. Please try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
        run_background_task(schedule_auto_delete(
            chat_id=update.effective_chat.id,
            message_ids=[user_msg_id, err_msg.message_id],
            delay=AUTO_DELETE_DELAY,
            bot=context.bot,
        ))
    except Exception as e:
        logger.error(f"Error handling request: {e}", exc_info=True)
        err_msg = await status_msg.edit_text(
            f"❌ <b>Error occurred:</b> <code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )
        run_background_task(schedule_auto_delete(
            chat_id=update.effective_chat.id,
            message_ids=[user_msg_id, err_msg.message_id],
            delay=AUTO_DELETE_DELAY,
            bot=context.bot,
        ))


import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - Gmail Bot is alive\n")

    def log_message(self, format, *args):
        # Silence default HTTP access logs to keep terminal clean
        return


def start_health_server():
    """Start dummy HTTP server for Render port binding health checks."""
    port_str = os.getenv("PORT")
    if port_str:
        try:
            port = int(port_str)
            server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            logger.info(f"Health check HTTP server listening on port {port} (Render compatible)")
        except Exception as e:
            logger.warning(f"Could not start health check server on port {port_str}: {e}")


async def keep_alive_ping():
    """Periodically ping API to keep instance awake."""
    while True:
        try:
            await asyncio.sleep(600)  # Ping every 10 minutes
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get("https://googleprofile-five.vercel.app/")
                logger.info(f"Keep-alive ping sent to API (Status: {r.status_code})")
        except Exception as e:
            logger.debug(f"Keep-alive ping error: {e}")


def main():
    """Start the Telegram bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("\n" + "=" * 60)
        print("⚠️  ERROR: BOT_TOKEN is not set!")
        print("Please edit the .env file and add your Telegram Bot Token from @BotFather:")
        print("BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ")
        print("=" * 60 + "\n")
        return

    # Start health check server if on Render/Cloud environment with PORT
    start_health_server()

    print("🚀 Starting Gmail OSINT Telegram Bot...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Schedule background keep-alive task in job queue
    if app.job_queue:
        async def job_keep_alive(ctx):
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.get("https://googleprofile-five.vercel.app/")
            except Exception:
                pass
        app.job_queue.run_repeating(job_keep_alive, interval=600, first=60)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is online and listening for messages!")
    app.run_polling()


if __name__ == "__main__":
    main()
