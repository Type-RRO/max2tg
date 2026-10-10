"""Tests for app/msg_mapper.py."""

import json
import os
import pytest

from app.msg_mapper import MessageMapper


class TestMessageMapper:
    def test_record_and_get(self, tmp_path):
        map_file = str(tmp_path / "map.json")
        mapper = MessageMapper(filepath=map_file)

        mapper.record(101, "max_msg_1")
        mapper.record(102, 2002)

        assert mapper.get_max_id(101) == "max_msg_1"
        assert mapper.get_max_id(102) == "2002"
        assert mapper.get_tg_id("max_msg_1") == 101
        assert mapper.get_tg_id(2002) == 102
        assert mapper.get_tg_id("2002") == 102
        assert mapper.get_max_id(999) is None
        assert mapper.get_tg_id("unknown") is None

    def test_persistence_across_instances(self, tmp_path):
        map_file = str(tmp_path / "map.json")
        mapper1 = MessageMapper(filepath=map_file)
        mapper1.record(42, "max_42")

        mapper2 = MessageMapper(filepath=map_file)
        assert mapper2.get_max_id(42) == "max_42"
        assert mapper2.get_tg_id("max_42") == 42

    def test_eviction_when_exceeding_max_entries(self, tmp_path):
        map_file = str(tmp_path / "map.json")
        mapper = MessageMapper(filepath=map_file, max_entries=3)

        mapper.record(1, "m1")
        mapper.record(2, "m2")
        mapper.record(3, "m3")
        mapper.record(4, "m4")  # should evict 1

        assert mapper.get_max_id(1) is None
        assert mapper.get_tg_id("m1") is None
        assert mapper.get_max_id(2) == "m2"
        assert mapper.get_max_id(4) == "m4"

    def test_handles_corrupt_file(self, tmp_path):
        map_file = str(tmp_path / "corrupt.json")
        with open(map_file, "w") as f:
            f.write("not valid json")

        mapper = MessageMapper(filepath=map_file)
        assert mapper.get_max_id(1) is None

        # Recording new message works normally
        mapper.record(1, "m1")
        assert mapper.get_max_id(1) == "m1"

    def test_record_and_get_chat_id(self, tmp_path):
        map_file = str(tmp_path / "map_chat.json")
        mapper1 = MessageMapper(filepath=map_file)
        mapper1.record(101, "max_1", chat_id=555)

        assert mapper1.get_chat_id(101) == 555
        assert mapper1.get_chat_id(999) is None

        # Test persistence
        mapper2 = MessageMapper(filepath=map_file)
        assert mapper2.get_chat_id(101) == 555

