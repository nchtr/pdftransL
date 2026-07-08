#Requires -Version 5.1
<#
pdftransl — быстрый старт на Windows (PowerShell 5.1+ / PowerShell 7).

    .\quickstart.ps1                  # окружение + зависимости + локальная настройка
    .\quickstart.ps1 -WithMinerU      # + MinerU (тяжёлый, ~несколько ГБ моделей)
    .\quickstart.ps1 -Model qwen2.5:7b  # какой моделью пользоваться в Ollama
    .\quickstart.ps1 -SkipFrontend    # не собирать React-интерфейс
    .\quickstart.ps1 -Yes             # отвечать «да» на все вопросы

Если PowerShell отказывается запускать скрипт:
    powershell -ExecutionPolicy Bypass -File .\quickstart.ps1
#>
param(
    [switch]$WithMinerU,
    [switch]$SkipFrontend,
    [switch]$Yes,
    [string]$Model = ""
)

$ErrorActionPreference = "Stop"

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Green }
function Note($msg) { Write-Host "    $msg" -ForegroundColor DarkGray }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "xx  $msg" -ForegroundColor Red; exit 1 }

function Ask($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "    $question [y/N]"
    return $answer -match "^[YyДд]"
}

Set-Location -Path $PSScriptRoot
Say "pdftransl quickstart (Windows)"

# ---------- python (только 3.12 или 3.13) -----------------------------------
# py-лаунчер ставится вместе с python.org-инсталлятором; пробуем его первым.
$python = $null
foreach ($candidate in @("py -3.13", "py -3.12", "python3.13", "python3.12", "python")) {
    $parts = $candidate -split " "
    $exe = $parts[0]
    $extra = @()
    if ($parts.Length -gt 1) { $extra = $parts[1..($parts.Length - 1)] }
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    $check = "import sys; sys.exit(0 if (3, 12) <= sys.version_info < (3, 14) else 1)"
    & $exe @extra -c $check 2>$null
    if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
}
if (-not $python) {
    Warn "Нужен Python 3.12 или 3.13 (другие версии не поддерживаются)."
    Warn "Установите с https://www.python.org/downloads/ (галочка 'Add to PATH')"
    Die  "или: winget install Python.Python.3.13"
}
$pyParts = $python -split " "
$pyExe = $pyParts[0]
$pyArgs = @()
if ($pyParts.Length -gt 1) { $pyArgs = $pyParts[1..($pyParts.Length - 1)] }
$version = & $pyExe @pyArgs --version
Say "Python: $version"

# ---------- виртуальное окружение --------------------------------------------
if (-not (Test-Path ".venv")) {
    Say "Создаю виртуальное окружение .venv"
    & $pyExe @pyArgs -m venv .venv
} else {
    Say "Виртуальное окружение .venv уже есть — использую его"
}
# Дальше зовём python из venv напрямую — активация не нужна.
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { Die "Не найден $venvPy — создание venv не удалось" }
& $venvPy -m pip install --quiet --upgrade pip

# ---------- зависимости --------------------------------------------------------
Say "Устанавливаю pdftransl со всеми локальными зависимостями"
Note "движок + PyMuPDF + экспорт (docx/pdf) + Django-бэкенд + телеграм-бот + тесты"
& $venvPy -m pip install --quiet -e ".[pymupdf,export,backend,bot,dev]"

if ($WithMinerU) {
    Say "Устанавливаю MinerU (это надолго: torch + модели ~ несколько ГБ)"
    Note "Без NVIDIA-GPU MinerU будет считать OCR на CPU — медленно."
    & $venvPy -m pip install "mineru[core]"
} else {
    Note "MinerU не ставлю (запустите с -WithMinerU, когда захотите"
    Note "максимальное качество распознавания формул). Пока парсит PyMuPDF."
}

# ---------- pandoc (лучший экспорт в DOCX/PDF) ---------------------------------
if (Get-Command pandoc -ErrorAction SilentlyContinue) {
    Say "pandoc найден — DOCX получит нативные формулы Word"
} elseif (Get-Command winget -ErrorAction SilentlyContinue) {
    if (Ask "pandoc не найден. Поставить через winget? (сильно улучшает DOCX/PDF)") {
        winget install --id JohnMacFarlane.Pandoc -e --accept-source-agreements --accept-package-agreements
    }
} else {
    Warn "pandoc не найден. Без него DOCX собирается через python-docx (формулы"
    Warn "рендерятся картинками). Установка: https://pandoc.org/installing.html"
}

# ---------- браузер для экспорта в PDF -----------------------------------------
# Playwright ставит пакет, но НЕ сам браузер — без него PDF не соберётся.
& $venvPy -c "import playwright" 2>$null
if ($LASTEXITCODE -eq 0) {
    $checkChromium = @"
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    exe = p.chromium.executable_path
sys.exit(0 if exe and Path(exe).exists() else 1)
"@
    & $venvPy -c $checkChromium 2>$null
    if ($LASTEXITCODE -eq 0) {
        Say "Chromium для PDF-экспорта уже установлен"
    } elseif (Ask "Скачать Chromium для экспорта в PDF? (~150 МБ, формулы в PDF)") {
        & $venvPy -m playwright install chromium
        if ($LASTEXITCODE -eq 0) { Say "Chromium установлен" }
        else { Warn "Не удалось скачать Chromium — PDF будет недоступен (docx/html работают)" }
    } else {
        Note "Без Chromium PDF-экспорт недоступен. Позже: python -m playwright install chromium"
    }
}

# ---------- фронтенд -------------------------------------------------------------
if ($SkipFrontend) {
    Note "Пропускаю сборку фронтенда (-SkipFrontend)"
} elseif (Get-Command npm -ErrorAction SilentlyContinue) {
    Say "Собираю React-интерфейс"
    Push-Location frontend
    try {
        npm install --no-audit --no-fund --silent
        npm run build --silent
    } finally { Pop-Location }
} else {
    Warn "npm не найден — веб-интерфейс не собран. Это не мешает CLI и боту."
    Warn "Node: winget install OpenJS.NodeJS.LTS (или https://nodejs.org), потом:"
    Warn "  cd frontend; npm install; npm run build"
}

# ---------- .env под локальную работу --------------------------------------------
if (-not (Test-Path ".env")) {
    Say "Создаю .env из .env.example"
    Copy-Item ".env.example" ".env"
} else {
    Say ".env уже есть — не трогаю ваши ключи"
}

# ---------- ollama -----------------------------------------------------------------
$totalGb = 8
try {
    $bytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
    $totalGb = [int][math]::Floor($bytes / 1GB)
} catch { }

if     ($totalGb -ge 48) { $recommended = "qwen2.5:32b" }
elseif ($totalGb -ge 24) { $recommended = "qwen2.5:14b" }
else                     { $recommended = "qwen2.5:7b" }

# опрашиваем Ollama: запущена ли и что уже скачано
$ollamaRunning = $false
$installedModels = @()
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    try {
        $tags = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 2
        $ollamaRunning = $true
        if ($tags.models) { $installedModels = @($tags.models | ForEach-Object { $_.name }) }
    } catch { }
}

# -Model имеет приоритет; иначе — предлагаем выбрать из уже скачанных
if ($Model) {
    Note "Модель задана флагом -Model: $Model"
} elseif ($installedModels.Count -gt 0) {
    Say "Ollama уже содержит модели — выберите, какой переводить:"
    for ($i = 0; $i -lt $installedModels.Count; $i++) {
        $mark = ""
        if ($installedModels[$i] -eq $recommended) { $mark = "  (рекомендуется под вашу память)" }
        Write-Host ("      {0}) {1}{2}" -f ($i + 1), $installedModels[$i], $mark)
    }
    $downloadIdx = $installedModels.Count + 1
    Write-Host ("      {0}) скачать {1}  (рекомендуется под ~{2} ГБ RAM)" -f $downloadIdx, $recommended, $totalGb)
    if ($Yes) {
        $Model = $installedModels | Where-Object { $_ -eq $recommended } | Select-Object -First 1
        if (-not $Model) { $Model = $installedModels[0] }
        Note "Автовыбор (-Yes): $Model"
    } else {
        $choice = Read-Host "    Номер [1]"
        if (-not $choice) { $choice = "1" }
        $num = 0
        if ([int]::TryParse($choice, [ref]$num) -and $num -eq $downloadIdx) {
            $Model = $recommended
        } elseif ($num -ge 1 -and $num -le $installedModels.Count) {
            $Model = $installedModels[$num - 1]
        } else {
            Warn "Не понял выбор — беру рекомендованную $recommended"
            $Model = $recommended
        }
    }
} else {
    $Model = $recommended
}
Say "Локальная модель: $Model (у машины ~$totalGb ГБ RAM)"

# оценка веса модели в RAM (МБ) — для memory guard (анти-OOM: не грузить
# модель, пока тяжёлый парсер не отдал память)
$modelMb = 6000
switch -Regex ($Model) {
    "72b|70b"  { $modelMb = 45000; break }
    "32b"      { $modelMb = 22000; break }
    "27b"      { $modelMb = 18000; break }
    "14b|12b"  { $modelMb = 11000; break }
    "7b|8b"    { $modelMb = 6000;  break }
    "3b|4b"    { $modelMb = 3500;  break }
    "1b|2b"    { $modelMb = 2000;  break }
}

$envText = Get-Content ".env" -Raw -ErrorAction SilentlyContinue
if ($envText -notmatch "(?m)^PDFTRANSL_PROVIDER=") {
    Say "Настраиваю .env на локальную Ollama"
    $block = @"

# --- добавлено quickstart.ps1: локальный перевод через Ollama ---
PDFTRANSL_PROVIDER=ollama
PDFTRANSL_MODEL=$Model
PDFTRANSL_BASE_URL=http://localhost:11434/v1

# Защита от OOM: перед загрузкой модели после тяжёлого парсера
# ждём, пока освободится ~размер модели (оценка под $Model).
PDFTRANSL_MEMORY_GUARD=true
PDFTRANSL_MIN_FREE_MEMORY_MB=$modelMb

# Перевод партиями: меньше пиковая нагрузка на память/потоки,
# частичный результат пишется на диск после каждой партии.
PDFTRANSL_TRANSLATE_BATCH_SIZE=40
"@
    Add-Content -Path ".env" -Value $block -Encoding UTF8
    Note "memory guard: жду $modelMb МБ свободной RAM перед загрузкой модели"
} else {
    Note "PDFTRANSL_PROVIDER уже настроен в .env — не переопределяю"
    Note "(чтобы сменить модель, поправьте PDFTRANSL_MODEL в .env)"
    if ($envText -notmatch "(?m)^PDFTRANSL_MIN_FREE_MEMORY_MB=") {
        Note "Совет: добавьте в .env защиту от OOM —"
        Note "  PDFTRANSL_MEMORY_GUARD=true"
        Note "  PDFTRANSL_MIN_FREE_MEMORY_MB=$modelMb   # ~размер вашей модели"
    }
}

if ($ollamaRunning) {
    Say "Сервер Ollama работает"
    if ($installedModels -contains $Model) {
        Note "Модель $Model уже скачана."
    } elseif (Ask "Скачать модель $Model сейчас? (несколько ГБ)") {
        ollama pull $Model
    }
} elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
    Warn "Ollama установлена, но сервер не отвечает. Запустите приложение Ollama"
    Warn "(или в терминале: ollama serve), затем: ollama pull $Model"
} else {
    Warn "Ollama не найдена. Как поставить (подробно — docs/OLLAMA.md):"
    Warn "  winget install Ollama.Ollama    # или скачайте с https://ollama.com"
    Warn "Потом: ollama pull $Model"
}

# ---------- база Django --------------------------------------------------------------
Say "Инициализирую базу данных Django"
Push-Location backend
try { & $venvPy manage.py migrate --run-syncdb | Out-Null } finally { Pop-Location }

# ---------- финал -----------------------------------------------------------------------
Write-Host ""
Say "Готово! Что дальше:"
Write-Host @"
    Активировать окружение (в каждом новом терминале):
        .venv\Scripts\Activate.ps1
        (если политика запрещает: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned)

    Перевести PDF из терминала:
        pdftransl translate статья.pdf --formats html,docx,pdf

    Запустить веб-интерфейс:
        cd backend; python manage.py runserver
        -> http://localhost:8000
        (выбор парсера и OCR-модели прямо в форме; прогресс по стадиям,
         оценка времени, пауза/продолжение задач)

    Запустить телеграм-бота (нужен TELEGRAM_BOT_TOKEN в .env):
        python -m bot

    Проверить, что всё живо:
        python -m pytest tests/ -q      # офлайн, без ключей
        pdftransl engines               # какие экспорт-движки доступны

    Если PDF — скан или «кракозябры»: поставьте мультимодальную модель
    (ollama pull qwen2.5-vl) — OCR включится сам; спец-OCR (DeepSeek-OCR
    через vLLM) — см. .env.example, блок «Спец-OCR модель».

    Гайд по Ollama и выбору модели: docs/OLLAMA.md
    Ключи облачных провайдеров (по желанию): отредактируйте .env
"@
