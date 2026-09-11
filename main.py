import requests
import re
import json
import hashlib
import time
import os
import asyncio
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Set, Tuple, Optional
from urllib.parse import urlparse, parse_qs, urlencode
from bs4 import BeautifulSoup
from html import escape

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1002325683219"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

CHANNELS = list(dict.fromkeys([
    "https://t.me/s/Config_HATunnel",
    "https://t.me/s/xixv2ray",
    "https://t.me/s/hddify",
    "https://t.me/s/khabari_18",
    "https://t.me/s/ProxyAnonymous",
    "https://t.me/s/JavidanNet",
    "https://t.me/s/ProxyMTProto_tel",
    "https://t.me/s/BestProxyTel1",
    "https://t.me/s/iRoProxy",
    "https://t.me/s/proxy_bolt",
    "https://t.me/s/proxyskyy"
]))

IPV4 = r'(?:25[0-5]|2[0-4]\d|1?\d?\d)'

PROXY_PATTERNS = [
    rf'(mtproto://[^\s<>"\'()]+)',
    rf'(https?://t\.me/proxy\?[^\s<>"\'()]+)',
    rf'(https?://t\.me/socks\?[^\s<>"\'()]+)',
    rf'(tg://proxy\?[^\s<>"\'()]+)',
    rf'(tg://socks\?[^\s<>"\'()]+)',
    rf'(socks5://[^\s<>"\'()]+)',
    rf'((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}}:[a-fA-F0-9]+)',
    rf'((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}}:[^:\s]+:[^:\s]+)',
    rf'((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}})'
]

AD_KEYWORDS = [
    'join',
    'channel',
    'عضویت',
    'کانال',
    'ادمین',
    'خرید',
    'فروش',
    'تبلیغ',
    'instagram.com',
    'اینستاگرام',
    'آموزش',
    'tutorial',
    'support',
    'telegram.me/join',
    't.me/join',
    'click',
    'لینک عضویت'
]

MAX_PROXIES_PER_POST = 20
MAX_MESSAGES_PER_CHANNEL = 50
MAX_PAGES_PER_CHANNEL = 5
KEEP_HOURS = 168
DB_PATH = "sent_proxies.db"

STICKER_ID = (
    "CAACAgQAAxkBAAFQIL5qZXtiZQTtLDIR56wqlUYO_JqmZgACvBsAAl2aMFOFxfprKF6fCz0E"
)


def init_db():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS sent_proxies (
                proxy_hash TEXT PRIMARY KEY,
                proxy TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS dead_cache (
                url TEXT PRIMARY KEY,
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS bot_messages (
                message_id INTEGER PRIMARY KEY,
                message_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    finally:
        conn.close()

    logger.info(f"Database initialized at {DB_PATH}")


def clean_old_proxies():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()
        cutoff = datetime.now() - timedelta(hours=KEEP_HOURS)

        c.execute(
            "DELETE FROM sent_proxies WHERE sent_at < ?",
            (cutoff,)
        )

        deleted = c.rowcount
        conn.commit()

    finally:
        conn.close()

    if deleted:
        logger.info(f"Cleaned {deleted} old proxies.")


def get_sent_proxy_hashes():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()
        c.execute("SELECT proxy_hash FROM sent_proxies")
        rows = c.fetchall()

    finally:
        conn.close()

    sent_count = len(rows)

    logger.info(
        f"Loaded {sent_count} previously sent proxies from database"
    )

    return {row[0] for row in rows}


def mark_as_sent(proxy):
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        proxy_hash = hashlib.md5(
            proxy.encode("utf-8")
        ).hexdigest()

        c.execute(
            """
            INSERT OR IGNORE INTO sent_proxies
            (proxy_hash, proxy, sent_at)
            VALUES (?, ?, ?)
            """,
            (
                proxy_hash,
                proxy,
                datetime.now()
            )
        )

        conn.commit()

    finally:
        conn.close()

    logger.info(f"Marked proxy as sent: {proxy[:50]}...")


def mark_as_sent_batch(proxies):
    if not proxies:
        return

    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()
        now = datetime.now()

        data = [
            (
                hashlib.md5(
                    proxy.encode("utf-8")
                ).hexdigest(),
                proxy,
                now
            )
            for proxy in proxies
        ]

        c.executemany(
            """
            INSERT OR IGNORE INTO sent_proxies
            (proxy_hash, proxy, sent_at)
            VALUES (?, ?, ?)
            """,
            data
        )

        conn.commit()

    finally:
        conn.close()

    logger.info(f"Marked {len(proxies)} proxies as sent.")


def get_dead_cache():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()
        c.execute("SELECT url FROM dead_cache")
        rows = c.fetchall()

    finally:
        conn.close()

    return {row[0] for row in rows}


def add_to_dead_cache(url):
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute(
            """
            INSERT OR REPLACE INTO dead_cache
            (url, failed_at)
            VALUES (?, ?)
            """,
            (
                url,
                datetime.now()
            )
        )

        conn.commit()

    finally:
        conn.close()


def remove_from_dead_cache(url):
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute(
            "DELETE FROM dead_cache WHERE url = ?",
            (url,)
        )

        conn.commit()

    finally:
        conn.close()


def clean_dead_cache():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()
        cutoff = datetime.now() - timedelta(hours=24)

        c.execute(
            "DELETE FROM dead_cache WHERE failed_at < ?",
            (cutoff,)
        )

        deleted = c.rowcount
        conn.commit()

    finally:
        conn.close()

    if deleted:
        logger.info(
            f"Cleaned {deleted} old dead cache entries."
        )


def save_bot_message(message_id: int, message_type: str):
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute(
            """
            INSERT OR REPLACE INTO bot_messages
            (message_id, message_type, created_at)
            VALUES (?, ?, ?)
            """,
            (
                message_id,
                message_type,
                datetime.now()
            )
        )

        conn.commit()

    finally:
        conn.close()

    logger.info(
        f"Saved {message_type} message: {message_id}"
    )


def get_bot_messages():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute(
            """
            SELECT message_id, message_type
            FROM bot_messages
            ORDER BY message_id ASC
            """
        )

        rows = c.fetchall()

    finally:
        conn.close()

    return rows


def delete_bot_message_record(message_id: int):
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute(
            "DELETE FROM bot_messages WHERE message_id = ?",
            (message_id,)
        )

        conn.commit()

    finally:
        conn.close()


def clear_bot_messages():
    conn = sqlite3.connect(DB_PATH)

    try:
        c = conn.cursor()

        c.execute("DELETE FROM bot_messages")

        conn.commit()

    finally:
        conn.close()


class MTProtoSocksExtractor:

    def __init__(self):
        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.5",
        })

        self.sent_hashes = get_sent_proxy_hashes()
        self.dead_cache = get_dead_cache()
        self.failed_counter = {}

    def should_skip_channel(self, url: str) -> bool:
        return url in self.dead_cache

    def update_dead_cache(self, url: str):
        self.failed_counter[url] = (
            self.failed_counter.get(url, 0) + 1
        )

        if self.failed_counter[url] >= 3:
            add_to_dead_cache(url)
            self.dead_cache.add(url)

            logger.warning(
                f"Channel added to dead cache: {url}"
            )

    def is_proxy_already_sent(self, proxy: str) -> bool:
        proxy_hash = hashlib.md5(
            proxy.encode("utf-8")
        ).hexdigest()

        return proxy_hash in self.sent_hashes

    def has_ad_keywords(self, text: str) -> bool:
        t = text.lower()

        return any(
            keyword.lower() in t
            for keyword in AD_KEYWORDS
        )

    def extract_from_text(self, text: str) -> List[str]:
        out = []

        if not text:
            return out

        text = (
            text
            .replace("&amp;", "&")
            .replace("&#x26;", "&")
        )

        for pattern in PROXY_PATTERNS:
            try:
                matches = re.findall(
                    pattern,
                    text,
                    re.IGNORECASE
                )

                if matches:
                    out.extend(matches)

            except re.error as e:
                logger.error(
                    f"Regex error: {e}"
                )

        cleaned = []

        for proxy in out:
            proxy = proxy.strip()
            proxy = proxy.rstrip(
                ".,;)]}>\"'،؛"
            )

            if proxy:
                cleaned.append(proxy)

        return list(dict.fromkeys(cleaned))

    def extract_proxy_buttons(self, soup) -> List[str]:
        proxies = []

        if not soup:
            return proxies

        for btn in soup.find_all("a", href=True):
            href = btn.get("href", "").strip()

            if not href:
                continue

            href = (
                href
                .replace("&amp;", "&")
                .replace("&#x26;", "&")
            )

            href_lower = href.lower()

            if "joinchat" in href_lower:
                continue

            if "/+" in href:
                continue

            if (
                href_lower.startswith("tg://proxy?")
                or href_lower.startswith("tg://socks?")
                or href_lower.startswith("https://t.me/proxy?")
                or href_lower.startswith("https://t.me/socks?")
                or href_lower.startswith("http://t.me/proxy?")
                or href_lower.startswith("http://t.me/socks?")
                or href_lower.startswith("mtproto://")
                or href_lower.startswith("socks5://")
            ):
                normalized = self.normalize_proxy(href)

                if normalized:
                    proxies.append(normalized)

        return list(dict.fromkeys(proxies))

    def normalize_proxy(self, proxy: str) -> str:
        proxy = proxy.strip()
        proxy = proxy.rstrip(
            ".,;)]}>\"'،؛"
        )
        proxy = proxy.replace("&amp;", "&")
        proxy = proxy.replace("&#x26;", "&")

        if proxy.startswith("http://t.me/proxy?"):
            return proxy

        if proxy.startswith("https://t.me/proxy?"):
            return proxy

        if proxy.startswith("http://t.me/socks?"):
            return proxy

        if proxy.startswith("https://t.me/socks?"):
            return proxy

        if proxy.startswith("tg://proxy?"):
            return proxy

        if proxy.startswith("tg://socks?"):
            return proxy

        if proxy.startswith("mtproto://"):
            return proxy

        if proxy.startswith("socks5://"):
            try:
                parsed = urlparse(proxy)

                if not parsed.hostname or not parsed.port:
                    return proxy

                server = parsed.hostname
                port = parsed.port

                params = [
                    ("server", server),
                    ("port", str(port))
                ]

                if parsed.username:
                    params.append(
                        ("user", parsed.username)
                    )

                if parsed.password:
                    params.append(
                        ("pass", parsed.password)
                    )

                return (
                    "tg://socks?"
                    + urlencode(params)
                )

            except Exception:
                return proxy

        if re.match(
            rf"^{IPV4}(\.{IPV4}){{3}}:\d{{1,5}}:[a-fA-F0-9]+$",
            proxy
        ):
            try:
                ip, port, secret = proxy.split(":")

                return (
                    f"tg://proxy?"
                    f"server={ip}"
                    f"&port={port}"
                    f"&secret={secret.lower()}"
                )

            except ValueError:
                return proxy

        if re.match(
            rf"^{IPV4}(\.{IPV4}){{3}}:\d{{1,5}}$",
            proxy
        ):
            try:
                ip, port = proxy.split(":")

                return (
                    f"tg://socks?"
                    f"server={ip}"
                    f"&port={port}"
                )

            except ValueError:
                return proxy

        if re.match(
            rf"^{IPV4}(\.{IPV4}){{3}}:\d{{1,5}}:[^:]+:[^:]+$",
            proxy
        ):
            try:
                ip, port, user, password = proxy.split(":")

                return (
                    f"tg://socks?"
                    f"server={ip}"
                    f"&port={port}"
                    f"&user={user}"
                    f"&pass={password}"
                )

            except ValueError:
                return proxy

        return proxy

    def fetch_page(
        self,
        url: str,
        before: Optional[int] = None
    ) -> Optional[str]:

        try:
            telegram_url = url.replace(
                "t.me",
                "telegram.me"
            )

            if before is not None:
                separator = "&" if "?" in telegram_url else "?"
                telegram_url = (
                    f"{telegram_url}"
                    f"{separator}before={before}"
                )

            response = self.session.get(
                telegram_url,
                timeout=20
            )

            if response.status_code != 200:
                logger.warning(
                    f"Channel request failed: "
                    f"{url} [{response.status_code}]"
                )

                return None

            return response.text

        except requests.RequestException as e:
            logger.warning(
                f"Channel request error: {url} - {e}"
            )

            return None

    def extract_proxies_from_channel(
        self,
        url: str
    ) -> List[str]:

        if self.should_skip_channel(url):
            logger.info(
                f"Skipping dead channel: {url}"
            )
            return []

        result = []
        seen = set()

        total_messages = 0
        scanned_messages = 0
        text_proxies = 0
        button_proxies = 0
        duplicate_proxies = 0
        already_sent = 0
        pages_loaded = 0
        before = None
        previous_oldest_id = None

        for page_number in range(1, MAX_PAGES_PER_CHANNEL + 1):

            html = self.fetch_page(
                url,
                before=before
            )

            if not html:
                if page_number == 1:
                    self.update_dead_cache(url)
                    return []

                break

            pages_loaded += 1

            try:
                soup = BeautifulSoup(
                    html,
                    "html.parser"
                )

                messages = soup.find_all(
                    "div",
                    class_="tgme_widget_message"
                )

                if not messages:
                    messages = soup.find_all(
                        "div",
                        class_="tgme_widget_message_wrap"
                    )

                if not messages:
                    logger.warning(
                        f"{url} -> no Telegram messages found on page {page_number}"
                    )
                    break

                total_messages += len(messages)

                page_oldest_id = None
                page_had_new_message = False

                for message in messages:

                    message_id = message.get(
                        "data-post",
                        ""
                    )

                    if message_id:
                        try:
                            message_number = int(
                                message_id.rsplit("/", 1)[-1]
                            )

                            if (
                                page_oldest_id is None
                                or message_number < page_oldest_id
                            ):
                                page_oldest_id = message_number

                        except (ValueError, AttributeError):
                            pass

                    text_nodes = message.find_all(
                        "div",
                        class_="tgme_widget_message_text"
                    )

                    if not text_nodes:
                        wrap = message.find(
                            "div",
                            class_="tgme_widget_message_text"
                        )

                        if wrap:
                            text_nodes = [wrap]

                    message_text = " ".join(
                        node.get_text(
                            " ",
                            strip=True
                        )
                        for node in text_nodes
                    ).strip()

                    scanned_messages += 1
                    page_had_new_message = True

                    found_from_text = self.extract_from_text(
                        message_text
                    )

                    text_proxies += len(
                        found_from_text
                    )

                    for found_proxy in found_from_text:

                        normalized = self.normalize_proxy(
                            found_proxy
                        )

                        if not normalized:
                            continue

                        if normalized in seen:
                            duplicate_proxies += 1
                            continue

                        if self.is_proxy_already_sent(
                            normalized
                        ):
                            already_sent += 1
                            continue

                        seen.add(normalized)
                        result.append(normalized)

                    buttons = message.find_all(
                        "a",
                        href=True
                    )

                    for btn in buttons:

                        href = btn.get(
                            "href",
                            ""
                        ).strip()

                        if not href:
                            continue

                        href = (
                            href
                            .replace("&amp;", "&")
                            .replace("&#x26;", "&")
                        )

                        href_lower = href.lower()

                        if "joinchat" in href_lower:
                            continue

                        if "/+" in href:
                            continue

                        if not (
                            href_lower.startswith("tg://proxy?")
                            or href_lower.startswith("tg://socks?")
                            or href_lower.startswith("https://t.me/proxy?")
                            or href_lower.startswith("https://t.me/socks?")
                            or href_lower.startswith("http://t.me/proxy?")
                            or href_lower.startswith("http://t.me/socks?")
                            or href_lower.startswith("mtproto://")
                            or href_lower.startswith("socks5://")
                        ):
                            continue

                        normalized = self.normalize_proxy(
                            href
                        )

                        if not normalized:
                            continue

                        button_proxies += 1

                        if normalized in seen:
                            duplicate_proxies += 1
                            continue

                        if self.is_proxy_already_sent(
                            normalized
                        ):
                            already_sent += 1
                            continue

                        seen.add(normalized)
                        result.append(normalized)

                if page_oldest_id is None:
                    break

                if (
                    previous_oldest_id is not None
                    and page_oldest_id >= previous_oldest_id
                ):
                    break

                previous_oldest_id = page_oldest_id

                if not page_had_new_message:
                    break

                if (
                    MAX_MESSAGES_PER_CHANNEL > 0
                    and scanned_messages >= MAX_MESSAGES_PER_CHANNEL
                ):
                    break

                before = page_oldest_id

                if len(result) >= (
                    MAX_MESSAGES_PER_CHANNEL
                    * MAX_PROXIES_PER_POST
                ):
                    break

                time.sleep(0.3)

            except Exception as e:
                logger.error(
                    f"Extraction failed for {url} "
                    f"on page {page_number}: {e}"
                )
                break

        if pages_loaded == 0:
            self.update_dead_cache(url)
            return []

        self.failed_counter[url] = 0

        remove_from_dead_cache(url)
        self.dead_cache.discard(url)

        logger.info(
            f"{url} -> "
            f"pages={pages_loaded}, "
            f"messages={total_messages}, "
            f"scanned={scanned_messages}, "
            f"text={text_proxies}, "
            f"buttons={button_proxies}, "
            f"duplicates={duplicate_proxies}, "
            f"already_sent={already_sent}, "
            f"new={len(result)}"
        )

        return result

    def collect_all_proxies(
        self
    ) -> List[Tuple[str, str]]:

        all_proxies = []
        seen = set()

        logger.info(
            f"Starting collection from {len(CHANNELS)} channels"
        )

        for channel_index, channel in enumerate(
            CHANNELS,
            start=1
        ):

            logger.info(
                f"Processing channel "
                f"{channel_index}/{len(CHANNELS)}: "
                f"{channel}"
            )

            proxies = self.extract_proxies_from_channel(
                channel
            )

            channel_new = 0

            for proxy in proxies:

                if proxy in seen:
                    continue

                seen.add(proxy)

                proxy_lower = proxy.lower()

                if (
                    proxy_lower.startswith("tg://proxy?")
                    or proxy_lower.startswith("https://t.me/proxy?")
                    or proxy_lower.startswith("http://t.me/proxy?")
                    or proxy_lower.startswith("mtproto://")
                ):
                    proxy_type = "MTProto"

                elif (
                    proxy_lower.startswith("tg://socks?")
                    or proxy_lower.startswith("https://t.me/socks?")
                    or proxy_lower.startswith("http://t.me/socks?")
                    or proxy_lower.startswith("socks5://")
                ):
                    proxy_type = "SOCKS5"

                else:
                    proxy_type = "SOCKS5"

                all_proxies.append(
                    (proxy, proxy_type)
                )

                channel_new += 1

            logger.info(
                f"Channel completed: "
                f"{channel} -> {channel_new} new unique proxies"
            )

        mtproto_count = sum(
            1
            for _, proxy_type in all_proxies
            if proxy_type == "MTProto"
        )

        socks_count = sum(
            1
            for _, proxy_type in all_proxies
            if proxy_type == "SOCKS5"
        )

        logger.info(
            f"Total new proxies collected: "
            f"{len(all_proxies)} | "
            f"MTProto: {mtproto_count} | "
            f"SOCKS5: {socks_count}"
        )

        return all_proxies


class TelegramSender:

    def __init__(
        self,
        token: str,
        chat_id: int
    ):
        self.token = token
        self.chat_id = chat_id
        self.api = (
            f"https://api.telegram.org/bot{token}"
        )

    def _request(
        self,
        method: str,
        data: dict
    ) -> Optional[dict]:

        try:
            response = requests.post(
                f"{self.api}/{method}",
                data=data,
                timeout=30
            )

            try:
                result = response.json()
            except ValueError:
                result = {
                    "ok": False,
                    "description": response.text
                }

            if not response.ok or not result.get("ok"):
                logger.error(
                    f"Telegram {method} failed: "
                    f"HTTP {response.status_code} - "
                    f"{result.get('description', 'Unknown error')}"
                )

                return result

            return result

        except requests.RequestException as e:
            logger.error(
                f"Telegram {method} request error: {e}"
            )

            return None

    def delete_message(
        self,
        message_id: int
    ) -> bool:

        result = self._request(
            "deleteMessage",
            {
                "chat_id": self.chat_id,
                "message_id": message_id
            }
        )

        if result and result.get("ok"):
            logger.info(
                f"Deleted Telegram message: {message_id}"
            )
            return True

        description = (
            result.get("description", "")
            if result
            else ""
        )

        if "message to delete not found" in description.lower():
            logger.info(
                f"Message already deleted: {message_id}"
            )
            return True

        logger.warning(
            f"Failed to delete message "
            f"{message_id}: {description}"
        )

        return False

    def delete_previous_messages(self) -> bool:

        previous_messages = get_bot_messages()

        if not previous_messages:
            logger.info(
                "No previous bot messages found."
            )
            return True

        logger.info(
            f"Found {len(previous_messages)} "
            f"previous bot messages."
        )

        all_deleted = True

        for message_id, message_type in previous_messages:

            if self.delete_message(message_id):
                delete_bot_message_record(
                    message_id
                )

                logger.info(
                    f"Previous {message_type} removed: "
                    f"{message_id}"
                )

            else:
                all_deleted = False

        return all_deleted

    def send_sticker(self) -> Optional[int]:

        result = self._request(
            "sendSticker",
            {
                "chat_id": self.chat_id,
                "sticker": STICKER_ID
            }
        )

        if result and result.get("ok"):

            message_id = result["result"]["message_id"]

            save_bot_message(
                message_id,
                "sticker"
            )

            logger.info(
                f"Logo sticker sent successfully: "
                f"{message_id}"
            )

            return message_id

        logger.warning(
            "Failed to send logo sticker."
        )

        return None

    def send_message(
        self,
        text: str,
        reply_markup=None
    ) -> Optional[int]:

        data = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        if reply_markup:
            data["reply_markup"] = json.dumps(
                reply_markup,
                ensure_ascii=False
            )

        result = self._request(
            "sendMessage",
            data
        )

        if result and result.get("ok"):

            message_id = result["result"]["message_id"]

            save_bot_message(
                message_id,
                "proxy"
            )

            return message_id

        return None

    def create_proxy_keyboard(
        self,
        proxies: List[Tuple[str, str]]
    ) -> Optional[dict]:

        keyboard = []
        row = []

        for proxy, proxy_type in proxies:

            if proxy_type == "MTProto":

                row.append({
                    "text": "MTProto",
                    "url": proxy
                })

            elif proxy_type == "SOCKS5":

                row.append({
                    "text": "SOCKS5",
                    "url": proxy
                })

            if len(row) == 4:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        if not keyboard:
            return None

        return {
            "inline_keyboard": keyboard
        }

    def create_socks_blockquote(
        self,
        proxies: List[Tuple[str, str]]
    ) -> str:

        socks = [
            proxy
            for proxy, proxy_type in proxies
            if proxy_type == "SOCKS5"
        ]

        if not socks:
            return ""

        lines = [
            escape(proxy)
            for proxy in socks
        ]

        return (
            "\n\n"
            "<blockquote>"
            + "\n".join(lines)
            + "</blockquote>"
        )

    def create_caption(
        self,
        proxies: List[Tuple[str, str]]
    ) -> str:

        return (
            """🅿🆁🅾🆇🆈

🛜 پروکسی‌های جدید.
✅ برای اتصال به پروکسی‌های MTProto و SOCKS5 از دکمه‌های زیر استفاده کنید.
"""
            + """
➖➖➖➖➖➖➖➖
<blockquote>@AristaProxy</blockquote>
➖➖➖➖➖➖➖➖
#Arista #پروکسی #proxy #MTProto #SOCKS5
<blockquote>مرگ بر جمهوری اسهالی</blockquote>"""
        )

    def send_proxies_batch(
        self,
        proxies: List[Tuple[str, str]]
    ) -> Optional[int]:

        if not proxies:
            return None

        text = self.create_caption(
            proxies
        )

        keyboard = self.create_proxy_keyboard(
            proxies
        )

        return self.send_message(
            text,
            keyboard
        )


class ProxyScheduler:

    def __init__(self):
        init_db()
        clean_old_proxies()
        clean_dead_cache()

        self.ext = MTProtoSocksExtractor()

        self.sender = TelegramSender(
            BOT_TOKEN,
            CHANNEL_ID
        )

    async def run_once(self):

        proxies = self.ext.collect_all_proxies()

        if not proxies:
            logger.info(
                "No new proxies found."
            )
            return

        logger.info(
            "Removing previous proxy messages "
            "and sticker."
        )

        self.sender.delete_previous_messages()

        sent_in_run = []

        total_batches = (
            (len(proxies) + MAX_PROXIES_PER_POST - 1)
            //
            MAX_PROXIES_PER_POST
        )

        logger.info(
            f"Sending {len(proxies)} proxies "
            f"in {total_batches} batches."
        )

        for index in range(
            0,
            len(proxies),
            MAX_PROXIES_PER_POST
        ):

            batch = proxies[
                index:index + MAX_PROXIES_PER_POST
            ]

            batch_number = (
                index // MAX_PROXIES_PER_POST
            ) + 1

            logger.info(
                f"Sending batch "
                f"{batch_number}/{total_batches} "
                f"with {len(batch)} proxies."
            )

            message_id = self.sender.send_proxies_batch(
                batch
            )

            if message_id:

                for proxy, _ in batch:
                    sent_in_run.append(proxy)

                logger.info(
                    f"Batch {batch_number} sent successfully: "
                    f"{message_id}"
                )

            else:
                logger.error(
                    f"Batch {batch_number} failed."
                )

            await asyncio.sleep(1)

        if sent_in_run:

            mark_as_sent_batch(
                sent_in_run
            )

            for proxy in sent_in_run:
                self.ext.sent_hashes.add(
                    hashlib.md5(
                        proxy.encode("utf-8")
                    ).hexdigest()
                )

            self.sender.send_sticker()

            logger.info(
                f"Run completed successfully. "
                f"Sent: {len(sent_in_run)} proxies."
            )

        else:
            logger.warning(
                "No proxy batch was sent successfully."
            )


def main():
    asyncio.run(
        ProxyScheduler().run_once()
    )


if __name__ == "__main__":
    main()
