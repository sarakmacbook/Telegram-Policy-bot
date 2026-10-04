import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ADMIN_TOKEN", "unit-test-only-token")

import app


class StubHandler:
    def __init__(self, payload):
        self.payload = payload
        self.response = None
        self.status = None

    def read_json(self, limit=1_000_000):
        return self.payload

    def send_json(self, payload, status=200):
        self.response = payload
        self.status = status


class TelegramUpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.originals = {
            "DATA_DIR": app.DATA_DIR,
            "MEDIA_DIR": app.MEDIA_DIR,
            "DB_PATH": app.DB_PATH,
            "IS_DEMO": app.IS_DEMO,
            "BOT_TOKEN": app.BOT_TOKEN,
            "WEBHOOK_SECRET": app.WEBHOOK_SECRET,
            "BOT_TOKEN_FROM_ENV": app.BOT_TOKEN_FROM_ENV,
            "WEBHOOK_SECRET_FROM_ENV": app.WEBHOOK_SECRET_FROM_ENV,
            "BOT_TOKEN_SOURCE": app.BOT_TOKEN_SOURCE,
            "BOT_USERNAME": app.BOT_USERNAME,
            "WEBHOOK_URL": app.WEBHOOK_URL,
            "download_telegram_file": app.download_telegram_file,
            "telegram_api": app.telegram_api,
        }
        app.DATA_DIR = Path(self.tmp.name)
        app.MEDIA_DIR = app.DATA_DIR / "media"
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        app.IS_DEMO = False
        app.BOT_TOKEN = ""
        app.WEBHOOK_SECRET = ""
        app.BOT_TOKEN_FROM_ENV = False
        app.WEBHOOK_SECRET_FROM_ENV = False
        app.BOT_TOKEN_SOURCE = "none"
        app.BOT_USERNAME = ""
        app.WEBHOOK_URL = ""
        app.init_db()
        conn = app.open_db()
        for key, value in {
            "auto_hide": False,
            "auto_hide_media": False,
            "review_all_media": False,
            "notify_chat": True,
            "dm_on_remove": False,
        }.items():
            app.save_setting(conn, key, value)
        conn.commit()
        conn.close()

    def tearDown(self):
        for name, value in self.originals.items():
            setattr(app, name, value)
        self.tmp.cleanup()

    @staticmethod
    def update(update_id, text):
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id + 100,
                "date": 1_800_000_000,
                "text": text,
                "from": {"id": 42, "first_name": "Test", "username": "test_user"},
                "chat": {"id": -1001234567890, "type": "supergroup", "title": "Test room"},
            },
        }

    def test_only_flagged_updates_are_retained_and_updates_are_deduplicated(self):
        app.handle_telegram_update(self.update(50, "I will hurt you."))
        app.handle_telegram_update(self.update(51, "The meeting starts at noon."))
        app.handle_telegram_update(self.update(50, "I will hurt you."))
        conn = app.open_db()
        try:
            rows = conn.execute("SELECT * FROM cases").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "Threats & violence")
        self.assertEqual(rows[0]["message_text"], "I will hurt you.")

    def test_auto_hide_is_blocked_when_flagged_image_cannot_be_archived(self):
        conn = app.open_db()
        app.save_setting(conn, "auto_hide", True)
        conn.commit()
        conn.close()
        app.BOT_TOKEN = "test-bot-token"
        app.download_telegram_file = lambda _file_id: ""
        calls = []
        app.telegram_api = lambda method, payload, **kwargs: calls.append((method, payload))
        update = self.update(60, "I will hurt you.")
        update["message"]["photo"] = [{"file_id": "telegram-file-id"}]
        del update["message"]["text"]
        update["message"]["caption"] = "I will hurt you."
        app.handle_telegram_update(update)
        conn = app.open_db()
        try:
            row = conn.execute("SELECT * FROM cases").fetchone()
        finally:
            conn.close()
        self.assertFalse(row["removed"])
        self.assertIn("automatic deletion is blocked", row["operation_note"])
        self.assertEqual(calls, [])

    def test_text_case_can_be_auto_hidden_after_a_copy_is_saved(self):
        conn = app.open_db()
        app.save_setting(conn, "auto_hide", True)
        conn.commit()
        conn.close()
        app.BOT_TOKEN = "test-bot-token"
        calls = []
        app.telegram_api = lambda method, payload, **kwargs: calls.append((method, payload)) or {}
        app.handle_telegram_update(self.update(70, "I will hurt you."))
        conn = app.open_db()
        try:
            row = conn.execute("SELECT * FROM cases").fetchone()
        finally:
            conn.close()
        self.assertTrue(row["removed"])
        self.assertEqual(row["status"], "pending")
        self.assertIn("deleteMessage", [call[0] for call in calls])

    def test_auto_hide_sends_reason_by_dm_when_enabled_and_user_is_eligible(self):
        conn = app.open_db()
        app.save_setting(conn, "auto_hide", True)
        app.save_setting(conn, "notify_chat", False)
        app.save_setting(conn, "dm_on_remove", True)
        conn.commit()
        conn.close()
        app.BOT_TOKEN = "test-bot-token"
        calls = []
        app.telegram_api = lambda method, payload, **kwargs: calls.append((method, payload)) or {}

        app.handle_telegram_update(self.update(80, "I will hurt you."))

        dm_calls = [payload for method, payload in calls if method == "sendMessage"]
        self.assertEqual(len(dm_calls), 1)
        self.assertEqual(dm_calls[0]["chat_id"], 42)
        self.assertIn("Reason:", dm_calls[0]["text"])
        self.assertIn("direct threat", dm_calls[0]["text"])
        conn = app.open_db()
        try:
            row = conn.execute("SELECT * FROM cases").fetchone()
        finally:
            conn.close()
        self.assertTrue(row["removed"])
        self.assertTrue(row["sender_dm_allowed"])
        self.assertIn("private removal notice was sent", row["operation_note"])

    def test_anonymous_sender_is_not_dmed(self):
        conn = app.open_db()
        app.save_setting(conn, "auto_hide", True)
        app.save_setting(conn, "notify_chat", False)
        app.save_setting(conn, "dm_on_remove", True)
        conn.commit()
        conn.close()
        app.BOT_TOKEN = "test-bot-token"
        calls = []
        app.telegram_api = lambda method, payload, **kwargs: calls.append((method, payload)) or {}
        update = self.update(81, "I will hurt you.")
        update["message"]["from"] = None
        update["message"]["sender_chat"] = {"id": -1001234567890, "title": "Anonymous admin"}

        app.handle_telegram_update(update)

        self.assertEqual([method for method, _ in calls], ["deleteMessage"])
        conn = app.open_db()
        try:
            row = conn.execute("SELECT * FROM cases").fetchone()
        finally:
            conn.close()
        self.assertFalse(row["sender_dm_allowed"])
        self.assertIn("no eligible personal sender", row["operation_note"])

    def test_failed_private_notice_does_not_cancel_removal(self):
        conn = app.open_db()
        app.save_setting(conn, "auto_hide", True)
        app.save_setting(conn, "notify_chat", False)
        app.save_setting(conn, "dm_on_remove", True)
        conn.commit()
        conn.close()
        app.BOT_TOKEN = "test-bot-token"
        calls = []

        def telegram_api(method, payload, **kwargs):
            if method == "sendMessage" and payload["chat_id"] == 42:
                raise RuntimeError("Forbidden: user has not started the bot")
            calls.append((method, payload))
            return {}

        app.telegram_api = telegram_api
        app.handle_telegram_update(self.update(82, "I will hurt you."))
        conn = app.open_db()
        try:
            row = conn.execute("SELECT * FROM cases").fetchone()
        finally:
            conn.close()
        self.assertTrue(row["removed"])
        self.assertIn("could not be delivered", row["operation_note"])

    def test_manual_hide_sends_review_reason_by_dm(self):
        app.BOT_TOKEN = "test-bot-token"
        app.handle_telegram_update(self.update(83, "I will hurt you."))
        conn = app.open_db()
        try:
            case_id = conn.execute("SELECT id FROM cases").fetchone()["id"]
            app.save_setting(conn, "dm_on_remove", True)
            conn.commit()
        finally:
            conn.close()
        calls = []
        app.telegram_api = lambda method, payload, **kwargs: calls.append((method, payload)) or {}
        request = StubHandler({"action": "hide", "note": "Reviewed by a moderator"})

        app.Handler.api_review(request, case_id)

        self.assertEqual(request.status, 200)
        dm = next(payload for method, payload in calls if method == "sendMessage")
        self.assertEqual(dm["chat_id"], 42)
        self.assertIn("direct threat", dm["text"])
        self.assertIn("private removal notice was sent", request.response["operation_note"])

    def test_web_ui_connect_verifies_and_persists_bot_credentials_privately(self):
        calls = []

        def telegram_api(method, payload, *, token=None, **kwargs):
            calls.append((method, payload, token))
            if method == "getMe":
                return {"is_bot": True, "username": "review_helper"}
            return {}

        app.telegram_api = telegram_api
        request = StubHandler({
            "bot_token": "123456:example-token-from-test",
            "public_url": "https://moderation.example.com/",
        })

        app.Handler.api_telegram_connect(request)

        self.assertEqual(request.status, 200)
        self.assertEqual([call[0] for call in calls], ["getMe", "setWebhook"])
        self.assertEqual(calls[0][2], "123456:example-token-from-test")
        self.assertEqual(calls[1][1]["url"], "https://moderation.example.com/telegram/webhook")
        self.assertTrue(calls[1][1]["secret_token"])
        self.assertNotIn("bot_token", request.response)
        self.assertEqual(request.response["bot_username"], "review_helper")

        credentials_path = app.DATA_DIR / "telegram_credentials.json"
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        self.assertEqual(credentials["bot_token"], "123456:example-token-from-test")
        self.assertEqual(credentials["webhook_secret"], calls[1][1]["secret_token"])
        self.assertEqual(credentials_path.stat().st_mode & 0o777, 0o600)

        app.BOT_TOKEN = ""
        app.WEBHOOK_SECRET = ""
        app.BOT_TOKEN_SOURCE = "none"
        app.BOT_USERNAME = ""
        app.WEBHOOK_URL = ""
        app.load_telegram_credentials()
        self.assertEqual(app.BOT_TOKEN, "123456:example-token-from-test")
        self.assertEqual(app.BOT_TOKEN_SOURCE, "web_ui")
        self.assertEqual(app.BOT_USERNAME, "review_helper")

    def test_web_ui_connect_rejects_non_https_webhook_url_before_contacting_telegram(self):
        calls = []
        app.telegram_api = lambda *args, **kwargs: calls.append((args, kwargs))
        request = StubHandler({"bot_token": "123456:test", "public_url": "http://localhost:8000"})

        app.Handler.api_telegram_connect(request)

        self.assertEqual(request.status, 400)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
