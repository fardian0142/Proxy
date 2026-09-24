import requests
import re
import json
import hashlib
import time
import os
import asyncio
import sqlite3
import logging
import ipaddress

from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from urllib.parse import urlparse, urlencode, unquote

from bs4 import BeautifulSoup


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(
    os.environ.get(
        "CHANNEL_ID",
        "-1002325683219"
    )
)

if not BOT_TOKEN:
    raise ValueError(
        "BOT_TOKEN environment variable is required"
    )


CHANNELS = list(dict.fromkeys([
    "https://t.me/s/Config_HATunnel",
    "https://t.me/s/rojproxy",
    "https://t.me/s/FREE2CONFIG",
    "https://t.me/s/v2raycollectorgroup",
    "https://t.me/s/oneclickvpnkeys",
    "https://t.me/s/ShadowProxy66",
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


IPV4 = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"


PROXY_PATTERNS = [
    rf"(mtproto://[^\s<>\"'()]+)",
    rf"(https?://t\.me/proxy\?[^\s<>\"'()]+)",
    rf"(https?://t\.me/socks\?[^\s<>\"'()]+)",
    rf"(tg://proxy\?[^\s<>\"'()]+)",
    rf"(tg://socks\?[^\s<>\"'()]+)",
    rf"(socks5://[^\s<>\"'()]+)",
    rf"((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}}:[a-fA-F0-9]+)",
    rf"((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}}:[^:\s]+:[^:\s]+)",
    rf"((?:{IPV4}\.){{3}}{IPV4}:\d{{1,5}})"
]


WEBPROXY_PATTERNS = [
    rf"(https?://t\.me/webproxy\?[^\s<>\"'()]+)",
    rf"(tg://webproxy\?[^\s<>\"'()]+)"
]


AD_KEYWORDS = [
    "join",
    "channel",
    "عضویت",
    "کانال",
    "ادمین",
    "خرید",
    "فروش",
    "تبلیغ",
    "instagram.com",
    "اینستاگرام",
    "آموزش",
    "tutorial",
    "support",
    "telegram.me/join",
    "t.me/join",
    "click",
    "لینک عضویت"
]


MAX_PROXIES_PER_POST = 20
MAX_MESSAGES_PER_CHANNEL = 50
MAX_PAGES_PER_CHANNEL = 5

KEEP_HOURS = 168
DEAD_CACHE_HOURS = 24
DEAD_FAILURE_THRESHOLD = 3

REQUEST_TIMEOUT = 20
TELEGRAM_TIMEOUT = 30
REQUEST_RETRIES = 2

DB_PATH = "sent_proxies.db"


STICKER_ID = (
    "CAACAgQAAxkBAAFQIL5qZXtiZQTtLDIR56wqlUYO_JqmZgACvBsAAl2aMFOFxfprKF6fCz0E"
)


def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():
    conn = get_db()

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
                failed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                failure_count INTEGER DEFAULT 1
            )
        """)

        columns = {
            row[1]
            for row in c.execute(
                "PRAGMA table_info(dead_cache)"
            ).fetchall()
        }

        if "failure_count" not in columns:
            c.execute(
                """
                ALTER TABLE dead_cache
                ADD COLUMN failure_count INTEGER DEFAULT 1
                """
            )

        conn.commit()

    finally:
        conn.close()

    logger.info(
        f"Database initialized at {DB_PATH}"
    )


def clean_old_proxies():
    conn = get_db()

    deleted = 0

    try:
        c = conn.cursor()

        cutoff = (
            datetime.now()
            - timedelta(hours=KEEP_HOURS)
        )

        c.execute(
            """
            DELETE FROM sent_proxies
            WHERE sent_at < ?
            """,
            (cutoff,)
        )

        deleted = c.rowcount

        conn.commit()

    finally:
        conn.close()

    if deleted:
        logger.info(
            f"Cleaned {deleted} old proxies."
        )


def get_sent_proxy_hashes():
    conn = get_db()

    try:
        c = conn.cursor()

        c.execute(
            "SELECT proxy_hash FROM sent_proxies"
        )

        rows = c.fetchall()

    finally:
        conn.close()

    sent_count = len(rows)

    logger.info(
        f"Loaded {sent_count} previously sent proxies "
        f"from database"
    )

    return {
        row[0]
        for row in rows
    }


def mark_as_sent_batch(proxies):
    if not proxies:
        return

    conn = get_db()

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

    logger.info(
        f"Marked {len(proxies)} proxies as sent."
    )


def get_dead_cache():
    conn = get_db()

    try:
        c = conn.cursor()

        cutoff = (
            datetime.now()
            - timedelta(hours=DEAD_CACHE_HOURS)
        )

        c.execute(
            """
            SELECT url
            FROM dead_cache
            WHERE failed_at >= ?
            """,
            (cutoff,)
        )

        rows = c.fetchall()

    finally:
        conn.close()

    return {
        row[0]
        for row in rows
    }


def record_channel_failure(url):
    conn = get_db()

    try:
        c = conn.cursor()

        c.execute(
            """
            SELECT failure_count
            FROM dead_cache
            WHERE url = ?
            """,
            (url,)
        )

        row = c.fetchone()

        if row:
            failure_count = row[0] + 1

            c.execute(
                """
                UPDATE dead_cache
                SET failure_count = ?,
                    failed_at = ?
                WHERE url = ?
                """,
                (
                    failure_count,
                    datetime.now(),
                    url
                )
            )

        else:
            failure_count = 1

            c.execute(
                """
                INSERT INTO dead_cache
                (url, failed_at, failure_count)
                VALUES (?, ?, ?)
                """,
                (
                    url,
                    datetime.now(),
                    failure_count
                )
            )

        conn.commit()

    finally:
        conn.close()

    if failure_count >= DEAD_FAILURE_THRESHOLD:
        logger.warning(
            f"Channel reached dead threshold: "
            f"{url} "
            f"({failure_count} failures)"
        )

    return failure_count


def remove_from_dead_cache(url):
    conn = get_db()

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
    conn = get_db()

    deleted = 0

    try:
        c = conn.cursor()

        cutoff = (
            datetime.now()
            - timedelta(hours=DEAD_CACHE_HOURS)
        )

        c.execute(
            """
            DELETE FROM dead_cache
            WHERE failed_at < ?
            """,
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

        self.sent_hashes = (
            get_sent_proxy_hashes()
        )

        self.dead_cache = (
            get_dead_cache()
        )

    def should_skip_channel(
        self,
        url: str
    ) -> bool:
        return url in self.dead_cache

    def update_dead_cache(
        self,
        url: str
    ):
        failure_count = record_channel_failure(
            url
        )

        if failure_count >= DEAD_FAILURE_THRESHOLD:
            self.dead_cache.add(url)

    def is_proxy_already_sent(
        self,
        proxy: str
    ) -> bool:
        proxy_hash = hashlib.md5(
            proxy.encode("utf-8")
        ).hexdigest()

        return proxy_hash in self.sent_hashes

    def has_ad_keywords(
        self,
        text: str
    ) -> bool:
        if not text:
            return False

        text_lower = text.lower()

        matches = sum(
            1
            for keyword in AD_KEYWORDS
            if keyword.lower() in text_lower
        )

        return matches >= 2

    def extract_from_text(
        self,
        text: str
    ) -> List[str]:

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

        return list(
            dict.fromkeys(cleaned)
        )

    def extract_webproxy_from_text(
        self,
        text: str
    ) -> List[str]:

        out = []

        if not text:
            return out

        text = (
            text
            .replace("&amp;", "&")
            .replace("&#x26;", "&")
        )

        for pattern in WEBPROXY_PATTERNS:
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
                    f"WebProxy regex error: {e}"
                )

        cleaned = []

        for proxy in out:
            proxy = proxy.strip()

            proxy = proxy.rstrip(
                ".,;)]}>\"'،؛"
            )

            if proxy:
                cleaned.append(proxy)

        return list(
            dict.fromkeys(cleaned)
        )

    def is_webproxy_url(
        self,
        url: str
    ) -> bool:

        if not url:
            return False

        url_lower = url.lower()

        return (
            url_lower.startswith("https://t.me/webproxy?")
            or url_lower.startswith("http://t.me/webproxy?")
            or url_lower.startswith("tg://webproxy?")
        )

    def is_webproxy(
        self,
        proxy: str
    ) -> bool:

        if not proxy:
            return False

        proxy_lower = proxy.lower()

        return (
            proxy_lower.startswith("https://t.me/webproxy?")
            or proxy_lower.startswith("http://t.me/webproxy?")
            or proxy_lower.startswith("tg://webproxy?")
        )

    def normalize_webproxy(
        self,
        proxy: str
    ) -> str:

        if not proxy:
            return proxy

        proxy = proxy.strip()

        proxy = proxy.rstrip(
            ".,;)]}>\"'،؛"
        )

        proxy = (
            proxy
            .replace("&amp;", "&")
            .replace("&#x26;", "&")
        )

        if self.is_webproxy(proxy):
            return proxy

        return proxy

    def validate_ipv4(
        self,
        value: str
    ) -> bool:
        try:
            ipaddress.IPv4Address(value)
            return True
        except ValueError:
            return False

    def validate_port(
        self,
        value: str
    ) -> bool:
        try:
            port = int(value)
            return 1 <= port <= 65535
        except (TypeError, ValueError):
            return False

    def normalize_proxy(
        self,
        proxy: str
    ) -> str:

        proxy = proxy.strip()

        proxy = proxy.rstrip(
            ".,;)]}>\"'،؛"
        )

        proxy = (
            proxy
            .replace("&amp;", "&")
            .replace("&#x26;", "&")
        )

        if self.is_webproxy(proxy):
            return proxy

        lower = proxy.lower()

        if lower.startswith(
            "http://t.me/proxy?"
        ):
            return proxy

        if lower.startswith(
            "https://t.me/proxy?"
        ):
            return proxy

        if lower.startswith(
            "http://t.me/socks?"
        ):
            return proxy

        if lower.startswith(
            "https://t.me/socks?"
        ):
            return proxy

        if lower.startswith(
            "tg://proxy?"
        ):
            return proxy

        if lower.startswith(
            "tg://socks?"
        ):
            return proxy

        if lower.startswith(
            "mtproto://"
        ):
            return proxy

        if lower.startswith(
            "socks5://"
        ):

            try:
                parsed = urlparse(
                    proxy
                )

                if (
                    not parsed.hostname
                    or not parsed.port
                ):
                    return proxy

                server = parsed.hostname
                port = parsed.port

                if not self.validate_port(
                    str(port)
                ):
                    return proxy

                params = [
                    (
                        "server",
                        server
                    ),
                    (
                        "port",
                        str(port)
                    )
                ]

                if parsed.username:
                    params.append(
                        (
                            "user",
                            unquote(
                                parsed.username
                            )
                        )
                    )

                if parsed.password:
                    params.append(
                        (
                            "pass",
                            unquote(
                                parsed.password
                            )
                        )
                    )

                return (
                    "tg://socks?"
                    + urlencode(
                        params
                    )
                )

            except Exception:
                return proxy

        match = re.match(
            rf"^({IPV4}(?:\.{IPV4}){{3}}):(\d{{1,5}}):([a-fA-F0-9]+)$",
            proxy
        )

        if match:
            ip = match.group(1)
            port = match.group(2)
            secret = match.group(3)

            if (
                self.validate_ipv4(ip)
                and self.validate_port(port)
            ):
                return (
                    f"tg://proxy?"
                    f"server={ip}"
                    f"&port={port}"
                    f"&secret={secret.lower()}"
                )

        match = re.match(
            rf"^({IPV4}(?:\.{IPV4}){{3}}):(\d{{1,5}}):([^:\s]+):([^:\s]+)$",
            proxy
        )

        if match:
            ip = match.group(1)
            port = match.group(2)
            user = match.group(3)
            password = match.group(4)

            if (
                self.validate_ipv4(ip)
                and self.validate_port(port)
            ):
                return (
                    f"tg://socks?"
                    f"server={ip}"
                    f"&port={port}"
                    f"&user={user}"
                    f"&pass={password}"
                )

        match = re.match(
            rf"^({IPV4}(?:\.{IPV4}){{3}}):(\d{{1,5}})$",
            proxy
        )

        if match:
            ip = match.group(1)
            port = match.group(2)

            if (
                self.validate_ipv4(ip)
                and self.validate_port(port)
            ):
                return (
                    f"tg://socks?"
                    f"server={ip}"
                    f"&port={port}"
                )

        return proxy

    def fetch_page(
        self,
        url: str,
        before: Optional[int] = None
    ) -> Optional[str]:

        telegram_url = url.replace(
            "t.me",
            "telegram.me"
        )

        if before is not None:
            separator = (
                "&"
                if "?" in telegram_url
                else "?"
            )

            telegram_url = (
                f"{telegram_url}"
                f"{separator}before={before}"
            )

        for attempt in range(
            REQUEST_RETRIES + 1
        ):

            try:
                response = self.session.get(
                    telegram_url,
                    timeout=REQUEST_TIMEOUT
                )

                if response.status_code == 200:
                    return response.text

                if response.status_code == 429:
                    retry_after = (
                        response.headers.get(
                            "Retry-After"
                        )
                    )

                    try:
                        delay = min(
                            int(retry_after),
                            30
                        )
                    except (
                        TypeError,
                        ValueError
                    ):
                        delay = 5

                    if attempt < REQUEST_RETRIES:
                        time.sleep(delay)
                        continue

                logger.warning(
                    f"Channel request failed: "
                    f"{url} "
                    f"[{response.status_code}]"
                )

                return None

            except requests.RequestException as e:

                if attempt >= REQUEST_RETRIES:
                    logger.warning(
                        f"Channel request error: "
                        f"{url} - {e}"
                    )

                    return None

                time.sleep(
                    1.5 * (attempt + 1)
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
        ad_messages = 0
        pages_loaded = 0

        before = None
        previous_oldest_id = None

        stop_scanning = False

        for page_number in range(
            1,
            MAX_PAGES_PER_CHANNEL + 1
        ):

            if stop_scanning:
                break

            html = self.fetch_page(
                url,
                before=before
            )

            if not html:
                if page_number == 1:
                    self.update_dead_cache(
                        url
                    )
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
                        f"{url} -> no Telegram "
                        f"messages found on page "
                        f"{page_number}"
                    )

                    if page_number == 1:
                        self.update_dead_cache(
                            url
                        )

                    break

                total_messages += len(
                    messages
                )

                page_oldest_id = None

                for message in messages:

                    if (
                        MAX_MESSAGES_PER_CHANNEL > 0
                        and scanned_messages
                        >= MAX_MESSAGES_PER_CHANNEL
                    ):
                        stop_scanning = True
                        break

                    message_id = message.get(
                        "data-post",
                        ""
                    )

                    if message_id:
                        try:
                            message_number = int(
                                message_id.rsplit(
                                    "/",
                                    1
                                )[-1]
                            )

                            if (
                                page_oldest_id is None
                                or message_number
                                < page_oldest_id
                            ):
                                page_oldest_id = (
                                    message_number
                                )

                        except (
                            ValueError,
                            AttributeError
                        ):
                            pass

                    text_nodes = message.find_all(
                        "div",
                        class_="tgme_widget_message_text"
                    )

                    message_text = " ".join(
                        node.get_text(
                            " ",
                            strip=True
                        )
                        for node in text_nodes
                    ).strip()

                    scanned_messages += 1

                    found_from_text = (
                        self.extract_from_text(
                            message_text
                        )
                    )

                    found_webproxy_from_text = (
                        self.extract_webproxy_from_text(
                            message_text
                        )
                    )

                    text_proxies += len(
                        found_from_text
                    )

                    text_proxies += len(
                        found_webproxy_from_text
                    )

                    button_proxies_before = (
                        len(result)
                    )

                    for found_proxy in found_from_text:

                        normalized = (
                            self.normalize_proxy(
                                found_proxy
                            )
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

                    for found_proxy in found_webproxy_from_text:

                        normalized = (
                            self.normalize_webproxy(
                                found_proxy
                            )
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
                            .replace(
                                "&amp;",
                                "&"
                            )
                            .replace(
                                "&#x26;",
                                "&"
                            )
                        )

                        href_lower = (
                            href.lower()
                        )

                        if "joinchat" in href_lower:
                            continue

                        if "/+" in href:
                            continue

                        if self.is_webproxy_url(href):

                            normalized = (
                                self.normalize_webproxy(
                                    href
                                )
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

                            continue

                        valid = (
                            href_lower.startswith(
                                "tg://proxy?"
                            )
                            or href_lower.startswith(
                                "tg://socks?"
                            )
                            or href_lower.startswith(
                                "https://t.me/proxy?"
                            )
                            or href_lower.startswith(
                                "https://t.me/socks?"
                            )
                            or href_lower.startswith(
                                "http://t.me/proxy?"
                            )
                            or href_lower.startswith(
                                "http://t.me/socks?"
                            )
                            or href_lower.startswith(
                                "mtproto://"
                            )
                            or href_lower.startswith(
                                "socks5://"
                            )
                        )

                        if not valid:
                            continue

                        normalized = (
                            self.normalize_proxy(
                                href
                            )
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

                    if (
                        not found_from_text
                        and not found_webproxy_from_text
                        and (
                            len(result)
                            == button_proxies_before
                        )
                        and self.has_ad_keywords(
                            message_text
                        )
                    ):
                        ad_messages += 1

                if stop_scanning:
                    break

                if page_oldest_id is None:
                    break

                if (
                    previous_oldest_id is not None
                    and page_oldest_id
                    >= previous_oldest_id
                ):
                    break

                previous_oldest_id = (
                    page_oldest_id
                )

                if len(result) >= (
                    MAX_MESSAGES_PER_CHANNEL
                    * MAX_PROXIES_PER_POST
                ):
                    break

                before = page_oldest_id

                time.sleep(0.3)

            except Exception as e:
                logger.error(
                    f"Extraction failed for "
                    f"{url} on page "
                    f"{page_number}: {e}"
                )
                break

        if pages_loaded == 0:
            self.update_dead_cache(url)
            return []

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
            f"ads={ad_messages}, "
            f"new={len(result)}"
        )

        return result

    def classify_proxy(
        self,
        proxy: str
    ) -> Optional[str]:

        proxy_lower = proxy.lower()

        if (
            proxy_lower.startswith(
                "https://t.me/webproxy?"
            )
            or proxy_lower.startswith(
                "http://t.me/webproxy?"
            )
            or proxy_lower.startswith(
                "tg://webproxy?"
            )
        ):
            return "WEB Proxy"

        if (
            proxy_lower.startswith(
                "tg://proxy?"
            )
            or proxy_lower.startswith(
                "https://t.me/proxy?"
            )
            or proxy_lower.startswith(
                "http://t.me/proxy?"
            )
            or proxy_lower.startswith(
                "mtproto://"
            )
        ):
            return "MTProto"

        if (
            proxy_lower.startswith(
                "tg://socks?"
            )
            or proxy_lower.startswith(
                "https://t.me/socks?"
            )
            or proxy_lower.startswith(
                "http://t.me/socks?"
            )
            or proxy_lower.startswith(
                "socks5://"
            )
        ):
            return "SOCKS5"

        return None

    def collect_all_proxies(
        self
    ) -> List[Tuple[str, str]]:

        all_proxies = []
        seen = set()
        unknown_rejected = 0

        logger.info(
            f"Starting collection from "
            f"{len(CHANNELS)} channels"
        )

        for channel_index, channel in enumerate(
            CHANNELS,
            start=1
        ):

            logger.info(
                f"Processing channel "
                f"{channel_index}/"
                f"{len(CHANNELS)}: "
                f"{channel}"
            )

            proxies = (
                self.extract_proxies_from_channel(
                    channel
                )
            )

            channel_new = 0

            for proxy in proxies:

                if proxy in seen:
                    continue

                proxy_type = (
                    self.classify_proxy(
                        proxy
                    )
                )

                if proxy_type is None:
                    unknown_rejected += 1
                    logger.warning(
                        f"Rejected unknown proxy type: "
                        f"{proxy[:80]}"
                    )
                    continue

                seen.add(proxy)

                all_proxies.append(
                    (
                        proxy,
                        proxy_type
                    )
                )

                channel_new += 1

            logger.info(
                f"Channel completed: "
                f"{channel} -> "
                f"{channel_new} new unique proxies"
            )

        mtproto_count = sum(
            1
            for _, proxy_type
            in all_proxies
            if proxy_type == "MTProto"
        )

        socks_count = sum(
            1
            for _, proxy_type
            in all_proxies
            if proxy_type == "SOCKS5"
        )

        webproxy_count = sum(
            1
            for _, proxy_type
            in all_proxies
            if proxy_type == "WEB Proxy"
        )

        logger.info(
            f"Total new proxies collected: "
            f"{len(all_proxies)} | "
            f"MTProto: {mtproto_count} | "
            f"SOCKS5: {socks_count} | "
            f"WEB Proxy: {webproxy_count} | "
            f"Rejected unknown: {unknown_rejected}"
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
                timeout=TELEGRAM_TIMEOUT
            )

            try:
                result = response.json()

            except ValueError:
                result = {
                    "ok": False,
                    "description": response.text
                }

            if (
                not response.ok
                or not result.get("ok")
            ):
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

    def send_sticker(
        self
    ) -> Optional[int]:

        result = self._request(
            "sendSticker",
            {
                "chat_id": self.chat_id,
                "sticker": STICKER_ID
            }
        )

        if result and result.get("ok"):

            message_id = (
                result["result"]["message_id"]
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

            return (
                result["result"]["message_id"]
            )

        return None

    def create_proxy_keyboard(
        self,
        proxies: List[Tuple[str, str]]
    ) -> Optional[dict]:

        keyboard = []
        row = []

        for proxy, proxy_type in proxies:

            if proxy_type == "WEB Proxy":

                row.append({
                    "text": "WEB Proxy",
                    "url": proxy
                })

            elif proxy_type == "MTProto":

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

    def create_caption(
        self,
        proxies: List[Tuple[str, str]]
    ) -> str:

        return (
            """🅿🆁🅾🆇🆈

🛜 پروکسی‌های جدید.
✅ برای اتصال به پروکسی‌های MTProto، SOCKS5 و WEB Proxy از دکمه‌های زیر استفاده کنید.

➖➖➖➖➖➖➖➖
<blockquote>@AristaProxy</blockquote>
➖➖➖➖➖➖➖➖
#Arista #پروکسی #proxy #MTProto #SOCKS5 #WebProxy
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

        keyboard = (
            self.create_proxy_keyboard(
                proxies
            )
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

        self.ext = (
            MTProtoSocksExtractor()
        )

        self.sender = TelegramSender(
            BOT_TOKEN,
            CHANNEL_ID
        )

    async def run_once(self):

        proxies = (
            self.ext.collect_all_proxies()
        )

        if not proxies:

            logger.info(
                "No new proxies found."
            )

            return

        sent_count = 0

        total_batches = (
            (
                len(proxies)
                + MAX_PROXIES_PER_POST
                - 1
            )
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
                index:
                index + MAX_PROXIES_PER_POST
            ]

            batch_number = (
                index
                // MAX_PROXIES_PER_POST
            ) + 1

            logger.info(
                f"Sending batch "
                f"{batch_number}/"
                f"{total_batches} "
                f"with {len(batch)} proxies."
            )

            message_id = (
                self.sender.send_proxies_batch(
                    batch
                )
            )

            if message_id:

                batch_proxies = [
                    proxy
                    for proxy, _
                    in batch
                ]

                mark_as_sent_batch(
                    batch_proxies
                )

                for proxy in batch_proxies:

                    self.ext.sent_hashes.add(
                        hashlib.md5(
                            proxy.encode(
                                "utf-8"
                            )
                        ).hexdigest()
                    )

                sent_count += len(
                    batch_proxies
                )

                logger.info(
                    f"Batch {batch_number} "
                    f"sent and committed successfully: "
                    f"{message_id}"
                )

            else:

                logger.error(
                    f"Batch {batch_number} failed."
                )

            await asyncio.sleep(1)

        if sent_count > 0:

            self.sender.send_sticker()

            logger.info(
                f"Run completed successfully. "
                f"Sent: {sent_count} proxies."
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
