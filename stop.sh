#!/bin/bash
# stop.sh — остановка Yandex Reviews Scraper на порту из .env

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PORT=$(grep -E "^APP_PORT=" .env 2>/dev/null | cut -d= -f2 | tr -d '\r' || echo 8001)
PORT=${PORT:-8001}

PIDS=$(lsof -ti :"$PORT" 2>/dev/null || true)
if [ -z "$PIDS" ]; then
  echo "ℹ️  На порту $PORT ничего не слушает"
  # на всякий случай ищем по имени
  PIDS2=$(pgrep -f "app.main" || true)
  if [ -n "$PIDS2" ]; then
    echo "Найден процесс app.main PID $PIDS2 — убиваю"
    kill $PIDS2 2>/dev/null || true
    sleep 1
  fi
  exit 0
fi

echo "⏹  Останавливаю сервис на порту $PORT (PID $PIDS)..."
for pid in $PIDS; do
  kill "$pid" 2>/dev/null || true
done
sleep 2
# если не умер — kill -9
STILL=$(lsof -ti :"$PORT" 2>/dev/null || true)
if [ -n "$STILL" ]; then
  echo "  Не остановился, делаю kill -9 $STILL"
  for pid in $STILL; do kill -9 "$pid" 2>/dev/null || true; done
  sleep 1
fi

if lsof -ti :"$PORT" >/dev/null 2>&1; then
  echo "❌ Порт $PORT всё ещё занят"
  lsof -i :"$PORT" 2>&1 | head -n 20
  exit 1
else
  echo "✅ Остановлен"
fi
