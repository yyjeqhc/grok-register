import json
import tempfile
import unittest
from pathlib import Path

from scripts import reconcile_grok2api_accounts as reconcile
from scripts.reconcile_grok2api_accounts import find_account_files, pool_from_config


class ReconcileGrok2apiAccountsTests(unittest.TestCase):
    def test_find_account_files_returns_txt_and_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wanted = [
                root / "accounts_20260707_010203.txt",
                root / "accounts_20260707_010203.jsonl",
            ]
            for path in wanted:
                path.write_text("", encoding="utf-8")
            (root / "notes.txt").write_text("", encoding="utf-8")

            self.assertEqual(find_account_files(root), wanted)

    def test_pool_from_config_maps_register_pool_name(self):
        self.assertEqual(pool_from_config({"grok2api_pool_name": "ssoBasic"}), "basic")
        self.assertEqual(pool_from_config({"grok2api_pool_name": "ssoSuper"}), "super")
        self.assertEqual(pool_from_config({"grok2api_pool_name": "basic"}), "basic")

    def test_add_tokens_remote_requests_auto_nsfw_when_enabled(self):
        calls = []
        original_post_json = reconcile._post_json

        def fake_post_json(url, payload, timeout):
            calls.append((url, payload, timeout))
            return {"status": "success"}

        try:
            reconcile._post_json = fake_post_json

            result = reconcile.add_tokens_remote(
                "http://127.0.0.1:8000/admin/api",
                "app-key",
                "basic",
                ["token-1"],
                auto_nsfw=True,
            )
        finally:
            reconcile._post_json = original_post_json

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(len(calls), 1)
        url, payload, timeout = calls[0]
        self.assertIn("app_key=app-key", url)
        self.assertIn("auto_nsfw=true", url)
        self.assertEqual(payload, {"tokens": ["token-1"], "pool": "basic", "tags": ["auto-register", "reconciled"]})
        self.assertEqual(timeout, 30)


if __name__ == "__main__":
    unittest.main()
