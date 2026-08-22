"""Deterministic document loading and section-level chunking.

Metadata (status, effective date, scope, authority rank) comes from the
verified catalog (docs/02_DOMAIN_SPEC.md §2, validated in Phase 0); the seed
step additionally asserts each PDF's own header states the expected status,
so catalog and document can never silently drift apart.

Historical ticket resolutions are chunked too — with status
HISTORICAL_RESOLUTION and authority_rank 5 — so they can be surfaced ONLY on
explicit request (03_AGENT_SPEC.md §6) and can never be mistaken for policy.
"""

import re
from pathlib import Path

from pypdf import PdfReader

SECTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")

# Verified catalog — docs/02_DOMAIN_SPEC.md §2 (Phase 0 validation).
DOCUMENT_CATALOG = {
    "01_Support_Policy_v3_CURRENT": {
        "file": "01_Support_Policy_v3_CURRENT.pdf",
        "status": "CURRENT",
        "effective": "2026-05-01",
        "scope": "GENERAL",
        "authority_rank": 2,
    },
    "02_Support_Policy_v2_DEPRECATED": {
        "file": "02_Support_Policy_v2_DEPRECATED.pdf",
        "status": "DEPRECATED",
        "effective": "2025-01-01",
        "scope": "GENERAL",
        "authority_rank": 4,
    },
    "03_Cancellation_and_Service_Credit_SOP_v4": {
        "file": "03_Cancellation_and_Service_Credit_SOP_v4.pdf",
        "status": "CURRENT",
        "effective": "2026-06-15",
        "scope": "GENERAL",
        "authority_rank": 2,
    },
    "04_Product_Operations_Guide_and_Known_Issues": {
        "file": "04_Product_Operations_Guide_and_Known_Issues.pdf",
        "status": "CURRENT",
        "effective": "2026-08-14",
        "scope": "GENERAL",
        "authority_rank": 3,
    },
    "05_Northstar_Logistics_Enterprise_Agreement": {
        "file": "05_Northstar_Logistics_Enterprise_Agreement.pdf",
        "status": "ACTIVE",
        "effective": "2026-01-01",
        "scope": "ACCT-001",
        "authority_rank": 1,
    },
    "06_LumenWorks_Service_Agreement": {
        "file": "06_LumenWorks_Service_Agreement.pdf",
        "status": "ACTIVE",
        "effective": "2026-03-01",
        "scope": "ACCT-002",
        "authority_rank": 1,
    },
}


def extract_pdf_text(pdf_path):
    """Layout-preserving text extraction (real text layer, no OCR)."""
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    return "\n".join(pages)


def chunk_text(text):
    """Split a document into (section, text) chunks deterministically.

    Emits a Header chunk (title/status lines), then one chunk per numbered
    section. Documents without numbered sections (Policy v2) yield a single
    body chunk named after their first body line.
    """
    lines = text.splitlines()
    headings = []  # (line_index, section_number, section_title)
    for i, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if match:
            headings.append((i, match.group(1), match.group(2)))

    chunks = []
    if headings:
        header_end = headings[0][0]
    else:
        # Header runs until the first blank line after the "Status:" line.
        header_end = 0
        seen_status = False
        for i, line in enumerate(lines):
            if "Status:" in line:
                seen_status = True
            elif seen_status and not line.strip():
                header_end = i
                break
        header_end = header_end or min(2, len(lines))

    header_lines = [ln.strip() for ln in lines[:header_end] if ln.strip()]
    if header_lines:
        chunks.append(("Header", "\n".join(header_lines)))

    if headings:
        for idx, (line_i, number, title) in enumerate(headings):
            stop = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
            body = "\n".join(lines[line_i:stop]).strip()
            chunks.append((f"Section {number}: {title}", body))
    else:
        body_lines = [ln.strip() for ln in lines[header_end:] if ln.strip()]
        if body_lines:
            chunks.append((body_lines[0][:60], "\n".join(body_lines)))
    return chunks


def document_chunks(doc_key, data_pack_dir):
    """Return chunk rows for one catalog entry, with a header sanity check."""
    meta = DOCUMENT_CATALOG[doc_key]
    text = extract_pdf_text(Path(data_pack_dir) / meta["file"])
    rows = []
    for section, chunk_body in chunk_text(text):
        rows.append(
            {
                "source_doc": doc_key,
                "section": section,
                "status": meta["status"],
                "effective": meta["effective"],
                "scope": meta["scope"],
                "authority_rank": meta["authority_rank"],
                "text": chunk_body,
            }
        )
    # The document must state its own status in its header (verified in Phase 0);
    # whitespace is normalised because layout extraction pads with double spaces.
    normalized = " ".join(text.split())
    if f"Status: {meta['status']}" not in normalized:
        raise AssertionError(
            f"{meta['file']} does not state expected status {meta['status']!r}"
        )
    return rows


def to_fts_query(query):
    """Turn arbitrary natural language into a safe FTS5 expression.

    Only word tokens survive, each individually double-quoted, so FTS
    operators (OR/NOT/NEAR), column filters, wildcards and quote escapes in
    user text can never change the query's meaning — and the result is still
    passed to SQLite as a bound parameter (never interpolated into SQL).
    """
    tokens = re.findall(r"\w+", str(query))
    if not tokens:
        raise ValueError(f"empty FTS query: {query!r}")
    return " ".join(f'"{token}"' for token in tokens)


def visible_to_account(chunk_row, account_id):
    """Account-scope visibility rule for retrieval (data-layer enforcement).

    GENERAL-scoped chunks are visible to everyone; account-scoped chunks
    (agreements) are visible ONLY to their own account. This is the Phase 1
    substrate for golden case 12 — a LumenWorks session must never be able to
    retrieve Northstar's agreement, regardless of query phrasing.
    """
    return chunk_row["scope"] == "GENERAL" or chunk_row["scope"] == account_id


def authoritative_chunks(chunk_rows, account_id):
    """Default-retrieval filter: authority ranks 1–3, visible to the account.

    Drops deprecated docs (rank 4) and historical resolutions (rank 5) — those
    are surfaced only on explicit request (03_AGENT_SPEC.md §5–6) and can
    never determine a policy outcome. This is the Layer A enforcement point
    for GI-1 and golden cases 10/11/12.
    """
    return [
        row
        for row in chunk_rows
        if row["authority_rank"] <= 3 and visible_to_account(row, account_id)
    ]


def historical_resolution_chunks(tickets):
    """Chunk historical resolutions as context-only evidence (rank 5).

    They live in the index so an explicit user request can surface them
    (golden cases 10/11), but their metadata makes them non-authoritative.
    """
    rows = []
    for ticket in tickets:
        resolution = ticket.get("historical_resolution")
        if not resolution:
            continue
        rows.append(
            {
                "source_doc": "tickets",
                "section": f"{ticket['ticket_id']}.historical_resolution",
                "status": "HISTORICAL_RESOLUTION",
                "effective": None,
                "scope": ticket["account_id"],
                "authority_rank": 5,
                "text": (
                    f"Historical resolution of {ticket['ticket_id']} "
                    f"({ticket['subject']}): {resolution}"
                ),
            }
        )
    return rows
