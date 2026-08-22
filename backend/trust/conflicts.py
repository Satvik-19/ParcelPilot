"""Deterministic conflict resolution (03_AGENT_SPEC.md §5).

Higher authority_rank wins; every losing record stays in the trace with
`overridden_by` set. Records with authority_rank None (deprecated/historical)
never decide and never participate as winners — they are context only.

Two refinements over "same topic wins":

1. Subtopic — only records about the SAME subtopic (cancellation, credits,
   SLA/severity, known-issues) compete. An agreement's cancellation section
   overrides the SOP's cancellation section, not the SOP's credit section.
   An agreement that merely CONFIRMS the default policy applies still wins
   by authority (the contract is the governing instrument); the model reads
   both records and reports what the contract actually states.

2. Headers never win — document Headers are metadata (name, dates, status),
   not policy content, so they never mark a substantive section as
   overridden. They stay in the trace without an overridden_by field, which
   is exactly what the UI evidence panel should show.
"""

from dataclasses import replace

_TOPIC_BY_DOC_PREFIX = (
    ("01_", "support_policy"),      # Support Policy v3 (SLA/severity)
    ("02_", "support_policy"),      # v2 (deprecated) — same topic, rank 4
    ("03_", "cancellation_credit"), # Cancellation & Service Credit SOP
    ("04_", "operations_known_issues"),
    ("05_", "cancellation_credit"), # Northstar agreement covers SLA + credits + cancellation
    ("06_", "cancellation_credit"), # LumenWorks agreement
)


def topic_of(source_doc):
    for prefix, topic in _TOPIC_BY_DOC_PREFIX:
        if source_doc.startswith(prefix):
            return topic
    return None


# Subtopic classifier — the section name (lower-cased) drives the label.
# Only records that share a subtopic compete in conflict resolution; Headers
# (empty label) never win and therefore never mark a substantive section as
# overridden by metadata.
_SUBTOPIC_KEYWORDS = (
    ("cancel",        "cancellation"),
    ("credit",        "credits"),
    ("known issue",   "known_issue"),
    ("severity",      "sla"),
    ("escalat",       "sla"),
    ("first-response","sla"),
    ("response target","sla"),
    ("sla",           "sla"),
    ("support term",  "support_terms"),
    ("scope and source","support_terms"),
    ("plan capabilit", "plan_capabilities"),
    ("account contact","account_contact"),
    ("resolved issue", "resolved_issue"),
    ("approval",       "approval"),
    ("uncertainty",    "approval"),
)


def _subtopic_of(section):
    """Return the subtopic label for a section name, or '' for Headers/metadata."""
    if not section or section == "Header":
        return ""
    s = section.lower()
    for keyword, subtopic in _SUBTOPIC_KEYWORDS:
        if keyword in s:
            return subtopic
    # Section 1/2/3 fallback: only reached for numbered sections whose
    # title carries no keyword (rare in this dataset; defensive default).
    return ""


def resolve_conflicts(records):
    """Return records with overridden_by set where a higher rank prevails.

    Two records compete when ALL of these hold:
      - both have a non-None authority_rank (context-only sources don't);
      - both belong to the same document-level topic;
      - both belong to the same section-level subtopic;
      - neither is a Header/metadata section (subtopic == "").
    """
    # (topic, subtopic) -> winning record — only for non-empty subtopics
    best = {}
    for record in records:
        if record.authority_rank is None:
            continue  # context-only sources never win or lose
        topic = topic_of(record.source_doc)
        if topic is None:
            continue
        subtopic = _subtopic_of(record.section)
        if not subtopic:
            continue  # Headers/metadata never win
        key = (topic, subtopic)
        current = best.get(key)
        if current is None or record.authority_rank < current.authority_rank:
            best[key] = record

    resolved = []
    for record in records:
        topic = topic_of(record.source_doc)
        subtopic = _subtopic_of(record.section)
        key = (topic, subtopic) if topic and subtopic else None
        winner = best.get(key) if key else None
        if (
            record.authority_rank is not None
            and winner is not None
            and record.evidence_id != winner.evidence_id
            and record.authority_rank > winner.authority_rank
        ):
            record = replace(record, overridden_by=winner.evidence_id)
        resolved.append(record)
    return resolved
