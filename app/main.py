import asyncio
import concurrent.futures
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Allow running as a script directly (python app/main.py)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from telegram import Update
from telegram.error import BadRequest, NetworkError

from app.config import load_settings
from app.history_syncer import HistorySyncer
from app.max_listener import create_max_client
from app.msg_mapper import MessageMapper
from app.tg_handler import build_tg_app
from app.tg_sender import TelegramSender
from app.topic_manager import TopicManager

threading.stack_size(524288)

log = logging.getLogger("max2tg")


def _log_polling_error(error: Exception) -> None:
    """Report a polling failure.

    A flaky proxy or network drop is routine - python-telegram-bot retries on its
    own - so those get one WARNING line, with the traceback kept for DEBUG. Anything
    else is a real bug and keeps its full trace at ERROR.

    NB: BadRequest subclasses NetworkError but is never transient, hence excluded.
    """
    if isinstance(error, NetworkError) and not isinstance(error, BadRequest):
        log.warning("Telegram polling error: %s: %s", type(error).__name__, error)
        log.debug("Telegram polling error detail", exc_info=error)
    else:
        log.error("Telegram polling error! %s", error, exc_info=error)


class _SyncExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that runs callables synchronously without spawning threads.

    Python 3.12 requires set_default_executor() to receive a ThreadPoolExecutor,
    so we subclass it and override submit() to bypass _adjust_thread_count().
    Used on low-resource servers where the OS cannot create new threads.
    DNS resolution (getaddrinfo) will block the event loop for a few ms,
    which is acceptable for a single-user forwarding bot.
    """

    def submit(self, fn, /, *args, **kwargs):
        f: concurrent.futures.Future = concurrent.futures.Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except Exception as exc:
            f.set_exception(exc)
        return f


async def main():
    loop = asyncio.get_running_loop()
    loop.set_default_executor(_SyncExecutor())

    settings = load_settings()

    level = logging.DEBUG if settings.debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    log_dir = os.environ.get("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=os.path.join(log_dir, "max2tg.log"),
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    logging.basicConfig(level=level, handlers=[console_handler, file_handler], force=True)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING if not settings.debug else logging.DEBUG)

    log.info("Debug mode: %s", "ON" if settings.debug else "OFF")

    if settings.tg_proxy:
        log.info("Using Telegram proxy: %s", settings.tg_proxy.split("@")[-1])
    if settings.tg_base_url:
        log.info("Using Telegram base url: %s", settings.tg_base_url)
    if settings.max_proxy:
        log.info("Using Max proxy: %s", settings.max_proxy.split("@")[-1])
    if settings.max_exclude_chat_ids:
        log.info("Excluding Max chats: %s", settings.max_exclude_chat_ids)

    sender = TelegramSender(
        settings.tg_bot_token,
        settings.tg_chat_id,
        proxy_url=settings.tg_proxy,
        read_timeout=settings.tg_read_timeout,
        write_timeout=settings.tg_write_timeout,
        media_write_timeout=settings.tg_media_write_timeout,
        base_url=settings.tg_base_url,
    )
    await sender.start()

    topic_manager = TopicManager(
        sender=sender,
        topics_file=settings.topics_file,
        enabled=settings.enable_topics,
    )
    if settings.enable_topics:
        log.info("Forum topics enabled (topics_file=%s)", settings.topics_file)

    syncer = HistorySyncer(
        client=None,
        resolver=None,
        forward_message_cb=None,
        seen_file=settings.seen_messages_file,
        poll_interval=settings.history_poll_interval,
        messages_count=settings.history_messages_count,
    )

    msg_map = MessageMapper(settings.messages_map_file)

    client = create_max_client(
        settings.max_token, settings.max_device_id, sender, settings.max_chat_ids,
        debug=settings.debug, reply_enabled=settings.reply_enabled, proxy_url=settings.max_proxy,
        exclude_chat_ids=settings.max_exclude_chat_ids, topic_manager=topic_manager,
        syncer=syncer, chats_count=settings.max_chats_count, msg_map=msg_map,
        show_reply_button=settings.show_reply_button,
    )

    tg_app = None
    if settings.reply_enabled:
        tg_app = build_tg_app(
            settings.tg_bot_token, client, settings.tg_chat_id,
            proxy_url=settings.tg_proxy, read_timeout=settings.tg_read_timeout,
            write_timeout=settings.tg_write_timeout, base_url=settings.tg_base_url,
            topic_manager=topic_manager, msg_map=msg_map,
            delivery_reaction=settings.delivery_reaction,
        )
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            error_callback=_log_polling_error
        )
        log.info("Telegram polling started (reply → Max enabled)")
    else:
        log.info("Reply to Max disabled (REPLY_ENABLED=false)")

    log.info("Starting Max listener...")
    try:
        await client.run()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        log.info("Shutting down...")
        if syncer:
            try:
                await syncer.stop()
            except Exception:
                log.exception("Error stopping syncer")
        if tg_app:
            try:
                if tg_app.updater and tg_app.updater.running:
                    await tg_app.updater.stop()
                if tg_app.running:
                    await tg_app.stop()
                await tg_app.shutdown()
            except Exception:
                log.exception("Error stopping tg_app")
        try:
            await client.stop()
        except Exception:
            log.exception("Error stopping client")
        try:
            await sender.stop()
        except Exception:
            log.exception("Error stopping sender")


if __name__ == "__main__":
    import signal

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(main())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, main_task.cancel)
        except (NotImplementedError, RuntimeError):
            pass

    try:
        loop.run_until_complete(main_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        if not main_task.done():
            main_task.cancel()
            try:
                loop.run_until_complete(main_task)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        log.info("Stopped.")
