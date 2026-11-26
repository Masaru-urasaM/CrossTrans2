"""
Tests for PDF text extraction (security audit S3): src/core/pdf_ocr.py and the PDF path of file_processor.py
read PDFs with pypdf (the maintained successor of PyPDF2) and render scanned pages with the `pymupdf` module
(the old `fitz` name is a deprecated alias). Real PDFs are generated with PyMuPDF; the vision API is a Mock.
"""
import inspect
import os
from unittest.mock import Mock

import pymupdf
import pytest

from src.core import file_processor, pdf_ocr
from src.core.file_processor import FileProcessor
from src.core.pdf_ocr import PDFOCRProcessor

SENTENCE = "The quick brown fox jumps over the lazy dog while the translation engine reads this page."


def make_pdf(path, pages):
    """Write a PDF; each entry is page text, or None for a page with only a filled rectangle (no text)."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text is None:
            page.draw_rect(pymupdf.Rect(60, 60, 300, 300), fill=(0, 0, 0))
        else:
            page.insert_text((72, 100), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


class TestTextPdf:
    def test_a_text_pdf_is_analyzed_as_not_scanned(self, tmp_path):
        info = PDFOCRProcessor(Mock()).analyze_pdf(make_pdf(tmp_path / "t.pdf", [SENTENCE, SENTENCE]))
        assert info.page_count == 2
        assert info.is_scanned is False
        assert info.has_text is True

    def test_text_is_extracted_page_by_page_without_calling_the_vision_api(self, tmp_path):
        api = Mock()
        seen = []
        text = PDFOCRProcessor(api).extract_text(
            make_pdf(tmp_path / "t.pdf", [SENTENCE, SENTENCE]), lambda cur, total, msg: seen.append((cur, total)))
        assert "--- Page 1 ---" in text and "--- Page 2 ---" in text
        assert "translation engine" in text
        assert seen == [(1, 2), (2, 2)]
        api.translate_image.assert_not_called()

    def test_file_processor_uses_the_same_path(self, tmp_path):
        text = FileProcessor(Mock()).extract_text(make_pdf(tmp_path / "t.pdf", [SENTENCE]))
        assert "translation engine" in text

    def test_file_processor_basic_fallback_reads_with_pypdf(self, tmp_path):
        processor = FileProcessor(Mock())
        processor.pdf_ocr = None  # as when the OCR module cannot be imported
        text = processor.extract_text(make_pdf(tmp_path / "t.pdf", [SENTENCE, SENTENCE]))
        assert text.startswith("--- Page 1 ---")
        assert "--- Page 2 ---" in text

    def test_a_pdf_without_text_raises_instead_of_returning_fake_document_text(self, tmp_path):
        processor = FileProcessor(Mock())
        processor.pdf_ocr = None
        with pytest.raises(ValueError, match="no extractable text"):
            processor.extract_text(make_pdf(tmp_path / "blank.pdf", [None]))


class TestScannedPdf:
    def test_a_page_without_text_is_rendered_and_sent_to_the_vision_api(self, tmp_path):
        api = Mock()
        sent = {}

        def translate_image(prompt, image_path):
            sent["prompt"], sent["path"] = prompt, image_path
            sent["is_png"] = open(image_path, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
            return "text read from the image"

        api.translate_image.side_effect = translate_image
        text = PDFOCRProcessor(api).extract_text(make_pdf(tmp_path / "scan.pdf", [None]))
        assert text == "--- Page 1 ---\ntext read from the image"
        assert sent["prompt"] == PDFOCRProcessor.OCR_PROMPT
        assert sent["is_png"] is True
        assert not os.path.exists(sent["path"])  # the temporary page image is always removed

    def test_an_ocr_failure_propagates_instead_of_injecting_error_text(self, tmp_path):
        api = Mock()
        api.translate_image.side_effect = RuntimeError("quota exhausted")
        with pytest.raises(RuntimeError, match="quota exhausted"):
            PDFOCRProcessor(api).extract_text(make_pdf(tmp_path / "scan.pdf", [None]))

    def test_file_processor_propagates_pdf_ocr_api_errors(self, tmp_path):
        api = Mock()
        api.translate_image.side_effect = RuntimeError("HTTP 401: Unauthorized")
        processor = FileProcessor(api)
        with pytest.raises(RuntimeError, match="401"):
            processor.extract_text(make_pdf(tmp_path / "scan.pdf", [None]))


class TestErrorsAndDependencies:
    def test_a_missing_file_is_reported(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PDFOCRProcessor(Mock()).analyze_pdf(str(tmp_path / "missing.pdf"))

    def test_a_corrupt_pdf_is_reported_as_a_value_error(self, tmp_path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        with pytest.raises(ValueError, match="Failed to analyze PDF"):
            PDFOCRProcessor(Mock()).analyze_pdf(str(bad))

    def test_both_libraries_are_available_and_named_correctly(self):
        processor = PDFOCRProcessor(Mock())
        assert processor.is_available() is True
        assert processor.get_missing_dependencies() == []
        assert pdf_ocr.pymupdf.__name__ == "pymupdf"
        assert pdf_ocr.pypdf.__name__ == "pypdf"

    def test_missing_libraries_are_named_by_their_package_names(self, monkeypatch):
        monkeypatch.setattr(pdf_ocr, "HAS_PYPDF", False)
        monkeypatch.setattr(pdf_ocr, "HAS_PYMUPDF", False)
        assert PDFOCRProcessor(Mock()).get_missing_dependencies() == ["pypdf", "PyMuPDF"]

    def test_the_deprecated_package_names_are_no_longer_imported(self):
        for module in (pdf_ocr, file_processor):
            source = inspect.getsource(module)
            assert "import PyPDF2" not in source
            assert "import fitz" not in source
