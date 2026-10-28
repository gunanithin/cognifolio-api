import json
import logging
import os
import re
from collections import Counter
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are an Enterprise Multimodal Document Intelligence Analyst.
Analyze the provided document text or visual scan/image with extreme precision and depth.
You process ANY business document: contracts, invoices, financial models, insurance policies, healthcare records, identity credentials, dashboards, receipts, tax filings, tables, and reports.

Return a valid JSON object strictly matching this schema:
{
  "document_type": "Invoice" | "Contract" | "Financial Report" | "Tax Document" | "Policy" | "Insurance Document" | "Employment Agreement" | "Dashboard / Portal" | "Identity Document" | "Receipt" | "General Document",
  "title": "Concise, descriptive title that accurately reflects the true document subject, organization, or portal",
  "summary": "Crisp executive summary in 3-5 sentences highlighting the most important findings, parties, numbers, and operational purpose",
  "key_points": [
    "Substantive, factual finding 1 derived directly from the document content",
    "Substantive, factual finding 2",
    "Substantive, factual finding 3",
    "Substantive, factual finding 4"
  ],
  "entities": {
    "vendor_or_issuer": "Issuing company, portal, authority, or vendor (or {\"value\": \"...\", \"box_2d\": [ymin, xmin, ymax, xmax]})",
    "client_or_recipient": "Recipient, user, policyholder, customer, or individual (or {\"value\": \"...\", \"box_2d\": [ymin, xmin, ymax, xmax]})",
    "total_amount": "Total financial amount or premium or balance if present, else null (or {\"value\": \"...\", \"box_2d\": [ymin, xmin, ymax, xmax]})",
    "currency": "Currency code (INR, USD, EUR, GBP, etc.) if detectable, else null",
    "invoice_number": "Reference number, invoice ID, policy number, or transaction ID if present, else null",
    "effective_date": "Start date, policy issuance date, or agreement date if present, else null",
    "due_date": "Due date, renewal date, or expiry date if present, else null"
  },
  "line_items": [
    {"description": "Item, policy, service, or entry description", "quantity": 1, "amount": 1000.0, "box_2d": [ymin, xmin, ymax, xmax]}
  ],
  "action_items": [
    "Immediate action, renewal, review item, or deadline if applicable"
  ],
  "risk_clauses": [
    "Identified risk, penalty, exclusion, forfeiture, or liability clause if applicable"
  ],
  "charts_data": {
    "chart_type": "pie" | "bar" | "none",
    "title": "Distribution or Breakdown",
    "labels": ["Item A", "Item B"],
    "values": [5000, 3000]
  },
  "extracted_text": "Complete, verbatim text and tables transcribed from the document or image"
}

SPATIAL BOUNDING BOXES FOR IMAGES & SCANS:
When analyzing an image or scan (PNG, JPG, WEBP), for each identified entity (issuer, recipient, amount, reference number, dates) and each line item or card entry, detect its exact 2D spatial bounding box `box_2d: [ymin, xmin, ymax, xmax]` normalized on a 0-1000 integer scale (where 0,0 is top-left and 1000,1000 is bottom-right of the image).
Example format:
"vendor_or_issuer": {"value": "PortfoTrack", "box_2d": [27, 39, 50, 112]}
"client_or_recipient": {"value": "John Doe", "box_2d": [784, 19, 800, 66]}
"total_amount": {"value": "$1,250.00", "box_2d": [780, 650, 815, 880]}
"line_items": [{"description": "Cloud Hosting", "quantity": 1, "amount": 120.0, "box_2d": [450, 120, 480, 880]}]
If an entity's coordinates are not identifiable or for plain text files, provide the string directly or leave box_2d as null.

MANDATORY LEAF-LEVEL GRANULARITY FOR BOUNDING BOXES & LINE ITEMS:
- When analyzing diagrams, flowcharts, architectures, invoices, receipts, dashboards, or tables, NEVER group an entire column, section, or parent container into one compound entry (e.g. do NOT return "Backend & Processing (FastAPI, PII Redaction Engine, Temporal Workflow Orchestration, Gemini Flash API)" with a giant bounding box covering the entire column).
- Instead, extract every individual leaf element, sub-card, workflow node, database block, table row, or button as its own dedicated entry in `line_items`.
- Each entry MUST have its own tight, precise `box_2d: [ymin, xmin, ymax, xmax]` bounding box tightly wrapping that specific component.
- For example, in an architecture diagram with a "Backend & Processing" column containing "PII Redaction Engine", "FastAPI Gateway", "Temporal Orchestration", and "Gemini Flash API", create 4 separate line items, one for each sub-box!
Return ONLY valid JSON with no markdown backticks or commentary.
"""

CHAT_SYSTEM_PROMPT = """You are the Cognifolio Multimodal Document Intelligence Assistant, powered by Gemini 3.5 Flash.
You are assisting an enterprise analyst with a deep-dive conversation about a specific document loaded in the viewer.

Your job is to answer the user's questions with high precision, grounded strictly in the provided document text and structured insights.

Always return a valid JSON object strictly matching this schema:
{
  "reply": "Your detailed, clear answer formatted in clean Markdown with bolding or bullet points where helpful.",
  "citations": [
    {
      "label": "Short reference or section title, e.g. 'Section 4: Payment Terms' or 'Invoice Total'",
      "text": "Exact short sentence or phrase excerpt (10 to 60 characters) from the document that verifies your answer, so the visual viewer can highlight it on the PDF",
      "page": 1,
      "type": "clause" | "entity" | "line_item" | "text"
    }
  ],
  "suggested_followups": [
    "Suggested question 1?",
    "Suggested question 2?",
    "Suggested question 3?"
  ]
}

CRITICAL RULES:
1. Grounding: Answer ONLY using facts from the document. If information is not mentioned, state clearly that the document does not contain that information.
2. Citations: Every key fact, fee, date, or clause mentioned must include an exact verbatim text excerpt in the `citations` list so the user can click it to jump the PDF canvas directly to that location.
3. Keep citation text exact (verbatim substring of document text) so the text search engine can locate the exact page and coordinates.
4. Output: Return ONLY the JSON object without markdown fences or extraneous text.
"""

COMPARE_SYSTEM_PROMPT = """You are the Cognifolio Senior Redline & Legal/Procurement Diff Auditor, powered by Gemini 3.5 Flash.
You are comparing two enterprise documents:
- Document A (Base Revision / Original Agreement / Purchase Order)
- Document B (Target Revision / Amendment / Vendor Invoice)

Your goal is to perform automated redlining:
1. Identify clause additions (present in B but not in A).
2. Identify clause deletions (present in A but omitted from B).
3. Identify clause modifications (changed language, shifted obligations, amended schedules).
4. Identify financial and fee discrepancies (total amount delta, line-item changes, payment term shifts).
5. Assess enterprise risk impact (has risk increased, decreased, or remained neutral? e.g., indemnity caps removed, warranties shortened, payment windows compressed).

Always return a valid JSON object strictly matching this schema:
{
  "executive_summary": "Comprehensive 2-4 sentence executive overview of what changed between Document A and Document B, highlighting major material alterations.",
  "metrics": {
    "added_count": 2,
    "removed_count": 1,
    "modified_count": 4,
    "financial_delta": "+$15,000 / +12.5%",
    "similarity_score": 82.5,
    "risk_delta": "increased"  // "increased" | "decreased" | "neutral"
  },
  "diffs": [
    {
      "id": "diff-1",
      "type": "modified", // "added" | "removed" | "modified" | "unchanged"
      "category": "liability", // "clause" | "financial" | "liability" | "term" | "entity" | "other"
      "title": "Limitation of Liability Cap",
      "doc_a_text": "Supplier's total liability under this agreement shall not exceed 12 months of fees paid ($120,000).",
      "doc_b_text": "Supplier's liability shall be capped at $25,000, excluding direct damages.",
      "risk_impact": "increased", // "increased" | "decreased" | "neutral" | "high" | "critical"
      "explanation": "Document B reduces the liability cap by $95,000, substantially increasing client risk exposure in the event of an outage.",
      "doc_a_page": 4,
      "doc_b_page": 5
    }
  ],
  "key_recommendations": [
    "Negotiate to restore 12-month liability cap or demand reciprocal indemnification.",
    "Clarify Net-30 payment term discrepancy before signing."
  ]
}

CRITICAL RULES:
1. Grounding: Every diff item must be based strictly on actual differences between Document A and Document B.
2. If two documents are completely different document types (e.g., an invoice and an insurance policy), explicitly note the fundamental divergence in executive_summary, calculate a low similarity score, and highlight cross-document mismatches.
3. If they are revisions of the same agreement/policy, extract granular line-by-line clause differences.
4. Output: Return ONLY the JSON object without markdown fences or extraneous text.
"""



class GeminiDocumentAnalyzer:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.GEMINI_MODEL
        self._client = None

    def _get_client(self):
        if not self._client and self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Failed to initialize google.genai client: {e}")
        return self._client

    async def analyze_document(
        self,
        sanitized_text: str,
        filename: str,
        file_path: str | None = None,
        is_scanned: bool = False,
    ) -> dict[str, Any]:
        """
        Analyzes a document using Gemini multimodal intelligence with multi-model fallback and backoff retries.
        Falls back to rule-based heuristic extraction only if all AI attempts fail on text documents.
        """
        _ = is_scanned
        client = self._get_client()

        if client and self.api_key:
            import asyncio
            from pathlib import Path
            from google.genai import types

            ext = Path(filename).suffix.lower() if filename else ""
            is_image = bool(file_path and os.path.exists(file_path) and ext in [".png", ".jpg", ".jpeg", ".webp"])

            contents = []
            if is_image and file_path is not None:
                safe_path: str = file_path
                mime_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                }
                mime_type = mime_map.get(ext, "image/jpeg")

                # Optimize large image scans (up to 30MB) locally in <100ms for sub-second AI inference
                try:
                    import io
                    from PIL import Image

                    with Image.open(safe_path) as img:
                        orig_w, orig_h = img.size
                        max_dim = 2048
                        file_sz = os.path.getsize(safe_path) if os.path.exists(safe_path) else 0

                        if max(orig_w, orig_h) > max_dim or file_sz > 1.5 * 1024 * 1024:
                            scale = min(1.0, max_dim / max(orig_w, orig_h))
                            new_size = (max(1, int(orig_w * scale)), max(1, int(orig_h * scale)))
                            img = img.resize(new_size, Image.Resampling.LANCZOS)
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGB")
                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=88, optimize=True)
                            img_bytes = buf.getvalue()
                            mime_type = "image/jpeg"
                            logger.info(f"Optimized visual document from {file_sz/1024:.1f}KB to {len(img_bytes)/1024:.1f}KB for rapid AI inference.")
                        else:
                            with open(safe_path, "rb") as f:
                                img_bytes = f.read()
                except Exception as opt_err:
                    logger.warning(f"Image optimization fallback: {opt_err}")
                    with open(safe_path, "rb") as f:
                        img_bytes = f.read()

                contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                contents.append(
                    f"Document Filename: {filename}\n"
                    f"Perform deep multimodal document intelligence on this visual document scan.\n"
                    f"1. Transcribe the entire document verbatim into 'extracted_text'.\n"
                    f"2. Extract true business intelligence: document_type, title, executive summary, and substantive key takeaways.\n"
                    f"3. Identify all key entities (issuing organization, recipient/user, reference numbers, amounts, dates, status).\n"
                    f"4. For every identified entity and line item / card, calculate its exact 2D spatial bounding box coordinates [ymin, xmin, ymax, xmax] on a 0-1000 normalized scale.\n"
                    f"5. IMPORTANT: Extract granular leaf components individually. NEVER lump an entire column, section, or table into a single compound line item. Every individual card, module, node, or row must be its own line item with its own tight bounding box."
                )
            else:
                contents.append(
                    f"Document Filename: {filename}\n"
                    f"Document Content:\n{sanitized_text[:120000]}"
                )

            # Candidate models in priority order (gemini-3.5-flash-lite provides 10x higher throughput and 6s inference)
            candidate_models = [
                "gemini-3.5-flash-lite",
                self.model_name or "gemini-3.5-flash-lite",
                "gemini-3.5-flash",
                "gemini-3.8-flash",
            ]
            seen_models = set()
            models_to_try = []
            for m in candidate_models:
                if m and m not in seen_models:
                    seen_models.add(m)
                    models_to_try.append(m)

            last_error = None
            for model_id in models_to_try:
                for attempt in range(2):
                    try:
                        response = await client.aio.models.generate_content(
                            model=model_id,
                            contents=contents,
                            config={
                                "system_instruction": EXTRACTION_SYSTEM_PROMPT,
                                "temperature": 0.1,
                                "response_mime_type": "application/json",
                            },
                        )

                        raw_json = (response.text or "").strip()
                        if raw_json.startswith("```"):
                            raw_json = re.sub(r"^```(?:json)?\n|\n```$", "", raw_json, flags=re.MULTILINE).strip()

                        parsed = json.loads(raw_json)
                        logger.info(f"Successfully analyzed '{filename}' using {model_id}")
                        return self._normalize_result(parsed)

                    except Exception as e:
                        last_error = e
                        err_str = str(e)
                        logger.warning(f"Gemini model {model_id} attempt {attempt+1} failed: {e}")
                        # If quota exceeded (429) or unavailable (503), fast-failover immediately to the next model
                        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "503" in err_str or "UNAVAILABLE" in err_str:
                            break
                        if attempt == 0:
                            await asyncio.sleep(0.5)

            logger.error(f"All Gemini models failed for '{filename}'. Last error: {last_error}")

            # For image files, DO NOT run text regex heuristics on placeholder strings!
            if is_image:
                return {
                    "document_type": "Image Scan",
                    "title": filename.replace(ext, "").replace("_", " ").title(),
                    "summary": f"Image scan '{filename}' uploaded. AI vision processing temporarily unavailable due to upstream API capacity. Please click Re-analyze to retry.",
                    "key_points": [
                        f"Image scan uploaded ({ext.upper()})",
                        "AI vision model was temporarily unavailable during upload. Use Re-analyze to generate insights."
                    ],
                    "entities": {},
                    "line_items": [],
                    "action_items": ["Click 'Re-analyze' to re-process visual intelligence."],
                    "risk_clauses": [],
                    "charts_data": {"chart_type": "none", "labels": [], "values": []},
                    "raw_extracted_text": "",
                    "confidence_score": 0.0,
                }

        # Fallback heuristic analyzer for plain text / PDF files
        return self._heuristic_analysis(sanitized_text, filename)

    def _sanitize_box_2d(self, box: Any) -> list[int] | None:
        """
        Validates and sanitizes a 2D bounding box [ymin, xmin, ymax, xmax] normalized to 0-1000 scale.
        """
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        try:
            coords = [round(float(c)) for c in box]
            coords = [max(0, min(1000, c)) for c in coords]
            ymin, xmin, ymax, xmax = coords
            if ymax >= ymin and xmax >= xmin:
                return coords
            return None
        except (ValueError, TypeError):
            return None

    def _unpack_compound_line_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Decomposes compound macro-container items (e.g. 'Backend & Processing (FastAPI, PII Redaction Engine, Temporal...)')
        into individual leaf items with proportional sub-boxes. Preserves the container while providing granular leaf targets.
        """
        unpacked: list[dict[str, Any]] = []
        for it in items:
            desc = (it.get("description") or "").strip()
            box = it.get("box_2d")

            paren_match = re.match(r"^([^(:\n]+?)\s*\(([^)]+)\)$", desc)
            if paren_match and box and len(box) == 4 and ("," in paren_match.group(2) or ";" in paren_match.group(2)):
                sub_parts = [s.strip() for s in re.split(r"[,;]+", paren_match.group(2)) if len(s.strip()) >= 2]
                if len(sub_parts) >= 2:
                    # Keep parent container
                    unpacked.append(it)

                    ymin, xmin, ymax, xmax = box
                    h = ymax - ymin
                    w = xmax - xmin
                    n = len(sub_parts)
                    is_vertical = h >= w
                    for idx, part in enumerate(sub_parts):
                        if is_vertical:
                            step = h / n
                            s_ymin = int(round(ymin + idx * step))
                            s_ymax = int(round(ymin + (idx + 1) * step))
                            sub_box = [s_ymin, xmin, s_ymax, xmax]
                        else:
                            step = w / n
                            s_xmin = int(round(xmin + idx * step))
                            s_xmax = int(round(xmin + (idx + 1) * step))
                            sub_box = [ymin, s_xmin, ymax, s_xmax]

                        sanitized_sub_box = self._sanitize_box_2d(sub_box)
                        unpacked.append({
                            "description": part,
                            "quantity": 1,
                            "amount": None,
                            "box_2d": sanitized_sub_box if sanitized_sub_box else box,
                        })
                    continue

            unpacked.append(it)
        return unpacked

    def _normalize_result(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_entities = data.get("entities") or {}
        normalized_entities = {}
        if isinstance(raw_entities, dict):
            for k, v in raw_entities.items():
                if isinstance(v, dict):
                    val = v.get("value")
                    box = self._sanitize_box_2d(v.get("box_2d"))
                    if box is not None:
                        normalized_entities[k] = {
                            "value": str(val) if val is not None else "",
                            "box_2d": box,
                        }
                    else:
                        normalized_entities[k] = val if val is not None else v
                else:
                    normalized_entities[k] = v
        else:
            normalized_entities = {}

        raw_line_items = data.get("line_items") or []
        normalized_line_items = []
        if isinstance(raw_line_items, list):
            for item in raw_line_items:
                if isinstance(item, dict):
                    clean_item = dict(item)
                    if "box_2d" in clean_item:
                        sanitized = self._sanitize_box_2d(clean_item["box_2d"])
                        if sanitized is not None:
                            clean_item["box_2d"] = sanitized
                        else:
                            clean_item.pop("box_2d", None)
                    normalized_line_items.append(clean_item)

        normalized_line_items = self._unpack_compound_line_items(normalized_line_items)

        return {
            "document_type": data.get("document_type") or "Document",
            "title": data.get("title") or "Untitled Document",
            "summary": data.get("summary") or "Document successfully processed.",
            "key_points": data.get("key_points") or [],
            "entities": normalized_entities,
            "line_items": normalized_line_items,
            "action_items": data.get("action_items") or [],
            "risk_clauses": data.get("risk_clauses") or [],
            "charts_data": data.get("charts_data") or {"chart_type": "none", "labels": [], "values": []},
            "raw_extracted_text": data.get("extracted_text") or "",
            "confidence_score": 0.95,
        }

    def _heuristic_analysis(self, text: str, filename: str) -> dict[str, Any]:
        """
        Dynamic heuristic extraction engine used when GEMINI_API_KEY is not configured.
        Parses document text dynamically across any document without hardcoded static templates.
        """
        text_lower = text.lower()

        # 1. Document Category Classification
        doc_type = "Document"
        if any(w in text_lower for w in ["tax invoice", "invoice no", "bill to", "subtotal", "gstin", "amount due"]):
            doc_type = "Invoice"
        elif any(w in text_lower for w in ["health insurance", "sum insured", "policyholder", "insurance policy", "premium details", "policy copy"]):
            doc_type = "Policy"
        elif any(w in text_lower for w in ["agreement", "contract", "parties", "hereby agreed", "terms and conditions"]):
            doc_type = "Contract"
        elif any(w in text_lower for w in ["balance sheet", "revenue", "ebitda", "profit", "quarterly", "q1", "q2", "q3", "q4"]):
            doc_type = "Financial Report"
        elif any(w in text_lower for w in ["guidelines", "compliance", "policy"]):
            doc_type = "Policy"

        # 2. Dynamic Duplicate Header/Footer Suppression across pages
        raw_pages = text.split("--- [Page ")
        line_page_counts = Counter()
        for p in raw_pages:
            seen_in_page = {ln.strip() for ln in p.split("\n") if len(ln.strip()) > 10}
            for ln in seen_in_page:
                line_page_counts[ln] += 1
        repeated_boilerplate = {ln for ln, count in line_page_counts.items() if count >= 3}

        clean_lines = []
        for raw_l in text.split("\n"):
            s = raw_l.strip()
            if len(s) > 10 and s not in repeated_boilerplate and not s.startswith("---"):
                clean_lines.append(s)

        # 3. Dynamic Currency & Total Amount Detection
        amounts = re.findall(r"(?:₹|\$|INR|USD|EUR|GBP|€|£)\s?[\d,]+(?:\.\d{2})?", text, re.IGNORECASE)
        currency = "USD"
        if "₹" in text or "inr" in text_lower:
            currency = "INR"
        elif "€" in text or "eur" in text_lower:
            currency = "EUR"
        elif "£" in text or "gbp" in text_lower:
            currency = "GBP"

        # Extract numeric amounts to find highest/total amount
        parsed_amounts = []
        for a in amounts:
            try:
                num = float(re.sub(r"[^\d.]", "", a))
                parsed_amounts.append((num, a))
            except (ValueError, TypeError):
                pass

        parsed_amounts.sort(key=lambda x: x[0], reverse=True)
        total_amount = parsed_amounts[0][1] if parsed_amounts else (amounts[0] if amounts else None)

        # 4. Dynamic Date Detection
        dates = re.findall(
            r"\b(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4})\b",
            text,
            re.IGNORECASE,
        )
        seen_dates = set()
        detected_dates = []
        for d in dates:
            if d not in seen_dates:
                seen_dates.add(d)
                detected_dates.append(d)

        # 5. Dynamic Entities: Issuer & Recipient
        issuer = None
        recipient = None
        for ln in clean_lines[:15]:
            if m := re.match(r"^(?:Client|Customer|Issued To|Policyholder|Bill To|Proposer|Insured Name):\s*(.+)$", ln, re.IGNORECASE):
                recipient = m.group(1).strip()
            elif m := re.search(r"\b(?:MR|MS|MRS|DR)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", ln):
                if not recipient:
                    recipient = m.group(0).strip()
            elif not issuer and re.match(r"^[A-Z][A-Za-z0-9\s,\.]{4,50}(?:Ltd|Limited|Inc|Corp|Corporation|Pvt|Company|Enterprises|Solutions)$", ln):
                issuer = ln.strip()

        # 6. Dynamic Table Line Items Extraction
        line_items = []
        seen_descs = set()
        for ln in clean_lines:
            m = re.match(r"^([A-Za-z][A-Za-z0-9\s\(\)\/\-\.\&]{3,60}?)\s+(?:₹|\$|INR|USD|EUR)?\s?([\d,]{1,9}(?:\.\d{2})?)$", ln)
            if m:
                desc = m.group(1).strip()
                val_str = m.group(2).replace(",", "")
                if len(val_str) > 8 and not re.search(r"[₹\$]", ln):
                    continue
                if desc.lower() in seen_descs:
                    continue
                if any(desc.lower().startswith(skip) for skip in ["page", "tel", "call", "date", "pin", "code", "gstin", "cin", "uin", "email"]):
                    continue

                try:
                    num_val = float(val_str)
                    seen_descs.add(desc.lower())
                    line_items.append({"description": desc, "quantity": 1, "amount": num_val})
                except (ValueError, TypeError):
                    pass

        # Take the most relevant table rows
        line_items = line_items[:6]

        # 7. Dynamic Risk & Penalty Clauses
        risk_keywords = [
            "penalty", "termination", "liquidated damages", "liability", "breach", "cancellation",
            "cancel", "interest", "forfeit", "moratorium", "exclusion", "warranty", "pre-existing",
            "non-disclosure", "dispute", "governing law"
        ]
        risk_clauses = []
        for ln in clean_lines:
            l_lower = ln.lower()
            if any(k in l_lower for k in risk_keywords) and 30 < len(ln) < 220:
                if not any(ln[:25] in r for r in risk_clauses):
                    risk_clauses.append(ln)

        risk_clauses = risk_clauses[:6]

        # 8. Dynamic Key Points / Section Headings
        key_points = []
        for ln in clean_lines:
            if re.match(r"^(?:[0-9]{1,2}[\.\)]|[A-Z][\.\)]|Section|Clause|Article|Product|Certificate|Subject:)\s", ln, re.IGNORECASE) and 25 < len(ln) < 180:
                if not any(ln[:20] in kp for kp in key_points):
                    key_points.append(ln)

        if not key_points:
            key_points = [ln for ln in clean_lines if 30 < len(ln) < 150][:5]
        else:
            key_points = key_points[:6]

        # 9. Dynamic Chart Data from Line Items
        labels = [item["description"][:24] for item in line_items if item["amount"] > 0][:5]
        values = [item["amount"] for item in line_items if item["amount"] > 0][:5]

        # 10. Dynamic Executive Summary
        summary = (
            f"Analyzed {doc_type} '{filename}'. "
            f"Detected {len(clean_lines)} content segments"
            + (f" with a total identified figure of {total_amount}." if total_amount else ".")
            + (f" Primary parties: {issuer or 'unspecified issuer'} and {recipient}." if recipient else "")
            + f" Extracted {len(key_points)} key findings and {len(line_items)} structured table rows."
        )

        return {
            "document_type": doc_type,
            "title": filename.replace(".pdf", "").replace("_", " ").title(),
            "summary": summary,
            "key_points": key_points,
            "entities": {
                "vendor_or_issuer": issuer,
                "client_or_recipient": recipient,
                "total_amount": total_amount,
                "currency": currency,
                "detected_dates": detected_dates[:3],
            },
            "line_items": line_items,
            "action_items": [
                f"Review {doc_type} terms and verify {len(line_items)} line items",
                "Ensure compliance with risk and policy clauses before retention deadline"
            ],
            "risk_clauses": risk_clauses,
            "charts_data": {
                "chart_type": "pie" if len(values) > 1 else "none",
                "title": f"Cost Distribution ({filename})",
                "labels": labels,
                "values": values,
            },
            "confidence_score": 0.92,
        }

    async def chat_with_document(
        self,
        document_title: str,
        document_type: str,
        summary: str,
        raw_text: str,
        insights_data: dict,
        message: str,
        conversation_history: list[Any] | None = None,
    ) -> dict[str, Any]:
        """
        Conducts a conversational deep-dive with Gemini 3.5 Flash about the active document,
        returning answers grounded strictly in the document text with interactive PDF citation links.
        """
        client = self._get_client()

        # Format conversation history
        history_text = ""
        if conversation_history:
            turns = []
            for item in conversation_history[-6:]:
                role = str(getattr(item, "role", None) or (item.get("role") if isinstance(item, dict) else "user")).capitalize()
                content = str(getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else ""))
                turns.append(f"{role}: {content}")
            history_text = "\nPrior Conversation Turns:\n" + "\n".join(turns) + "\n"

        prompt = f"""DOCUMENT CONTEXT:
Title: {document_title}
Type: {document_type}
Summary: {summary}
Key Entities: {json.dumps(insights_data.get('entities', {}))}
Risk Clauses: {json.dumps(insights_data.get('risk_clauses', []))}
Line Items: {json.dumps(insights_data.get('line_items', []))}

DOCUMENT RAW CONTENT:
{raw_text[:60000]}

{history_text}
USER QUESTION:
{message}
"""

        if client and self.api_key:
            try:
                response = await client.aio.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "system_instruction": CHAT_SYSTEM_PROMPT,
                        "temperature": 0.2,
                        "response_mime_type": "application/json",
                    },
                )
                raw_json = (response.text or "").strip()
                if raw_json.startswith("```"):
                    raw_json = re.sub(r"^```(?:json)?\n|\n```$", "", raw_json, flags=re.MULTILINE).strip()

                parsed = json.loads(raw_json)
                return {
                    "reply": parsed.get("reply", "I reviewed the document regarding your question."),
                    "citations": parsed.get("citations", []),
                    "suggested_followups": parsed.get("suggested_followups", []),
                }
            except Exception as e:
                logger.warning(f"Gemini chat_with_document call failed ({e}). Falling back to contextual search.")

        # Fallback contextual search
        return self._fallback_chat_reply(message, raw_text, insights_data, document_title)

    def _fallback_chat_reply(self, message: str, raw_text: str, insights: dict, doc_title: str) -> dict:
        _ = insights
        q_lower = message.lower()
        sentences = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", raw_text) if len(s.strip()) > 15]

        # Find matching sentences
        words = [w for w in re.findall(r"\w+", q_lower) if len(w) > 3 and w not in ["what", "where", "when", "which", "about", "this", "that"]]
        matches = []
        if words:
            for s in sentences:
                score = sum(1 for w in words if w in s.lower())
                if score > 0:
                    matches.append((score, s))
            matches.sort(key=lambda x: x[0], reverse=True)

        citations = []
        if matches:
            best_sentence = matches[0][1]
            citations.append({
                "label": "Document Excerpt",
                "text": best_sentence[:75].strip(),
                "page": 1,
                "type": "text",
            })
            reply = f"Based on **{doc_title}**, the document states:\n\n> \"{best_sentence}\""
        else:
            reply = f"I examined **{doc_title}**, but could not find a specific statement directly addressing your query. You can ask about payment terms, parties involved, or risk clauses."

        return {
            "reply": reply,
            "citations": citations,
            "suggested_followups": [
                "What are the payment terms?",
                "Are there any penalty or risk clauses?",
                "Who are the named parties in this document?"
            ],
        }

    async def compare_documents(
        self,
        doc_a_title: str,
        doc_a_type: str,
        doc_a_summary: str,
        doc_a_text: str,
        doc_a_insights: dict,
        doc_b_title: str,
        doc_b_type: str,
        doc_b_summary: str,
        doc_b_text: str,
        doc_b_insights: dict,
        focus_aspects: list[str] | None = None,
    ) -> dict:
        """
        Perform automated redline comparison between Document A and Document B using Gemini 3.5 Flash.
        """
        client = self._get_client()
        if client:
            prompt_parts = [
                "# DOCUMENT A (BASE / ORIGINAL REVISION)",
                f"Title: {doc_a_title}",
                f"Type: {doc_a_type}",
                f"Summary: {doc_a_summary}",
                f"Structured Insights: {json.dumps(doc_a_insights, indent=2)}",
                f"Document A Text Content (Excerpt):\n{doc_a_text[:12000]}",
                "\n---",
                "# DOCUMENT B (TARGET / REVISED VERSION)",
                f"Title: {doc_b_title}",
                f"Type: {doc_b_type}",
                f"Summary: {doc_b_summary}",
                f"Structured Insights: {json.dumps(doc_b_insights, indent=2)}",
                f"Document B Text Content (Excerpt):\n{doc_b_text[:12000]}",
            ]
            if focus_aspects:
                prompt_parts.append(f"\nUser Priority Focus Aspects: {', '.join(focus_aspects)}")

            prompt_parts.append("\nExecute full redline comparison. Return ONLY the strict JSON object with executive_summary, metrics, diffs, and key_recommendations.")
            user_prompt = "\n".join(prompt_parts)

            for attempt in range(2):
                try:
                    response = await client.aio.models.generate_content(
                        model=self.model_name,
                        contents=user_prompt,
                        config={
                            "system_instruction": COMPARE_SYSTEM_PROMPT,
                            "temperature": 0.2,
                            "response_mime_type": "application/json",
                        },
                    )
                    raw_content = (response.text or "").strip()
                    if raw_content.startswith("```"):
                        raw_content = re.sub(r"^```(?:json)?\n|\n```$", "", raw_content, flags=re.MULTILINE).strip()
                    parsed = json.loads(raw_content)
                    if "executive_summary" in parsed and "diffs" in parsed and "metrics" in parsed:
                        return parsed
                except Exception as e:
                    logger.warning(f"Gemini compare_documents call attempt {attempt+1} failed: {e}")
                    if attempt == 0:
                        import asyncio
                        await asyncio.sleep(1.0)

        # Fallback comparison
        return self._fallback_compare(
            doc_a_title, doc_a_type, doc_a_summary, doc_a_text, doc_a_insights,
            doc_b_title, doc_b_type, doc_b_summary, doc_b_text, doc_b_insights
        )

    def _fallback_compare(
        self,
        doc_a_title: str,
        doc_a_type: str,
        doc_a_summary: str,
        doc_a_text: str,
        doc_a_insights: dict,
        doc_b_title: str,
        doc_b_type: str,
        doc_b_summary: str,
        doc_b_text: str,
        doc_b_insights: dict,
    ) -> dict:
        """Deterministic comparison fallback based on structured insights and lexical analysis."""
        _ = (doc_a_summary, doc_b_summary)
        diffs = []
        item_id = 1

        # Check Document Type divergence
        if doc_a_type.lower() != doc_b_type.lower():
            diffs.append({
                "id": f"diff-{item_id}",
                "type": "modified",
                "category": "other",
                "title": "Document Classification Variance",
                "doc_a_text": f"Document Type: {doc_a_type}",
                "doc_b_text": f"Document Type: {doc_b_type}",
                "risk_impact": "high",
                "explanation": f"Document A is classified as '{doc_a_type}' while Document B is '{doc_b_type}', indicating fundamentally different contractual or operational purposes.",
                "doc_a_page": 1,
                "doc_b_page": 1,
            })
            item_id += 1

        # Compare Financials / Entities
        entities_a = doc_a_insights.get("entities") or {}
        entities_b = doc_b_insights.get("entities") or {}
        fin_a = entities_a.get("financial") or {}
        fin_b = entities_b.get("financial") or {}

        tot_a = fin_a.get("total_amount")
        tot_b = fin_b.get("total_amount")
        fin_delta_str = None
        if tot_a or tot_b:
            if tot_a != tot_b:
                fin_delta_str = f"From {tot_a or 'Unspecified'} to {tot_b or 'Unspecified'}"
                diffs.append({
                    "id": f"diff-{item_id}",
                    "type": "modified",
                    "category": "financial",
                    "title": "Total Contract / Invoiced Valuation",
                    "doc_a_text": f"Total: {tot_a or 'Not defined'}",
                    "doc_b_text": f"Total: {tot_b or 'Not defined'}",
                    "risk_impact": "increased" if tot_b and not tot_a else "neutral",
                    "explanation": f"Commercial discrepancy detected: valuation changed from '{tot_a}' in Document A to '{tot_b}' in Document B.",
                    "doc_a_page": 1,
                    "doc_b_page": 1,
                })
                item_id += 1

        # Compare Risk Clauses
        clauses_a = {c.get("clause", ""): c for c in doc_a_insights.get("risk_clauses", []) if isinstance(c, dict)}
        clauses_b = {c.get("clause", ""): c for c in doc_b_insights.get("risk_clauses", []) if isinstance(c, dict)}

        for c_title, c_val in clauses_b.items():
            if c_title not in clauses_a:
                diffs.append({
                    "id": f"diff-{item_id}",
                    "type": "added",
                    "category": "clause",
                    "title": c_title or "Added Clause",
                    "doc_a_text": "",
                    "doc_b_text": c_val.get("excerpt", c_val.get("reason", "")),
                    "risk_impact": c_val.get("severity", "neutral"),
                    "explanation": f"New provision introduced in Document B: {c_val.get('reason', 'Clause added to target version.')}",
                    "doc_a_page": None,
                    "doc_b_page": c_val.get("page", 1),
                })
                item_id += 1

        for c_title, c_val in clauses_a.items():
            if c_title not in clauses_b:
                diffs.append({
                    "id": f"diff-{item_id}",
                    "type": "removed",
                    "category": "clause",
                    "title": c_title or "Removed Clause",
                    "doc_a_text": c_val.get("excerpt", c_val.get("reason", "")),
                    "doc_b_text": "",
                    "risk_impact": "increased" if c_val.get("severity") in ["high", "critical"] else "neutral",
                    "explanation": f"Clause from Document A was omitted in Document B: {c_val.get('reason', 'Removed from target version.')}",
                    "doc_a_page": c_val.get("page", 1),
                    "doc_b_page": None,
                })
                item_id += 1

        # Jaccard lexical similarity
        words_a = set(re.findall(r"\w+", doc_a_text.lower()))
        words_b = set(re.findall(r"\w+", doc_b_text.lower()))
        intersection = words_a.intersection(words_b)
        union = words_a.union(words_b)
        similarity = round((len(intersection) / len(union) * 100), 1) if union else 100.0

        added_cnt = sum(1 for d in diffs if d["type"] == "added")
        removed_cnt = sum(1 for d in diffs if d["type"] == "removed")
        modified_cnt = sum(1 for d in diffs if d["type"] == "modified")

        risk_delta = "neutral"
        if any(d["risk_impact"] in ["high", "critical", "increased"] for d in diffs):
            risk_delta = "increased"

        exec_summary = (
            f"Comparison between '{doc_a_title}' and '{doc_b_title}' identified {len(diffs)} material variations "
            f"with an overall content similarity of {similarity}%. "
            f"{'Target document introduces heightened risk exposure.' if risk_delta == 'increased' else 'Terms remain broadly aligned without major liability shifts.'}"
        )

        return {
            "executive_summary": exec_summary,
            "metrics": {
                "added_count": added_cnt,
                "removed_count": removed_cnt,
                "modified_count": modified_cnt,
                "financial_delta": fin_delta_str,
                "similarity_score": similarity,
                "risk_delta": risk_delta,
            },
            "diffs": diffs,
            "key_recommendations": [
                "Audit added and omitted clauses with counsel prior to contract execution.",
                "Verify fee calculations and payment schedules against agreed purchase orders.",
            ],
        }



gemini_analyzer = GeminiDocumentAnalyzer()
