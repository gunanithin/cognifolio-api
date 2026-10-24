import re
from typing import Tuple


class PIIRedactor:
    """
    Client-side/Pre-AI PII Redaction Engine.
    Ensures sensitive personally identifiable information (PII) is masked 
    before any text or metadata is sent to third-party AI APIs.
    """

    PATTERNS = {
        # Indian Aadhaar Card (12 digits, often formatted as 4-4-4)
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
    def redact(cls, text: str) -> Tuple[str, dict[str, int]]:
        """
        Scans text for sensitive patterns and replaces them with clean tokens.
        Returns the sanitized text along with a count report of masked items.
        """
        if not text:
            return "", {}

        sanitized_text = text
        stats = {}

        for pii_type, (pattern, replacement) in cls.PATTERNS.items():
            matches = list(pattern.finditer(sanitized_text))
            if matches:
                count = len(matches)
                stats[pii_type] = stats.get(pii_type, 0) + count
                sanitized_text = pattern.sub(replacement, sanitized_text)

        return sanitized_text, stats


redactor = PIIRedactor()
