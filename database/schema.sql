-- ============================================================
-- Ginchiee Bot — Database Schema
-- SQLite
-- ============================================================

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,   -- Telegram user_id
    name        TEXT    NOT NULL,
    username    TEXT,
    timezone    TEXT    NOT NULL DEFAULT 'Asia/Jakarta',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Diet Profiles
CREATE TABLE IF NOT EXISTS diet_profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL UNIQUE,
    age            INTEGER NOT NULL,
    gender         TEXT    NOT NULL CHECK(gender IN ('male','female')),
    height_cm      REAL    NOT NULL,
    weight_kg      REAL    NOT NULL,
    goal           TEXT    NOT NULL CHECK(goal IN ('LOSE_WEIGHT','MAINTAIN_WEIGHT','GAIN_WEIGHT')),
    activity_level TEXT    NOT NULL CHECK(activity_level IN ('SEDENTARY','LIGHT','MODERATE','ACTIVE','VERY_ACTIVE')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Meal Schedules
CREATE TABLE IF NOT EXISTS meal_schedules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    meal_type  TEXT    NOT NULL CHECK(meal_type IN ('breakfast','lunch','snack','dinner')),
    time       TEXT    NOT NULL,   -- format HH:MM
    enabled    INTEGER NOT NULL DEFAULT 1,
    UNIQUE(user_id, meal_type),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Food Logs
CREATE TABLE IF NOT EXISTS food_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    date       TEXT    NOT NULL,   -- format YYYY-MM-DD
    meal_type  TEXT,
    food_name  TEXT    NOT NULL,
    amount_g   REAL    NOT NULL DEFAULT 0,
    calories   REAL    NOT NULL DEFAULT 0,
    protein_g  REAL    NOT NULL DEFAULT 0,
    carbs_g    REAL    NOT NULL DEFAULT 0,
    fat_g      REAL    NOT NULL DEFAULT 0,
    logged_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Daily Nutrition Totals (derived / cached)
CREATE TABLE IF NOT EXISTS daily_nutrition (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    date            TEXT    NOT NULL,
    total_calories  REAL    NOT NULL DEFAULT 0,
    total_protein_g REAL    NOT NULL DEFAULT 0,
    total_carbs_g   REAL    NOT NULL DEFAULT 0,
    total_fat_g     REAL    NOT NULL DEFAULT 0,
    UNIQUE(user_id, date),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Conversation History
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL CHECK(role IN ('user','model')),
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Google Calendar OAuth2 Tokens (per user)
CREATE TABLE IF NOT EXISTS google_calendar_tokens (
    user_id       INTEGER PRIMARY KEY,
    token         TEXT    NOT NULL,   -- JSON: access_token, refresh_token, expiry, etc.
    calendar_id   TEXT    NOT NULL DEFAULT 'primary',
    connected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_food_logs_user_date    ON food_logs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_daily_nutrition_user   ON daily_nutrition(user_id, date);
CREATE INDEX IF NOT EXISTS idx_conversations_user     ON conversations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_meal_schedules_user    ON meal_schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_calendar_tokens_user   ON google_calendar_tokens(user_id);
