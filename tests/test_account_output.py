import json
import tempfile
import unittest
from pathlib import Path

from account_output import (
    account_jsonl_path,
    append_account_record,
    collect_account_records,
    parse_account_line,
)


TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.payload.signature"


class AccountOutputTests(unittest.TestCase):
    def test_legacy_parser_handles_password_ending_with_dash(self):
        line = f"user@example.com----N4057c52c!a7#zSx-inH-----{TOKEN}"

        record = parse_account_line(line)

        self.assertIsNotNone(record)
        self.assertEqual(record["email"], "user@example.com")
        self.assertEqual(record["password"], "N4057c52c!a7#zSx-inH-")
        self.assertEqual(record["sso"], TOKEN)

    def test_append_account_record_writes_legacy_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = Path(tmp) / "accounts_20260707_010203.txt"

            append_account_record(txt_path, "user@example.com", "pass----word", TOKEN)

            self.assertEqual(
                txt_path.read_text(encoding="utf-8").splitlines(),
                [f"user@example.com----pass----word----{TOKEN}"],
            )
            jsonl_path = account_jsonl_path(txt_path)
            payload = json.loads(jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["email"], "user@example.com")
            self.assertEqual(payload["password"], "pass----word")
            self.assertEqual(payload["sso"], TOKEN)
            self.assertEqual(payload["format"], "grok-register-account-v1")

    def test_collect_account_records_deduplicates_txt_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt_path = Path(tmp) / "accounts_20260707_010203.txt"
            append_account_record(txt_path, "user@example.com", "pass", TOKEN)

            records = collect_account_records([txt_path, account_jsonl_path(txt_path)])

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["sso"], TOKEN)


if __name__ == "__main__":
    unittest.main()
