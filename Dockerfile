FROM python:3.11-slim

# No build-essential / sqlite3: nothing here compiles C extensions (asyncpg
# ships wheels), so the packages only inflate the image and its attack surface.
WORKDIR /app

# Copy and install dependencies first so layer caching works when only code
# changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy only the source. .dockerignore excludes .env, data/, .venv/, .git/ and
# the OAuth secrets, so credentials and user data cannot leak into the image.
COPY . .

# The app writes nothing to disk: all state lives in Postgres. Create a
# non-writable runtime directory only so a stray relative path cannot fail.
RUN mkdir -p /app/data \
    && chmod 755 /app/data

# Run as a non-root user.
RUN useradd --create-home --uid 10001 ginchiee \
    && chown -R ginchiee:ginchiee /app
USER ginchiee

# Hugging Face Spaces port. main.py serves a lightweight health endpoint on it.
ENV PORT=7860
EXPOSE 7860

# DATABASE_URL (plus TELEGRAM_BOT_TOKEN / GEMINI_API_KEY) must be supplied at
# runtime via Space Secrets or `docker run -e`, never baked into the image.
CMD ["python", "main.py"]
