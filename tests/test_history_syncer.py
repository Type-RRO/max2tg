"""Tests for app/history_syncer.py — HistorySyncer."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.history_syncer import HistorySyncer
from app.max_client import MaxMessage, OpCode


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.chat_ids = []
    client.exclude_chat_ids = []
    client.cmd = AsyncMock()
    client._parse_message = lambda payload: MaxMessage(
        chat_id=payload.get("chatId"),
        message_id=str(payload["message"].get("id")),
        text=payload["message"].get("text", ""),
        timestamp=payload["message"].get("time", 0),
        is_self=payload["message"].get("is_self", False),
    )
    return client


@pytest.fixture
def mock_resolver():
    resolver = MagicMock()
    resolver.chats = {100: "Chat 100", 200: "Chat 200"}
    return resolver


@pytest.mark.asyncio
async def test_syncer_deduplicates_and_sorts_chronologically(tmp_path, mock_client, mock_resolver):
    seen_file = str(tmp_path / "seen.json")
    forwarded = []

    async def forward_cb(msg: MaxMessage):
        forwarded.append(msg)

    syncer = HistorySyncer(
        client=mock_client,
        resolver=mock_resolver,
        forward_message_cb=forward_cb,
        seen_file=seen_file,
        poll_interval=0,
        messages_count=100,
    )

    # Pre-mark msg 1 as seen
    syncer.mark_seen("msg-1")
    assert syncer.is_seen("msg-1") is True
    assert syncer.is_seen("msg-2") is False

    # Mock response with messages in reverse or mixed order (as chat history usually comes from server)
    mock_client.cmd.return_value = {
        "messages": [
            {"id": "msg-3", "time": 3000, "text": "third"},
            {"id": "msg-1", "time": 1000, "text": "first already seen"},
            {"id": "msg-2", "time": 2000, "text": "second"},
        ]
    }

    count = await syncer.sync_chat(100)
    assert count == 2  # only msg-2 and msg-3

    # Verify chronological ordering: msg-2 (time=2000) then msg-3 (time=3000)
    assert len(forwarded) == 2
    assert forwarded[0].message_id == "msg-2"
    assert forwarded[0].text == "second"
    assert forwarded[1].message_id == "msg-3"
    assert forwarded[1].text == "third"

    # Both marked as seen
    assert syncer.is_seen("msg-2") is True
    assert syncer.is_seen("msg-3") is True

    # Syncing again doesn't forward anything
    count2 = await syncer.sync_chat(100)
    assert count2 == 0
    assert len(forwarded) == 2


@pytest.mark.asyncio
async def test_syncer_skips_self_messages(tmp_path, mock_client, mock_resolver):
    seen_file = str(tmp_path / "seen.json")
    forwarded = []

    async def forward_cb(msg: MaxMessage):
        forwarded.append(msg)

    syncer = HistorySyncer(
        client=mock_client,
        resolver=mock_resolver,
        forward_message_cb=forward_cb,
        seen_file=seen_file,
        poll_interval=0,
    )

    mock_client.cmd.return_value = {
        "messages": [
            {"id": "msg-self", "time": 1500, "text": "my own msg", "is_self": True},
            {"id": "msg-other", "time": 1600, "text": "hello"},
        ]
    }

    count = await syncer.sync_chat(100)
    assert count == 1
    assert len(forwarded) == 1
    assert forwarded[0].message_id == "msg-other"
    assert syncer.is_seen("msg-self") is True
