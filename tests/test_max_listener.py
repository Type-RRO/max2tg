"""Tests for app/max_listener.py — pure helper functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.max_listener import _human_size, _guess_media_kind, _send_attach, create_max_client


# ---------------------------------------------------------------------------
# _human_size
# ---------------------------------------------------------------------------

class TestHumanSize:
    """Tests for the _human_size byte-formatter."""

    # Byte range (< 1024)
    def test_zero_bytes(self):
        assert _human_size(0) == "0 Б"

    def test_single_byte(self):
        assert _human_size(1) == "1 Б"

    def test_max_bytes(self):
        assert _human_size(1023) == "1023 Б"

    # Kilobyte range (1024 – 1024²-1)
    def test_exact_one_kb(self):
        assert _human_size(1024) == "1.0 КБ"

    def test_fractional_kb(self):
        assert _human_size(1536) == "1.5 КБ"

    def test_large_kb(self):
        assert _human_size(1023 * 1024) == "1023.0 КБ"

    # Megabyte range
    def test_exact_one_mb(self):
        assert _human_size(1024 ** 2) == "1.0 МБ"

    def test_fractional_mb(self):
        assert _human_size(int(2.5 * 1024 ** 2)) == "2.5 МБ"

    def test_large_mb(self):
        assert _human_size(500 * 1024 ** 2) == "500.0 МБ"

    # Gigabyte range
    def test_exact_one_gb(self):
        assert _human_size(1024 ** 3) == "1.0 ГБ"

    def test_fractional_gb(self):
        assert _human_size(int(1.5 * 1024 ** 3)) == "1.5 ГБ"

    # Terabyte range (overflow past ГБ loop)
    def test_terabyte(self):
        result = _human_size(1024 ** 4)
        assert "ТБ" in result

    def test_large_terabyte(self):
        result = _human_size(5 * 1024 ** 4)
        assert result.startswith("5")
        assert "ТБ" in result

    # Return type
    def test_returns_string(self):
        assert isinstance(_human_size(42), str)


# ---------------------------------------------------------------------------
# _guess_media_kind
# ---------------------------------------------------------------------------

class TestGuessMediaKind:
    """Tests for the filename-to-media-kind classifier."""

    # Photo extensions
    def test_jpg_is_photo(self):
        assert _guess_media_kind("image.jpg") == "photo"

    def test_jpeg_is_photo(self):
        assert _guess_media_kind("photo.jpeg") == "photo"

    def test_png_is_photo(self):
        assert _guess_media_kind("screenshot.png") == "photo"

    def test_gif_is_photo(self):
        assert _guess_media_kind("anim.gif") == "photo"

    def test_webp_is_photo(self):
        assert _guess_media_kind("sticker.webp") == "photo"

    def test_bmp_is_photo(self):
        assert _guess_media_kind("old.bmp") == "photo"

    # Video extensions
    def test_mp4_is_video(self):
        assert _guess_media_kind("clip.mp4") == "video"

    def test_mov_is_video(self):
        assert _guess_media_kind("recording.mov") == "video"

    def test_avi_is_video(self):
        assert _guess_media_kind("video.avi") == "video"

    def test_mkv_is_video(self):
        assert _guess_media_kind("movie.mkv") == "video"

    def test_webm_is_video(self):
        assert _guess_media_kind("stream.webm") == "video"

    # Document / unknown extensions
    def test_pdf_is_document(self):
        assert _guess_media_kind("report.pdf") == "document"

    def test_zip_is_document(self):
        assert _guess_media_kind("archive.zip") == "document"

    def test_docx_is_document(self):
        assert _guess_media_kind("contract.docx") == "document"

    def test_txt_is_document(self):
        assert _guess_media_kind("notes.txt") == "document"

    def test_no_extension_is_document(self):
        assert _guess_media_kind("README") == "document"

    def test_empty_string_is_document(self):
        assert _guess_media_kind("") == "document"

    # Case-insensitivity
    def test_uppercase_jpg_is_photo(self):
        assert _guess_media_kind("PHOTO.JPG") == "photo"

    def test_mixed_case_mp4_is_video(self):
        assert _guess_media_kind("Video.MP4") == "video"

    def test_mixed_case_png_is_photo(self):
        assert _guess_media_kind("Image.PNG") == "photo"

    # Paths with directories
    def test_full_path_jpg(self):
        assert _guess_media_kind("/tmp/uploads/img.jpg") == "photo"

    def test_full_path_mp4(self):
        assert _guess_media_kind("/home/user/videos/clip.mp4") == "video"

    # Extension appearing in the middle of filename should not trigger false match
    def test_mp4_in_name_not_extension_is_document(self):
        assert _guess_media_kind("mp4_notes.txt") == "document"


# ---------------------------------------------------------------------------
# _send_attach — POLL handling
# ---------------------------------------------------------------------------

class TestSendAttachPoll:
    """Tests for the POLL branch of _send_attach."""

    def _make_sender(self):
        sender = MagicMock()
        sender.send_poll = AsyncMock()
        sender.send = AsyncMock()
        return sender

    @pytest.mark.asyncio
    async def test_poll_with_enough_options_is_forwarded_as_poll(self):
        sender = self._make_sender()
        attach = {
            "_type": "POLL",
            "title": "Любимый цвет?",
            "answers": [{"text": "Красный"}, {"text": "Синий"}],
        }

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_awaited_once()
        sender.send.assert_not_called()
        _, options = sender.send_poll.await_args.args[:2]
        assert options == ["Красный", "Синий"]

    @pytest.mark.asyncio
    async def test_poll_with_less_than_two_options_falls_back_to_text(self):
        sender = self._make_sender()
        attach = {"_type": "POLL", "title": "Опрос", "answers": [{"text": "Единственный"}]}

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_not_called()
        sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_with_no_answers_key_falls_back_to_text(self):
        sender = self._make_sender()
        attach = {"_type": "POLL", "title": "Опрос"}

        result = await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        assert result is True
        sender.send_poll.assert_not_called()
        sender.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poll_ignores_answers_without_text(self):
        sender = self._make_sender()
        attach = {
            "_type": "POLL",
            "title": "Опрос",
            "answers": [{"text": "Да"}, {}, {"text": "Нет"}, {"text": ""}],
        }

        await _send_attach(attach, client=MagicMock(), sender=sender, header_text="hdr", chat_id=1, message_id=1)

        _, options = sender.send_poll.await_args.args[:2]
        assert options == ["Да", "Нет"]


# ---------------------------------------------------------------------------
# _send_attach — VIDEO handling
# ---------------------------------------------------------------------------

class TestSendAttachVideo:
    """Tests for the VIDEO branch of _send_attach."""

    def _make_client(self, cmd_response=None):
        client = MagicMock()
        client.cmd = AsyncMock(return_value=cmd_response or {})
        client.download_file = AsyncMock(return_value=b"video-bytes")
        return client

    def _make_sender(self, send_video_ok=True):
        sender = MagicMock()
        sender.send_video = AsyncMock(return_value=send_video_ok)
        sender.send_photo = AsyncMock()
        sender.send = AsyncMock()
        return sender

    @pytest.mark.asyncio
    async def test_video_downloaded_and_sent_successfully(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender(send_video_ok=True)
        attach = {"_type": "VIDEO", "videoId": "vid1", "token": "tok"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_awaited_once()
        sender.send_photo.assert_not_called()
        sender.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_download_uses_omit_origin(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        client.download_file.assert_awaited_once_with("https://ok.ru/video720.mp4", omit_origin=True)

    @pytest.mark.asyncio
    async def test_prefers_highest_available_resolution(self):
        client = self._make_client({
            "MP4_144": "https://ok.ru/144.mp4",
            "MP4_1080": "https://ok.ru/1080.mp4",
            "MP4_480": "https://ok.ru/480.mp4",
        })
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        client.download_file.assert_awaited_once_with("https://ok.ru/1080.mp4", omit_origin=True)

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_telegram_upload_fails(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender(send_video_ok=False)
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_awaited_once()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_download_fails(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})

        async def download_file(url, omit_origin=False):
            return None if omit_origin else b"thumb-bytes"

        client.download_file = AsyncMock(side_effect=download_file)
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_thumbnail_when_no_url_in_response(self):
        client = self._make_client({})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_video_url_lookup_without_video_id(self):
        client = self._make_client({"MP4_720": "https://ok.ru/video720.mp4"})
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "thumbnail": "https://max.ru/thumb.jpg"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        client.cmd.assert_not_called()
        sender.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_thumbnail_and_no_video_falls_back_to_text(self):
        client = self._make_client({})
        client.download_file = AsyncMock(return_value=None)
        sender = self._make_sender()
        attach = {"_type": "VIDEO", "videoId": "vid1"}

        result = await _send_attach(attach, client=client, sender=sender, header_text="hdr", chat_id=1, message_id=2)

        assert result is True
        sender.send_video.assert_not_called()
        sender.send_photo.assert_not_called()
        sender.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# create_max_client — proxy_url wiring
# ---------------------------------------------------------------------------

class TestCreateMaxClientProxy:
    def test_proxy_url_passed_to_max_client(self):
        client = create_max_client(
            "tok", "dev", sender=MagicMock(), proxy_url="socks5://127.0.0.1:1080"
        )
        assert client.proxy_url == "socks5://127.0.0.1:1080"

    def test_proxy_url_defaults_to_none(self):
        client = create_max_client("tok", "dev", sender=MagicMock())
        assert client.proxy_url is None

    def test_exclude_chat_ids_passed_to_max_client(self):
        client = create_max_client("tok", "dev", sender=MagicMock(), exclude_chat_ids="1,2")
        assert client.exclude_chat_ids == [1, 2]

    def test_exclude_chat_ids_defaults_to_empty(self):
        client = create_max_client("tok", "dev", sender=MagicMock())
        assert client.exclude_chat_ids == []


# ---------------------------------------------------------------------------
# Header formatting in topics, button omission, and reply linking
# ---------------------------------------------------------------------------

class TestForwardMessageInTopics:
    @pytest.mark.asyncio
    async def test_chat_title_omitted_when_in_topic(self):
        from app.max_listener import _forward_single_message
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=10, sender_id=99, text="Hello world", message_id="msg1")
        client = MagicMock()
        client.chat_ids = [10, 20]  # multiple chats
        sender = MagicMock()
        sender.send = AsyncMock()

        resolver = MagicMock()
        resolver.resolve_user = AsyncMock(return_value="Alice")
        resolver.is_dm.return_value = False
        resolver.chat_name.return_value = "Group Chat Name"

        topic_manager = MagicMock()
        topic_manager.enabled = True
        topic_manager.get_or_create_topic = AsyncMock(return_value=5)

        await _forward_single_message(
            msg, client, sender, resolver, topic_manager=topic_manager, reply_enabled=True
        )

        sender.send.assert_awaited_once()
        sent_text = sender.send.await_args[0][0]
        # Must contain sender name
        assert "Alice" in sent_text
        # Must NOT contain group chat title
        assert "Group Chat Name" not in sent_text
        # Inline reply button must be omitted by default
        assert sender.send.await_args.kwargs.get("reply_markup") is None
        # Must be sent to topic 5
        assert sender.send.await_args.kwargs.get("thread_id") == 5

    @pytest.mark.asyncio
    async def test_chat_title_included_when_no_topics_and_multiple_chats(self):
        from app.max_listener import _forward_single_message
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=10, sender_id=99, text="Hello world", message_id="msg1")
        client = MagicMock()
        client.chat_ids = [10, 20]
        sender = MagicMock()
        sender.send = AsyncMock()

        resolver = MagicMock()
        resolver.resolve_user = AsyncMock(return_value="Bob")
        resolver.is_dm.return_value = False
        resolver.chat_name.return_value = "Project Alpha"

        await _forward_single_message(
            msg, client, sender, resolver, topic_manager=None, reply_enabled=False
        )

        sender.send.assert_awaited_once()
        sent_text = sender.send.await_args[0][0]
        assert "Project Alpha" in sent_text
        assert "Bob" in sent_text

    @pytest.mark.asyncio
    async def test_native_reply_linking_used_when_in_msg_map(self):
        from app.max_listener import _forward_single_message
        from app.max_client import MaxMessage

        msg = MaxMessage(
            chat_id=10,
            sender_id=99,
            text="Reply text",
            message_id="msg2",
            link={"type": "REPLY", "message": {"id": "orig_max_1", "text": "Original text"}},
        )
        client = MagicMock()
        client.chat_ids = [10]
        sender = MagicMock()
        sender.send = AsyncMock()

        resolver = MagicMock()
        resolver.resolve_user = AsyncMock(return_value="Alice")
        resolver.is_dm.return_value = False
        resolver.chat_name.return_value = "Chat"

        msg_map = MagicMock()
        msg_map.get_tg_id.return_value = 888  # TG message ID of original message

        await _forward_single_message(
            msg, client, sender, resolver, topic_manager=None, msg_map=msg_map
        )

        sender.send.assert_awaited_once()
        assert sender.send.await_args.kwargs.get("reply_to_message_id") == 888
        # Because native reply is used, blockquote is omitted
        assert "<blockquote>" not in sender.send.await_args[0][0]

    @pytest.mark.asyncio
    async def test_header_and_body_separated_by_double_newline(self):
        from app.max_listener import _forward_single_message
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=10, sender_id=99, text="Hello body", message_id="msg3")
        client = MagicMock()
        client.chat_ids = [10, 20]
        sender = MagicMock()
        sender.send = AsyncMock()

        resolver = MagicMock()
        resolver.resolve_user = AsyncMock(return_value="Charlie")
        resolver.is_dm.return_value = False
        resolver.chat_name.return_value = "Group 1"

        await _forward_single_message(msg, client, sender, resolver, topic_manager=None)

        sender.send.assert_awaited_once()
        sent_text = sender.send.await_args[0][0]
        # Sender is bold in group header, separated by \n\n from body
        assert sent_text == "💬 <b>Group 1</b> | 👤 <b>Charlie</b>\n\nHello body"


# ---------------------------------------------------------------------------
# _header formatting
# ---------------------------------------------------------------------------

class TestHeaderFormatting:
    def test_dm_header_has_bold_sender(self):
        from app.max_listener import _header
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=1, sender_id=2)
        assert _header(msg, "Alice", "", is_dm=True) == "✉ <b>Alice</b>"

    def test_group_with_chat_label_has_bold_sender_and_chat(self):
        from app.max_listener import _header
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=1, sender_id=2)
        assert _header(msg, "Alice", "General", is_dm=False) == "💬 <b>General</b> | 👤 <b>Alice</b>"

    def test_group_without_chat_label_has_bold_sender(self):
        from app.max_listener import _header
        from app.max_client import MaxMessage

        msg = MaxMessage(chat_id=1, sender_id=2)
        assert _header(msg, "Alice", "", is_dm=False) == "👤 <b>Alice</b>"

