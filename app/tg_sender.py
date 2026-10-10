import asyncio
import io
import logging
from typing import Sequence

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    ReplyParameters,
)
from telegram.constants import ParseMode, PollLimit
from telegram.error import RetryAfter, TimedOut
from telegram.request import HTTPXRequest

log = logging.getLogger(__name__)

TG_MAX_LENGTH = 4096
TG_CAPTION_MAX = 1024
MAX_RETRIES = 3


def reply_keyboard(max_chat_id) -> InlineKeyboardMarkup:
    """Build an inline keyboard with a single 'Reply' button."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💬 Ответить", callback_data=f"reply:{max_chat_id}")
    ]])

def _make_reply_parameters(reply_to_message_id: int | None) -> ReplyParameters | None:
    if reply_to_message_id:
        return ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
    return None


class TelegramSender:
    def __init__(
            self,
            token: str,
            chat_id: str,
            proxy_url: str | None = None,
            read_timeout: int | None = None,
            write_timeout: int | None = None,
            media_write_timeout: int | None = None,
            base_url: str | None = None,
    ):
        request = HTTPXRequest(proxy=proxy_url, read_timeout=read_timeout, write_timeout=write_timeout, media_write_timeout=media_write_timeout)
        if base_url:
            self._bot = Bot(token=token, request=request, base_url=base_url+"/bot", base_file_url=base_url+"/file/bot")
        else:
            self._bot = Bot(token=token, request=request)
        self._chat_id = chat_id
        self.last_sent_message: Message | None = None

    @property
    def bot(self) -> Bot:
        return self._bot

    async def start(self):
        await self._bot.initialize()
        me = await self._bot.get_me()
        log.info("Telegram bot ready: @%s", me.username)

    async def stop(self):
        await self._bot.shutdown()

    def _truncate(self, text: str, limit: int, suffix: str = "…") -> str:
        if len(text) > limit:
            return text[: limit - len(suffix)] + suffix
        return text

    def _truncate_caption(self, text: str) -> str:
        if len(text) > TG_CAPTION_MAX:
            return text[: TG_CAPTION_MAX - 20] + "\n\n[...усечено]"
        return text

    async def _retry(self, coro_factory):
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                res = await coro_factory()
                if hasattr(res, "message_id"):
                    self.last_sent_message = res
                return res
            except RetryAfter as e:
                log.warning("Telegram rate limit, retry after %ss", e.retry_after)
                await asyncio.sleep(e.retry_after)
            except TimedOut:
                log.warning("Telegram timeout (attempt %d/%d). Consider increasing TG_ timeouts settings", attempt, MAX_RETRIES)
                await asyncio.sleep(2 * attempt)
            except Exception:
                log.exception("Failed to send to Telegram (attempt %d/%d)", attempt, MAX_RETRIES)
                if attempt == MAX_RETRIES:
                    return None
                await asyncio.sleep(2 * attempt)
        return None

    @property
    def chat_id(self) -> str:
        return self._chat_id

    async def create_forum_topic(
        self,
        name: str,
        icon_color: int | None = None,
        icon_custom_emoji_id: str | None = None,
    ) -> Any:
        return await self._retry(
            lambda: self._bot.create_forum_topic(
                chat_id=self._chat_id,
                name=name,
                icon_color=icon_color,
                icon_custom_emoji_id=icon_custom_emoji_id,
            )
        )

    async def send(
        self,
        text: str,
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        if not text:
            return None

        if len(text) > TG_MAX_LENGTH:
            text = text[: TG_MAX_LENGTH - 20] + "\n\n[...усечено]"

        return await self._retry(
            lambda: self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )

    async def send_photo(
        self,
        data: bytes,
        caption: str = "",
        filename: str = "photo.jpg",
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        caption = self._truncate_caption(caption)
        return await self._retry(
            lambda: self._bot.send_photo(
                chat_id=self._chat_id,
                photo=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )

    async def send_document(
        self,
        data: bytes,
        caption: str = "",
        filename: str = "file",
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        caption = self._truncate_caption(caption)
        return await self._retry(
            lambda: self._bot.send_document(
                chat_id=self._chat_id,
                document=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )

    async def send_video(
        self,
        data: bytes,
        caption: str = "",
        filename: str = "video.mp4",
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        caption = self._truncate_caption(caption)
        result = await self._retry(
            lambda: self._bot.send_video(
                chat_id=self._chat_id,
                video=InputFile(io.BytesIO(data), filename=filename),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )
        return result is not None

    async def send_voice(
        self,
        data: bytes,
        caption: str = "",
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        caption = self._truncate_caption(caption)
        result = await self._retry(
            lambda: self._bot.send_voice(
                chat_id=self._chat_id,
                voice=InputFile(io.BytesIO(data), filename="voice.ogg"),
                caption=caption or None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )
        if result is None:
            log.info("send_voice failed, falling back to send_audio")
            result = await self._retry(
                lambda: self._bot.send_audio(
                    chat_id=self._chat_id,
                    audio=InputFile(io.BytesIO(data), filename="audio.m4a"),
                    caption=caption or None,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    message_thread_id=thread_id,
                    reply_parameters=_make_reply_parameters(reply_to_message_id),
                )
            )
        return result

    async def send_sticker(
        self,
        data: bytes,
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        return await self._retry(
            lambda: self._bot.send_sticker(
                chat_id=self._chat_id,
                sticker=InputFile(io.BytesIO(data), filename="sticker.webp"),
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )

    async def send_poll(
        self,
        question: str,
        options: Sequence[str],
        reply_markup=None,
        thread_id: int | None = None,
        reply_to_message_id: int | None = None,
    ) -> Message | None:
        """Send a poll. Caller must ensure at least PollLimit.MIN_OPTION_NUMBER non-empty options."""
        question = self._truncate(question, PollLimit.MAX_QUESTION_LENGTH)
        options = [
            self._truncate(opt, PollLimit.MAX_OPTION_LENGTH)
            for opt in options[: PollLimit.MAX_OPTION_NUMBER]
        ]
        return await self._retry(
            lambda: self._bot.send_poll(
                chat_id=self._chat_id,
                question=question,
                options=options,
                question_parse_mode=ParseMode.HTML,
                is_anonymous=False,
                allows_multiple_answers=False,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
                reply_parameters=_make_reply_parameters(reply_to_message_id),
            )
        )

    async def set_reaction(self, message_id: int, emoji: str = "👍") -> bool:
        """Set an emoji reaction on a message in the target chat."""
        try:
            res = await self._retry(
                lambda: self._bot.set_message_reaction(
                    chat_id=self._chat_id,
                    message_id=message_id,
                    reaction=emoji,
                )
            )
            return bool(res)
        except Exception as e:
            log.debug("Failed to set reaction %s on msg %s: %s", emoji, message_id, e)
            return False
