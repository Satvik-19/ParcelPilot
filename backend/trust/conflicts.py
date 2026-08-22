"""Deterministic conflict resolution (03_AGENT_SPEC.md §5).

Higher authority_rank wins; every losing record stays in the trace with
`overridden_by` set. Records with authority_rank None (deprecated/historical)
never decide and never participate as winners — they are context only.

"Same matter" is the policy topic the source document belongs to; two sources
conflict only when both speak to that topic (agreements overriding the
general SOP/Policy is the designed conflict shape in this dataset).
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


def resolve_conflicts(records):
    """Return records with overridden_by set where a higher rank prevails."""
    best = {}  # topic -> winning evidence_id
    for record in records:
        if record.authority_rank is None:
            continue  # context-only sources never win or lose a policy conflict
        topic = topic_of(record.source_doc)
        if topic is None:
            continue
        current = best.get(topic)
        if current is None:
            best[topic] = record
        elif record.authority_rank < current.authority_rank:
            best[topic] = record

    resolved = []
    for record in records:
        topic = topic_of(record.source_doc)
        winner = best.get(topic)
        if (
            record.authority_rank is not None
            and winner is not None
            and record.evidence_id != winner.evidence_id
            and record.authority_rank > winner.authority_rank
        ):
            # Only a strictly lower authority loses; equal-rank chunks of the
            # winning source stay unmarked.
            record = replace(record, overridden_by=winner.evidence_id)
        resolved.append(record)
    return resolved
