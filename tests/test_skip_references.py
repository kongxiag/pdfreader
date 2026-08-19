# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from pdfreader.chunk import Chunk, _is_reference_heading, chunk_markdown
from pdfreader.report import build_bilingual_markdown, build_plain_chinese
from pdfreader.translate import LLMTranslator, TranslationResult


class SkipReferencesTests(unittest.TestCase):
    def test_reference_heading_detection(self) -> None:
        for h in ("References", "references", "REFERENCES", "Bibliography", "1. References", "6.2. References",
                  "Works Cited", "Literature Cited", "参考文献", "引用文献"):
            self.assertTrue(_is_reference_heading(h), f"应识别为参考文献标题: {h!r}")
        for h in ("Introduction", "Conclusion", "Methods", "Discussion", "Appendix", ""):
            self.assertFalse(_is_reference_heading(h), f"不应识别为参考文献标题: {h!r}")

    def test_chunk_marks_references(self) -> None:
        intro = "This is the introduction body. " * 20
        refs = "[1] Author A. Title. " * 20
        md = f"# Introduction\n\n{intro}\n\n# References\n\n{refs}\n"
        chunks = chunk_markdown(md, max_tokens=1000)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertFalse(chunks[0].is_reference)
        self.assertTrue(any(c.is_reference for c in chunks))

    def test_translate_document_skips_references(self) -> None:
        translator = LLMTranslator(dry_run=True)
        chunks = [
            Chunk(index=0, text="Introduction body text.", heading_path="Introduction"),
            Chunk(index=1, text="[1] Some reference.", heading_path="References", is_reference=True),
        ]
        results = translator.translate_document(chunks, skip_references=True)
        self.assertTrue(results[0].ok)          # 正文走 dry-run 占位，ok=True
        self.assertTrue(results[1].skipped)     # 参考文献被跳过
        self.assertFalse(results[1].ok)
        self.assertEqual(results[1].translation, "")

        # 关闭跳过后，参考文献也走翻译（dry-run 占位）
        results2 = translator.translate_document(chunks, skip_references=False)
        self.assertTrue(results2[1].ok)
        self.assertFalse(results2[1].skipped)

    def test_report_marks_reference_untranslated(self) -> None:
        chunk = Chunk(index=0, text="# References\n\n[1] Some reference.", heading_path="References", is_reference=True)
        res = TranslationResult(index=0, source=chunk.text, translation="", ok=False, skipped=True,
                                error="参考文献，已跳过翻译")
        md = build_bilingual_markdown("T", [chunk], [res])
        self.assertIn("参考文献", md)
        self.assertIn("未翻译", md)
        zh = build_plain_chinese("T", [chunk], [res])
        self.assertIn("参考文献", zh)
        self.assertIn("未翻译", zh)


if __name__ == "__main__":
    unittest.main()
