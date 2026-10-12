import html
import logging
from typing import Any

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    MessageReactionHandler,
    filters,
)
import telegram.constants

from app.max_client import MaxClient

log = logging.getLogger(__name__)

PENDING_REPLY_KEY = "pending_reply_chat_id"
PENDING_REPLY_LABEL_KEY = "pending_reply_label"

_ALLOWED_CHAT_ID_KEY = "allowed_chat_id"


async def _on_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline 'Reply' button press."""
    query = update.callback_query

    allowed_chat_id = context.bot_data.get(_ALLOWED_CHAT_ID_KEY)
    if allowed_chat_id is not None and (
        update.effective_chat.id != allowed_chat_id
        and update.effective_user.id != allowed_chat_id
    ):
        await query.answer()
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("reply:"):
        return

    chat_id_str = data[len("reply:"):]
    try:
        max_chat_id = int(chat_id_str)
    except ValueError:
        max_chat_id = chat_id_str

    context.user_data[PENDING_REPLY_KEY] = max_chat_id

    source_text = query.message.text or query.message.caption or (query.message.poll.question if query.message.poll else "") or ""
    label = source_text.split("\n")[0] if source_text else str(max_chat_id)
    context.user_data[PENDING_REPLY_LABEL_KEY] = label
    addressee = ""
    if query.message.chat.type in (telegram.constants.ChatType.GROUP, telegram.constants.ChatType.SUPERGROUP):
        addressee = f" {html.escape(query.from_user.full_name)},"
    await query.message.reply_text(
        f"✏️{addressee} напишите ответ для <b>{html.escape(label)}</b> (ответом на оригинальное сообщение):\n"
        "<i>(или /cancel для отмены)</i>",
        parse_mode="HTML",
        disable_notification=True,
    )


async def _on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel pending reply."""
    if context.user_data.pop(PENDING_REPLY_KEY, None):
        context.user_data.pop(PENDING_REPLY_LABEL_KEY, None)
        await update.message.reply_text("❌ Ответ отменён.", disable_notification=True)
    else:
        await update.message.reply_text("Нет активного ответа для отмены.", disable_notification=True)


def _with_sender_prefix(message, text: str) -> tuple[str, list]:
    """In a group, prefix the text with who wrote it and mark the name bold for Max."""
    if message.chat.type not in [telegram.constants.ChatType.GROUP, telegram.constants.ChatType.SUPERGROUP]:
        return text, []
    name = message.from_user.full_name
    return f"👤 {name}:\n{text}", [{"type": "STRONG", "length": len(name) + 1, "from": 2}]


def _extract_media(message) -> tuple[object, str] | None:
    """Pick the attachment to forward from a Telegram message, with a filename for Max."""
    if message.photo:
        return message.photo[-1], "photo.jpg"
    if message.document:
        return message.document, message.document.file_name or "file"
    if message.video:
        return message.video, message.video.file_name or "video.mp4"
    if message.voice:
        return message.voice, "voice.ogg"
    if message.audio:
        return message.audio, message.audio.file_name or "audio.m4a"
    if message.animation:
        return message.animation, message.animation.file_name or "animation.mp4"
    return None


async def _on_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user's text as a reply to Max."""
    from_pending_reply = False
    max_chat_id = context.user_data.pop(PENDING_REPLY_KEY, None)
    label = context.user_data.pop(PENDING_REPLY_LABEL_KEY, None)
    if max_chat_id is not None:
        from_pending_reply = True

    topic_manager = context.bot_data.get("topic_manager")
    msg_map = context.bot_data.get("msg_map")
    delivery_reaction = context.bot_data.get("delivery_reaction", "👍")
    thread_id = update.effective_message.message_thread_id if update.effective_message else None

    if max_chat_id is None and topic_manager and thread_id:
        max_chat_id = topic_manager.get_chat_id_by_thread(thread_id)
        if max_chat_id:
            label = topic_manager.get_chat_name(max_chat_id)

    max_client: MaxClient | None = context.bot_data.get("max_client")
    if max_chat_id is None and max_client and len(max_client.chat_ids) == 1:
        max_chat_id = max_client.chat_ids[0]
        if topic_manager:
            label = topic_manager.get_chat_name(max_chat_id)

    if max_chat_id is None:
        return

    if not max_client:
        await update.message.reply_text("⚠️ Max клиент не подключён.", disable_notification=True)
        return

    # Check for reply-to message
    reply_to_max_id = None
    reply_msg = update.effective_message.reply_to_message if update.effective_message else None
    if reply_msg and msg_map:
        is_topic_root = (
            reply_msg.message_id == thread_id
            or getattr(reply_msg, "forum_topic_created", None) is not None
        )
        if not is_topic_root:
            reply_to_max_id = msg_map.get_max_id(reply_msg.message_id)

    text, elements = _with_sender_prefix(update.message, update.message.text)
    try:
        if reply_to_max_id is not None:
            resp = await max_client.send_message(max_chat_id, text, elements, reply_to_id=reply_to_max_id)
        else:
            resp = await max_client.send_message(max_chat_id, text, elements)
        if resp:
            if msg_map and isinstance(resp, dict):
                new_mid = (
                    resp.get("message", {}).get("id")
                    or resp.get("id")
                    or resp.get("messageId")
                )
                if new_mid and update.effective_message:
                    msg_map.record(update.effective_message.message_id, new_mid, chat_id=max_chat_id)

            reacted = False
            if update.effective_message and hasattr(update.effective_message, "set_reaction"):
                try:
                    res = await update.effective_message.set_reaction(delivery_reaction)
                    reacted = bool(res)
                except Exception as e:
                    log.debug("Failed to set reaction %s: %s", delivery_reaction, e)
                    if delivery_reaction != "👍":
                        try:
                            res = await update.effective_message.set_reaction("👍")
                            reacted = bool(res)
                        except Exception:
                            pass

            if not reacted and (from_pending_reply or not thread_id):
                safe_target = html.escape(str(label or max_chat_id))
                await update.message.reply_text(f"✅ Отправлено → <b>{safe_target}</b>", parse_mode="HTML", disable_notification=True)
        else:
            await update.message.reply_text("⚠️ Не удалось отправить сообщение в Max.", disable_notification=True)
    except Exception:
        log.exception("Failed to send reply to Max chat %s", max_chat_id)
        await update.message.reply_text("⚠️ Ошибка при отправке в Max.", disable_notification=True)


async def _on_media_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward user's photo/file/video/voice as a reply to Max."""
    from_pending_reply = False
    max_chat_id = context.user_data.pop(PENDING_REPLY_KEY, None)
    label = context.user_data.pop(PENDING_REPLY_LABEL_KEY, None)
    if max_chat_id is not None:
        from_pending_reply = True

    topic_manager = context.bot_data.get("topic_manager")
    msg_map = context.bot_data.get("msg_map")
    delivery_reaction = context.bot_data.get("delivery_reaction", "👍")
    thread_id = update.effective_message.message_thread_id if update.effective_message else None

    if max_chat_id is None and topic_manager and thread_id:
        max_chat_id = topic_manager.get_chat_id_by_thread(thread_id)
        if max_chat_id:
            label = topic_manager.get_chat_name(max_chat_id)

    max_client: MaxClient | None = context.bot_data.get("max_client")
    if max_chat_id is None and max_client and len(max_client.chat_ids) == 1:
        max_chat_id = max_client.chat_ids[0]
        if topic_manager:
            label = topic_manager.get_chat_name(max_chat_id)

    if max_chat_id is None:
        return

    if not max_client:
        await update.message.reply_text("⚠️ Max клиент не подключён.", disable_notification=True)
        return

    media = _extract_media(update.message)
    if media is None:
        await update.message.reply_text("⚠️ Этот тип вложения не поддерживается.", disable_notification=True)
        return
    obj, filename = media

    # Check for reply-to message
    reply_to_max_id = None
    reply_msg = update.effective_message.reply_to_message if update.effective_message else None
    if reply_msg and msg_map:
        is_topic_root = (
            reply_msg.message_id == thread_id
            or getattr(reply_msg, "forum_topic_created", None) is not None
        )
        if not is_topic_root:
            reply_to_max_id = msg_map.get_max_id(reply_msg.message_id)

    text, elements = _with_sender_prefix(update.message, update.message.caption or "")
    try:
        # download_as_bytearray keeps the file's URL (which embeds the bot token) out of Max.
        tg_file = await obj.get_file()
        data = bytes(await tg_file.download_as_bytearray())

        attach = await max_client.upload_file(data, filename)
        if not attach:
            await update.message.reply_text("⚠️ Не удалось загрузить файл в Max.", disable_notification=True)
            return

        if reply_to_max_id is not None:
            resp = await max_client.send_message(
                max_chat_id, text, elements, attaches=[attach], reply_to_id=reply_to_max_id
            )
        else:
            resp = await max_client.send_message(
                max_chat_id, text, elements, attaches=[attach]
            )
        if resp:
            if msg_map and isinstance(resp, dict):
                new_mid = (
                    resp.get("message", {}).get("id")
                    or resp.get("id")
                    or resp.get("messageId")
                )
                if new_mid and update.effective_message:
                    msg_map.record(update.effective_message.message_id, new_mid, chat_id=max_chat_id)

            reacted = False
            if update.effective_message and hasattr(update.effective_message, "set_reaction"):
                try:
                    res = await update.effective_message.set_reaction(delivery_reaction)
                    reacted = bool(res)
                except Exception as e:
                    log.debug("Failed to set reaction %s: %s", delivery_reaction, e)
                    if delivery_reaction != "👍":
                        try:
                            res = await update.effective_message.set_reaction("👍")
                            reacted = bool(res)
                        except Exception:
                            pass

            if not reacted and (from_pending_reply or not thread_id):
                safe_target = html.escape(str(label or max_chat_id))
                await update.message.reply_text(f"✅ Отправлено → <b>{safe_target}</b>", parse_mode="HTML", disable_notification=True)
        else:
            await update.message.reply_text("⚠️ Не удалось отправить сообщение в Max.", disable_notification=True)
    except Exception:
        log.exception("Failed to send file reply to Max chat %s", max_chat_id)
        await update.message.reply_text("⚠️ Ошибка при отправке файла в Max.", disable_notification=True)


async def _on_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a message from Max when user replies to it with /del or /delete."""
    reply_msg = update.effective_message.reply_to_message if update.effective_message else None
    if not reply_msg:
        await update.effective_message.reply_text(
            "ℹ️ Ответьте этой командой (/del) на сообщение, которое нужно удалить в Max.",
            disable_notification=True,
        )
        return

    msg_map = context.bot_data.get("msg_map")
    max_client: MaxClient | None = context.bot_data.get("max_client")
    if not max_client:
        await update.effective_message.reply_text("⚠️ Max клиент не подключён.", disable_notification=True)
        return

    max_msg_id = msg_map.get_max_id(reply_msg.message_id) if msg_map else None
    if not max_msg_id:
        await update.effective_message.reply_text("⚠️ Не удалось найти это сообщение в связке с Max.", disable_notification=True)
        return

    topic_manager = context.bot_data.get("topic_manager")
    thread_id = update.effective_message.message_thread_id if update.effective_message else None
    max_chat_id = msg_map.get_chat_id(reply_msg.message_id) if msg_map else None
    if max_chat_id is None and topic_manager and thread_id:
        max_chat_id = topic_manager.get_chat_id_by_thread(thread_id)
    if max_chat_id is None and len(max_client.chat_ids) == 1:
        max_chat_id = max_client.chat_ids[0]

    if max_chat_id is None:
        await update.effective_message.reply_text("⚠️ Не удалось определить чат Max для удаления.", disable_notification=True)
        return

    try:
        await max_client.delete_message(max_chat_id, max_msg_id)
        # Try to delete both the command message and the target message in TG
        try:
            await reply_msg.delete()
        except Exception:
            pass
        try:
            await update.effective_message.delete()
        except Exception:
            pass
        log.info("Deleted message %s in Max and TG via /del command", max_msg_id)
    except Exception as e:
        log.exception("Failed to delete message %s in Max: %s", max_msg_id, e)
        await update.effective_message.reply_text(f"❌ Ошибка удаления в Max: {e}", disable_notification=True)


async def _on_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete a message from Max and TG when user reacts with 🗑️ or ❌."""
    reaction = update.message_reaction
    if not reaction:
        return
    allowed_chat_id = context.bot_data.get(_ALLOWED_CHAT_ID_KEY)
    if allowed_chat_id is not None and reaction.chat.id != allowed_chat_id:
        return

    new_reactions = [r.emoji for r in (reaction.new_reaction or []) if hasattr(r, "emoji")]
    if not any(e in ("🗑", "🗑️", "❌") for e in new_reactions):
        return

    msg_map = context.bot_data.get("msg_map")
    max_client: MaxClient | None = context.bot_data.get("max_client")
    if not msg_map or not max_client:
        return

    max_msg_id = msg_map.get_max_id(reaction.message_id)
    if not max_msg_id:
        return

    max_chat_id = msg_map.get_chat_id(reaction.message_id)
    if max_chat_id is None and len(max_client.chat_ids) == 1:
        max_chat_id = max_client.chat_ids[0]

    if max_chat_id is not None:
        try:
            await max_client.delete_message(max_chat_id, max_msg_id)
            await context.bot.delete_message(chat_id=reaction.chat.id, message_id=reaction.message_id)
            log.info("Deleted message %s in Max and TG via reaction", max_msg_id)
        except Exception:
            log.exception("Failed to delete message via reaction")


def build_tg_app(token: str, max_client: MaxClient, allowed_chat_id: str,
                  proxy_url: str | None = None, read_timeout: int | None = None, write_timeout: int | None = None, base_url: str | None = None,
                  topic_manager: Any = None, msg_map: Any = None, delivery_reaction: str = "👍") -> Application:
    """Build and configure the Telegram Application with handlers."""
    builder = Application.builder().token(token)
    if proxy_url:
        builder = builder.proxy(proxy_url).get_updates_proxy(proxy_url)
    if read_timeout:
        builder = builder.read_timeout(read_timeout)
    if write_timeout:
        builder = builder.write_timeout(write_timeout)
    if base_url:
        builder = builder.base_url(base_url + "/bot").base_file_url(base_url + "/file/bot")
    app = builder.build()
    app.bot_data["max_client"] = max_client
    app.bot_data[_ALLOWED_CHAT_ID_KEY] = int(allowed_chat_id)
    if topic_manager is not None:
        app.bot_data["topic_manager"] = topic_manager
    if msg_map is not None:
        app.bot_data["msg_map"] = msg_map
    app.bot_data["delivery_reaction"] = delivery_reaction

    chat_filter = filters.Chat(chat_id=int(allowed_chat_id))

    media_filter = (
        filters.PHOTO | filters.Document.ALL | filters.VIDEO
        | filters.VOICE | filters.AUDIO | filters.ANIMATION
    )

    app.add_handler(CallbackQueryHandler(_on_reply_button, pattern=r"^reply:"))
    app.add_handler(CommandHandler("cancel", _on_cancel, filters=chat_filter))
    app.add_handler(CommandHandler(["del", "delete", "rm"], _on_delete_command, filters=chat_filter))
    app.add_handler(MessageReactionHandler(_on_reaction))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & chat_filter, _on_text_reply))
    app.add_handler(MessageHandler(media_filter & chat_filter, _on_media_reply))

    return app
