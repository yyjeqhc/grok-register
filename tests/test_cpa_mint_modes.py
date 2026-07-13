"""Unit tests for sso_first / device fallback mint modes (no network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cpa_export


class MintModeTests(unittest.TestCase):
    def _cfg(self, **over):
        base = {
            "cpa_export_enabled": True,
            "cpa_auth_dir": "",  # filled per test
            "cpa_mint_mode": "sso_first",
            "cpa_device_fallback": True,
            "cpa_probe_chat": False,
            "cpa_require_chat": False,
            "cpa_copy_to_hotload": False,
            "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
        }
        base.update(over)
        return base

    def test_disabled(self):
        r = cpa_export.export_cpa_xai_for_account(
            "a@b.com", "pw", sso="tok", config={"cpa_export_enabled": False}
        )
        self.assertTrue(r.get("skipped"))

    def test_sso_first_success_skips_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auths"
            auth.mkdir()
            fake_path = auth / "xai-a@b.com.json"
            fake_path.write_text(
                json.dumps(
                    {
                        "type": "xai",
                        "access_token": "at",
                        "refresh_token": "rt",
                        "email": "a@b.com",
                        "base_url": "https://cli-chat-proxy.grok.com/v1",
                        "headers": {},
                    }
                ),
                encoding="utf-8",
            )

            def fake_sso(*_a, **_k):
                return {
                    "mint_ok": True,
                    "method": "sso",
                    "path": str(fake_path),
                    "access_token": "at",
                    "email": "a@b.com",
                    "base_url": "https://cli-chat-proxy.grok.com/v1",
                }

            device_calls = []

            def fake_device(*_a, **_k):
                device_calls.append(1)
                return {"mint_ok": False, "error": "should not run", "method": "device"}

            cfg = self._cfg(cpa_auth_dir=str(auth))
            with patch.object(cpa_export, "mint_via_sso", side_effect=fake_sso), patch.object(
                cpa_export, "mint_via_device", side_effect=fake_device
            ):
                r = cpa_export.export_cpa_xai_for_account(
                    "a@b.com", "pw", sso="sso-jwt", config=cfg
                )

            self.assertTrue(r["mint_ok"])
            self.assertTrue(r["ok"])
            self.assertEqual(r["method"], "sso")
            self.assertEqual(device_calls, [])

    def test_sso_fail_falls_back_to_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auths"
            auth.mkdir()
            fake_path = auth / "xai-a@b.com.json"
            fake_path.write_text(
                json.dumps(
                    {
                        "type": "xai",
                        "access_token": "at2",
                        "refresh_token": "rt2",
                        "email": "a@b.com",
                        "headers": {},
                    }
                ),
                encoding="utf-8",
            )

            def fake_sso(*_a, **_k):
                return {"mint_ok": False, "error": "sso dead", "method": "sso"}

            def fake_device(*_a, **_k):
                return {
                    "mint_ok": True,
                    "method": "device",
                    "path": str(fake_path),
                    "access_token": "at2",
                    "email": "a@b.com",
                    "base_url": "https://cli-chat-proxy.grok.com/v1",
                }

            cfg = self._cfg(cpa_auth_dir=str(auth), cpa_mint_mode="sso_first")
            with patch.object(cpa_export, "mint_via_sso", side_effect=fake_sso), patch.object(
                cpa_export, "mint_via_device", side_effect=fake_device
            ):
                r = cpa_export.export_cpa_xai_for_account(
                    "a@b.com", "pw", sso="sso-jwt", config=cfg
                )

            self.assertTrue(r["mint_ok"])
            self.assertEqual(r["method"], "device")
            self.assertIn("sso", r.get("tried") or [])
            self.assertIn("device", r.get("tried") or [])

    def test_require_chat_blocks_ok_and_hotload(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auths"
            live = Path(tmp) / "live"
            auth.mkdir()
            live.mkdir()
            fake_path = auth / "xai-a@b.com.json"
            fake_path.write_text(
                json.dumps(
                    {
                        "type": "xai",
                        "access_token": "at",
                        "refresh_token": "rt",
                        "email": "a@b.com",
                        "headers": {},
                    }
                ),
                encoding="utf-8",
            )

            def fake_sso(*_a, **_k):
                return {
                    "mint_ok": True,
                    "method": "sso",
                    "path": str(fake_path),
                    "access_token": "at",
                    "email": "a@b.com",
                    "base_url": "https://cli-chat-proxy.grok.com/v1",
                }

            def fake_probe(*_a, **_k):
                return {"ok": False, "status": 403, "error": "permission denied"}

            cfg = self._cfg(
                cpa_auth_dir=str(auth),
                cpa_hotload_dir=str(live),
                cpa_copy_to_hotload=True,
                cpa_probe_chat=True,
                cpa_require_chat=True,
            )
            with patch.object(cpa_export, "mint_via_sso", side_effect=fake_sso), patch.object(
                cpa_export, "_probe_chat", side_effect=fake_probe
            ):
                r = cpa_export.export_cpa_xai_for_account(
                    "a@b.com", "pw", sso="sso-jwt", config=cfg
                )

            self.assertTrue(r["mint_ok"])
            self.assertFalse(r["chat_ok"])
            self.assertFalse(r["ok"])
            self.assertEqual(list(live.glob("*.json")), [])
            self.assertEqual(r.get("hotload_skipped"), "chat_required")

    def test_sso_only_no_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "auths"
            auth.mkdir()
            device_calls = []

            def fake_sso(*_a, **_k):
                return {"mint_ok": False, "error": "sso fail", "method": "sso"}

            def fake_device(*_a, **_k):
                device_calls.append(1)
                return {"mint_ok": True, "method": "device", "path": "x"}

            cfg = self._cfg(cpa_auth_dir=str(auth), cpa_mint_mode="sso_only")
            with patch.object(cpa_export, "mint_via_sso", side_effect=fake_sso), patch.object(
                cpa_export, "mint_via_device", side_effect=fake_device
            ):
                r = cpa_export.export_cpa_xai_for_account(
                    "a@b.com", "pw", sso="sso-jwt", config=cfg
                )

            self.assertFalse(r["mint_ok"])
            self.assertEqual(device_calls, [])


if __name__ == "__main__":
    unittest.main()
