---
title: Ginchiee Bot
emoji: 🌸
colorFrom: pink
colorTo: purple
sdk: gradio
app_file: app.py
pinned: false
---

# 🍎 Ginchiee — AI Diet Companion Bot

Bot Telegram yang membantu kamu menjaga pola makan melalui reminder cerdas, food logging, estimasi nutrisi, dan percakapan natural dengan AI.

## ✨ Fitur MVP

- 🤖 **AI Conversation** — Ngobrol natural tentang diet & pola makan
- 📝 **Food Logging** — Catat makanan dengan bahasa natural
- 📊 **Nutrition Tracking** — Hitung kalori, protein, karbs, lemak otomatis
- ⏰ **Intelligent Reminder** — Reminder makan yang personal & kontekstual
- 👤 **User Profile** — Setup profil & target nutrisi personal
- 🎯 **Daily Progress** — Pantau progress nutrisi harian

## 🚀 Quick Start

### 1. Clone & Setup

```bash
git clone <repo>
cd ginchiee-bot
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

### 2. Konfigurasi

```bash
cp .env.example .env
```

Edit `.env`:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
GEMINI_API_KEY=your_gemini_api_key_here
```

**Cara dapat token:**
- **Telegram Bot Token**: Chat [@BotFather](https://t.me/BotFather) di Telegram → `/newbot`
- **Gemini API Key**: https://aistudio.google.com/app/apikey

### 3. Jalankan

```bash
python main.py
```

## 📋 Commands

| Command | Fungsi |
|---------|--------|
| `/start` | Mulai & setup profil |
| `/profile` | Lihat profil & target nutrisi |
| `/today` | Progress nutrisi hari ini |
| `/log [makanan]` | Catat makanan |
| `/schedule` | Atur jadwal makan & reminder |
| `/reminders` | Lihat jadwal makan |
| `/help` | Bantuan |

## 🏗️ Struktur Project

```
ginchiee-bot/
├── main.py                  # Entry point
├── config/
│   └── settings.py          # Konfigurasi
├── database/
│   ├── connection.py        # SQLite connection
│   ├── migrations.py        # Auto-migration
│   └── schema.sql           # DDL
├── nutrition/
│   ├── calculator.py        # BMR/TDEE/macro calculator
│   ├── food_database.py     # Database nutrisi makanan
│   └── parser.py            # NLP food parser
├── services/
│   ├── ai_service.py        # Gemini LLM abstraction
│   ├── nutrition_service.py # Food logging & tracking
│   ├── reminder_service.py  # Reminder generation
│   ├── scheduler_service.py # APScheduler
│   └── user_service.py      # User CRUD
├── bot/
│   ├── conversations/
│   │   └── onboarding.py    # Multi-step registration
│   └── handlers/
│       ├── food_handler.py
│       ├── help_handler.py
│       ├── message_handler.py
│       ├── profile_handler.py
│       ├── schedule_handler.py
│       └── today_handler.py
└── docs/
    ├── PRD.md
    ├── Architecture.md
    └── Config.md
```

## 🔧 Stack

- **Python 3.11+**
- **python-telegram-bot** — Telegram handler
- **Google Gemini** — LLM
- **SQLite + aiosqlite** — Database
- **APScheduler** — Meal reminder scheduler

## ⚠️ Disclaimer

Ginchiee adalah AI companion, **bukan dokter atau ahli gizi**. Semua estimasi nutrisi bersifat perkiraan. Konsultasikan ke profesional kesehatan untuk saran medis.
