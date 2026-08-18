# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from pdfreader.cli import _apply_config, _load_config, build_parser, _validate_config, main
from pdfreader.url_security import InsecureBaseUrlError


class ConfigSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(__file__).parent / ".tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for path in self.tmp_dir.glob("*"):
            if path.is_file():
                path.unlink()
        self.tmp_dir.rmdir()

    def _config_path(self, name: str = "config.json") -> Path:
        return self.tmp_dir / name

    def test_accepts_nonempty_api_keys_in_config(self) -> None:
        _validate_config({"translate": {"api_key": "secret"}})
        _validate_config({"vision": {"api_key": "secret"}})
        with self.assertRaisesRegex(ValueError, "非空字符串"):
            _validate_config({"translate": {"api_key": ""}})

    def test_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知字段"):
            _validate_config({"unexpected": True})

    def test_rejects_wrong_types_and_blank_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            _validate_config({"use_vision": "yes"})
        with self.assertRaisesRegex(ValueError, "非空字符串"):
            _validate_config({"translate": {"model": ""}})

    def test_validates_urls_during_config_load(self) -> None:
        with self.assertRaises(InsecureBaseUrlError):
            _validate_config(
                {"translate": {"base_url": "http://relay.example.com/v1"}}
            )
        _validate_config(
            {
                "translate": {"base_url": "http://relay.example.com/v1"},
                "allow_insecure_http": True,
            }
        )

    def test_missing_or_invalid_config_is_fatal(self) -> None:
        self.assertEqual(
            main(["missing.pdf", "--config", "definitely-missing.json"]),
            2,
        )
        path = self._config_path()
        path.write_text("not-json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "解析失败"):
            _load_config(str(path))

    def test_valid_config_with_local_key_loads(self) -> None:
        path = self._config_path()
        payload = {
            "translate": {
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "test-config-key",
            },
            "use_vision": False,
            "allow_insecure_http": False,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(_load_config(str(path)), payload)

    def test_config_key_is_applied_and_cli_key_wins(self) -> None:
        config = {
            "translate": {
                "model": "config-model",
                "base_url": "https://api.example.com/v1",
                "api_key": "config-key",
            },
            "vision": {
                "model": "vision-model",
                "base_url": "https://vision.example.com/v1",
                "api_key": "vision-key",
            },
        }
        args = build_parser().parse_args(["x.pdf"])
        result = _apply_config(args, config)
        self.assertEqual(result.api_key, "config-key")
        self.assertEqual(result.vision_api_key, "vision-key")

        explicit = build_parser().parse_args(
            ["x.pdf", "--api-key", "cli-key", "--vision-api-key", "cli-vision-key"]
        )
        explicit = _apply_config(explicit, config)
        self.assertEqual(explicit.api_key, "cli-key")
        self.assertEqual(explicit.vision_api_key, "cli-vision-key")

    def test_missing_key_does_not_fall_back_to_dry_run(self) -> None:
        path = self._config_path()
        path.write_text(
            json.dumps(
                {
                    "translate": {
                        "model": "test",
                        "base_url": "https://api.example.com/v1",
                    }
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(
            "os.environ",
            {
                "LLM_API_KEY": "",
                "DEEPSEEK_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            self.assertEqual(
                main(["missing.pdf", "--config", str(path)]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
