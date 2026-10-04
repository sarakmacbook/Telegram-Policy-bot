#!/usr/bin/env python3
"""Telegram moderation webhook and private admin review console (stdlib only)."""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from moderation import _clean_custom_rules, evaluate_message, summarize_findings

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "moderation.sqlite3"
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_DEMO = APP_ENV == "demo"
PORT = int(os.environ.get("PORT", "8000"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
MONITOR_PRIVATE_CHATS = os.environ.get("MONITOR_PRIVATE_CHATS", "0").strip().lower() in {"1", "true", "yes"}
MEDIA_MAX_BYTES = max(100_000, int(os.environ.get("MEDIA_MAX_BYTES", "12000000")))

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()
if not ADMIN_TOKEN:
    ADMIN_TOKEN = secrets.token_urlsafe(32)
    print("\nAdmin token (set ADMIN_TOKEN to keep it stable): " + ADMIN_TOKEN, flush=True)
    print("Keep this value private. It grants access to stored message content and media.\n", flush=True)

ENV_CUSTOM_RULES: list[dict[str, Any]] = []
try:
    parsed_rules = json.loads(os.environ.get("CUSTOM_RULES_JSON", "[]"))
    if isinstance(parsed_rules, list):
        ENV_CUSTOM_RULES = _clean_custom_rules(parsed_rules)
except (json.JSONDecodeError, TypeError):
    print("Warning: CUSTOM_RULES_JSON was invalid; ignoring it.", file=sys.stderr)

DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_hide": os.environ.get("AUTO_HIDE", "0").strip().lower() in {"1", "true", "yes"},
    "auto_hide_media": os.environ.get("AUTO_HIDE_MEDIA", "0").strip().lower() in {"1", "true", "yes"},
    "review_all_media": os.environ.get("REVIEW_ALL_MEDIA", "0").strip().lower() in {"1", "true", "yes"},
    "notify_chat": os.environ.get("NOTIFY_CHAT", "1").strip().lower() in {"1", "true", "yes"},
    "jurisdictions": os.environ.get("JURISDICTIONS", "Cambodia").strip()[:240] or "Cambodia",
    "custom_rules": ENV_CUSTOM_RULES,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id INTEGER UNIQUE,
    chat_id INTEGER,
    message_id INTEGER,
    chat_title TEXT NOT NULL DEFAULT '',
    chat_type TEXT NOT NULL DEFAULT '',
    sender_name TEXT NOT NULL DEFAULT 'Unknown sender',
    sender_username TEXT NOT NULL DEFAULT '',
    sender_id INTEGER,
    message_text TEXT NOT NULL DEFAULT '',
    media_type TEXT NOT NULL DEFAULT '',
    media_file_id TEXT NOT NULL DEFAULT '',
    media_path TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Needs review',
    severity TEXT NOT NULL DEFAULT 'medium',
    reason TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    signals_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    removed INTEGER NOT NULL DEFAULT 0,
    is_demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT '',
    review_note TEXT NOT NULL DEFAULT '',
    operation_note TEXT NOT NULL DEFAULT '',
    message_link TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS cases_status_created_idx ON cases(status, created_at DESC);
CREATE INDEX IF NOT EXISTS cases_chat_message_idx ON cases(chat_id, message_id);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(DATA_DIR, 0o700)
        os.chmod(MEDIA_DIR, 0o700)
    except OSError:
        pass
    conn = open_db()
    try:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value_json) VALUES (?, ?)",
                (key, json.dumps(value, ensure_ascii=False)),
            )
        conn.commit()
        if IS_DEMO:
            seed_demo_cases(conn)
            conn.commit()
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
    finally:
        conn.close()


def seed_demo_cases(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]:
        return
    now = int(time.time())
    samples = [
        {
            "chat_title": "Community · Announcements",
            "chat_type": "supergroup",
            "sender_name": "Nara S.",
            "sender_username": "nara_s",
            "message_text": "Verify your wallet urgently to claim your reward. This is only a demo message.",
            "category": "Scam or phishing",
            "severity": "medium",
            "reason": "The message resembles a credential-stealing or financial scam.",
            "source": "Built-in phrase check",
            "signals_json": [{"category": "Scam or phishing", "severity": "medium", "source": "Built-in phrase check"}],
            "removed": 1,
            "status": "pending",
            "media_type": "",
            "created_at": now - 9 * 60,
            "operation_note": "Demo record — no Telegram message was changed.",
        },
        {
            "chat_title": "Neighborhood Watch",
            "chat_type": "supergroup",
            "sender_name": "Chan M.",
            "sender_username": "chan_m",
            "message_text": "I'm going to hurt you. This is a simulated example for moderator training.",
            "category": "Threats & violence",
            "severity": "high",
            "reason": "A direct threat or intent to harm may be present.",
            "source": "Built-in phrase check",
            "signals_json": [{"category": "Threats & violence", "severity": "high", "source": "Built-in phrase check"}],
            "removed": 0,
            "status": "pending",
            "media_type": "",
            "created_at": now - 26 * 60,
            "operation_note": "Demo record — no Telegram message was changed.",
        },
        {
            "chat_title": "Travel & Culture",
            "chat_type": "group",
            "sender_name": "Sophea K.",
            "sender_username": "sophea_k",
            "message_text": "Image shared without a caption. Sample only; image content has not been analyzed.",
            "category": "Visual review needed",
            "severity": "medium",
            "reason": "Media is queued for a person to review; image content is not automatically classified.",
            "source": "Media review setting",
            "signals_json": [{"category": "Visual review needed", "severity": "medium", "source": "Media review setting"}],
            "removed": 0,
            "status": "pending",
            "media_type": "photo",
            "created_at": now - 54 * 60,
            "operation_note": "Demo image placeholder — no real image is stored.",
        },
        {
            "chat_title": "Community · Announcements",
            "chat_type": "supergroup",
            "sender_name": "Admin Team",
            "sender_username": "community_admin",
            "message_text": "Demo case reviewed and dismissed. It remains as a sample history row.",
            "category": "Targeted harassment",
            "severity": "low",
            "reason": "A configured demo phrase matched; a human dismissed this sample.",
            "source": "Admin-configured phrase check",
            "signals_json": [{"category": "Targeted harassment", "severity": "low", "source": "Admin-configured phrase check"}],
            "removed": 0,
            "status": "dismissed",
            "media_type": "",
            "created_at": now - 8 * 3600,
            "reviewed_at": now - 7 * 3600,
            "operation_note": "Demo record — no Telegram message was changed.",
        },
        {
            "chat_title": "Travel & Culture",
            "chat_type": "group",
            "sender_name": "Dara P.",
            "sender_username": "dara_p",
            "message_text": "Demo case approved by a human reviewer.",
            "category": "Scam or phishing",
            "severity": "medium",
            "reason": "A built-in phrase check surfaced this sample for review.",
            "source": "Built-in phrase check",
            "signals_json": [{"category": "Scam or phishing", "severity": "medium", "source": "Built-in phrase check"}],
            "removed": 0,
            "status": "approved",
            "media_type": "",
            "created_at": now - 26 * 3600,
            "reviewed_at": now - 25 * 3600,
            "operation_note": "Demo record — no Telegram message was changed.",
        },
    ]
    for sample in samples:
        created_at = datetime.fromtimestamp(sample.pop("created_at"), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        reviewed_ts = sample.pop("reviewed_at", None)
        reviewed_at = datetime.fromtimestamp(reviewed_ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if reviewed_ts else ""
        columns = [
            "chat_title", "chat_type", "sender_name", "sender_username", "message_text",
            "category", "severity", "reason", "source", "signals_json", "removed", "status",
            "media_type", "created_at", "reviewed_at", "operation_note", "is_demo",
        ]
        values: list[Any] = []
        for column in columns:
            if column == "signals_json":
                values.append(json.dumps(sample.get(column, []), ensure_ascii=False))
            elif column == "is_demo":
                values.append(1)
            elif column == "reviewed_at":
                values.append(reviewed_at)
            else:
                values.append(sample.get(column, ""))
        conn.execute(
            f"INSERT INTO cases ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            values,
        )


def get_settings(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    should_close = conn is None
    conn = conn or open_db()
    try:
        values = dict(DEFAULT_SETTINGS)
        for row in conn.execute("SELECT key, value_json FROM settings"):
            try:
                values[row["key"]] = json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                continue
        return values
    finally:
        if should_close:
            conn.close()


def save_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO settings(key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def telegram_api(method: str, payload: dict[str, Any], *, upload: tuple[str, bytes, str] | None = None) -> dict[str, Any]:
    if not BOT_TOKEN:
        raise RuntimeError("Telegram bot is not configured (BOT_TOKEN is missing).")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    headers: dict[str, str] = {}
    if upload is None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    else:
        boundary = "----moderation" + secrets.token_hex(12)
        chunks: list[bytes] = []
        for key, value in payload.items():
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
                str(value).encode("utf-8"),
                b"\r\n",
            ])
        filename, content, content_type = upload
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{upload[0]}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        body = b"".join(chunks)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read(1_000_000).decode("utf-8"))
        except Exception:
            raise RuntimeError(f"Telegram API returned HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Telegram API request failed: {type(exc).__name__}.") from None
    if not result.get("ok"):
        description = str(result.get("description", "Telegram API rejected the request."))[:240]
        raise RuntimeError(description)
    return result.get("result", {})


def download_telegram_file(file_id: str) -> str:
    """Download a flagged attachment to a private, unguessable filename."""
    if not BOT_TOKEN:
        return ""
    meta = telegram_api("getFile", {"file_id": file_id})
    remote_path = str(meta.get("file_path", ""))
    try:
        remote_size = int(meta.get("file_size", 0) or 0)
    except (TypeError, ValueError):
        remote_size = 0
    if not remote_path or remote_size > MEDIA_MAX_BYTES:
        return ""
    quoted_path = urllib.parse.quote(remote_path, safe="/")
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{quoted_path}"
    try:
        with urllib.request.urlopen(url, timeout=25) as response:
            data = response.read(MEDIA_MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError):
        return ""
    if len(data) > MEDIA_MAX_BYTES:
        return ""
    suffix = Path(remote_path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".avif", ".mp4", ".m4v", ".webm", ".ogg", ".pdf", ".mp3", ".m4a", ".oga", ".opus", ".tgs"}:
        suffix = ".bin"
    filename = uuid.uuid4().hex + suffix
    target = MEDIA_DIR / filename
    target.write_bytes(data)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return filename


def build_message_link(chat: dict[str, Any], message_id: Any) -> str:
    username = str(chat.get("username", "")).strip()
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return ""


def extract_media(message: dict[str, Any]) -> tuple[str, str]:
    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        item = photos[-1]
        return "photo", str(item.get("file_id", ""))
    for kind in ("animation", "video", "document", "audio", "voice", "video_note", "sticker"):
        item = message.get(kind)
        if isinstance(item, dict):
            mime = str(item.get("mime_type", ""))
            if kind == "animation":
                media_type = "animation"
            elif kind == "document" and mime.startswith("image/"):
                media_type = "image"
            else:
                media_type = kind
            return media_type, str(item.get("file_id", ""))
    return "", ""


def handle_telegram_update(update: dict[str, Any]) -> None:
    message: dict[str, Any] | None = None
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        candidate = update.get(key)
        if isinstance(candidate, dict):
            message = candidate
            break
    if not message:
        return
    sender = message.get("from") or message.get("sender_chat") or {}
    if sender.get("is_bot"):
        return
    chat = message.get("chat") or {}
    chat_type = str(chat.get("type", ""))
    if chat_type == "private" and not MONITOR_PRIVATE_CHATS:
        return

    text = str(message.get("text") or message.get("caption") or "")[:20_000]
    media_type, media_file_id = extract_media(message)
    settings = get_settings()
    findings = evaluate_message(
        text,
        has_media=media_type in {"photo", "image"},
        review_all_media=bool(settings.get("review_all_media")),
        custom_rules=settings.get("custom_rules", []),
    )
    if not findings:
        return
    summary = summarize_findings(findings)
    update_id = update.get("update_id")
    try:
        update_id = int(update_id) if update_id is not None else None
    except (TypeError, ValueError):
        update_id = None
    created_at = utc_now()
    try:
        if message.get("date"):
            created_at = datetime.fromtimestamp(int(message["date"]), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        pass

    first_name = str(sender.get("first_name", "")).strip()
    last_name = str(sender.get("last_name", "")).strip()
    sender_name = " ".join(part for part in (first_name, last_name) if part) or str(sender.get("title", "Unknown sender"))
    username = str(sender.get("username", ""))
    chat_title = str(chat.get("title") or chat.get("username") or "Private chat")
    message_id = message.get("message_id")
    try:
        message_id = int(message_id) if message_id is not None else None
    except (TypeError, ValueError):
        message_id = None

    conn = open_db()
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO cases
            (update_id, chat_id, message_id, chat_title, chat_type, sender_name, sender_username, sender_id,
             message_text, media_type, media_file_id, category, severity, reason, source, signals_json,
             status, created_at, message_link)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                update_id,
                chat.get("id"),
                message_id,
                chat_title[:160],
                chat_type[:32],
                sender_name[:160],
                username[:64],
                sender.get("id"),
                text,
                media_type,
                media_file_id,
                summary["category"],
                summary["severity"],
                summary["reason"],
                summary["source"],
                json.dumps(findings, ensure_ascii=False),
                created_at,
                build_message_link(chat, message_id),
            ),
        )
        if cursor.rowcount == 0:
            conn.close()
            return
        case_id = cursor.lastrowid
        conn.commit()
        private_path = ""
        if media_type and media_file_id:
            try:
                private_path = download_telegram_file(media_file_id)
                if private_path:
                    conn.execute("UPDATE cases SET media_path=? WHERE id=?", (private_path, case_id))
                    conn.commit()
                else:
                    conn.execute("UPDATE cases SET operation_note='Attachment could not be archived (unsupported size or download unavailable); automatic deletion is blocked to preserve restorability.' WHERE id=?", (case_id,))
                    conn.commit()
            except Exception as exc:
                conn.execute("UPDATE cases SET operation_note=? WHERE id=?", (f"Attachment archival failed; automatic deletion is blocked. {str(exc)[:140]}", case_id))
                conn.commit()

        contains_text_signal = any(signal.get("key") != "visual_review" for signal in findings)
        contains_media_signal = any(signal.get("key") == "visual_review" for signal in findings)
        should_hide = (
            (bool(settings.get("auto_hide")) and contains_text_signal)
            or (bool(settings.get("auto_hide_media")) and contains_media_signal)
        )
        can_archive_for_restore = not media_type or bool(private_path)
        if should_hide and can_archive_for_restore and chat.get("id") is not None and message_id is not None:
            try:
                telegram_api("deleteMessage", {"chat_id": chat["id"], "message_id": message_id})
                conn.execute("UPDATE cases SET removed=1, operation_note='Message hidden automatically; a saved copy is available to authorized moderators.' WHERE id=?", (case_id,))
                conn.commit()
                if settings.get("notify_chat") and chat_type in {"group", "supergroup"}:
                    try:
                        telegram_api("sendMessage", {
                            "chat_id": chat["id"],
                            "text": f"A message was hidden pending moderator review (case #{case_id}).",
                            "disable_notification": True,
                        })
                    except Exception:
                        pass
            except Exception as exc:
                conn.execute("UPDATE cases SET operation_note=? WHERE id=?", (f"Auto-hide failed; check bot permissions. {str(exc)[:160]}", case_id))
                conn.commit()
    finally:
        conn.close()


def case_to_dict(row: sqlite3.Row, *, include_text: bool = True) -> dict[str, Any]:
    result = dict(row)
    try:
        result["signals"] = json.loads(result.pop("signals_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        result["signals"] = []
    result["removed"] = bool(result.get("removed"))
    result["is_demo"] = bool(result.get("is_demo"))
    media_path = result.get("media_path", "")
    is_image = result.get("media_type") in {"photo", "image"}
    result["has_preview"] = bool(is_image and media_path and (MEDIA_DIR / Path(media_path).name).is_file())
    if not include_text:
        result["message_text"] = str(result.get("message_text", ""))[:240]
    return result


def send_restored_text(chat_id: int, prefix: str, text: str) -> None:
    combined = prefix + ("\n\n" + text if text else "")
    if len(combined) <= 4096:
        telegram_api("sendMessage", {"chat_id": chat_id, "text": combined, "disable_web_page_preview": True})
        return
    telegram_api("sendMessage", {"chat_id": chat_id, "text": prefix[:4096], "disable_web_page_preview": True})
    for start in range(0, len(text), 4096):
        telegram_api("sendMessage", {"chat_id": chat_id, "text": text[start:start + 4096], "disable_web_page_preview": True})


def perform_restore(case: sqlite3.Row) -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Telegram bot is not configured; set BOT_TOKEN to restore this message.")
    chat_id = case["chat_id"]
    if chat_id is None:
        raise RuntimeError("The original chat could not be identified.")
    text = str(case["message_text"] or "")
    author = str(case["sender_name"] or "Unknown sender")
    if case["sender_username"]:
        author += f" (@{case['sender_username']})"
    prefix = f"Moderator-restored copy of a message from {author}. Original Telegram metadata is not restored."
    media_type = str(case["media_type"] or "")
    supported_media = {"photo", "image", "video", "animation", "document", "audio", "voice", "video_note", "sticker"}
    if media_type in supported_media:
        path = str(case["media_path"] or "")
        if not path:
            raise RuntimeError("The attachment was not archived, so it cannot be reposted. Review the original chat manually.")
        file_path = MEDIA_DIR / Path(path).name
        if not file_path.is_file():
            raise RuntimeError("The archived attachment is unavailable, so it cannot be reposted.")
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        methods = {
            "photo": ("sendPhoto", "photo"),
            "image": ("sendDocument", "document"),
            "video": ("sendVideo", "video"),
            "animation": ("sendAnimation", "animation"),
            "document": ("sendDocument", "document"),
            "audio": ("sendAudio", "audio"),
            "voice": ("sendVoice", "voice"),
            "video_note": ("sendVideoNote", "video_note"),
            "sticker": ("sendSticker", "sticker"),
        }
        method, field = methods[media_type]
        payload: dict[str, Any] = {"chat_id": chat_id}
        supports_caption = media_type not in {"video_note", "sticker"}
        if supports_caption:
            caption = prefix + ("\n\n" + text if text else "")
            payload["caption"] = caption[:1000]
        telegram_api(method, payload, upload=(field, file_path.read_bytes(), mime))
        if not supports_caption or (text and len(prefix) + 2 + len(text) > 1000):
            send_restored_text(chat_id, prefix, text)
        return
    send_restored_text(chat_id, prefix, text)


class Handler(BaseHTTPRequestHandler):
    server_version = "PolicyReview/1.0"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        # Avoid logging API query strings, webhook payloads, moderation text, or bearer tokens.
        request_path = getattr(self, "path", "")
        if request_path.startswith("/telegram/webhook") or request_path.startswith("/api/"):
            return
        super().log_message(fmt, *args)

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self, limit: int = 1_000_000) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid content length.") from None
        if length < 0 or length > limit:
            raise ValueError("Request body is too large.")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        return payload

    def is_authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
        return bool(supplied) and hmac.compare_digest(supplied.encode(), ADMIN_TOKEN.encode())

    def require_auth(self) -> bool:
        if self.is_authorized():
            return True
        self.send_json({"error": "Unauthorized"}, 401)
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/health":
            self.send_json({"ok": True, "service": "telegram-policy-review"})
            return
        if path == "/api/public-config":
            payload: dict[str, Any] = {"demo": IS_DEMO, "bot_configured": bool(BOT_TOKEN)}
            if IS_DEMO:
                payload["demo_password"] = ADMIN_TOKEN
            self.send_json(payload)
            return
        if path.startswith("/api/") and not self.require_auth():
            return
        if path == "/api/stats":
            self.api_stats()
            return
        if path == "/api/cases":
            self.api_cases()
            return
        match = re.fullmatch(r"/api/cases/(\d+)", path)
        if match:
            self.api_case(int(match.group(1)))
            return
        match = re.fullmatch(r"/api/media/(\d+)", path)
        if match:
            self.api_media(int(match.group(1)))
            return
        if path == "/api/settings":
            self.send_json(get_settings())
            return
        if path == "/api/connection":
            self.send_json({
                "bot_configured": bool(BOT_TOKEN),
                "webhook_secret_configured": bool(WEBHOOK_SECRET),
                "webhook_path": "/telegram/webhook",
                "private_chats_monitored": MONITOR_PRIVATE_CHATS,
            })
            return
        if path == "/" or path == "/index.html":
            self.serve_static("index.html")
            return
        if path in {"/styles.css", "/app.js", "/favicon.svg"}:
            self.serve_static(path.lstrip("/"))
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/telegram/webhook":
            self.handle_webhook()
            return
        if path.startswith("/api/") and not self.require_auth():
            return
        if path == "/api/settings":
            self.api_save_settings()
            return
        if path == "/api/scan-test":
            self.api_scan_test()
            return
        match = re.fullmatch(r"/api/cases/(\d+)/review", path)
        if match:
            self.api_review(int(match.group(1)))
            return
        self.send_json({"error": "Not found"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()

    def serve_static(self, name: str) -> None:
        allowed = {"index.html", "styles.css", "app.js", "favicon.svg"}
        if name not in allowed:
            self.send_json({"error": "Not found"}, 404)
            return
        target = WEB_DIR / name
        if not target.is_file():
            self.send_json({"error": "Application assets are missing."}, 500)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript"
        if target.suffix == ".css":
            content_type = "text/css"
        self.send_bytes(target.read_bytes(), content_type + "; charset=utf-8", cache="no-cache")

    def api_stats(self) -> None:
        conn = open_db()
        try:
            row = conn.execute(
                """SELECT
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN removed=1 THEN 1 ELSE 0 END) AS hidden,
                   SUM(CASE WHEN status!='pending' AND substr(reviewed_at,1,10)=? THEN 1 ELSE 0 END) AS reviewed_today,
                   SUM(CASE WHEN status='pending' AND severity IN ('high','critical') THEN 1 ELSE 0 END) AS urgent,
                   COUNT(*) AS total
                   FROM cases""",
                (utc_now()[:10],),
            ).fetchone()
            settings = get_settings(conn)
            self.send_json({
                "pending": int(row["pending"] or 0),
                "hidden": int(row["hidden"] or 0),
                "reviewed_today": int(row["reviewed_today"] or 0),
                "urgent": int(row["urgent"] or 0),
                "total": int(row["total"] or 0),
                "jurisdiction_count": len([x for x in str(settings.get("jurisdictions", "")).split(",") if x.strip()]),
                "auto_hide": bool(settings.get("auto_hide")),
                "bot_configured": bool(BOT_TOKEN),
            })
        finally:
            conn.close()

    def api_cases(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        status = params.get("status", ["pending"])[0]
        query = params.get("q", [""])[0].strip()[:120]
        if status not in {"pending", "approved", "dismissed", "all"}:
            self.send_json({"error": "Invalid status filter."}, 400)
            return
        sql = "SELECT * FROM cases WHERE 1=1"
        values: list[Any] = []
        if status != "all":
            sql += " AND status=?"
            values.append(status)
        if query:
            like = f"%{query}%"
            sql += " AND (chat_title LIKE ? OR sender_name LIKE ? OR sender_username LIKE ? OR message_text LIKE ? OR category LIKE ?)"
            values.extend([like] * 5)
        sql += " ORDER BY CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, created_at DESC LIMIT 250"
        conn = open_db()
        try:
            rows = conn.execute(sql, values).fetchall()
            self.send_json({"cases": [case_to_dict(row, include_text=False) for row in rows], "count": len(rows)})
        finally:
            conn.close()

    def api_case(self, case_id: int) -> None:
        conn = open_db()
        try:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                self.send_json({"error": "Case not found."}, 404)
                return
            self.send_json(case_to_dict(row))
        finally:
            conn.close()

    def api_media(self, case_id: int) -> None:
        conn = open_db()
        try:
            row = conn.execute("SELECT media_path FROM cases WHERE id=?", (case_id,)).fetchone()
        finally:
            conn.close()
        if not row or not row["media_path"]:
            self.send_json({"error": "No archived image is available."}, 404)
            return
        filename = Path(row["media_path"]).name
        target = MEDIA_DIR / filename
        if not target.is_file():
            self.send_json({"error": "Archived image is unavailable."}, 404)
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/avif"}:
            self.send_json({"error": "Unsupported image format."}, 415)
            return
        self.send_bytes(target.read_bytes(), content_type, cache="private, no-store")

    def api_save_settings(self) -> None:
        try:
            payload = self.read_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        conn = open_db()
        try:
            if "auto_hide" in payload:
                if not isinstance(payload["auto_hide"], bool):
                    self.send_json({"error": "auto_hide must be a boolean."}, 400)
                    return
                save_setting(conn, "auto_hide", payload["auto_hide"])
            if "auto_hide_media" in payload:
                if not isinstance(payload["auto_hide_media"], bool):
                    self.send_json({"error": "auto_hide_media must be a boolean."}, 400)
                    return
                save_setting(conn, "auto_hide_media", payload["auto_hide_media"])
            if "review_all_media" in payload:
                if not isinstance(payload["review_all_media"], bool):
                    self.send_json({"error": "review_all_media must be a boolean."}, 400)
                    return
                save_setting(conn, "review_all_media", payload["review_all_media"])
            if "notify_chat" in payload:
                if not isinstance(payload["notify_chat"], bool):
                    self.send_json({"error": "notify_chat must be a boolean."}, 400)
                    return
                save_setting(conn, "notify_chat", payload["notify_chat"])
            if "jurisdictions" in payload:
                jurisdictions = str(payload["jurisdictions"]).strip()[:240]
                save_setting(conn, "jurisdictions", jurisdictions or "Cambodia")
            if "custom_rules" in payload:
                if not isinstance(payload["custom_rules"], list):
                    self.send_json({"error": "custom_rules must be a list."}, 400)
                    return
                save_setting(conn, "custom_rules", _clean_custom_rules(payload["custom_rules"]))
            conn.commit()
            self.send_json(get_settings(conn))
        finally:
            conn.close()

    def api_scan_test(self) -> None:
        try:
            payload = self.read_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        text = str(payload.get("text", ""))[:10_000]
        has_media = bool(payload.get("has_media", False))
        settings = get_settings()
        findings = evaluate_message(
            text,
            has_media=has_media,
            review_all_media=bool(payload.get("review_all_media", settings.get("review_all_media"))),
            custom_rules=payload.get("custom_rules", settings.get("custom_rules", [])),
        )
        self.send_json({"flagged": bool(findings), "findings": findings})

    def api_review(self, case_id: int) -> None:
        try:
            payload = self.read_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        action = str(payload.get("action", ""))
        note = str(payload.get("note", "")).strip()[:1000]
        if action not in {"approve", "dismiss", "restore", "hide"}:
            self.send_json({"error": "Choose approve, dismiss, restore, or hide."}, 400)
            return
        conn = open_db()
        try:
            row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                self.send_json({"error": "Case not found."}, 404)
                return
            removed = bool(row["removed"])
            if row["status"] != "pending" and not (action == "restore" and removed):
                self.send_json({"error": "This case has already been reviewed."}, 409)
                return
            operation_note = str(row["operation_note"] or "")
            if not row["is_demo"]:
                try:
                    if action == "hide" and not removed:
                        if row["chat_id"] is None or row["message_id"] is None:
                            raise RuntimeError("The original Telegram message could not be identified.")
                        if row["media_type"] and not row["media_path"]:
                            raise RuntimeError("The attachment was not archived, so hiding is blocked to preserve the option to restore it.")
                        telegram_api("deleteMessage", {"chat_id": row["chat_id"], "message_id": row["message_id"]})
                        removed = True
                    elif action == "restore" and removed:
                        perform_restore(row)
                        removed = False
                except Exception as exc:
                    self.send_json({"error": str(exc)[:280]}, 502)
                    return
            elif action == "hide":
                removed = True
            elif action == "restore":
                removed = False
            status = "approved" if action in {"approve", "restore"} else "dismissed"
            reviewer_note = note or {
                "approve": "Approved by moderator; original remains visible.",
                "dismiss": "Alert dismissed by moderator.",
                "restore": "A reviewed copy was restored to the chat.",
                "hide": "Message removed by moderator.",
            }[action]
            if action == "hide":
                operation_note = "Demo only — hide action was simulated." if row["is_demo"] else "Message hidden by moderator; saved case retained for review."
            elif action == "restore":
                operation_note = "Demo only — restore action was simulated." if row["is_demo"] else "A new copy was posted after review; original Telegram metadata was not recreated."
            elif action == "dismiss" and removed:
                operation_note = "Moderator confirmed the existing removal. A saved copy remains available to restore."
            conn.execute(
                "UPDATE cases SET status=?, removed=?, reviewed_at=?, review_note=?, operation_note=? WHERE id=?",
                (status, int(removed), utc_now(), reviewer_note, operation_note, case_id),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            self.send_json(case_to_dict(updated))
        finally:
            conn.close()

    def handle_webhook(self) -> None:
        if not BOT_TOKEN:
            self.send_json({"error": "BOT_TOKEN is not configured."}, 503)
            return
        if not WEBHOOK_SECRET:
            self.send_json({"error": "WEBHOOK_SECRET is required before accepting updates."}, 503)
            return
        supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied.encode(), WEBHOOK_SECRET.encode()):
            self.send_json({"error": "Forbidden"}, 403)
            return
        try:
            update = self.read_json(limit=2_000_000)
            handle_telegram_update(update)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, 400)
            return
        except Exception as exc:
            print(f"Webhook processing error: {type(exc).__name__}: {str(exc)[:180]}", file=sys.stderr, flush=True)
            self.send_json({"error": "Update could not be processed."}, 500)
            return
        self.send_json({"ok": True})


def main() -> None:
    init_db()
    print(f"Policy review console listening on 0.0.0.0:{PORT} (environment: {APP_ENV})", flush=True)
    if BOT_TOKEN and not WEBHOOK_SECRET:
        print("Warning: webhook updates are disabled until WEBHOOK_SECRET is configured.", file=sys.stderr, flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping moderation console.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
