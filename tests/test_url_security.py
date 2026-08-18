# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import unittest

from pdfreader.cli import _apply_config
from pdfreader.translate import LLMTranslator
from pdfreader.url_security import InsecureBaseUrlError, validate_base_url
from pdfreader.vision import VisionReader


class UrlSecurityTests(unittest.TestCase):
    def test_https_is_allowed(self) -> None:
        self.assertEqual(
            validate_base_url("https://api.example.com/v1/"),
            "https://api.example.com/v1",
        )

    def test_loopback_http_is_allowed(self) -> None:
        for url in (
            "http://localhost:11434/v1",
            "http://127.0.0.1:11434/v1",
            "http://[::1]:11434/v1",
        ):
            with self.subTest(url=url):
                self.assertEqual(validate_base_url(url), url)

    def test_remote_http_is_rejected_by_default(self) -> None:
        with self.assertRaises(InsecureBaseUrlError):
            validate_base_url("http://relay.example.com/v1")

        with self.assertRaises(InsecureBaseUrlError):
            LLMTranslator(
                api_key="test",
                base_url="http://relay.example.com/v1",
            )

        with self.assertRaises(InsecureBaseUrlError):
            VisionReader(
                api_key="test",
                base_url="http://relay.example.com/v1",
            )

    def test_remote_http_can_be_explicitly_allowed(self) -> None:
        url = "http://relay.example.com/v1"
        self.assertEqual(
            validate_base_url(url, allow_insecure_http=True),
            url,
        )
        self.assertEqual(
            LLMTranslator(
                api_key="test",
                base_url=url,
                allow_insecure_http=True,
            ).base_url,
            url,
        )
        self.assertEqual(
            VisionReader(
                api_key="test",
                base_url=url,
                allow_insecure_http=True,
            ).base_url,
            url,
        )

    def test_config_enables_insecure_http_only_explicitly(self) -> None:
        args = argparse.Namespace(
            model=None,
            base_url=None,
            api_key=None,
            vision_model=None,
            vision_base_url=None,
            vision_api_key=None,
            vision=False,
            allow_insecure_http=False,
        )
        result = _apply_config(args, {"allow_insecure_http": True})
        self.assertTrue(result.allow_insecure_http)


if __name__ == "__main__":
    unittest.main()
