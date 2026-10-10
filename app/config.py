import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    max_token: str
    max_device_id: str
    tg_bot_token: str
    tg_chat_id: str
    max_chat_ids: str | None = None
    max_exclude_chat_ids: str | None = None
    max_proxy: str | None = None
    tg_proxy: str | None = None
    tg_read_timeout: int | None = None
    tg_write_timeout: int | None = None
    tg_media_write_timeout: int | None = None
    tg_base_url: str | None = None
    debug: bool = False
    reply_enabled: bool = False
    enable_topics: bool = True
    topics_file: str = "topics.json"
    history_poll_interval: int = 60
    history_messages_count: int = 100
    seen_messages_file: str = "seen_messages.json"
    max_chats_count: int = 100
    messages_map_file: str = "messages_map.json"
    delivery_reaction: str = "👍"
    show_reply_button: bool = False


def load_settings() -> Settings:
    load_dotenv()

    required = ["MAX_TOKEN", "MAX_DEVICE_ID", "TG_BOT_TOKEN", "TG_CHAT_ID"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in the values."
        )

    tg_chat_id = os.environ["TG_CHAT_ID"]
    try:
        int(tg_chat_id)
    except ValueError:
        raise SystemExit(
            f"TG_CHAT_ID must be a valid integer, got: {tg_chat_id!r}"
        )

    # Strip a trailing slash so downstream "+ /bot" / "+ /file/bot" concatenation
    # doesn't end up with a double slash if the user includes one in TG_BASE_URL.
    tg_base_url = (os.environ.get("TG_BASE_URL") or "").rstrip("/") or None

    # aiohttp-socks only accepts the socks5/socks4/http schemes, not socks5h (a
    # curl/requests convention for "resolve DNS via the proxy" that SOCKS5 proxy
    # libraries already do by default) - normalize it so MAX_PROXY doesn't silently
    # fail to parse.
    max_proxy = os.environ.get("MAX_PROXY") or None
    if max_proxy and max_proxy.startswith("socks5h://"):
        max_proxy = "socks5://" + max_proxy[len("socks5h://"):]

    data_dir = os.environ.get("DATA_DIR")
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)
        import shutil
        for fname in ("topics.json", "seen_messages.json", "messages_map.json"):
            old_p = fname
            new_p = os.path.join(data_dir, fname)
            if os.path.exists(old_p) and not os.path.exists(new_p):
                try:
                    shutil.copy2(old_p, new_p)
                except Exception:
                    pass

    default_topics = os.path.join(data_dir, "topics.json") if data_dir else "topics.json"
    default_seen = os.path.join(data_dir, "seen_messages.json") if data_dir else "seen_messages.json"
    default_map = os.path.join(data_dir, "messages_map.json") if data_dir else "messages_map.json"

    return Settings(
        max_token=os.environ["MAX_TOKEN"],
        max_device_id=os.environ["MAX_DEVICE_ID"],
        tg_bot_token=os.environ["TG_BOT_TOKEN"],
        tg_chat_id=os.environ["TG_CHAT_ID"],
        max_chat_ids=os.environ.get("MAX_CHAT_IDS") or None,
        max_exclude_chat_ids=os.environ.get("MAX_EXCLUDE_CHAT_IDS") or None,
        max_proxy=max_proxy,
        tg_proxy=os.environ.get("TG_PROXY") or None,
        tg_read_timeout=int(os.environ.get("TG_READ_TIMEOUT", 0)) or None,
        tg_write_timeout=int(os.environ.get("TG_WRITE_TIMEOUT", 0)) or None,
        tg_media_write_timeout=int(os.environ.get("TG_MEDIA_WRITE_TIMEOUT", 0)) or None,
        tg_base_url=tg_base_url,
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        reply_enabled=os.environ.get("REPLY_ENABLED", "").lower() in ("1", "true", "yes"),
        enable_topics=os.environ.get("ENABLE_TOPICS", "true").lower() in ("1", "true", "yes"),
        topics_file=os.environ.get("TOPICS_FILE", default_topics),
        history_poll_interval=int(os.environ.get("HISTORY_POLL_INTERVAL", "60")),
        history_messages_count=int(os.environ.get("HISTORY_MESSAGES_COUNT", "100")),
        seen_messages_file=os.environ.get("SEEN_MESSAGES_FILE", default_seen),
        max_chats_count=int(os.environ.get("MAX_CHATS_COUNT", "100")),
        messages_map_file=os.environ.get("MESSAGES_MAP_FILE", default_map),
        delivery_reaction=os.environ.get("DELIVERY_REACTION", "👍"),
        show_reply_button=os.environ.get("SHOW_REPLY_BUTTON", "false").lower() in ("1", "true", "yes"),
    )
