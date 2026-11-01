FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py proxy.py ./
COPY web ./web
RUN useradd --uid 1000 --create-home dart && mkdir /data && chown dart:dart /data
USER dart
ENV DART_HOST=0.0.0.0 DART_DATA=/data
EXPOSE 8000
CMD ["python", "server.py"]
