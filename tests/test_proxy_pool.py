"""Unit tests for sticky proxy pool (no network)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import proxy_pool


class ProxyPoolTests(unittest.TestCase):
    def tearDown(self):
        proxy_pool.clear_runtime_proxy()
        proxy_pool.configure_pool(path="", mode="off", proxies=[])

    def test_normalize_and_load_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "p.txt"
            p.write_text(
                "# comment\n"
                "http://u:p@1.1.1.1:3129\n"
                "2.2.2.2:8080\n"
                "http://u:p@1.1.1.1:3129\n"
                "\n",
                encoding="utf-8",
            )
            items = proxy_pool.load_proxy_file(p)
            self.assertEqual(len(items), 2)
            self.assertTrue(items[0].startswith("http://"))
            self.assertEqual(items[1], "http://2.2.2.2:8080")

    def test_rotate_and_pin(self):
        proxies = [
            "http://a:x@10.0.0.1:3129",
            "http://b:y@10.0.0.2:3129",
            "http://c:z@10.0.0.3:3129",
        ]
        proxy_pool.configure_pool(proxies=proxies, mode="rotate")
        cfg = {"proxy_file": "x", "proxy_pool_mode": "rotate", "proxy": ""}
        # Force pool without re-read of missing file path by patching size path:
        # acquire reconfigures from file; use pick + set for unit clarity.
        a = proxy_pool.pick_pool_proxy()
        b = proxy_pool.pick_pool_proxy()
        c = proxy_pool.pick_pool_proxy()
        d = proxy_pool.pick_pool_proxy()
        self.assertEqual(a, proxies[0])
        self.assertEqual(b, proxies[1])
        self.assertEqual(c, proxies[2])
        self.assertEqual(d, proxies[0])

        proxy_pool.set_runtime_proxy(a)
        self.assertEqual(proxy_pool.get_runtime_proxy(), a)
        self.assertEqual(proxy_pool.resolve_active_proxy(cfg), a)
        # mint override
        self.assertEqual(
            proxy_pool.resolve_active_proxy(
                {**cfg, "cpa_proxy": "http://mint:1@9.9.9.9:1"}, for_mint=True
            ),
            "http://mint:1@9.9.9.9:1",
        )
        # mint without override keeps pin
        self.assertEqual(proxy_pool.resolve_active_proxy(cfg, for_mint=True), a)

    def test_mark_dead_skips(self):
        proxies = ["http://a@1.1.1.1:1", "http://b@2.2.2.2:2"]
        proxy_pool.configure_pool(proxies=proxies, mode="rotate")
        proxy_pool.mark_proxy_dead(proxies[0])
        self.assertEqual(proxy_pool.pick_pool_proxy(), proxies[1])
        self.assertEqual(proxy_pool.pick_pool_proxy(), proxies[1])

    def test_acquire_static_fallback(self):
        proxy_pool.configure_pool(proxies=[], mode="rotate")
        cfg = {"proxy": "http://static:pw@8.8.8.8:3129", "proxy_pool_mode": "rotate"}
        # Empty pool file path
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "none.txt")
            cfg["proxy_file"] = missing
            p = proxy_pool.acquire_proxy_for_account(cfg)
            self.assertEqual(p, "http://static:pw@8.8.8.8:3129")
            self.assertEqual(proxy_pool.get_runtime_proxy(), p)

    def test_clear_pin_for_direct(self):
        proxy_pool.set_runtime_proxy("http://x@1.1.1.1:1")
        proxy_pool.clear_pin_for_direct()
        self.assertIsNone(proxy_pool.get_runtime_proxy())

    def test_log_label_redacts(self):
        lab = proxy_pool.proxy_log_label("http://user:secret@1.2.3.4:3129")
        self.assertIn("1.2.3.4:3129", lab)
        self.assertNotIn("secret", lab)
        self.assertIn("user:***", lab)

    def test_bypass_mail_and_tailscale(self):
        cfg = {
            "cloudflare_api_base": "https://sf4.yyjeqhc.cn/mail-api",
            "grok2api_remote_base": "http://oe:8000/admin/api",
            "proxy_bypass_hosts": "sf4.yyjeqhc.cn,oe,localhost",
        }
        self.assertTrue(
            proxy_pool.should_bypass_proxy_for_url(
                "https://sf4.yyjeqhc.cn/mail-api/api/new_address", cfg
            )
        )
        self.assertTrue(
            proxy_pool.should_bypass_proxy_for_url("http://oe:8000/admin/api/tokens", cfg)
        )
        self.assertTrue(
            proxy_pool.should_bypass_proxy_for_url("http://127.0.0.1:8000/x", cfg)
        )
        # x.ai must still use residential proxy
        self.assertFalse(
            proxy_pool.should_bypass_proxy_for_url("https://accounts.x.ai/sign-up", cfg)
        )
        self.assertFalse(
            proxy_pool.should_bypass_proxy_for_url(
                "https://cli-chat-proxy.grok.com/v1/models", cfg
            )
        )


if __name__ == "__main__":
    unittest.main()
