# PRD — AI Diet Companion

**Version:** 0.1.0
**Status:** MVP
**Last Updated:** 2026-08-30

---

## 1. Product Overview

**AI Diet Companion** adalah bot personal yang membantu pengguna menjaga pola makan dan kebiasaan diet melalui reminder, food logging, estimasi nutrisi, serta percakapan natural menggunakan LLM.

Produk tidak dirancang sebagai sekadar alarm makan.

Tujuan utamanya adalah membuat pengguna merasa memiliki **personal diet companion** yang:

* mengingatkan waktu makan,
* membantu mencatat makanan,
* memperkirakan kalori dan makronutrisi,
* memberikan saran sederhana,
* memahami konteks pengguna,
* dapat diajak bercanda dan berbicara secara natural,
* serta membantu pengguna tetap konsisten.

### MVP Channel

MVP menggunakan:

> **Telegram Bot**

WhatsApp belum menjadi bagian dari MVP dan akan ditambahkan setelah core system stabil.

---

# 2. Problem Statement

Aplikasi diet pada umumnya berfokus pada tracking atau reminder yang bersifat statis.

Contoh:

> "It's time to eat."

Pendekatan tersebut kurang engaging karena pengguna hanya menerima notifikasi tanpa adanya interaksi atau konteks.

AI Diet Companion mencoba menyelesaikan masalah tersebut dengan menggabungkan:

```text
Reminder
+
Nutrition Tracking
+
LLM Conversation
+
Personalization
```

Contoh:

> 🍱 Lunch time!

berubah menjadi:

> 🍱 Bro, lunch time 😂
> Jangan sampai tugasmu selesai tapi makan siangmu masih pending.
>
> Hari ini protein kamu baru 42g dari target 130g.
> Kalau makan sekarang, coba pilih makanan yang punya protein cukup ya 💪

---

# 3. Goals

## Primary Goals

1. Membuat bot diet yang dapat berjalan secara otomatis.
2. Memberikan reminder berdasarkan jadwal pengguna.
3. Menyimpan profil dan data diet pengguna.
4. Mencatat makanan yang dikonsumsi.
5. Mengestimasi kalori dan makronutrisi.
6. Menggunakan LLM untuk conversational interaction.
7. Memberikan personalized suggestions.
8. Membuat fondasi yang mudah dikembangkan menjadi AI Agent.

## Secondary Goals

1. Integrasi Google Calendar.
2. Integrasi Google Tasks.
3. Integrasi WhatsApp.
4. Memory yang lebih kompleks.
5. Food recognition dari gambar.
6. Deployment 24/7.

---

# 4. Non-Goals for MVP

Fitur berikut tidak wajib pada MVP:

* Medical diagnosis.
* Prescription.
* Medical treatment.
* Real-time professional nutritionist.
* Advanced computer vision.
* Google Calendar integration.
* WhatsApp integration.
* Multi-agent LangGraph architecture.
* Web dashboard.
* Mobile application.

---

# 5. Target User

Target awal:

* mahasiswa,
* pekerja,
* pengguna yang sedang diet,
* pengguna yang ingin menjaga pola makan,
* pengguna yang membutuhkan reminder,
* pengguna yang menyukai conversational AI.

---

# 6. Core Features

## 6.1 User Registration

Command:

```text
/start
```

Bot membuat profile baru.

Data minimal:

```text
user_id
name
age
gender
height
weight
goal
activity_level
timezone
```

---

# 7. User Profile

User dapat memasukkan:

```text
Age
Gender
Height
Weight
Activity Level
Diet Goal
```

Diet goal:

```text
LOSE_WEIGHT
MAINTAIN_WEIGHT
GAIN_WEIGHT
```

Activity level:

```text
SEDENTARY
LIGHT
MODERATE
ACTIVE
VERY_ACTIVE
```

Profile digunakan untuk menghitung estimasi kebutuhan energi dan target makronutrisi.

---

# 8. Nutrition Target

System menghitung estimasi:

```text
Daily Calories
Protein
Carbohydrates
Fat
Water
```

Contoh:

```text
Daily Target

Calories     2,100 kcal
Protein        130 g
Carbs          230 g
Fat             70 g
Water          2.5 L
```

Nilai tersebut adalah **estimasi**, bukan diagnosis atau rekomendasi medis.

---

# 9. Meal Schedule

User dapat menentukan jadwal:

```text
Breakfast
Lunch
Snack
Dinner
```

Contoh:

```text
Breakfast → 07:00
Lunch     → 12:00
Snack     → 15:30
Dinner    → 19:00
```

Scheduler akan mengirim reminder.

---

# 10. Intelligent Reminder

Reminder tidak selalu berupa teks statis.

System memberikan context kepada LLM.

Input:

```text
meal = lunch
current_time = 12:00
user_name = ...
protein_consumed = 42
protein_target = 130
personality = funny
```

LLM menghasilkan pesan.

Contoh:

> 🍱 Lunch time!
>
> Protein kamu baru 42g hari ini.
> Jangan cuma makan kerupuk terus bilang sudah makan 😂

---

# 11. Food Logging

User dapat mencatat makanan menggunakan natural language.

Contoh:

> Aku makan nasi 200 gram, ayam 150 gram dan telur satu.

System mengekstrak:

```text
rice = 200g
chicken = 150g
egg = 1
```

Nutrition engine kemudian menghitung:

```text
Calories
Protein
Carbs
Fat
```

---

# 12. Daily Nutrition Tracking

System menyimpan konsumsi harian.

Contoh:

```text
TARGET

Calories   2100 kcal
Protein     130 g
Carbs       230 g
Fat          70 g


CONSUMED

Calories   1450 kcal
Protein      92 g
Carbs       160 g
Fat          48 g
```

System dapat menghitung progress:

```text
Calories: 69%
Protein: 71%
Carbs: 70%
Fat: 69%
```

---

# 13. AI Conversation

User dapat berbicara secara bebas.

Contoh:

> Aku lapar banget.

LLM mendapatkan context:

```text
User profile
+
Today's nutrition
+
Meal schedule
+
Recent food logs
+
Current time
```

Kemudian memberikan response.

Contoh:

> Waduh 😭
> Baru beberapa jam sejak lunch, tapi kalau memang lapar jangan dipaksa tahan.
>
> Coba snack yang ada protein seperti telur, yogurt, atau susu.
>
> Kita masih punya ruang sekitar 500 kcal hari ini.

---

# 14. Personality

Bot memiliki personality.

MVP default:

```text
PLAYFUL + SUPPORTIVE
```

Karakter:

* casual,
* friendly,
* sedikit bercanda,
* tidak menghakimi,
* tetap informatif,
* tidak berlebihan.

Contoh:

> Jangan khawatir kalau hari ini agak melenceng 😭
> Satu kali makan tidak menghancurkan progress kamu. Yang penting balik ke rutinitas.

---

# 15. AI Safety

LLM tidak boleh:

* mendiagnosis penyakit,
* memberikan prescription,
* mengklaim sebagai dokter,
* memberikan instruksi medis berisiko,
* menjamin hasil penurunan berat badan.

Jika user memberikan pertanyaan medis berisiko, bot harus memberikan batasan dan menyarankan konsultasi profesional.

---

# 16. Commands

MVP commands:

```text
/start
/profile
/setprofile
/today
/log
/food
/schedule
/reminders
/help
```

Natural language tetap menjadi interface utama.

---

# 17. Example User Journey

```text
/start
   ↓
Create Profile
   ↓
Set Goal
   ↓
Calculate Nutrition Target
   ↓
Set Meal Schedule
   ↓
Enable Reminder
   ↓
Daily Reminder
   ↓
User Logs Food
   ↓
Nutrition Calculation
   ↓
Daily Progress
   ↓
AI Suggestion
```

---

# 18. MVP Success Criteria

MVP dianggap berhasil apabila:

### Bot

* dapat menerima message Telegram,
* dapat membalas message,
* dapat menerima command,
* dapat menyimpan user.

### Reminder

* dapat membuat jadwal,
* dapat mengirim reminder otomatis,
* dapat menggunakan timezone user.

### Nutrition

* dapat menyimpan food log,
* dapat menghitung estimasi nutrisi,
* dapat menghitung daily progress.

### AI

* dapat memahami natural language,
* dapat menggunakan user context,
* dapat memberikan suggestion,
* dapat mempertahankan personality.

---

# 19. Future Roadmap

## Phase 1 — MVP

```text
Telegram
Python
SQLite
Scheduler
LLM
Nutrition Engine
```

## Phase 2 — Better AI

```text
Memory
Conversation Context
Better Nutrition Database
Personalization
```

## Phase 3 — Google Ecosystem

```text
Google OAuth
Google Calendar
Google Tasks
```

## Phase 4 — AI Agent

```text
LangGraph
Tool Calling
Multiple Specialized Agents
```

## Phase 5 — Multichannel

```text
Telegram
WhatsApp
Web
```

## Phase 6 — Vision

```text
Food Photo
   ↓
Vision Model
   ↓
Food Detection
   ↓
Nutrition Estimation
```

## Phase 7 — Production

```text
Docker
VPS / Cloud
PostgreSQL
Redis
Monitoring
HTTPS
```
