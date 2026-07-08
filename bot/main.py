"""Телеграм-бот (aiogram v3): PDF на входе — перевод на выходе.

Пришлите боту PDF — он ответит переводом в выбранных через /settings
форматах, редактируя статус-сообщение по стадиям. Работает с движком
напрямую через service.process (статусы задач видны и из CLI/веба);
Django для бота не нужен, но SQLite-состояние (TM, глоссарий) общее.

Запуск:  TELEGRAM_BOT_TOKEN=... python -m bot
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from pdftransl.config import PROVIDER_PRESETS, PipelineConfig
from pdftransl.service import TranslationService
from pdftransl.translation.prompts import LANG_NAMES

from bot.settings_store import ChatSettings, SettingsStore

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("PDFTRANSL_DATA_DIR", "data"))
MAX_FILE_MB = 20  # Telegram Bot API download limit

router = Router()
store = SettingsStore(DATA_DIR / "bot_settings.json")

_LANG_CHOICES = ["ru", "en", "de", "fr", "es", "zh", "uk"]
_FORMAT_CHOICES = ["docx", "pdf", "html", "latex"]

HELP = (
    "<b>pdftransl</b> — перевод научных PDF с сохранением формул, таблиц и рисунков.\n\n"
    "Просто пришлите PDF-файл (до 20 МБ) — я верну перевод.\n\n"
    "Команды:\n"
    "/settings — язык перевода, форматы результата, опции\n"
    "/status — текущие настройки\n"
    "/help — эта справка"
)


def _pipeline_config(settings: ChatSettings) -> PipelineConfig:
    overrides: dict = {
        "source_lang": settings.source_lang,
        "target_lang": settings.target_lang,
        "review": settings.review,
        "bilingual": settings.bilingual,
        "export_formats": settings.formats,
        "db_path": str(DATA_DIR / "pdftransl.db"),
        "output_dir": str(DATA_DIR / "output"),
    }
    if settings.provider:
        overrides["provider"] = settings.provider
    return PipelineConfig.from_env(**overrides)


def _settings_keyboard(settings: ChatSettings):
    kb = InlineKeyboardBuilder()
    for lang in _LANG_CHOICES:
        mark = "✅ " if lang == settings.target_lang else ""
        kb.button(text=f"{mark}→ {lang}", callback_data=f"lang:{lang}")
    kb.adjust(4)
    fmt_kb = InlineKeyboardBuilder()
    for fmt in _FORMAT_CHOICES:
        mark = "✅ " if fmt in settings.formats else "○ "
        fmt_kb.button(text=f"{mark}{fmt}", callback_data=f"fmt:{fmt}")
    fmt_kb.adjust(3)
    opts_kb = InlineKeyboardBuilder()
    opts_kb.button(
        text=("✅ двуязычный режим" if settings.bilingual else "○ двуязычный режим"),
        callback_data="opt:bilingual",
    )
    opts_kb.button(
        text=("✅ LLM-ревью" if settings.review else "○ LLM-ревью"),
        callback_data="opt:review",
    )
    opts_kb.adjust(1)
    providers_kb = InlineKeyboardBuilder()
    for name in PROVIDER_PRESETS:
        mark = "✅ " if name == settings.provider else ""
        providers_kb.button(text=f"{mark}{name}", callback_data=f"prov:{name}")
    providers_kb.adjust(4)
    kb.attach(fmt_kb)
    kb.attach(opts_kb)
    kb.attach(providers_kb)
    return kb.as_markup()


def _settings_text(settings: ChatSettings) -> str:
    lang = LANG_NAMES.get(settings.target_lang, settings.target_lang)
    formats = ", ".join(["md"] + settings.formats)
    provider = settings.provider or "по умолчанию (сервер)"
    return (
        f"<b>Настройки</b>\n"
        f"Язык перевода: <b>{lang}</b>\n"
        f"Форматы: <b>{formats}</b>\n"
        f"Провайдер: <b>{provider}</b>\n"
        f"Двуязычный режим: <b>{'да' if settings.bilingual else 'нет'}</b>\n"
        f"LLM-ревью: <b>{'да' if settings.review else 'нет'}</b>"
    )


@router.message(Command("start", "help"))
async def cmd_start(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(_settings_text(store.get(message.chat.id)))


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    settings = store.get(message.chat.id)
    await message.answer(
        _settings_text(settings), reply_markup=_settings_keyboard(settings)
    )


@router.callback_query(F.data.startswith(("lang:", "fmt:", "opt:", "prov:")))
async def on_setting(callback: CallbackQuery) -> None:
    chat_id = callback.message.chat.id
    settings = store.get(chat_id)
    kind, value = callback.data.split(":", 1)
    if kind == "lang":
        settings = store.update(chat_id, target_lang=value)
    elif kind == "fmt":
        formats = list(settings.formats)
        if value in formats:
            formats.remove(value)
        else:
            formats.append(value)
        settings = store.update(chat_id, formats=formats)
    elif kind == "opt":
        settings = store.update(chat_id, **{value: not getattr(settings, value)})
    elif kind == "prov":
        new = "" if settings.provider == value else value
        settings = store.update(chat_id, provider=new)
    await callback.message.edit_text(
        _settings_text(settings), reply_markup=_settings_keyboard(settings)
    )
    await callback.answer()


@router.message(F.document)
async def on_document(message: Message, bot: Bot) -> None:
    document = message.document
    name = document.file_name or "document.pdf"
    if not name.lower().endswith(".pdf"):
        await message.reply("Пришлите файл в формате PDF.")
        return
    if document.file_size and document.file_size > MAX_FILE_MB * 1024 * 1024:
        await message.reply(f"Файл больше {MAX_FILE_MB} МБ — это лимит Telegram для ботов.")
        return

    settings = store.get(message.chat.id)
    status = await message.reply("⏳ Скачиваю файл…")

    with tempfile.TemporaryDirectory(prefix="pdftransl_bot_") as tmp:
        pdf_path = Path(tmp) / name
        await bot.download(document, destination=pdf_path)

        loop = asyncio.get_running_loop()
        last: dict = {"text": ""}

        def on_stage(stage: str, progress: float) -> None:
            text = f"⚙️ {stage} — {progress:.0%}"
            if text != last["text"]:
                last["text"] = text
                asyncio.run_coroutine_threadsafe(
                    _safe_edit(status, text), loop
                )

        def run():
            # service.process (not pipeline.run directly) keeps the shared
            # job repository honest: status/progress get persisted, so
            # `pdftransl jobs` doesn't show bot jobs stuck as "queued".
            service = TranslationService(_pipeline_config(settings))
            job_id = service.submit(str(pdf_path))
            return service.process(job_id, on_stage=on_stage)

        try:
            result = await asyncio.to_thread(run)
        except Exception as exc:  # noqa: BLE001 - report any failure to the user
            logger.exception("Bot translation failed")
            await _safe_edit(status, f"❌ Ошибка: {exc}")
            return

        if result.status == "failed":
            await _safe_edit(status, f"❌ Ошибка: {result.error}")
            return

        report = result.report or {}
        summary = (
            f"✅ Готово ({result.status}). Сегментов: "
            f"{report.get('segments_translated', '?')}, "
            f"проблемных: {report.get('segments_failed', 0)}"
        )
        await _safe_edit(status, summary)

        outputs: list[Path] = []
        if result.output_markdown_path:
            outputs.append(Path(result.output_markdown_path))
        for fmt in settings.formats:
            path = (result.exports or {}).get(fmt)
            if path:
                outputs.append(Path(path))
        if report.get("bilingual_markdown"):
            outputs.append(Path(report["bilingual_markdown"]))

        for path in outputs:
            if path.exists():
                await message.answer_document(FSInputFile(path))
        missing = [
            f"{fmt}: {reason}"
            for fmt, reason in (report.get("export_engines") or {}).items()
            if (result.exports or {}).get(fmt) is None and "unavailable" in str(reason)
        ]
        if missing:
            await message.answer(
                "⚠️ Часть форматов не собрана:\n" + "\n".join(missing)
            )


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception:  # message unchanged / rate limit — not critical
        pass


@router.message(F.text)
async def on_text(message: Message) -> None:
    await message.answer("Пришлите PDF-файл научной статьи или /help.")


def main() -> None:
    from pdftransl.logging_setup import setup_logging

    setup_logging()   # PDFTRANSL_LOG_LEVEL / PDFTRANSL_LOG_FILE
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN")
    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    asyncio.run(dispatcher.start_polling(bot))


if __name__ == "__main__":
    main()
