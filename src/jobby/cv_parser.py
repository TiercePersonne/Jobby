"""CV and Resume text extraction for Jobby (PDF, TXT, Markdown)."""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_cv(file_bytes: bytes, filename: str) -> str:
    """
    Extracts readable text content from an uploaded resume file (PDF, TXT, MD).
    Returns cleaned text ready for NLP analysis.
    """
    ext = Path(filename).suffix.lower()
    
    if ext == ".pdf":
        return _extract_from_pdf(file_bytes)
    elif ext in [".txt", ".md", ".markdown", ".rtf"]:
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return file_bytes.decode("latin-1", errors="ignore")
    else:
        # Fallback text attempt
        try:
            return file_bytes.decode("utf-8")
        except Exception:
            return ""


def _extract_from_pdf(file_bytes: bytes) -> str:
    """Extracts text from PDF bytes using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        pages_text = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages_text.append(text.strip())
        
        extracted = "\n\n".join(pages_text)
        return extracted.strip()
    except Exception as e:
        logger.error(f"Error parsing PDF resume: {e}")
        return ""
