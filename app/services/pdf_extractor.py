import os
from pathlib import Path
from pypdf import PdfReader
from PIL import Image


class DocumentExtractor:
    """
    Extracts text, metadata, and page counts from PDFs and image documents.
    """

    @classmethod
    def extract(cls, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()

        if ext == ".pdf":
            return cls._extract_pdf(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            return cls._extract_image(file_path)
        else:
            # Fallback text reading
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                return {
                    "text": content,
                    "page_count": 1,
                    "is_scanned": False,
                    "pages": [content],
                }
            except Exception as e:
                return {
                    "text": f"Error reading file: {str(e)}",
                    "page_count": 1,
                    "is_scanned": False,
                    "pages": [],
                }

    @classmethod
    def _extract_pdf(cls, file_path: str) -> dict:
        try:
            reader = PdfReader(file_path)
            pages_text = []
            full_text = []

            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                pages_text.append(text)
                full_text.append(f"--- [Page {i + 1}] ---\n{text}")

            combined_text = "\n\n".join(full_text).strip()
            is_scanned = len(combined_text.replace("-", "").strip()) < 50

            return {
                "text": combined_text,
                "page_count": max(1, len(reader.pages)),
                "is_scanned": is_scanned,
                "pages": pages_text,
            }
        except Exception as e:
            # Fallback if PDF has non-standard header or is text disguised as PDF
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                return {
                    "text": content,
                    "page_count": 1,
                    "is_scanned": False,
                    "pages": [content],
                }
            except Exception:
                return {
                    "text": f"Document content could not be decoded as standard PDF ({str(e)}).",
                    "page_count": 1,
                    "is_scanned": False,
                    "pages": [],
                }

    @classmethod
    def _extract_image(cls, file_path: str) -> dict:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                return {
                    "text": f"[Image document: {Path(file_path).name}, dimensions: {width}x{height}]",
                    "page_count": 1,
                    "is_scanned": True,
                    "pages": [],
                }
        except Exception:
            return {
                "text": "[Image document]",
                "page_count": 1,
                "is_scanned": True,
                "pages": [],
            }


extractor = DocumentExtractor()
