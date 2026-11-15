FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip install -U yt-dlp
COPY . .
ENV WHISPER_MODEL=base PORT=5000
EXPOSE 5000
# One worker: job state lives in memory. Threads handle concurrent polling.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-t", "600", "-b", "0.0.0.0:5000", "app:app"]
