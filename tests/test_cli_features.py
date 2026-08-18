# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pdfreader.cli import _safe_output_name, main, process_one, process_vision_only
from pdfreader.convert import PdfConversionResult, convert_pdf_to_markdown
from pdfreader.figures import FigureExtractionResult
from pdfreader.vision import VisionResult


class CliFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).parent / ".tmp-cli"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    def test_invalid_format_is_rejected(self) -> None:
        self.assertEqual(main(["x.pdf", "--no-translate", "--formats", "pdf"]), 2)
        self.assertEqual(main(["x.pdf", "--no-translate", "--formats", ""]), 2)

    def test_pdf_is_required_except_for_test_config(self) -> None:
        self.assertEqual(main(["--no-translate"]), 2)

    def test_no_ocr_preserves_detected_pages_and_marks_skipped(self) -> None:
        classification = SimpleNamespace(pages_needing_ocr=[0, 2])
        processed = SimpleNamespace(
            title="test",
            markdown="text",
            pdf_type="scanned",
            page_count=3,
            ocr_reasons_by_page={0: "low_text", 2: "no_text"},
            has_encoding_issues=False,
            is_complex_layout=False,
        )
        pdf = self.tmp / "scan.pdf"
        pdf.write_bytes(b"pdf")
        with mock.patch("pdfreader.convert.pi.classify_pdf", return_value=classification), mock.patch(
            "pdfreader.convert.pi.process_pdf", return_value=processed
        ):
            result = convert_pdf_to_markdown(pdf, enable_ocr=False)
        self.assertEqual(result.pages_needing_ocr, [0, 2])
        self.assertTrue(result.ocr_skipped)
        self.assertFalse(result.ocr_used)
        self.assertTrue(any("主动跳过" in warning for warning in result.warnings))

    def test_process_one_writes_reports_to_isolated_document_directory(self) -> None:
        pdf = self.tmp / "paper.pdf"
        pdf.write_bytes(b"pdf")
        result = PdfConversionResult(
            source=pdf,
            title="paper",
            pdf_type="text_based",
            page_count=1,
            pages_needing_ocr=[],
            markdown="hello",
        )
        args = argparse.Namespace(
            out_dir=str(self.tmp / "out"),
            no_ocr=False,
            chunk_tokens=100,
            no_translate=True,
            formats="md",
            figures=False,
        )
        with mock.patch("pdfreader.cli.convert_pdf_to_markdown", return_value=result):
            summary = process_one(str(pdf), args, None)
        expected_dir = Path(args.out_dir) / _safe_output_name(pdf.stem, pdf)
        self.assertTrue(expected_dir.is_dir())
        self.assertTrue(all(Path(path).parent == expected_dir for path in summary["outputs"].values()))

    def test_vision_only_does_not_invoke_text_conversion(self) -> None:
        pdf = self.tmp / "paper.pdf"
        pdf.write_bytes(b"pdf")
        args = argparse.Namespace(
            out_dir=str(self.tmp / "out"),
            fig_min_size=80,
            vision_api_key="key",
            vision_model="vision-model",
            vision_base_url="https://api.example.com/v1",
            allow_insecure_http=False,
        )
        figures = FigureExtractionResult(source=pdf, figures=[])
        reader = SimpleNamespace(
            available=True,
            on_progress=None,
            read_figures=lambda items: VisionResult(),
        )
        with mock.patch("pdfreader.figures.extract_figures", return_value=figures), mock.patch(
            "pdfreader.cli._create_vision_reader", return_value=reader
        ), mock.patch("pdfreader.cli.convert_pdf_to_markdown") as convert:
            summary = process_vision_only(str(pdf), args)
        convert.assert_not_called()
        self.assertEqual(summary["type"], "vision_only")
        document_id = _safe_output_name(pdf.stem, pdf)
        self.assertTrue((Path(args.out_dir) / document_id).is_dir())

    def test_test_config_uses_minimal_connection_calls(self) -> None:
        translator = SimpleNamespace(test_connection=mock.Mock(return_value="OK"))
        with mock.patch("pdfreader.cli.LLMTranslator", return_value=translator):
            code = main(
                [
                    "--test-config",
                    "--api-key",
                    "key",
                    "--model",
                    "model",
                    "--base-url",
                    "https://api.example.com/v1",
                ]
            )
        self.assertEqual(code, 0)
        translator.test_connection.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
