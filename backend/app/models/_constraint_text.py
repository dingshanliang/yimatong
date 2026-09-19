"""Cross-dialect CHECK constraint text builders shared by model modules.

Models must render on both SQLite (unit-test ``create_all``) and PostgreSQL
(migration-parity checks). Constraint text here therefore stays portable by
construction — see ``contains_only`` for why nesting depth is a hard
constraint, not a style choice.
"""


def contains_only(column: str, allowed: str) -> str:
    """Build a PostgreSQL/SQLite-compatible exact character whitelist.

    trim()/btrim strips allowed characters from both ends, so the result is
    empty iff no other character occurs anywhere in the value. Must stay
    flat: the nested-replace formulation overflows older SQLite parser
    stacks (CI's SQLite 3.45 failed CREATE TABLE export_logs on a 67-deep
    chain) and is semantically identical here. Migration-authored PG
    constraints may use the idiomatic regex form instead; names and
    semantics match, the text is intentionally not byte-identical.
    """

    return f"trim({column}, '{allowed}') = ''"
