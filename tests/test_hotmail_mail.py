"""Unit tests for hotmail pool + code extraction (no network)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hotmail_mail


class HotmailMailTests(unittest.TestCase):
    def test_parse_line(self):
        acc = hotmail_mail.parse_line(
            "a@hotmail.com----pw----cid----rtoken"
        )
        self.assertEqual(acc.email, "a@hotmail.com")
        self.assertEqual(acc.client_id, "cid")
        self.assertIsNone(hotmail_mail.parse_line("bad"))

    def test_extract_xai_code(self):
        code = hotmail_mail.extract_xai_code(
            "body", "MJB-CT6 xAI confirmation code"
        )
        self.assertEqual(code, "MJB-CT6")

    def test_claim_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = Path(tmp) / "tokens.txt"
            pool.write_text(
                "one@hotmail.com----p----c----r1\n"
                "two@hotmail.com----p----c----r2\n",
                encoding="utf-8",
            )
            cfg = {"hotmail_tokens_file": str(pool)}
            with patch.object(hotmail_mail, "refresh_access_token", return_value="atok"):
                email, tok = hotmail_mail.get_email_and_token(config=cfg)
            self.assertEqual(email, "one@hotmail.com")
            self.assertTrue(tok.startswith("hotmail:"))
            remaining = pool.read_text(encoding="utf-8")
            self.assertIn("two@hotmail.com", remaining)
            self.assertNotIn("one@hotmail.com", remaining)

            hotmail_mail.release_account(email, reason="retry", config=cfg)
            back = pool.read_text(encoding="utf-8")
            self.assertIn("one@hotmail.com", back)

    def test_mark_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = Path(tmp) / "tokens.txt"
            pool.write_text("u@hotmail.com----p----c----r\n", encoding="utf-8")
            cfg = {"hotmail_tokens_file": str(pool)}
            with patch.object(hotmail_mail, "refresh_access_token", return_value="atok"):
                email, _ = hotmail_mail.get_email_and_token(config=cfg)
            hotmail_mail.mark_used(email, config=cfg)
            used = pool.with_name("tokens_used.txt")
            self.assertTrue(used.is_file())
            self.assertIn("u@hotmail.com", used.read_text(encoding="utf-8"))
            self.assertEqual(pool.read_text(encoding="utf-8").strip(), "")


if __name__ == "__main__":
    unittest.main()
