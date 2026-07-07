import unittest

import grok_register_ttk as register


class Grok2apiRemoteTagsTests(unittest.TestCase):
    def setUp(self):
        self.original_config = register.config.copy()
        self.original_http_post = register.http_post

    def tearDown(self):
        register.config = self.original_config
        register.http_post = self.original_http_post

    def test_remote_add_does_not_forward_nsfw_state(self):
        calls = []

        class _Response:
            def raise_for_status(self):
                return None

        def fake_http_post(url, **kwargs):
            calls.append((url, kwargs))
            return _Response()

        register.config = {
            **register.DEFAULT_CONFIG,
            "enable_nsfw": True,
            "grok2api_remote_base": "http://127.0.0.1:8000/admin/api",
            "grok2api_remote_app_key": "app-key",
            "grok2api_pool_name": "basic",
        }
        register.http_post = fake_http_post

        ok = register.add_token_to_grok2api_remote_pool(
            "sso=sso-token",
            email="user@example.com",
            nsfw_enabled=True,
        )

        self.assertTrue(ok)
        self.assertEqual(len(calls), 1)
        url, kwargs = calls[0]
        self.assertEqual(url, "http://127.0.0.1:8000/admin/api/tokens/add")
        self.assertEqual(kwargs["params"], {"app_key": "app-key"})
        self.assertEqual(kwargs["json"], {"tokens": ["sso-token"], "pool": "basic", "tags": ["auto-register"]})


if __name__ == "__main__":
    unittest.main()
