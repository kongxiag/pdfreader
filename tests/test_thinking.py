# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from pdfreader.cli import _apply_config, _validate_config, build_parser
from pdfreader.translate import LLMTranslator


class ThinkingTests(unittest.TestCase):
    def test_config_accepts_thinking_bool(self) -> None:
        _validate_config({"translate": {"api_key": "x", "thinking": True}})
        _validate_config({"translate": {"api_key": "x", "thinking": False}})
        with self.assertRaisesRegex(ValueError, "thinking"):
            _validate_config({"translate": {"api_key": "x", "thinking": "yes"}})

    def test_config_merge_thinking(self) -> None:
        args = build_parser().parse_args(["x.pdf"])
        args = _apply_config(args, {"translate": {"thinking": True}})
        self.assertTrue(args.thinking)
        # CLI 显式覆盖配置
        args2 = build_parser().parse_args(["x.pdf", "--no-thinking"])
        args2 = _apply_config(args2, {"translate": {"thinking": True}})
        self.assertFalse(args2.thinking)

    def _call_payload(self, base_url: str, thinking: bool) -> dict:
        t = LLMTranslator(api_key="test", base_url=base_url, thinking=thinking)
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            t._call([{"role": "user", "content": "hi"}], max_tokens=16)
        return json.loads(captured["data"])

    def test_thinking_disabled_for_deepseek(self) -> None:
        payload = self._call_payload("https://api.deepseek.com/v1", False)
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_thinking_enabled_for_deepseek(self) -> None:
        payload = self._call_payload("https://api.deepseek.com/v1", True)
        self.assertEqual(payload["thinking"], {"type": "enabled"})

    def test_thinking_not_sent_for_non_deepseek(self) -> None:
        payload = self._call_payload("https://api.openai.com/v1", False)
        self.assertNotIn("thinking", payload)


if __name__ == "__main__":
    unittest.main()
