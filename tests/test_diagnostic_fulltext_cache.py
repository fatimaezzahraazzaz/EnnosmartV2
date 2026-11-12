"""Un fichier inchangé n'est pas ré-extrait (OCR) d'un diagnostic à l'autre."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.NLP import document_loader as loader


class FulltextCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache"
        self.doc = Path(self.tmp.name) / "essai.md"
        self.doc.write_text("Essai de séchage à 80 °C.", encoding="utf-8")
        self.calls = []

        def extract(path, **kwargs):
            self.calls.append(path)
            return Path(path).read_text(encoding="utf-8")

        patcher = mock.patch.object(loader, "extract_with_ennosmart_router", side_effect=extract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, metadata=None):
        return loader.load_document(str(self.doc), metadata=metadata, vision_mode="text_only", formula_mode="fast")

    def test_without_the_cache_every_call_extracts(self):
        self.load()
        self.load()
        self.assertEqual(len(self.calls), 2)

    def test_unchanged_file_is_extracted_once_and_metadata_still_applies(self):
        with loader.fulltext_cache(self.cache):
            first = self.load({"document_id": "12"})
            second = self.load({"document_id": "34"})
            self.assertEqual(loader.fulltext_cache_stats(), {"hits": 1, "misses": 1})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(second["document_id"], "34")
        self.assertEqual(second["source_path"], str(self.doc))

    def test_changed_file_is_extracted_again(self):
        with loader.fulltext_cache(self.cache):
            self.load()
            self.doc.write_text("Essai de séchage à 90 °C.", encoding="utf-8")
            changed = self.load()
        self.assertEqual(len(self.calls), 2)
        self.assertIn("90", changed["text"])

    def test_changed_extraction_settings_are_a_new_extraction(self):
        with loader.fulltext_cache(self.cache):
            self.load()
            loader.load_document(str(self.doc), vision_mode="text_only", formula_mode="precise")
        self.assertEqual(len(self.calls), 2)

    def test_empty_extraction_is_not_kept(self):
        self.doc.write_text("   ", encoding="utf-8")
        with mock.patch.object(loader, "fallback_extract", return_value=""), loader.fulltext_cache(self.cache):
            self.load()
            self.load()
        self.assertEqual(len(self.calls), 2)


class OcrWorkersTests(unittest.TestCase):
    def test_default_is_eight_pages_at_most_and_can_be_overridden(self):
        from services.diagnostic_service import _diagnostic_ocr_page_workers
        with mock.patch.dict(os.environ, {"ENNOSMART_OCR_PAGE_WORKERS": ""}):
            self.assertTrue(1 <= _diagnostic_ocr_page_workers() <= 8)
        with mock.patch.dict(os.environ, {"ENNOSMART_OCR_PAGE_WORKERS": "3"}):
            self.assertEqual(_diagnostic_ocr_page_workers(), 3)


if __name__ == "__main__":
    unittest.main()
