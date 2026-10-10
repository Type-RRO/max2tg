"""Forum topic manager for Telegram supergroups."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from html import escape
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.tg_sender import TelegramSender

log = logging.getLogger(__name__)

TOPIC_NAME_MAX_LENGTH = 128


class TopicManager:
    """Manages mapping between Max chat IDs and Telegram forum topic message_thread_ids."""

    def __init__(
        self,
        sender: TelegramSender,
        topics_file: str = "topics.json",
        enabled: bool = True,
    ):
        self.sender = sender
        self.topics_file = topics_file
        self.enabled = enabled
        self._topics: dict[str, int] = {}  # str(max_chat_id) -> message_thread_id
        self._reverse: dict[int, str] = {}  # message_thread_id -> str(max_chat_id)
        self._names: dict[str, str] = {}    # str(max_chat_id) -> topic_name
        self._lock = asyncio.Lock()
        self._disabled_permanently = False
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.topics_file):
            return
        try:
            with open(self.topics_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                topics = data.get("topics", {})
                for k, v in topics.items():
                    try:
                        thread_id = int(v)
                        self._topics[str(k)] = thread_id
                        self._reverse[thread_id] = str(k)
                    except (ValueError, TypeError):
                        pass
                names = data.get("names", {})
                if isinstance(names, dict):
                    self._names = {str(k): str(v) for k, v in names.items()}
                log.info("Loaded %d forum topics from %s", len(self._topics), self.topics_file)
        except Exception:
            log.exception("Failed to load topics from %s", self.topics_file)

    def _save(self) -> None:
        try:
            tmp = f"{self.topics_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"topics": self._topics, "names": self._names}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.topics_file)
        except Exception:
            log.exception("Failed to save topics to %s", self.topics_file)

    def get_thread_id(self, max_chat_id: Any) -> int | None:
        if not self.enabled or self._disabled_permanently:
            return None
        return self._topics.get(str(max_chat_id))

    def get_chat_id_by_thread(self, thread_id: int | None) -> Any | None:
        if not thread_id:
            return None
        chat_id_str = self._reverse.get(thread_id)
        if chat_id_str is None:
            return None
        try:
            return int(chat_id_str)
        except ValueError:
            return chat_id_str

    def get_chat_name(self, max_chat_id: Any) -> str | None:
        return self._names.get(str(max_chat_id))

    async def get_or_create_topic(
        self,
        max_chat_id: Any,
        chat_name: str,
        avatar_data: bytes | None = None,
    ) -> int | None:
        if not self.enabled or self._disabled_permanently:
            return None

        cid_str = str(max_chat_id)
        if cid_str in self._topics:
            return self._topics[cid_str]

        async with self._lock:
            if cid_str in self._topics:
                return self._topics[cid_str]

            clean_name = (chat_name.strip() or f"Чат {max_chat_id}")[:TOPIC_NAME_MAX_LENGTH]
            log.info("Creating Telegram forum topic for chat %s (%s)...", max_chat_id, clean_name)

            try:
                topic = await self.sender.create_forum_topic(name=clean_name)
                if not topic or not getattr(topic, "message_thread_id", None):
                    log.warning("Could not create forum topic for %s (empty response)", clean_name)
                    return None

                thread_id = topic.message_thread_id
                self._topics[cid_str] = thread_id
                self._reverse[thread_id] = cid_str
                self._names[cid_str] = clean_name
                self._save()
                log.info("Created forum topic %s (thread_id=%d) for Max chat %s", clean_name, thread_id, max_chat_id)

                intro_text = f"💬 Топик для чата: <b>{escape(clean_name)}</b>\n<code>Max ID: {escape(cid_str)}</code>"
                if avatar_data:
                    await self.sender.send_photo(avatar_data, caption=intro_text, thread_id=thread_id)
                else:
                    await self.sender.send(intro_text, thread_id=thread_id)

                return thread_id

            except Exception as e:
                err_str = str(e).lower()
                if "not enough rights" in err_str or "not a forum" in err_str or "chat_not_found" in err_str:
                    log.error("Telegram forum topic creation failed: %s. Topics will be disabled.", e)
                    self._disabled_permanently = True
                else:
                    log.exception("Error creating forum topic for %s: %s", clean_name, e)
                return None
