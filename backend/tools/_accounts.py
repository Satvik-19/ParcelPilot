"""Account reference canonicalization (trusted layer only).

Users refer to accounts by display name ("LumenWorks", "lumenworks") while
every authorization decision works on canonical ids ("ACCT-002"). Resolving
the reference HERE — before the scope check, never instead of it — means a
customer asking about their own account by name is no longer mis-denied,
while a cross-account reference still resolves to the other account's
canonical id and fails the unchanged scope check with the same neutral
denial. Session identity stays authoritative: nothing in chat text can
change which account a session IS, only which account it ASKS ABOUT.
"""


def canonical_account_id(conn, reference):
    """Resolve an account reference to its canonical account_id.

    Accepts the canonical id itself or the account's display name
    (case-insensitive, exact name match). Returns None when nothing
    matches. Performs NO authorization — callers must still enforce scope
    on the returned id.
    """
    if not reference or not isinstance(reference, str):
        return None
    ref = reference.strip()
    if not ref:
        return None
    row = conn.execute(
        "SELECT account_id FROM accounts WHERE account_id = ?", (ref,)
    ).fetchone()
    if row is not None:
        return row["account_id"]
    rows = conn.execute(
        "SELECT account_id FROM accounts"
        " WHERE lower(account_name) = lower(?)"
        " ORDER BY account_id",
        (ref,),
    ).fetchall()
    return rows[0]["account_id"] if rows else None
