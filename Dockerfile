FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Ensure data directory exists for SQLite storage
RUN mkdir -p /app/data && chmod -R 777 /app/data

# Hugging Face Spaces port
EXPOSE 7860

CMD ["python", "main.py"]
