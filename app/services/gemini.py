import json
import logging
import re
from collections import Counter
from typing import Any
from app.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You are an Enterprise Multimodal Document Intelligence Analyst.
Analyze the provided document text (or image) with high precision and return a valid JSON object strictly matching this schema:
{
  "document_type": "Invoice" | "Contract" | "Financial Report" | "Tax Document" | "Policy" | "Employment Agreement" | "General Document",
  "title": "Concise descriptive document title",
  "summary": "Crisp executive summary in 3-5 sentences highlighting the most important findings and purpose",
  "key_points": ["Point 1", "Point 2", "Point 3", "Point 4"],
  "entities": {
    "vendor_or_issuer": "string or null",
    "client_or_recipient": "string or null",
    "total_amount": "formatted string with currency or null",
    "currency": "INR | USD | EUR | etc. or null",
    "invoice_number": "string or null",
    "effective_date": "YYYY-MM-DD or readable string or null",
    "due_date": "YYYY-MM-DD or readable string or null"
  },
  "line_items": [
    {"description": "Item description", "quantity": 1, "amount": 1000.0}
  ],
  "action_items": [
    "Immediate action or deadline required"
  ],
  "risk_clauses": [
    "Penalty or auto-renewal or liability clause if applicable"
  ],
  "charts_data": {
    "chart_type": "pie" | "bar" | "none",
    "title": "Cost / Item Breakdown",
    "labels": ["Item A", "Item B"],
    "values": [5000, 3000]
  },
  "extracted_text": "Complete, verbatim text and tables transcribed from the document or image"
}
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
        file_path: str = None,
        is_scanned: bool = False,
    ) -> dict[str, Any]:
        """
        Analyzes a document using Gemini Flash. 
        Falls back to rule-based heuristic extraction if API key is not supplied or fails.
        """
        client = self._get_client()

        if client and self.api_key:
            try:
                from pathlib import Path
                from google.genai import types

                ext = Path(filename).suffix.lower() if filename else ""
                contents = []

                if file_path and os.path.exists(file_path) and ext in [".png", ".jpg", ".jpeg", ".webp"]:
                    mime_map = {
                        ".png": "image/png",
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".webp": "image/webp",
                    }
                    mime_type = mime_map.get(ext, "image/jpeg")
                    with open(file_path, "rb") as f:
                        img_bytes = f.read()
                    contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                    contents.append(
                        f"Document Filename: {filename}\n"
                        f"Perform deep multimodal document intelligence on this image. "
                        f"Extract full text transcription into 'extracted_text', detect all entities, amounts, line items, and summarize."
                    )
                else:
                    contents.append(
                        f"Document Filename: {filename}\n"
                        f"Document Content:\n{sanitized_text[:120000]}"
                    )

                response = client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config={
                        "system_instruction": EXTRACTION_SYSTEM_PROMPT,
                        "temperature": 0.1,
                        "response_mime_type": "application/json",
                    },
                )

                raw_json = response.text.strip()
                # Clean any markdown if present
                if raw_json.startswith("```"):
                    raw_json = re.sub(r"^```(?:json)?\n|\n```$", "", raw_json, flags=re.MULTILINE).strip()

                parsed = json.loads(raw_json)
                return self._normalize_result(parsed)

            except Exception as e:
                logger.warning(f"Gemini API call failed ({e}). Falling back to intelligent heuristic extraction.")

        # Fallback heuristic analyzer
        return self._heuristic_analysis(sanitized_text, filename)

    def _normalize_result(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_type": data.get("document_type") or "Document",
            "title": data.get("title") or "Untitled Document",
            "summary": data.get("summary") or "Document successfully processed.",
            "key_points": data.get("key_points") or [],
            "entities": data.get("entities") or {},
            "line_items": data.get("line_items") or [],
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
            seen_in_page = set(l.strip() for l in p.split("\n") if len(l.strip()) > 10)
            for l in seen_in_page:
                line_page_counts[l] += 1
        repeated_boilerplate = set(l for l, count in line_page_counts.items() if count >= 3)

        clean_lines = []
        for l in text.split("\n"):
            s = l.strip()
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
            except:
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
        for l in clean_lines[:15]:
            if re.match(r"^(?:Client|Customer|Issued To|Policyholder|Bill To|Proposer|Insured Name):\s*(.+)$", l, re.IGNORECASE):
                m = re.match(r"^(?:Client|Customer|Issued To|Policyholder|Bill To|Proposer|Insured Name):\s*(.+)$", l, re.IGNORECASE)
                recipient = m.group(1).strip()
            elif re.search(r"\b(?:MR|MS|MRS|DR)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", l):
                m = re.search(r"\b(?:MR|MS|MRS|DR)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+", l)
                if not recipient:
                    recipient = m.group(0).strip()
            elif not issuer and re.match(r"^[A-Z][A-Za-z0-9\s,\.]{4,50}(?:Ltd|Limited|Inc|Corp|Corporation|Pvt|Company|Enterprises|Solutions)$", l):
                issuer = l.strip()

        # 6. Dynamic Table Line Items Extraction
        line_items = []
        seen_descs = set()
        for l in clean_lines:
            m = re.match(r"^([A-Za-z][A-Za-z0-9\s\(\)\/\-\.\&]{3,60}?)\s+(?:₹|\$|INR|USD|EUR)?\s?([\d,]{1,9}(?:\.\d{2})?)$", l)
            if m:
                desc = m.group(1).strip()
                val_str = m.group(2).replace(",", "")
                if len(val_str) > 8 and not re.search(r"[₹\$]", l):
                    continue
                if desc.lower() in seen_descs:
                    continue
                if any(desc.lower().startswith(skip) for skip in ["page", "tel", "call", "date", "pin", "code", "gstin", "cin", "uin", "email"]):
                    continue

                try:
                    num_val = float(val_str)
                    seen_descs.add(desc.lower())
                    line_items.append({"description": desc, "quantity": 1, "amount": num_val})
                except:
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
        for l in clean_lines:
            l_lower = l.lower()
            if any(k in l_lower for k in risk_keywords) and 30 < len(l) < 220:
                if not any(l[:25] in r for r in risk_clauses):
                    risk_clauses.append(l)

        risk_clauses = risk_clauses[:6]

        # 8. Dynamic Key Points / Section Headings
        key_points = []
        for l in clean_lines:
            if re.match(r"^(?:[0-9]{1,2}[\.\)]|[A-Z][\.\)]|Section|Clause|Article|Product|Certificate|Subject:)\s", l, re.IGNORECASE) and 25 < len(l) < 180:
                if not any(l[:20] in kp for kp in key_points):
                    key_points.append(l)

        if not key_points:
            key_points = [l for l in clean_lines if 30 < len(l) < 150][:5]
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
        conversation_history: list = None,
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
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "system_instruction": CHAT_SYSTEM_PROMPT,
                        "temperature": 0.2,
                        "response_mime_type": "application/json",
                    },
                )
                raw_json = response.text.strip()
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
        focus_aspects: list = None,
    ) -> dict:
        """
        Perform automated redline comparison between Document A and Document B using Gemini 3.5 Flash.
        """
        client = self._get_client()
        if client:
            prompt_parts = [
                f"# DOCUMENT A (BASE / ORIGINAL REVISION)",
                f"Title: {doc_a_title}",
                f"Type: {doc_a_type}",
                f"Summary: {doc_a_summary}",
                f"Structured Insights: {json.dumps(doc_a_insights, indent=2)}",
                f"Document A Text Content (Excerpt):\n{doc_a_text[:12000]}",
                f"\n---",
                f"# DOCUMENT B (TARGET / REVISED VERSION)",
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
                    response = client.models.generate_content(
                        model=self.model_name,
                        contents=user_prompt,
                        config={
                            "system_instruction": COMPARE_SYSTEM_PROMPT,
                            "temperature": 0.2,
                            "response_mime_type": "application/json",
                        },
                    )
                    raw_content = response.text.strip()
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
