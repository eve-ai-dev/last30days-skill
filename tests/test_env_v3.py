import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))

from lib import bird_x, env


class EnvV3Tests(unittest.TestCase):
    def setUp(self):
        self._saved_credentials = dict(bird_x._credentials)

    def tearDown(self):
        bird_x._credentials.clear()
        bird_x._credentials.update(self._saved_credentials)

    def test_x_source_prefers_xai_without_bird_probe(self):
        with mock.patch("lib.bird_x.is_bird_authenticated", side_effect=AssertionError("should not probe bird auth")):
            source = env.get_x_source({"XAI_API_KEY": "test"})
        self.assertEqual("xai", source)

    def test_x_source_uses_bird_with_explicit_cookies(self):
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            source = env.get_x_source({"AUTH_TOKEN": "a", "CT0": "b"})
        self.assertEqual("bird", source)
        self.assertEqual("a", bird_x._credentials["AUTH_TOKEN"])
        self.assertEqual("b", bird_x._credentials["CT0"])

    def test_camofox_x_cookies_override_stale_env_cookies_in_memory(self):
        config = {"AUTH_TOKEN": "stale-auth", "CT0": "stale-ct0", "CAMOFOX_ACCESS_KEY": "key", "CAMOFOX_USER_ID": "u"}
        storage = {
            "cookies": [
                {"name": "auth_token", "domain": ".x.com", "value": "fresh-auth"},
                {"name": "ct0", "domain": ".x.com", "value": "fresh-ct0"},
            ]
        }
        with mock.patch("lib.env._fetch_camofox_storage_state", return_value=storage):
            extracted = env.extract_camofox_x_credentials(config)
        self.assertEqual({"AUTH_TOKEN": "fresh-auth", "CT0": "fresh-ct0"}, extracted)

    def test_get_config_prefers_camofox_x_cookies_over_env(self):
        storage = {
            "cookies": [
                {"name": "auth_token", "domain": ".x.com", "value": "fresh-auth"},
                {"name": "ct0", "domain": ".x.com", "value": "fresh-ct0"},
            ]
        }
        with mock.patch("lib.env._find_project_env", return_value=None), \
             mock.patch("lib.env.load_env_file", return_value={}), \
             mock.patch("lib.env.extract_browser_credentials", return_value={}), \
             mock.patch("lib.env._fetch_camofox_storage_state", return_value=storage), \
             mock.patch.dict(os.environ, {
                 "AUTH_TOKEN": "stale-auth",
                 "CT0": "stale-ct0",
                 "CAMOFOX_ACCESS_KEY": "key",
                 "CAMOFOX_USER_ID": "u",
             }, clear=True):
            config = env.get_config()
        self.assertEqual("fresh-auth", config["AUTH_TOKEN"])
        self.assertEqual("fresh-ct0", config["CT0"])
        self.assertEqual("camofox", config["_AUTH_TOKEN_SOURCE"])
        self.assertEqual("camofox", config["_CT0_SOURCE"])

    def test_bird_auth_never_checks_browser_cookies(self):
        # The guarantee: is_bird_authenticated() must not spawn any child
        # process to probe for cookies. All subprocess paths in bird_x go
        # through subproc.run_with_timeout, so patching that covers it.
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), mock.patch(
            "lib.bird_x.subproc.run_with_timeout",
            side_effect=AssertionError("browser-cookie whoami should not run"),
        ):
            bird_x._credentials.clear()
            with mock.patch.dict(os.environ, {}, clear=False):
                self.assertIsNone(bird_x.is_bird_authenticated())


if __name__ == "__main__":
    unittest.main()
