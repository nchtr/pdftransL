#!/usr/bin/env bash
#
# pdftransl — быстрый старт на macOS (работает и на Linux).
#
#   ./quickstart.sh                 # окружение + зависимости + локальная настройка
#   ./quickstart.sh --with-mineru   # + MinerU (тяжёлый, ~несколько ГБ моделей)
#   ./quickstart.sh --model qwen2.5:7b   # какой моделью пользоваться в Ollama
#   ./quickstart.sh --skip-frontend # не собирать React-интерфейс
#   ./quickstart.sh -y              # отвечать «да» на все вопросы
#
set -euo pipefail

# ---------- красивый вывод -------------------------------------------------
BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
say()  { printf "%s\n" "${GREEN}==>${RESET} ${BOLD}$*${RESET}"; }
note() { printf "%s\n" "${DIM}    $*${RESET}"; }
warn() { printf "%s\n" "${YELLOW}!!  $*${RESET}"; }
die()  { printf "%s\n" "${RED}xx  $*${RESET}"; exit 1; }

ask() {  # ask "вопрос" -> 0 если да
  [ "$ASSUME_YES" = "1" ] && return 0
  read -r -p "    $1 [y/N] " answer
  [[ "$answer" =~ ^[YyДд] ]]
}

# ---------- флаги ------------------------------------------------------------
WITH_MINERU=0; SKIP_FRONTEND=0; ASSUME_YES=0; OLLAMA_MODEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --with-mineru)   WITH_MINERU=1 ;;
    --skip-frontend) SKIP_FRONTEND=1 ;;
    --model)         OLLAMA_MODEL="${2:?--model требует имя}"; shift ;;
    -y|--yes)        ASSUME_YES=1 ;;
    -h|--help)       grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "Неизвестный флаг: $1 (см. --help)" ;;
  esac
  shift
done

cd "$(dirname "$0")"
OS="$(uname -s)"
say "pdftransl quickstart ($OS)"

# ---------- python (только 3.12 или 3.13) -----------------------------------
PYTHON=""
for candidate in python3.13 python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    # принимаем строго 3.12.x или 3.13.x
    if "$candidate" -c 'import sys; sys.exit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  warn "Нужен Python 3.12 или 3.13 (другие версии не поддерживаются)."
  if [ "$OS" = "Darwin" ]; then
    die "Установите: brew install python@3.13   (или python@3.12)"
  fi
  die "Установите Python 3.12/3.13 — например через pyenv или пакетный менеджер."
fi
say "Python: $($PYTHON --version)"

# ---------- виртуальное окружение -----------------------------------------
if [ ! -d .venv ]; then
  say "Создаю виртуальное окружение .venv"
  "$PYTHON" -m venv .venv
else
  say "Виртуальное окружение .venv уже есть — использую его"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

# ---------- зависимости -----------------------------------------------------
say "Устанавливаю pdftransl со всеми локальными зависимостями"
note "движок + PyMuPDF + экспорт (docx/pdf) + Django-бэкенд + телеграм-бот + тесты"
pip install --quiet -e ".[pymupdf,export,backend,bot,dev]"

if [ "$WITH_MINERU" = "1" ]; then
  say "Устанавливаю MinerU (это надолго: torch + модели ~ несколько ГБ)"
  note "На Apple Silicon MinerU работает через MPS; на Intel-маке будет медленно."
  pip install "mineru[core]"
else
  note "MinerU не ставлю (запустите с --with-mineru, когда захотите"
  note "максимальное качество распознавания формул). Пока парсит PyMuPDF."
fi

# ---------- pandoc (лучший экспорт в DOCX/PDF) -------------------------------
if command -v pandoc >/dev/null 2>&1; then
  say "pandoc найден — DOCX получит нативные формулы Word"
elif command -v brew >/dev/null 2>&1; then
  if ask "pandoc не найден. Поставить через brew? (сильно улучшает DOCX/PDF)"; then
    brew install pandoc
  fi
else
  warn "pandoc не найден. Без него DOCX собирается через python-docx (формулы"
  warn "рендерятся картинками). Установка: https://pandoc.org/installing.html"
fi

# ---------- браузер для экспорта в PDF --------------------------------------
# Playwright ставит пакет, но НЕ сам браузер — без него PDF не соберётся
# (это и был баг «PDF в списке есть, а файла нет»). Скачиваем Chromium.
if python -c "import playwright" 2>/dev/null; then
  if python -c "
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    exe = p.chromium.executable_path
sys.exit(0 if exe and Path(exe).exists() else 1)
" 2>/dev/null; then
    say "Chromium для PDF-экспорта уже установлен"
  elif ask "Скачать Chromium для экспорта в PDF? (~150 МБ, формулы в PDF)"; then
    python -m playwright install chromium && say "Chromium установлен" \
      || warn "Не удалось скачать Chromium — PDF будет недоступен (docx/html работают)"
  else
    note "Без Chromium PDF-экспорт недоступен. Позже: python -m playwright install chromium"
  fi
fi

# ---------- фронтенд ----------------------------------------------------------
if [ "$SKIP_FRONTEND" = "1" ]; then
  note "Пропускаю сборку фронтенда (--skip-frontend)"
elif command -v npm >/dev/null 2>&1; then
  say "Собираю React-интерфейс"
  (cd frontend && npm install --no-audit --no-fund --silent && npm run build --silent)
else
  warn "npm не найден — веб-интерфейс не собран. Это не мешает CLI и боту."
  warn "Node: brew install node (или https://nodejs.org), потом:"
  warn "  cd frontend && npm install && npm run build"
fi

# ---------- .env под локальную работу ---------------------------------------
if [ ! -f .env ]; then
  say "Создаю .env из .env.example"
  cp .env.example .env
else
  say ".env уже есть — не трогаю ваши ключи"
fi

# ---------- ollama ------------------------------------------------------------
TOTAL_GB=8
if [ "$OS" = "Darwin" ]; then
  TOTAL_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
elif [ -r /proc/meminfo ]; then
  TOTAL_GB=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
fi

# рекомендация по объёму памяти (используется, если ничего не выбрано)
if   [ "$TOTAL_GB" -ge 48 ]; then RECOMMENDED_MODEL="qwen2.5:32b"
elif [ "$TOTAL_GB" -ge 24 ]; then RECOMMENDED_MODEL="qwen2.5:14b"
else                              RECOMMENDED_MODEL="qwen2.5:7b"
fi

# опрашиваем Ollama: запущена ли и что уже скачано
OLLAMA_RUNNING=0
INSTALLED_MODELS=()
if command -v ollama >/dev/null 2>&1 \
   && curl -s --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; then
  OLLAMA_RUNNING=1
  while IFS= read -r line; do
    [ -n "$line" ] && INSTALLED_MODELS+=("$line")
  done < <(ollama list 2>/dev/null | awk 'NR>1 && $1 != "" {print $1}')
fi

# --model имеет приоритет; иначе — предлагаем выбрать из уже скачанных
if [ -n "$OLLAMA_MODEL" ]; then
  note "Модель задана флагом --model: $OLLAMA_MODEL"
elif [ "${#INSTALLED_MODELS[@]}" -gt 0 ]; then
  say "Ollama уже содержит модели — выберите, какой переводить:"
  idx=1
  for m in "${INSTALLED_MODELS[@]}"; do
    mark=""
    [ "$m" = "$RECOMMENDED_MODEL" ] && mark=" ${DIM}(рекомендуется под вашу память)${RESET}"
    printf "      %d) %s%s\n" "$idx" "$m" "$mark"
    idx=$((idx + 1))
  done
  printf "      %d) скачать %s ${DIM}(рекомендуется под ~%d ГБ RAM)${RESET}\n" \
         "$idx" "$RECOMMENDED_MODEL" "$TOTAL_GB"
  if [ "$ASSUME_YES" = "1" ]; then
    OLLAMA_MODEL=""
    for m in "${INSTALLED_MODELS[@]}"; do
      [ "$m" = "$RECOMMENDED_MODEL" ] && OLLAMA_MODEL="$m" && break
    done
    [ -z "$OLLAMA_MODEL" ] && OLLAMA_MODEL="${INSTALLED_MODELS[0]}"
    note "Автовыбор (-y): $OLLAMA_MODEL"
  else
    read -r -p "    Номер [1]: " choice
    choice="${choice:-1}"
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -eq "$idx" ]; then
      OLLAMA_MODEL="$RECOMMENDED_MODEL"
    elif [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -lt "$idx" ]; then
      OLLAMA_MODEL="${INSTALLED_MODELS[$((choice - 1))]}"
    else
      warn "Не понял выбор — беру рекомендованную $RECOMMENDED_MODEL"
      OLLAMA_MODEL="$RECOMMENDED_MODEL"
    fi
  fi
else
  # Ollama не запущена или без моделей — берём рекомендацию по памяти
  OLLAMA_MODEL="$RECOMMENDED_MODEL"
fi
say "Локальная модель: $OLLAMA_MODEL (у машины ~${TOTAL_GB} ГБ RAM)"

if ! grep -q '^PDFTRANSL_PROVIDER=' .env 2>/dev/null; then
  say "Настраиваю .env на локальную Ollama"
  {
    echo ""
    echo "# --- добавлено quickstart.sh: локальный перевод через Ollama ---"
    echo "PDFTRANSL_PROVIDER=ollama"
    echo "PDFTRANSL_MODEL=$OLLAMA_MODEL"
    echo "PDFTRANSL_BASE_URL=http://localhost:11434/v1"
  } >> .env
else
  note "PDFTRANSL_PROVIDER уже настроен в .env — не переопределяю"
  note "(чтобы сменить модель, поправьте PDFTRANSL_MODEL в .env)"
fi

if [ "$OLLAMA_RUNNING" = "1" ]; then
  say "Сервер Ollama работает"
  model_present=0
  for m in "${INSTALLED_MODELS[@]}"; do
    [ "$m" = "$OLLAMA_MODEL" ] && model_present=1 && break
  done
  if [ "$model_present" = "1" ]; then
    note "Модель $OLLAMA_MODEL уже скачана."
  elif ask "Скачать модель $OLLAMA_MODEL сейчас? (несколько ГБ)"; then
    ollama pull "$OLLAMA_MODEL"
  fi
elif command -v ollama >/dev/null 2>&1; then
  warn "Ollama установлена, но сервер не отвечает. Запустите: ollama serve"
  warn "(на macOS достаточно открыть приложение Ollama), затем:"
  warn "  ollama pull $OLLAMA_MODEL"
else
  warn "Ollama не найдена. Как поставить (подробно — docs/OLLAMA.md):"
  if [ "$OS" = "Darwin" ]; then
    warn "  brew install --cask ollama    # или скачайте с https://ollama.com"
  else
    warn "  curl -fsSL https://ollama.com/install.sh | sh"
  fi
  warn "Потом: ollama pull $OLLAMA_MODEL"
fi

# ---------- база Django --------------------------------------------------------
say "Инициализирую базу данных Django"
(cd backend && python manage.py migrate --run-syncdb >/dev/null)

# ---------- финал ---------------------------------------------------------------
echo ""
say "Готово! Что дальше:"
cat <<EOF
    ${BOLD}Активировать окружение${RESET} (в каждом новом терминале):
        source .venv/bin/activate

    ${BOLD}Перевести PDF из терминала${RESET}:
        pdftransl translate статья.pdf --formats html,docx,pdf

    ${BOLD}Запустить веб-интерфейс${RESET}:
        cd backend && python manage.py runserver
        → http://localhost:8000

    ${BOLD}Запустить телеграм-бота${RESET} (нужен TELEGRAM_BOT_TOKEN в .env):
        python -m bot

    ${BOLD}Проверить, что всё живо${RESET}:
        python -m pytest tests/ -q

    Гайд по Ollama и выбору модели: ${BOLD}docs/OLLAMA.md${RESET}
    Ключи облачных провайдеров (по желанию): отредактируйте ${BOLD}.env${RESET}
EOF
