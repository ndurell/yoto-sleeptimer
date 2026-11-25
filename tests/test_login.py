import asyncio
import os
import unittest
import urllib.parse

import aiohttp

from yoto_sleeptimer import login


class Login(unittest.TestCase):
    def test_authorize_url(self):
        url = login.build_authorize_url("cid", "http://127.0.0.1:8787/callback",
                                        "openid offline_access", "chal", "st")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["redirect_uri"], "http://127.0.0.1:8787/callback")
        self.assertEqual(q["audience"], "https://api.yotoplay.com")
        self.assertIn("offline_access", q["scope"])

    def test_parse_callback(self):
        self.assertEqual(login.parse_callback({"code": "c", "state": "s"}, "s"), "c")
        with self.assertRaisesRegex(RuntimeError, "state"):
            login.parse_callback({"code": "c", "state": "x"}, "s")
        with self.assertRaisesRegex(RuntimeError, "refused"):
            login.parse_callback({"error": "access_denied"}, "s")

    def test_pkce_pair(self):
        verifier, challenge = login._pkce_pair()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertNotIn("=", challenge)

    def test_loopback_callback(self):
        os.environ["LOGIN_BIND"] = "127.0.0.1"
        uri = "http://127.0.0.1:18787/callback"

        async def scenario():
            waiter = asyncio.ensure_future(login._wait_for_code(uri, "st"))
            await asyncio.sleep(0.3)
            async with aiohttp.ClientSession() as s:
                async with s.get(uri, params={"code": "abc", "state": "st"}) as r:
                    self.assertIn("Signed in", await r.text())
            return await waiter

        self.assertEqual(asyncio.run(scenario()), "abc")


if __name__ == "__main__":
    unittest.main()
