#!/bin/bash
# start.sh — запуск Yandex Reviews Scraper + открытие браузера
# Использование: ./start.sh  (порт берётся из .env APP_PORT или 8001)

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Порт из .env
PORT=$(grep -E "^APP_PORT=" .env 2>/dev/null | cut -d= -f2 | tr -d '\r' || echo 8001)
PORT=${PORT:-8001}
HOST=$(grep -E "^APP_HOST=" .env 2>/dev/null | cut -d= -f2 | tr -d '\r' || echo 127.0.0.1)
HOST=${HOST:-127.0.0.1}
URL="http://${HOST}:${PORT}"

# Проверка venv
if [ ! -f ".venv/bin/python" ]; then
  echo "❌ .venv не найден. Сначала: /opt/homebrew/bin/python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# Если уже слушает — не запускаем второй раз
if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "⚠️  Порт $PORT уже занят (PID $(lsof -ti :"$PORT" | tr '\n' ' ')). Открываю $URL"
else
  echo "▶️  Запускаю сервис на $URL ..."
  mkdir -p logs
  nohup .venv/bin/python -m app.main > logs/app.log 2>&1 &
  PID=$!
  echo "   PID $PID, лог logs/app.log"
  # Ждём /health до 20с
  for i in $(seq 1 20); do
    if curl -sf "$URL/health" >/dev/null 2>&1; then
      echo "✅ Сервис поднялся"
      break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
      echo "⚠️  /health не ответил за 20с, смотри logs/app.log"
    fi
  done
fi

# Открыть в браузере (macOS open, Linux xdg-open)
echo "🌐 Открываю $URL"
if command -v open >/dev/null 2>&1; then
  open "$URL" 2>/dev/null || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" 2>/dev/null || true
else
  echo "Открой вручную: $URL"
fi

echo "Готово. Лог: tail -f logs/app.log"
