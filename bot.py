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
API_BASE_URL = "https://fetch-ykhk.onrender.com/gmail"

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Regular expression to extract email addresses
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
AUTO_DELETE_DELAY = 300  # 5 minutes in seconds


async def schedule_auto_delete(chat_id: int, message_ids: list[int], delay: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete specified messages after delay seconds."""
    await asyncio.sleep(delay)
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            logger.info(f"Auto-deleted message {msg_id} in chat {chat_id}")
        except Exception as e:
            logger.debug(f"Could not delete message {msg_id}: {e}")


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


def extract_names(profile: dict, email_query: str) -> tuple[str, str, str, str]:
    """Extract full name, first name, last name, and an intelligent handle fallback."""
    names_dict = profile.get("names") or {}
    
    full_name = ""
    first_name = ""
    last_name = ""

    # Search through all name sources (PROFILE, CONTACT, etc.)
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
    """Parse JSON response and format into human-readable HTML message."""
    # Check if API returned an error/not found in detail
    if "detail" in data:
        detail_msg = data.get("detail", "")
        if "The target wasn't found" in detail_msg:
            return (
                f"❌ <b>Target Account Not Found:</b> <code>{html.escape(email_query)}</code>\n"
                "<i>This Google account does not exist or has strict privacy settings enabled.</i>",
                None,
                None,
            )

    profile_container = data.get("PROFILE_CONTAINER") or {}
    profile = profile_container.get("profile") or {}
    maps = data.get("maps") or {}
    play_games = data.get("play_games")
    calendar = data.get("calendar")

    if not profile and not maps and not play_games and not calendar:
        return (
            f"❌ <b>No profile information found for:</b> <code>{html.escape(email_query)}</code>",
            None,
            None,
        )

    # Basic Info
    person_id = profile.get("personId") or "N/A"
    
    # Extract Names
    full_name, first_name, last_name, inferred_name = extract_names(profile, email_query)

    # Emails
    emails_obj = (profile.get("emails") or {}).get("PROFILE") or {}
    email_val = emails_obj.get("value") or email_query

    # Profile infos
    profile_infos = (profile.get("profileInfos") or {}).get("PROFILE") or {}
    user_types = profile_infos.get("userTypes") or []
    user_type_str = ", ".join(user_types) if user_types else "Standard User"

    # In-app reachability
    reachability = (profile.get("inAppReachability") or {}).get("PROFILE") or {}
    apps = reachability.get("apps") or []
    apps_str = ", ".join(f"<code>{html.escape(app)}</code>" for app in apps) if apps else "None detected"

    # Timestamps
    source_ids = (profile.get("sourceIds") or {}).get("PROFILE") or {}
    last_updated = source_ids.get("lastUpdated") or "N/A"

    # Extended data
    extended = profile.get("extendedData") or {}
    dynamite = extended.get("dynamiteData") or {}
    entity_type = dynamite.get("entityType") or "PERSON"
    presence = dynamite.get("presence") or "UNKNOWN"
    dnd_state = dynamite.get("dndState") or "AVAILABLE"

    # Photos
    profile_photos = (profile.get("profilePhotos") or {}).get("PROFILE") or {}
    photo_url = profile_photos.get("url")

    # Construct formatted message
    lines = [
        f"🎯 <b>Google Profile Intel:</b> <code>{html.escape(email_val)}</code>\n",
    ]

    if full_name:
        lines.append(f"👤 <b>Name:</b> <b>{html.escape(full_name)}</b>")
        if first_name or last_name:
            lines.append(f"▫️ <b>First / Last:</b> {html.escape(first_name or '-')} / {html.escape(last_name or '-')}")
    else:
        lines.append(f"👤 <b>Name:</b> <i>Not set / Hidden on Google</i> (Inferred: <b>{html.escape(inferred_name)}</b>)")

    lines.extend([
        f"🆔 <b>Person ID:</b> <code>{html.escape(person_id)}</code>",
        f"🏷️ <b>User Type:</b> {html.escape(user_type_str)} ({html.escape(entity_type)})",
        f"🔔 <b>Presence:</b> {html.escape(presence)} | {html.escape(dnd_state)}",
        f"🕒 <b>Last Updated:</b> <code>{html.escape(last_updated)}</code>",
        f"📱 <b>Linked Apps:</b> {apps_str}",
    ])

    # Google Maps & Reviews
    reviews_data = maps.get("reviews")
    stats_data = maps.get("stats") or {}
    photos_data = maps.get("photos")
    maps_contrib_url = f"https://www.google.com/maps/contrib/{person_id}" if person_id and person_id != "N/A" else None

    lines.append("\n🗺️ <b>Google Maps & Reviews:</b>")
    if stats_data:
        stats_list = [f"{k.capitalize()}: {v}" for k, v in stats_data.items()]
        lines.append(f"📊 <b>Stats:</b> {', '.join(stats_list)}")

    if reviews_data:
        if isinstance(reviews_data, list):
            lines.append(f"⭐ <b>Reviews ({len(reviews_data)}):</b>")
            for rev in reviews_data[:5]:
                if isinstance(rev, dict):
                    place = rev.get("placeName") or rev.get("title") or "Place"
                    rating = rev.get("rating") or "N/A"
                    comment = rev.get("comment") or rev.get("snippet") or ""
                    lines.append(f" • <b>{html.escape(str(place))}</b> (Rating: {rating}⭐)")
                    if comment:
                        lines.append(f"   <i>\"{html.escape(comment[:120])}...\"</i>")
                else:
                    lines.append(f" • {html.escape(str(rev)[:100])}")
        elif isinstance(reviews_data, dict):
            lines.append(f"⭐ <b>Reviews:</b> {html.escape(str(reviews_data)[:200])}")
    else:
        lines.append("⭐ <i>No public Google Maps reviews found.</i>")

    if photos_data:
        if isinstance(photos_data, list):
            lines.append(f"📸 <b>Maps Photos:</b> {len(photos_data)} photo(s) uploaded")
        else:
            lines.append("📸 <b>Maps Photos:</b> Available")

    # Play Games
    if play_games:
        lines.append(f"\n🎮 <b>Play Games:</b> {html.escape(str(play_games))}")

    # Calendar
    if calendar:
        lines.append(f"\n📅 <b>Calendar:</b> {html.escape(str(calendar))}")

    # Add auto-delete notification
    lines.append("\n⏳ <i>This message will auto-delete in 5 minutes.</i>")

    # Inline Buttons: Only Google Maps Reviews and Profile Photo
    buttons = []
    if maps_contrib_url:
        buttons.append([InlineKeyboardButton("🗺️ Google Maps Reviews", url=maps_contrib_url)])
    if photo_url and photo_url.startswith("http"):
        buttons.append([InlineKeyboardButton("🖼️ Profile Photo", url=photo_url)])

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
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(API_BASE_URL, params={"id": target_email})
            
            if resp.status_code != 200:
                await status_msg.edit_text(
                    f"⚠️ <b>API Error ({resp.status_code}):</b> Unable to retrieve data for <code>{html.escape(target_email)}</code>.",
                    parse_mode=ParseMode.HTML,
                )
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
            asyncio.create_task(schedule_auto_delete(
                chat_id=update.effective_chat.id,
                message_ids=[user_msg_id] + sent_msg_ids,
                delay=AUTO_DELETE_DELAY,
                context=context,
            ))

    except httpx.TimeoutException:
        await status_msg.edit_text(
            "⏱️ <b>Request Timeout:</b> The API server took too long to respond. Please try again in a moment.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error handling request: {e}", exc_info=True)
        await status_msg.edit_text(
            f"❌ <b>Error occurred:</b> <code>{html.escape(str(e))}</code>",
            parse_mode=ParseMode.HTML,
        )


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

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is online and listening for messages!")
    app.run_polling()


if __name__ == "__main__":
    main()
