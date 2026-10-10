"""Bidirectional mapping between Telegram messages and Max messages."""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MAP_FILE = "messages_map.json"
DEFAULT_MAX_ENTRIES = 10000


class MessageMapper:
    """Stores tg_msg_id <-> max_msg_id bidirectional mappings with JSON persistence."""

    def __init__(self, filepath: str = DEFAULT_MAP_FILE, max_entries: int = DEFAULT_MAX_ENTRIES):
        self.filepath = filepath
        self.max_entries = max_entries
        self._tg_to_max: OrderedDict[int, str] = OrderedDict()
        self._max_to_tg: OrderedDict[str, int] = OrderedDict()
        self._tg_to_chat: dict[int, Any] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.filepath):
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else data.items()
            for item in items:
                chat_val = None
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    tg_id, max_id = item[0], item[1]
                    if len(item) >= 3:
                        chat_val = item[2]
                elif isinstance(item, dict) and "tg" in item and "max" in item:
                    tg_id, max_id = item["tg"], item["max"]
                    chat_val = item.get("chat")
                else:
                    continue
                try:
                    tg_int = int(tg_id)
                    max_str = str(max_id)
                    self._tg_to_max[tg_int] = max_str
                    self._max_to_tg[max_str] = tg_int
                    if chat_val is not None:
                        self._tg_to_chat[tg_int] = chat_val
                except (ValueError, TypeError):
                    continue
            log.info("Loaded %d message mappings from %s", len(self._tg_to_max), self.filepath)
        except Exception:
            log.exception("Failed to load message mappings from %s", self.filepath)

    def save(self) -> None:
        try:
            items = [
                [tg_id, max_id, self._tg_to_chat.get(tg_id)]
                if tg_id in self._tg_to_chat
                else [tg_id, max_id]
                for tg_id, max_id in self._tg_to_max.items()
            ]
            tmp = f"{self.filepath}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(items, f)
            os.replace(tmp, self.filepath)
        except Exception:
            log.exception("Failed to save message mappings to %s", self.filepath)

    def record(self, tg_msg_id: int, max_msg_id: str | int, chat_id: Any = None) -> None:
        try:
            tg_int = int(tg_msg_id)
            max_str = str(max_msg_id)
        except (ValueError, TypeError):
            return

        # If already exists, move to end (MRU)
        if tg_int in self._tg_to_max:
            self._tg_to_max.move_to_end(tg_int)
        self._tg_to_max[tg_int] = max_str

        if max_str in self._max_to_tg:
            self._max_to_tg.move_to_end(max_str)
        self._max_to_tg[max_str] = tg_int

        if chat_id is not None:
            self._tg_to_chat[tg_int] = chat_id

        while len(self._tg_to_max) > self.max_entries:
            old_tg, old_max = self._tg_to_max.popitem(last=False)
            self._max_to_tg.pop(old_max, None)
            self._tg_to_chat.pop(old_tg, None)

        self.save()

    def get_max_id(self, tg_msg_id: int | None) -> str | None:
        if tg_msg_id is None:
            return None
        try:
            return self._tg_to_max.get(int(tg_msg_id))
        except (ValueError, TypeError):
            return None

    def get_tg_id(self, max_msg_id: str | int | None) -> int | None:
        if max_msg_id is None:
            return None
        return self._max_to_tg.get(str(max_msg_id))

    def get_chat_id(self, tg_msg_id: int | None) -> Any | None:
        if tg_msg_id is None:
            return None
        try:
            return self._tg_to_chat.get(int(tg_msg_id))
        except (ValueError, TypeError):
            return None
