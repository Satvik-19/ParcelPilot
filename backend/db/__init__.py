"""Data layer: SQLite + FTS5 (ADR-001).

schema.sql defines the relational tables and the FTS5 index over document
chunks; workbook.py is the single deterministic interpretation of the
assessment workbook; documents.py chunks the six supplied PDFs with verified
metadata; database.py opens/initialises the database; seed.py populates it.
"""
