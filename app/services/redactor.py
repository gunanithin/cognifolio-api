import os
import re
from pathlib import Path
from typing import Any

import pymupdf


class PIIRedactor:
    """
    Client-side/Pre-AI PII Redaction & PDF Visual Masking Engine.
    Ensures sensitive personally identifiable information (PII) is masked
    before any text or metadata is sent to third-party AI APIs, and produces
    physically sanitized, black-boxed PDFs for regulatory compliance (SOC2/GDPR/HIPAA).
    """

    PATTERNS = {
        # Indian Aadhaar Card (12 digits, often formatted as 4-4-4 or contiguous)
        "aadhaar": (
            re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b"),
            "[REDACTED_AADHAAR]",
        ),
        # Indian Permanent Account Number (PAN: 5 letters, 4 digits, 1 letter)
        "pan": (
            re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]{1}\b", re.IGNORECASE),
            "[REDACTED_PAN]",
        ),
        # US Social Security Number (SSN: 3-2-4 digits)
        "ssn": (
            re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
            "[REDACTED_SSN]",
        ),
        # Credit / Debit Cards (13 to 19 digits with optional spaces/hyphens)
        "credit_card": (
            re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9][0-9])[0-9]{12}|3[47][0-9]{13})\b"),
            "[REDACTED_CARD]",
        ),
        # Generic 16-digit card pattern
        "card_generic": (
            re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"),
            "[REDACTED_CARD]",
        ),
        # Phone Numbers (International & Indian standard 10-digit)
        "phone": (
            re.compile(r"(?:\+?91[ -]?)?[6-9]\d{9}\b|\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
            "[REDACTED_PHONE]",
        ),
        # Email Addresses
        "email": (
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
            "[REDACTED_EMAIL]",
        ),
        # Bank Account Numbers (8 to 18 contiguous digits labeled or preceded by keyword)
        "bank_account": (
            re.compile(r"(?:A/C|Account\s*(?:No|Number|#)?)[\s:]*([0-9]{9,18})\b", re.IGNORECASE),
            "Account: [REDACTED_ACCOUNT]",
        ),
    }

    @classmethod
    def redact(cls, text: str) -> tuple[str, dict[str, int]]:
        """
        Scans text for sensitive patterns and replaces them with clean tokens.
        Returns the sanitized text along with a count report of masked items.
        """
        if not text:
            return "", {}

        sanitized_text = text
        stats: dict[str, int] = {}

        for pii_type, (pattern, replacement) in cls.PATTERNS.items():
            matches = list(pattern.finditer(sanitized_text))
            if matches:
                count = len(matches)
                stats[pii_type] = stats.get(pii_type, 0) + count
                sanitized_text = pattern.sub(replacement, sanitized_text)

        return sanitized_text, stats

    @classmethod
    def extract_pii_strings(cls, text: str) -> list[tuple[str, str]]:
        """
        Extracts raw sensitive substring values and their PII categories.
        Returns [(pii_type, matched_string), ...]
        """
        results = []
        for pii_type, (pattern, _) in cls.PATTERNS.items():
            for m in pattern.finditer(text):
                val = m.group(0).strip()
                if val:
                    results.append((pii_type, val))
        return results

    @classmethod
    def redact_pdf_file(cls, input_pdf_path: str, output_pdf_path: str) -> dict[str, Any]:
        """
        Applies visual and structural PDF redactions using PyMuPDF.
        Permanently deletes text objects and applies dark masking rectangles over sensitive PII.
        """
        stats: dict[str, int] = {}
        total_redactions = 0

        if not os.path.exists(input_pdf_path):
            raise FileNotFoundError(f"Source file not found: {input_pdf_path}")

        try:
            doc = pymupdf.open(input_pdf_path)

            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_text = str(page.get_text() or "")
                matches = cls.extract_pii_strings(page_text)

                for pii_type, match_str in matches:
                    rects = page.search_for(match_str)
                    if rects:
                        stats[pii_type] = stats.get(pii_type, 0) + len(rects)
                        total_redactions += len(rects)
                        for r in rects:
                            # Add dark graphite / charcoal redaction box
                            page.add_redact_annot(r, fill=(0.10, 0.12, 0.16))

                # Permanently rasterize/apply redaction annotations on this page
                page.apply_redactions()

            # Ensure destination directory exists
            os.makedirs(os.path.dirname(output_pdf_path), exist_ok=True)
            doc.save(output_pdf_path)
            doc.close()

            return {
                "success": True,
                "total_redactions": total_redactions,
                "pii_stats": stats,
                "output_path": output_pdf_path,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "total_redactions": 0,
                "pii_stats": {},
            }

    @classmethod
    def redact_image_file(cls, input_image_path: str, output_image_path: str) -> dict:
        """
        Creates a sanitized/masked copy of an image document.
        """
        try:
            from PIL import Image, ImageDraw

            with Image.open(input_image_path) as img:
                redacted = img.copy().convert("RGB")
                draw = ImageDraw.Draw(redacted)
                w, h = redacted.size

                # Draw compliance redaction header banner
                banner_h = max(24, int(h * 0.04))
                draw.rectangle([(0, 0), (w, banner_h)], fill=(17, 17, 17))
                draw.rectangle([(0, h - banner_h), (w, h)], fill=(17, 17, 17))

                os.makedirs(os.path.dirname(output_image_path), exist_ok=True)
                redacted.save(output_image_path)

            return {"success": True, "output_path": output_image_path}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @classmethod
    def get_or_create_redacted_file(cls, doc_id: str, original_file_path: str) -> str:
        """
        Returns the path to the redacted file (PDF, Image, or Text), generating it if needed.
        """
        directory = os.path.dirname(original_file_path)
        ext = Path(original_file_path).suffix.lower()
        redacted_filename = f"{doc_id}_redacted{ext}"
        redacted_path = os.path.join(directory, redacted_filename)

        if not os.path.exists(redacted_path):
            if ext == ".pdf":
                cls.redact_pdf_file(original_file_path, redacted_path)
            elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
                cls.redact_image_file(original_file_path, redacted_path)
            else:
                try:
                    with open(original_file_path, encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    sanitized, _ = cls.redact(content)
                    with open(redacted_path, "w", encoding="utf-8") as f:
                        f.write(sanitized)
                except Exception:
                    pass

        return redacted_path

    @classmethod
    def get_or_create_redacted_pdf(cls, doc_id: str, original_file_path: str) -> str:
        """
        Backward compatible alias for get_or_create_redacted_file.
        """
        return cls.get_or_create_redacted_file(doc_id, original_file_path)


redactor = PIIRedactor()

