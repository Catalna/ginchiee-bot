-- ============================================================
-- Ginchiee Bot — PostgreSQL Schema
-- Managed by the versioned migrations in database/migrations/.
-- This file is the initial migration (0001_initial.sql) and is
-- applied automatically on startup.
-- ============================================================

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,          -- Telegram user_id
    name        TEXT        NOT NULL,
    username    TEXT,
    timezone    TEXT        NOT NULL DEFAULT 'Asia/Jakarta',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Diet Profiles
CREATE TABLE IF NOT EXISTS diet_profiles (
    id             INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        BIGINT  NOT NULL UNIQUE,
    age            INTEGER NOT NULL,
    gender         TEXT    NOT NULL CHECK (gender IN ('male', 'female')),
    height_cm      DOUBLE PRECISION NOT NULL,
    weight_kg      DOUBLE PRECISION NOT NULL,
    goal           TEXT    NOT NULL CHECK (goal IN ('LOSE_WEIGHT', 'MAINTAIN_WEIGHT', 'GAIN_WEIGHT')),
    activity_level TEXT    NOT NULL CHECK (activity_level IN ('SEDENTARY', 'LIGHT', 'MODERATE', 'ACTIVE', 'VERY_ACTIVE')),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_diet_profiles_user
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Meal Schedules (used by the reminder scheduler)
CREATE TABLE IF NOT EXISTS meal_schedules (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    meal_type  TEXT   NOT NULL CHECK (meal_type IN ('breakfast', 'lunch', 'snack', 'dinner')),
    time       TEXT   NOT NULL,          -- HH:MM, local to the user's timezone
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (user_id, meal_type),
    CONSTRAINT fk_meal_schedules_user
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Food Logs
CREATE TABLE IF NOT EXISTS food_logs (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    date       DATE   NOT NULL,
    meal_type  TEXT,
    food_name  TEXT   NOT NULL,
    amount_g   DOUBLE PRECISION NOT NULL DEFAULT 0,
    calories   DOUBLE PRECISION NOT NULL DEFAULT 0,
    protein_g  DOUBLE PRECISION NOT NULL DEFAULT 0,
    carbs_g    DOUBLE PRECISION NOT NULL DEFAULT 0,
    fat_g      DOUBLE PRECISION NOT NULL DEFAULT 0,
    logged_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_food_logs_user
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Conversation History
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    BIGINT NOT NULL,
    role       TEXT   NOT NULL CHECK (role IN ('user', 'model')),
    content    TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_conversations_user
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- User Activities (replaces the old Google Calendar integration)
CREATE TABLE IF NOT EXISTS user_activities (
    id           INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    title        TEXT   NOT NULL,
    description  TEXT,
    activity_dt  TIMESTAMPTZ NOT NULL,
    remind_mins  INTEGER NOT NULL DEFAULT 30,
    reminded     BOOLEAN NOT NULL DEFAULT FALSE,
    done         BOOLEAN NOT NULL DEFAULT FALSE,
    done_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_user_activities_user
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_food_logs_user_date  ON food_logs(user_id, date);
CREATE INDEX IF NOT EXISTS idx_conversations_user   ON conversations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_meal_schedules_user  ON meal_schedules(user_id);
CREATE INDEX IF NOT EXISTS idx_user_activities_user ON user_activities(user_id, activity_dt);

-- Partial index: the scheduler only ever looks for activities still waiting
-- for a reminder, which is a tiny fraction of the table.
CREATE INDEX IF NOT EXISTS idx_user_activities_pending
    ON user_activities(user_id)
    WHERE reminded = FALSE;
