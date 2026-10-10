# Konfigurasi

Semua konfigurasi Ginchiee dibaca dari **environment variables**. Tidak ada
nilai rahasia yang boleh masuk ke git atau ke Docker image.

---

## Di mana memasukkan nilai

| Lingkungan | Cara |
|---|---|
| **Laptop / lokal** | file `.env` di root project (sudah masuk `.gitignore`) |
| **Hugging Face Space** | **Settings → Variables and secrets → Secrets** |
| **Docker** | `docker run -e KEY=value …` |
| **GitHub Actions** | *Settings → Secrets and variables → Actions* |

> **Penting:** file `.env` **tidak pernah terkirim ke Hugging Face**.
> `.env` masuk `.gitignore` (baris 19) dan `.dockerignore`, sehingga hanya
> `.env.example` yang ada di repository. Space membangun dari repository,
> jadi kredensial di sana **wajib** diletakkan di *Space Secrets*.
>
> Bukti: bot yang berjalan di Space sekarang sudah menerima
> `TELEGRAM_BOT_TOKEN` dan `GEMINI_API_KEY` dari Secrets — kalau `.env` yang
> dipakai, bot mustahil bisa jalan sama sekali karena `.env` tidak ada di sana.

---

## Variabel

### Wajib

Bot **tidak akan start** kalau salah satu ini kosong (fail-fast di
`config/settings.py`, bukan diam-diam pakai nilai default yang salah).

| Key | Keterangan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token dari [@BotFather](https://t.me/BotFather) |
| `GEMINI_API_KEY` | API key dari [Google AI Studio](https://aistudio.google.com) |
| `DATABASE_URL` | Connection string PostgreSQL |

`DATABASE_URL` menerima alias `BOT_TOKEN` (untuk Telegram) dan `GEMINI_KEY`
(untuk Gemini), tapi `TELEGRAM_BOT_TOKEN` dan `GEMINI_API_KEY` lebih disarankan.

### Opsional

| Key | Default | Keterangan |
|---|---|---|
| `GEMINI_MODEL` | `gemini-flash-lite-latest` | Model Gemini |
| `AI_TEMPERATURE` | `0.95` | Makin tinggi = makin ekspresif (0.0–2.0) |
| `AI_TOP_P` | `0.95` | Nucleus sampling |
| `AI_MAX_OUTPUT_TOKENS` | `220` | Batas panjang balasan — menjaga Ginchiee tetap singkat |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ALLOWED_USER_IDS` | *(kosong = semua orang)* | Batasi bot ke user id Telegram tertentu, dipisah koma |
| `PORT` | `7860` | Port health-check HTTP |

---

## `DATABASE_URL`

Format:

```
postgresql://USER:PASSWORD@HOST:PORT/DBNAME
```

**Supabase** — ambil dari *Dashboard → Connect → tab Session pooler (port 5432)*:

```
postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[region].pooler.supabase.com:5432/postgres
```

**Postgres lokal:**

```
postgresql://ginchiee:password@127.0.0.1:5432/ginchiee
```

### Catatan Supabase

- Tambahkan `?sslmode=require` di akhir **bila** muncul error
  *"SSL connection required"* (kadang perlu saat dideploy, kadang tidak).
- Pakai **Session pooler**, bukan Transaction pooler — asyncpg memakai
  *prepared statement*, dan pooler mode Transaction tidak mendukung itu.
- Password database jangan pernah masuk ke file lain atau ke git.

---

## Migrasi database

Skema dibuat otomatis saat bot start (`database/migrations.py`), jadi tidak ada
langkah `psql` manual:

1. File `database/migrations/NNN_*.sql` dijalankan berurutan.
2. Setiap versi yang sudah diterapkan dicatat di tabel `schema_migrations`
   (dengan checksum) sehingga aman dijalankan setiap boot.
3. Mengubah file migrasi yang **sudah** diterapkan akan membuat startup gagal
   dengan pesan jelas — tambahkan migrasi baru, jangan edit yang lama.

Memindahkan data lama dari SQLite ke PostgreSQL:

```bash
python scripts/migrate_sqlite_to_postgres.py --source data/nichie.db --dry-run
python scripts/migrate_sqlite_to_postgres.py --source data/nichie.db
```

---

## Keamanan

- `.env`, `data/`, dan seluruh file `*client_secret*.json` sudah dikecualikan
  oleh `.gitignore` **dan** `.dockerignore`.
- Kredensial tidak pernah dicetak ke log (password database di-mask pada log
  migrasi; `httpx`/`httpcore` di-silence agar token tidak muncul di URL).
- `.env` tidak ikut masuk image Docker, sehingga tidak bisa diekstrak dari
  image yang ter-push ke registry mana pun.
