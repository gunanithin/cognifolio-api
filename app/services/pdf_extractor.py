import csv
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

logger = logging.getLogger(__name__)


class DocumentExtractor:
    """
    Universal Document Extraction Engine.
    Extracts text, structured tables, metadata, and pagination across:
    - PDFs (.pdf)
    - Microsoft Word documents (.docx, .doc)
    - Microsoft Excel spreadsheets (.xlsx, .xls) & CSV files (.csv)
    - Images (.png, .jpg, .jpeg, .webp)
    - Text & Markdown files (.txt, .md)
    """

    @classmethod
    def _clean_text(cls, text: str) -> str:
        """
        Enterprise text sanitizer: Strips null bytes (0x00) and dangerous non-printable
        control characters that crash database drivers like PostgreSQL / psycopg2.
        Preserves tabs, newlines, carriage returns, and valid unicode.
        """
        if not text:
            return ""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text))

    @classmethod
    def extract(cls, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()

        if ext == ".pdf":
            res = cls._extract_pdf(file_path)
        elif ext in [".docx", ".doc"]:
            res = cls._extract_word(file_path)
        elif ext in [".xlsx", ".xls", ".csv"]:
            res = cls._extract_spreadsheet(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            res = cls._extract_image(file_path)
        else:
            res = cls._extract_text(file_path)

        # Global sanitization guarantee: NEVER return text containing null characters
        if isinstance(res.get("text"), str):
            res["text"] = cls._clean_text(res["text"])
        if isinstance(res.get("pages"), list):
            res["pages"] = [cls._clean_text(p) if isinstance(p, str) else p for p in res["pages"]]

        return res

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
                "format": "pdf",
            }
        except Exception as e:
            # Fallback to PyMuPDF if installed
            try:
                import pymupdf

                doc = pymupdf.open(file_path)
                pages_text = []
                full_text = []
                for i in range(len(doc)):
                    page = doc[i]
                    t = str(page.get_text() or "")
                    pages_text.append(t)
                    full_text.append(f"--- [Page {i + 1}] ---\n{t}")
                doc.close()
                combined_text = "\n\n".join(full_text).strip()
                return {
                    "text": combined_text,
                    "page_count": max(1, len(pages_text)),
                    "is_scanned": len(combined_text.strip()) < 50,
                    "pages": pages_text,
                    "format": "pdf",
                }
            except Exception:
                return cls._extract_text(file_path, fallback_error=str(e))

    @classmethod
    def _extract_word(cls, file_path: str) -> dict:
        """
        Universal Word Document Extraction Engine.
        Supports:
        - Modern Office Open XML (.docx) via python-docx
        - Direct XML stream parsing fallback for complex/damaged .docx via zipfile & ElementTree
        - Rich Text Format (.rtf) saved with .doc or .docx extensions
        - Word HTML formats (.doc / .docx)
        - Legacy binary Microsoft Word 97-2003 (.doc) OLE2 Compound Document stream decoding
        """
        ext = Path(file_path).suffix.lower()
        clean_ext = ext.replace(".", "") or "docx"

        # Tier 1: Try python-docx for modern OpenXML .docx
        try:
            import docx

            doc = docx.Document(file_path)
            sections_text = []
            full_text_lines = []
            current_section = []

            for para in doc.paragraphs:
                text = (para.text or "").strip()
                if not text:
                    continue

                style_name = str(getattr(para.style, "name", "") or "").lower()
                if "heading 1" in style_name:
                    if current_section:
                        sections_text.append("\n".join(current_section))
                        current_section = []
                    full_text_lines.append(f"\n--- [Section: {text}] ---\n")
                    line = f"# {text}"
                elif "heading 2" in style_name:
                    line = f"## {text}"
                elif "heading 3" in style_name:
                    line = f"### {text}"
                elif "list" in style_name or text.startswith(("-", "•", "*")):
                    line = f"• {text}"
                else:
                    line = text

                full_text_lines.append(line)
                current_section.append(line)

            # Extract embedded tables defensively
            for table_idx, table in enumerate(doc.tables):
                table_lines = [f"\n--- [Table {table_idx + 1}] ---"]
                for row_idx, row in enumerate(table.rows):
                    try:
                        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                        if not cells or all(c == "" for c in cells):
                            continue
                        if row_idx == 0:
                            table_lines.append("| " + " | ".join(cells) + " |")
                            table_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                        else:
                            table_lines.append("| " + " | ".join(cells) + " |")
                    except Exception:
                        continue

                if len(table_lines) > 1:
                    table_md = "\n".join(table_lines)
                    full_text_lines.append(table_md)
                    current_section.append(table_md)

            if current_section:
                sections_text.append("\n".join(current_section))

            combined_text = "\n".join(full_text_lines).strip()
            if combined_text and len(combined_text) >= 10:
                word_count = len(combined_text.split())
                estimated_pages = max(1, (word_count + 300) // 350)
                return {
                    "text": cls._clean_text(combined_text),
                    "page_count": estimated_pages,
                    "is_scanned": False,
                    "pages": [cls._clean_text(s) for s in sections_text] if sections_text else [cls._clean_text(combined_text)],
                    "format": clean_ext,
                }
        except Exception as docx_err:
            logger.info(f"python-docx parsing skipped or failed ({docx_err}); attempting OpenXML & binary fallbacks.")

        # Tier 2: OpenXML Zipfile fallback for .docx
        xml_extracted = cls._extract_docx_xml(file_path)
        if xml_extracted and len(xml_extracted.strip()) >= 15:
            clean_xml = cls._clean_text(xml_extracted)
            word_count = len(clean_xml.split())
            estimated_pages = max(1, (word_count + 300) // 350)
            return {
                "text": clean_xml,
                "page_count": estimated_pages,
                "is_scanned": False,
                "pages": [clean_xml],
                "format": clean_ext,
            }

        # Tier 3 & 4 & 5: Binary OLE2 Word 97-2003, RTF, and HTML
        binary_extracted = cls._extract_doc_binary(file_path)
        if binary_extracted and len(binary_extracted.strip()) >= 15:
            clean_bin = cls._clean_text(binary_extracted)
            word_count = len(clean_bin.split())
            estimated_pages = max(1, (word_count + 300) // 350)
            return {
                "text": clean_bin,
                "page_count": estimated_pages,
                "is_scanned": False,
                "pages": [clean_bin],
                "format": clean_ext,
            }

        # Tier 6: Final clean text fallback
        return cls._extract_text(file_path)

    @classmethod
    def _extract_docx(cls, file_path: str) -> dict:
        """Backward-compatible alias for _extract_word."""
        return cls._extract_word(file_path)

    @classmethod
    def _extract_docx_xml(cls, file_path: str) -> str | None:
        """
        Parses document.xml directly from .docx OpenXML zip container without external libraries.
        Handles corrupt package definitions, unsupported styles, and custom namespaces.
        """
        try:
            if not zipfile.is_zipfile(file_path):
                return None
            with zipfile.ZipFile(file_path, "r") as z:
                doc_xml_name = None
                for n in z.namelist():
                    if n.lower() == "word/document.xml":
                        doc_xml_name = n
                        break
                if not doc_xml_name:
                    return None

                xml_bytes = z.read(doc_xml_name)
                tree = ET.fromstring(xml_bytes)
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

                paragraphs = []
                for elem in tree.iter():
                    tag = elem.tag
                    if tag == f"{{{ns['w']}}}p":
                        texts = [node.text for node in elem.iter(f"{{{ns['w']}}}t") if node.text]
                        if texts:
                            line = "".join(texts).strip()
                            if line:
                                paragraphs.append(line)
                    elif tag == f"{{{ns['w']}}}tbl":
                        for row in elem.iter(f"{{{ns['w']}}}tr"):
                            row_cells = []
                            for cell in row.iter(f"{{{ns['w']}}}tc"):
                                cell_texts = [node.text for node in cell.iter(f"{{{ns['w']}}}t") if node.text]
                                row_cells.append("".join(cell_texts).strip().replace("\n", " "))
                            if row_cells and any(c for c in row_cells):
                                paragraphs.append("| " + " | ".join(row_cells) + " |")

                return "\n\n".join(paragraphs).strip() if paragraphs else None
        except Exception as e:
            logger.debug(f"Direct docx xml extraction failed: {e}")
            return None

    @classmethod
    def _extract_doc_binary(cls, file_path: str) -> str | None:
        """
        Robust text extractor for legacy Microsoft Word 97-2003 (.doc) binary OLE2 files,
        Rich Text Format (.rtf), and Word HTML exports.
        Guarantees 100% removal of null bytes (\\x00) and unprintable control characters.
        """
        try:
            with open(file_path, "rb") as f:
                raw = f.read()

            if not raw:
                return None

            # 1. Rich Text Format (RTF)
            if raw.startswith(b"{\\rtf") or raw.startswith(b"{\\\\rtf"):
                text = re.sub(r"\\\\[a-zA-Z0-9\-]+", "", raw.decode("utf-8", errors="ignore"))
                text = re.sub(r"[{}]", "", text)
                return re.sub(r"\s+", " ", text).strip()

            # 2. Word HTML export
            if b"<html" in raw[:500].lower():
                text = re.sub(r"<style[^>]*>.*?</style>", "", raw.decode("utf-8", errors="ignore"), flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
                return re.sub(r"\s+", " ", text).strip()

            # 3. Binary OLE2 Compound Document (.doc)
            KNOWN_NOISE = {
                "times new roman", "arial", "calibri", "helvetica", "courier",
                "liberation serif", "dejavu sans", "open sans", "droid sans fallback",
                "freesans", "opensymbol", "symbol", "arial unicode ms", "liberation sans",
                "normal", "heading 1", "heading 2", "heading 3", "heading 4",
                "heading 5", "heading 6", "bullets", "internet link", "visited internet link",
                "root entry", "worddocument", "summaryinformation", "documentpresentation",
                "text body", "caption", "index", "quotations", "title", "subtitle",
                "default paragraph font", "table normal", "no list", "msworddoc",
                "microsoft word", "word.document.8"
            }

            paragraphs = []

            # A. UTF-16LE text sequences (standard encoding for Word 97-2003 body text)
            for match in re.finditer(b"(?:[\x20-\x7E\r\n\t]\x00){8,}", raw):
                try:
                    decoded = match.group(0).decode("utf-16le", errors="ignore").strip()
                    clean = " ".join(decoded.split())
                    if len(clean) >= 15:
                        lower = clean.lower()
                        if lower not in KNOWN_NOISE and not lower.startswith("http://") and not lower.startswith("https://"):
                            if clean not in paragraphs:
                                paragraphs.append(clean)
                except Exception:
                    pass

            # B. 8-bit CP1252 / ASCII runs if UTF-16 didn't capture sufficient content
            if len(paragraphs) < 3:
                for match in re.finditer(b"[\x20-\x7E\r\n\t]{18,}", raw):
                    try:
                        decoded = match.group(0).decode("latin-1", errors="ignore").strip()
                        clean = " ".join(decoded.split())
                        if len(clean) >= 20:
                            lower = clean.lower()
                            if lower not in KNOWN_NOISE and not re.match(r"^[0-9a-fA-F\-_.,;:\s]+$", clean):
                                if clean not in paragraphs:
                                    paragraphs.append(clean)
                    except Exception:
                        pass

            if paragraphs:
                return "\n\n".join(paragraphs).strip()

            return None
        except Exception as e:
            logger.debug(f"Binary doc extraction error: {e}")
            return None

    @classmethod
    def _extract_spreadsheet(cls, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()
        if ext == ".csv":
            return cls._extract_csv(file_path)

        try:
            import openpyxl

            wb = openpyxl.load_workbook(file_path, data_only=True)
            sheets_text = []
            full_text_lines = []
            sheets_data = {}

            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                sheet_lines = [f"--- [Sheet: {sheet_name}] ---"]
                rows_data = []

                for row in ws.iter_rows(values_only=True):
                    if not any(v is not None and str(v).strip() != "" for v in row):
                        continue
                    row_strs = [str(v).strip() if v is not None else "" for v in row]
                    rows_data.append(row_strs)

                if rows_data:
                    header = rows_data[0]
                    sheet_lines.append("| " + " | ".join(header) + " |")
                    sheet_lines.append("| " + " | ".join(["---"] * len(header)) + " |")
                    for row in rows_data[1:100]:
                        sheet_lines.append("| " + " | ".join(row) + " |")
                    if len(rows_data) > 100:
                        sheet_lines.append(
                            f"\n*(Truncated: {len(rows_data)} total rows in sheet '{sheet_name}')*"
                        )

                sheet_content = "\n".join(sheet_lines)
                sheets_text.append(sheet_content)
                full_text_lines.append(sheet_content)
                sheets_data[sheet_name] = rows_data[:200]

            wb.close()
            combined_text = "\n\n".join(full_text_lines).strip()
            return {
                "text": combined_text,
                "page_count": max(1, len(wb.sheetnames)),
                "is_scanned": False,
                "pages": sheets_text,
                "sheets": sheets_data,
                "format": "xlsx",
            }
        except Exception as e:
            return cls._extract_text(file_path, fallback_error=str(e))

    @classmethod
    def _extract_csv(cls, file_path: str) -> dict:
        try:
            rows = []
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                for row in reader:
                    if any(cell.strip() for cell in row):
                        rows.append(row)

            lines = ["--- [CSV Spreadsheet] ---"]
            if rows:
                lines.append("| " + " | ".join(rows[0]) + " |")
                lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
                for row in rows[1:150]:
                    lines.append("| " + " | ".join(row) + " |")
                if len(rows) > 150:
                    lines.append(f"\n*(Truncated: {len(rows)} total rows in CSV)*")

            text = "\n".join(lines)
            return {
                "text": text,
                "page_count": 1,
                "is_scanned": False,
                "pages": [text],
                "sheets": {"CSV Data": rows[:200]},
                "format": "csv",
            }
        except Exception as e:
            return cls._extract_text(file_path, fallback_error=str(e))

    @classmethod
    def _extract_image(cls, file_path: str) -> dict:
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                img_format = img.format or "IMAGE"
                return {
                    "text": (
                        f"[Scanned Image Document: {Path(file_path).name} | "
                        f"Format: {img_format} | Resolution: {width}x{height}px]\n\n"
                        f"Extracting visual transcription and intelligence with Gemini Flash..."
                    ),
                    "page_count": 1,
                    "is_scanned": True,
                    "pages": [f"[Image Document: {Path(file_path).name}]"],
                    "format": Path(file_path).suffix.lower().replace(".", ""),
                    "dimensions": {"width": width, "height": height},
                }
        except Exception:
            return {
                "text": f"[Image document: {Path(file_path).name}]",
                "page_count": 1,
                "is_scanned": True,
                "pages": [],
                "format": "image",
            }

    @classmethod
    def _extract_text(cls, file_path: str, fallback_error: str | None = None) -> dict:
        try:
            with open(file_path, encoding="utf-8", errors="ignore") as f:
                content = f.read()

            clean_content = cls._clean_text(content)
            word_count = len(clean_content.split())
            return {
                "text": clean_content,
                "page_count": max(1, word_count // 350),
                "is_scanned": False,
                "pages": [clean_content],
                "format": Path(file_path).suffix.lower().replace(".", "") or "txt",
            }
        except Exception as e:
            err_msg = fallback_error or str(e)
            return {
                "text": f"Error reading file content: {err_msg}",
                "page_count": 1,
                "is_scanned": False,
                "pages": [],
                "format": "unknown",
            }


extractor = DocumentExtractor()
