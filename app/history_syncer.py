"""Periodically polls Max chat history, deduping and forwarding missing messages sequentially."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from app.max_client import MaxMessage, OpCode

if TYPE_CHECKING:
    from app.max_client import MaxClient
    from app.resolver import ContactResolver

log = logging.getLogger(__name__)

MAX_SEEN_IDS_SAVED = 10000


class HistorySyncer:
    def __init__(
        self,
        client: MaxClient,
        resolver: ContactResolver,
        forward_message_cb: Callable[[MaxMessage], Awaitable[None]],
        seen_file: str = "seen_messages.json",
        poll_interval: int = 60,
        messages_count: int = 100,
    ):
        self.client = client
        self.resolver = resolver
        self.forward_message_cb = forward_message_cb
        self.seen_file = seen_file
        self.poll_interval = poll_interval
        self.messages_count = messages_count

        self._seen_ids: set[str] = set()
        self._seen_queue: deque[str] = deque(maxlen=MAX_SEEN_IDS_SAVED)
        self._chat_locks: dict[Any, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None
        self._load_seen()

    def _load_seen(self) -> None:
        if not os.path.exists(self.seen_file):
            return
        try:
            with open(self.seen_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for mid in data[-MAX_SEEN_IDS_SAVED:]:
                    mid_str = str(mid)
                    self._seen_ids.add(mid_str)
                    self._seen_queue.append(mid_str)
            log.info("Loaded %d seen message IDs from %s", len(self._seen_ids), self.seen_file)
        except Exception:
            log.exception("Failed to load seen messages from %s", self.seen_file)

    def _save_seen(self) -> None:
        try:
            tmp = f"{self.seen_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self._seen_queue), f)
            os.replace(tmp, self.seen_file)
        except Exception:
            log.exception("Failed to save seen messages to %s", self.seen_file)

    def is_seen(self, message_id: Any) -> bool:
        if not message_id:
            return False
        return str(message_id) in self._seen_ids

    def mark_seen(self, message_id: Any) -> None:
        if not message_id:
            return
        mid_str = str(message_id)
        if mid_str not in self._seen_ids:
            self._seen_ids.add(mid_str)
            self._seen_queue.append(mid_str)
            if len(self._seen_queue) % 20 == 0:
                self._save_seen()

    def get_chat_lock(self, chat_id: Any) -> asyncio.Lock:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    async def start(self) -> None:
        if self.poll_interval <= 0:
            log.info("History polling disabled (poll_interval <= 0)")
            return
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._save_seen()

    async def _poll_loop(self) -> None:
        log.info(
            "History syncer loop started (interval=%ds, limit=%d msgs)",
            self.poll_interval,
            self.messages_count,
        )
        # Initial sleep so initial connection and ready handling finish
        await asyncio.sleep(5)
        while True:
            try:
                await self.sync_all_chats()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Error during history sync cycle")

            await asyncio.sleep(self.poll_interval)

    async def sync_all_chats(self) -> None:
        all_chats: set[Any] = set()
        if self.client.chat_ids:
            all_chats.update(self.client.chat_ids)
        else:
            all_chats.update(self.resolver.chats.keys())

        for chat_id in all_chats:
            if chat_id in self.client.exclude_chat_ids:
                continue
            try:
                await self.sync_chat(chat_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error syncing history for chat %s", chat_id)

    async def sync_chat(self, chat_id: Any) -> int:
        """Fetch latest messages, sort unseen chronologically, and forward sequentially."""
        lock = self.get_chat_lock(chat_id)
        async with lock:
            now_ms = int(time.time() * 1000)
            resp = await self.client.cmd(
                OpCode.GET_MESSAGES,
                {
                    "chatId": chat_id,
                    "from": now_ms,
                    "backward": self.messages_count,
                    "forward": 0,
                    "getMessages": True,
                },
                timeout=15,
            )
            raw_messages = resp.get("messages", [])
            if not raw_messages:
                return 0

            unseen_raw = []
            for m in raw_messages:
                mid = str(m.get("id", ""))
                if mid and mid not in self._seen_ids:
                    unseen_raw.append(m)

            if not unseen_raw:
                return 0

            # Sort chronologically ascending (oldest first)
            # so messages appear in Telegram in exact chronological order
            unseen_raw.sort(key=lambda m: (m.get("time", 0), m.get("id", 0)))

            forwarded_count = 0
            for m in unseen_raw:
                mid = str(m.get("id", ""))
                parsed_msg = self.client._parse_message({"message": m, "chatId": chat_id})
                if not parsed_msg:
                    self.mark_seen(mid)
                    continue

                if parsed_msg.is_self:
                    self.mark_seen(mid)
                    continue

                self.mark_seen(mid)
                try:
                    await self.forward_message_cb(parsed_msg)
                    forwarded_count += 1
                    # Small delay between messages to preserve Telegram order and avoid flood limits
                    await asyncio.sleep(0.3)
                except Exception:
                    log.exception("Failed to forward synced message %s in chat %s", mid, chat_id)

            if forwarded_count > 0:
                log.info("Synced and forwarded %d missing messages for chat %s", forwarded_count, chat_id)
                self._save_seen()

            return forwarded_count
