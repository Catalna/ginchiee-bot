# Architecture — AI Diet Companion

**Version:** 0.1.0
**Architecture Stage:** MVP

---

# 1. Architecture Philosophy

MVP menggunakan arsitektur sederhana tetapi modular.

Prinsip utama:

```text
Simple now
Scalable later
```

Jangan memasukkan infrastructure kompleks sebelum dibutuhkan.

MVP tidak membutuhkan:

* Kubernetes,
* microservices,
* PostgreSQL,
* Redis,
* LangGraph,
* Docker.

Semua dapat berjalan sebagai satu Python application.

---

# 2. High-Level Architecture

```text
                         USER
                          │
                          ▼
                  ┌───────────────┐
                  │ Telegram Bot  │
                  └───────┬───────┘
                          │
                          ▼
                  ┌───────────────┐
                  │ Bot Handler   │
                  └───────┬───────┘
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
          Commands     Messages     Callback
             │            │            │
             └────────────┼────────────┘
                          ▼
                  ┌───────────────┐
                  │ Application   │
                  │ Service Layer │
                  └───────┬───────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
    User Service    Nutrition Service   AI Service
         │                │                │
         ▼                ▼                ▼
      SQLite       Nutrition Data       LLM API
         │
         ▼
       Scheduler
```

---

# 3. Application Layers

## 3.1 Presentation Layer

Responsible for Telegram interaction.

```text
Telegram Update
      ↓
Handler
      ↓
Application Service
```

Tidak boleh mengandung business logic kompleks.

---

# 4. Service Layer

Service layer menangani business logic.

Contoh:

```text
UserService
DietService
MealService
NutritionService
ReminderService
AIService
```

Contoh:

```python
nutrition_service.calculate_daily_progress(user_id)
```

---

# 5. Data Layer

MVP menggunakan SQLite.

```text
SQLite
  │
  ├── users
  ├── diet_profiles
  ├── meal_schedules
  ├── food_logs
  ├── daily_nutrition
  └── conversations
```

SQLite dipilih karena:

* zero configuration,
* gratis,
* local,
* mudah backup,
* cukup untuk MVP.

---

# 6. AI Layer

AI Service menjadi abstraction layer untuk LLM.

```text
Application
     ↓
AIService
     ↓
LLM Provider
```

Application tidak boleh langsung memanggil SDK LLM di seluruh codebase.

Contoh:

```python
response = ai_service.chat(
    message=user_message,
    context=user_context
)
```

Dengan abstraction ini provider dapat diganti tanpa mengubah seluruh application.

---

# 7. Nutrition Layer

Nutrition engine bertanggung jawab terhadap kalkulasi.

```text
Food Input
   ↓
Food Parser
   ↓
Nutrition Database
   ↓
Nutrition Calculator
   ↓
Nutrition Result
```

Contoh:

```text
Rice 200g
Chicken 150g
Egg 1
```

menjadi:

```text
Calories
Protein
Carbs
Fat
```

LLM tidak menjadi sumber kebenaran utama untuk kalkulasi matematika.

---

# 8. Scheduler

Scheduler bertanggung jawab mengirim reminder.

```text
Scheduler
    ↓
Check Meal Schedule
    ↓
Is reminder due?
    ↓
YES
    ↓
Get User Context
    ↓
AI Service
    ↓
Generate Message
    ↓
Telegram
```

Untuk MVP:

```text
APScheduler
```

dapat digunakan.

---

# 9. Reminder Flow

```text
Every minute
     ↓
Scheduler
     ↓
Query active schedules
     ↓
Check current timezone
     ↓
Find due reminders
     ↓
Create reminder context
     ↓
LLM
     ↓
Telegram Bot
     ↓
User
```

---

# 10. Food Logging Flow

```text
User:
"Aku makan nasi 200g dan ayam 150g"
             │
             ▼
        Telegram
             │
             ▼
        AI / Parser
             │
             ▼
       Structured Food
             │
             ▼
     Nutrition Service
             │
             ▼
       Calculate macros
             │
             ▼
          SQLite
             │
             ▼
        AI Response
             │
             ▼
          Telegram
```

---

# 11. AI Context Flow

LLM tidak diberikan seluruh database.

Context builder memilih informasi yang relevan.

```text
User Message
      │
      ▼
Context Builder
      │
      ├── User Profile
      ├── Diet Goal
      ├── Daily Target
      ├── Today's Food
      ├── Today's Progress
      ├── Meal Schedule
      └── Recent Conversation
      │
      ▼
      LLM
      │
      ▼
   Response
```

---

# 12. Google Integration — Future

Google integration menggunakan OAuth 2.0.

```text
User
 │
 ▼
Telegram
 │
 ▼
"Connect Google"
 │
 ▼
OAuth Authorization
 │
 ▼
Google
 │
 ▼
Access Token
 │
 ▼
Backend
```

Token disimpan secara aman.

---

# 13. Google Calendar

Future architecture:

```text
Diet Schedule
      │
      ▼
Calendar Service
      │
      ▼
Google Calendar API
      │
      ▼
Calendar Event
```

Contoh:

```text
Breakfast
07:00
Recurring Daily
```

Calendar menjadi external scheduling ecosystem.

---

# 14. Google Tasks

Tasks digunakan untuk habit/checklist.

```text
Drink Water
Take Snack
Exercise
Meal Preparation
```

Architecture:

```text
Diet Service
     ↓
Google Tasks Service
     ↓
Google Tasks API
```

---

# 15. WhatsApp Integration — Future

Channel abstraction:

```text
                Application
                     │
              Message Service
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   Telegram Adapter      WhatsApp Adapter
          │                     │
          ▼                     ▼
      Telegram API         WhatsApp API
```

Business logic tidak boleh bergantung langsung pada Telegram.

---

# 16. LangGraph — Future

LangGraph belum diperlukan untuk MVP.

Ketika workflow mulai kompleks:

```text
User Message
     ↓
Intent Agent
     ↓
Context Agent
     ↓
Diet Agent
     ↓
Nutrition Tool
     ↓
Google Calendar Tool
     ↓
Response Agent
```

LangGraph menjadi orchestration layer.

---

# 17. Production Architecture

Ketika sudah membutuhkan 24/7 deployment:

```text
                    Internet
                       │
                       ▼
                    Nginx
                       │
                       ▼
                  FastAPI App
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Worker       PostgreSQL    Redis
          │
          ▼
       Scheduler
          │
    ┌─────┴─────┐
    ▼           ▼
 Telegram     WhatsApp
```

Deployment:

```text
GitHub
   ↓
CI/CD
   ↓
Docker
   ↓
VPS
```

---

# 18. Design Principles

### Separation of concerns

Telegram handler tidak menghitung nutrisi.

Nutrition service tidak mengirim Telegram.

AI service tidak mengakses database secara sembarangan.

### Provider abstraction

LLM provider dapat diganti.

### Channel abstraction

Telegram dan WhatsApp dapat menggunakan business logic yang sama.

### Deterministic calculation

Nutrition calculation dilakukan oleh code/database.

### AI for language

LLM digunakan untuk:

* natural language,
* suggestions,
* conversational response,
* personality,
* summarization.

---

# 19. MVP Dependency Flow

```text
Telegram
   ↓
Handler
   ↓
Service
   ↓
Database
```

AI:

```text
Service
   ↓
Context Builder
   ↓
AI Service
   ↓
LLM
```

Reminder:

```text
Scheduler
   ↓
Reminder Service
   ↓
AI Service
   ↓
Telegram
```

Nutrition:

```text
Food Input
   ↓
Parser
   ↓
Nutrition Engine
   ↓
Database
```
