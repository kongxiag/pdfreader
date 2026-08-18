# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from pdfreader.chunk import Chunk, chunk_markdown
from pdfreader.cli import _safe_output_name, main
from pdfreader.figures import FigureExtractionResult, FigureInfo
from pdfreader.http_retry import run_with_retries
from pdfreader.report import build_bilingual_markdown, build_plain_chinese, write_figures_report
from pdfreader.translate import TranslationResult


class PipelineRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(__file__).parent / ".tmp-regressions"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for path in sorted(self.tmp_dir.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.tmp_dir.rmdir()

    def test_figure_report_links_to_nested_document_directory(self) -> None:
        image = self.tmp_dir / "figures" / "paper-1234" / "p01_fig01.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"png")
        result = FigureExtractionResult(
            source=Path("paper.pdf"),
            figures=[FigureInfo(1, 1, image, 100, 100)],
        )
        written = write_figures_report(result, self.tmp_dir)
        text = written["figures"].read_text(encoding="utf-8")
        self.assertIn("figures/paper-1234/p01_fig01.png", text)
        link = unquote(text.split("](", 1)[1].split(")", 1)[0])
        self.assertTrue((self.tmp_dir / link).is_file())

    def test_figure_links_work_when_output_paths_are_relative(self) -> None:
        relative_root = Path("tests") / ".relative-output"
        image = relative_root / "figures" / "paper-id" / "figure.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"png")
        result = FigureExtractionResult(
            source=Path("paper.pdf"),
            figures=[FigureInfo(1, 1, image, 100, 100)],
        )
        written = write_figures_report(result, relative_root)
        text = written["figures"].read_text(encoding="utf-8")
        link = unquote(text.split("](", 1)[1].split(")", 1)[0])
        self.assertTrue((relative_root / link).is_file())
        for path in sorted(relative_root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        relative_root.rmdir()

    def test_document_directory_names_are_isolated_by_source_path(self) -> None:
        first = _safe_output_name("paper", self.tmp_dir / "a" / "paper.pdf")
        second = _safe_output_name("paper", self.tmp_dir / "b" / "paper.pdf")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("paper-"))

    def test_retry_only_retries_transient_errors(self) -> None:
        permanent_calls = 0

        def permanent():
            nonlocal permanent_calls
            permanent_calls += 1
            raise urllib.error.HTTPError(
                "https://x", 401, "Unauthorized", {}, None
            )

        with self.assertRaises(urllib.error.HTTPError) as permanent_error:
            run_with_retries(permanent, max_attempts=3, sleep=lambda _: None)
        permanent_error.exception.close()
        self.assertEqual(permanent_calls, 1)

        transient_calls = 0
        transient_errors: list[urllib.error.HTTPError] = []

        def transient():
            nonlocal transient_calls
            transient_calls += 1
            error = urllib.error.HTTPError(
                "https://x", 503, "Unavailable", {}, None
            )
            transient_errors.append(error)
            raise error

        with self.assertRaises(urllib.error.HTTPError):
            run_with_retries(transient, max_attempts=3, sleep=lambda _: None)
        for error in transient_errors:
            error.close()
        self.assertEqual(transient_calls, 3)

    def test_oversized_single_line_is_hard_split(self) -> None:
        chunks = chunk_markdown("A" * 20000, max_tokens=100, overlap_chars=0)
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(len(chunk.text) for chunk in chunks), 800)
        self.assertEqual("".join(chunk.text for chunk in chunks), "A" * 20000)

    def test_no_translate_is_not_reported_as_failure(self) -> None:
        chunks = [Chunk(0, "source")]
        results = [
            TranslationResult(
                0,
                "source",
                "",
                ok=False,
                error="已跳过翻译",
                skipped=True,
            )
        ]
        bilingual = build_bilingual_markdown("title", chunks, results)
        chinese = build_plain_chinese("title", chunks, results)
        self.assertNotIn("翻译失败", bilingual)
        self.assertIn("已跳过翻译", bilingual)
        self.assertNotIn("翻译失败", chinese)

    def test_batch_continues_after_one_document_fails(self) -> None:
        first = self.tmp_dir / "first.pdf"
        second = self.tmp_dir / "second.pdf"
        first.write_bytes(b"pdf")
        second.write_bytes(b"pdf")
        calls: list[str] = []

        def fake_process(pdf, args, translator):
            calls.append(Path(pdf).name)
            if Path(pdf).name == "first.pdf":
                raise RuntimeError("broken")
            return {
                "pdf": pdf,
                "type": "text_based",
                "pages": 1,
                "chars": 10,
                "chunks": 1,
                "figures": 0,
                "outputs": {},
            }

        with mock.patch("pdfreader.cli.process_one", side_effect=fake_process):
            code = main([str(first), str(second), "--no-translate", "--no-figures"])
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["first.pdf", "second.pdf"])


if __name__ == "__main__":
    unittest.main()
