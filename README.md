# 📧 Gmail OSINT & Profile Telegram Bot

A Telegram bot that fetches and displays detailed Google account and profile information from the Gmail OSINT API whenever a user sends an email address.

---

## ✨ Features
- 📩 **Zero-Command Lookup**: Just send any Gmail address (e.g. `user@gmail.com`) directly.
- 🖼️ **Direct Profile Photo**: Returns the user's Google profile picture directly with caption.
- 👤 **Account Details**:
  - Full Name, First Name, Last Name
  - Google Account Person ID
  - User Type (`GOOGLE_USER`, entity type)
  - Presence & DND status
  - Last updated timestamp
  - Linked reachability apps (`Photos`, `Maps`, `Meet`, `Kaboo`, etc.)
- 🗺️ **Google Maps & Reviews**:
  - Reviews count and ratings
  - Uploaded photos stats
  - Snippets of recent reviews
- 🔗 **Inline Action Buttons**: Instant links to view full-res profile and cover pictures.

---

## 🛠️ Setup Instructions

### 1. Prerequisites
Ensure you have Python 3.10+ installed.

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Get a Telegram Bot Token
1. Open Telegram and search for [@BotFather](https://t.me/botfather).
2. Send `/newbot` and follow the steps to choose a name and username.
3. Copy the HTTP API token provided by BotFather.

### 4. Configure Environment
Open the `.env` file in the project folder and paste your token:
```env
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
```

### 5. Start the Bot
```bash
python bot.py
```

---

## 🧪 Testing the API Parser
To verify that the API connection and data formatting work without starting the Telegram polling:
```bash
python test_parser.py
```
