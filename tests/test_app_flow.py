import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("ADMIN_TOKEN", "unit-test-only-token")

import app


class TelegramUpdateFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.originals = {
            "DATA_DIR": app.DATA_DIR,
            "MEDIA_DIR": app.MEDIA_DIR,
            "DB_PATH": app.DB_PATH,
            "IS_DEMO": app.IS_DEMO,
            "BOT_TOKEN": app.BOT_TOKEN,
            "download_telegram_file": app.download_telegram_file,
            "telegram_api": app.telegram_api,
        }
        app.DATA_DIR = Path(self.tmp.name)
        app.MEDIA_DIR = app.DATA_DIR / "media"
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        app.IS_DEMO = False
        app.BOT_TOKEN = ""
        app.init_db()

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


if __name__ == "__main__":
    unittest.main()
