import os
import csv
from pathlib import Path
from pypdf import PdfReader
from PIL import Image


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
    def extract(cls, file_path: str) -> dict:
        ext = Path(file_path).suffix.lower()

        if ext == ".pdf":
            return cls._extract_pdf(file_path)
        elif ext in [".docx", ".doc"]:
            return cls._extract_docx(file_path)
        elif ext in [".xlsx", ".xls", ".csv"]:
            return cls._extract_spreadsheet(file_path)
        elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
            return cls._extract_image(file_path)
        else:
            return cls._extract_text(file_path)

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
                for i, page in enumerate(doc):
                    t = page.get_text() or ""
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
    def _extract_docx(cls, file_path: str) -> dict:
        try:
            import docx

            doc = docx.Document(file_path)
            sections_text = []
            full_text_lines = []
            current_section = []

            for para in doc.paragraphs:
                text = para.text.strip()
                if not text:
                    continue

                style_name = para.style.name.lower() if para.style else ""
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

            # Extract embedded tables
            for table_idx, table in enumerate(doc.tables):
                table_lines = [f"\n--- [Table {table_idx + 1}] ---"]
                for row_idx, row in enumerate(table.rows):
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    if row_idx == 0:
                        table_lines.append("| " + " | ".join(cells) + " |")
                        table_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                    else:
                        table_lines.append("| " + " | ".join(cells) + " |")

                table_md = "\n".join(table_lines)
                full_text_lines.append(table_md)
                current_section.append(table_md)

            if current_section:
                sections_text.append("\n".join(current_section))

            combined_text = "\n".join(full_text_lines).strip()
            word_count = len(combined_text.split())
            estimated_pages = max(1, (word_count + 300) // 350)

            return {
                "text": combined_text,
                "page_count": estimated_pages,
                "is_scanned": False,
                "pages": sections_text if sections_text else [combined_text],
                "format": "docx",
            }
        except Exception as e:
            return cls._extract_text(file_path, fallback_error=str(e))

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
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
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
    def _extract_text(cls, file_path: str, fallback_error: str = None) -> dict:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            return {
                "text": content,
                "page_count": max(1, len(content.split()) // 350),
                "is_scanned": False,
                "pages": [content],
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
