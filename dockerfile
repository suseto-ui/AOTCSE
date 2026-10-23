FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p input_media output_media webhook_dumps models

# Klíčové pro správné fungování importů (např. from src.config import settings)
ENV PYTHONPATH=/app

EXPOSE 8501

# Spuštění aplikace z nové složky src/ui/
CMD ["streamlit", "run", "src/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--theme.base=dark"]