"""Tests for app/topic_manager.py — TopicManager."""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.topic_manager import TopicManager


@pytest.fixture
def mock_sender():
    sender = MagicMock()
    sender.create_forum_topic = AsyncMock()
    sender.send_photo = AsyncMock()
    sender.send = AsyncMock()
    return sender


@pytest.mark.asyncio
async def test_topic_manager_creates_topic(tmp_path, mock_sender):
    topics_file = str(tmp_path / "topics.json")
    topic_mock = MagicMock()
    topic_mock.message_thread_id = 42
    mock_sender.create_forum_topic.return_value = topic_mock

    tm = TopicManager(sender=mock_sender, topics_file=topics_file, enabled=True)
    thread_id = await tm.get_or_create_topic(1001, "Work Group")

    assert thread_id == 42
    assert tm.get_thread_id(1001) == 42
    assert tm.get_chat_id_by_thread(42) == 1001
    assert tm.get_chat_name(1001) == "Work Group"
    mock_sender.create_forum_topic.assert_awaited_once_with(name="Work Group")
    mock_sender.send.assert_awaited_once()

    # Re-call returns cached without calling create_forum_topic again
    thread_id_again = await tm.get_or_create_topic(1001, "Work Group")
    assert thread_id_again == 42
    assert mock_sender.create_forum_topic.await_count == 1

    # Check persistence to disk
    with open(topics_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["topics"]["1001"] == 42


@pytest.mark.asyncio
async def test_topic_manager_loads_persisted(tmp_path, mock_sender):
    topics_file = str(tmp_path / "topics.json")
    with open(topics_file, "w", encoding="utf-8") as f:
        json.dump({"topics": {"555": 77}, "names": {"555": "Existing"}}, f)

    tm = TopicManager(sender=mock_sender, topics_file=topics_file, enabled=True)
    assert tm.get_thread_id(555) == 77
    assert tm.get_chat_id_by_thread(77) == 555
    assert tm.get_chat_name(555) == "Existing"

    thread_id = await tm.get_or_create_topic(555, "Existing")
    assert thread_id == 77
    mock_sender.create_forum_topic.assert_not_called()


@pytest.mark.asyncio
async def test_topic_manager_with_avatar(tmp_path, mock_sender):
    topics_file = str(tmp_path / "topics.json")
    topic_mock = MagicMock()
    topic_mock.message_thread_id = 101
    mock_sender.create_forum_topic.return_value = topic_mock

    tm = TopicManager(sender=mock_sender, topics_file=topics_file, enabled=True)
    thread_id = await tm.get_or_create_topic(2002, "Avatar Chat", avatar_data=b"img-bytes")

    assert thread_id == 101
    mock_sender.send_photo.assert_awaited_once()
    args, kwargs = mock_sender.send_photo.await_args
    assert args[0] == b"img-bytes"
    assert kwargs["thread_id"] == 101


@pytest.mark.asyncio
async def test_topic_manager_disabled(tmp_path, mock_sender):
    topics_file = str(tmp_path / "topics.json")
    tm = TopicManager(sender=mock_sender, topics_file=topics_file, enabled=False)

    thread_id = await tm.get_or_create_topic(3003, "Disabled")
    assert thread_id is None
    mock_sender.create_forum_topic.assert_not_called()
