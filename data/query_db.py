"""
data/query_db.py
----------------
Connects to data/venue.db and prints a readable summary of every table:
row counts plus formatted rows.

Run from the project root:
    python data/query_db.py
"""

import sqlite3
import os

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "venue.db")

# Column widths for wrapping long text in output
WRAP_WIDTH = 60


def separator(char: str = "-", width: int = 80) -> str:
    return char * width


def wrap_text(text: str, width: int = WRAP_WIDTH) -> str:
    """Break long strings into multiple lines for readability."""
    if text is None:
        return "NULL"
    text = str(text)
    if len(text) <= width:
        return text
    lines = []
    while len(text) > width:
        lines.append(text[:width])
        text = text[width:]
    if text:
        lines.append(text)
    return ("\n" + " " * 4).join(lines)


def print_table(conn: sqlite3.Connection, table: str) -> None:
    """Print a formatted summary of a single table."""
    cur = conn.cursor()

    # Row count
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    count = cur.fetchone()[0]

    # Column names
    cur.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cur.fetchall()]

    print(separator("="))
    print(f"  TABLE: {table.upper()}  ({count} rows)")
    print(separator("="))

    if count == 0:
        print("  (empty)")
        print()
        return

    cur.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()

    for row in rows:
        print(separator("-"))
        for col, val in zip(columns, row):
            # Pretty-print booleans stored as integers
            if col == "per_person":
                display = "yes" if val else "no"
            elif val is None:
                display = "NULL"
            else:
                display = wrap_text(str(val))
            print(f"  {col:<22}: {display}")
    print(separator("-"))
    print()


# ---------------------------------------------------------------------------
# Join queries for human-readable cross-table views
# ---------------------------------------------------------------------------

def print_pricing_with_names(conn: sqlite3.Connection) -> None:
    """Print pricing joined with attraction names."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            a.name              AS attraction,
            a.category,
            COALESCE(CAST(p.duration_minutes AS TEXT) || ' min', 'per ride')
                                AS duration,
            '$' || p.price_aud  AS price_per_person
        FROM pricing p
        JOIN attractions a ON a.id = p.attraction_id
        ORDER BY a.id, p.duration_minutes
    """)
    rows = cur.fetchall()
    cols = ["attraction", "category", "duration", "price_per_person"]

    print(separator("="))
    print(f"  PRICING (with attraction names)  ({len(rows)} rows)")
    print(separator("="))
    # Header
    header = "  " + "  ".join(f"{c:<22}" for c in cols)
    print(header)
    print(separator("-"))
    for row in rows:
        print("  " + "  ".join(f"{str(v):<22}" for v in row))
    print()


def print_bookings_with_names(conn: sqlite3.Connection) -> None:
    """Print bookings joined with attraction names."""
    cur = conn.cursor()
    cur.execute("""
        SELECT
            b.id,
            b.customer_name,
            a.name              AS attraction,
            b.start_datetime,
            COALESCE(CAST(b.duration_minutes AS TEXT) || 'm', '-')
                                AS duration,
            b.party_size,
            b.status,
            '$' || b.total_price_aud AS total
        FROM bookings b
        JOIN attractions a ON a.id = b.attraction_id
        ORDER BY b.start_datetime
    """)
    rows = cur.fetchall()
    cols = ["id", "customer", "attraction", "start_datetime", "duration", "party", "status", "total"]

    print(separator("="))
    print(f"  BOOKINGS (with attraction names)  ({len(rows)} rows)")
    print(separator("="))
    header = "  " + "  ".join(f"{c:<18}" for c in cols)
    print(header)
    print(separator("-"))
    for row in rows:
        print("  " + "  ".join(f"{str(v):<18}" for v in row))
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Run `python data/init_db.py` first to create it.")
        return

    print(f"\nReading database: {DB_PATH}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # Raw table dumps
        print_table(conn, "brand")
        print_table(conn, "attractions")
        print_table(conn, "pricing")
        print_table(conn, "bookings")
        print_table(conn, "customer_interactions")
        print_table(conn, "scheduled_posts")
        print_table(conn, "instagram_messages")
        print_table(conn, "social_comments")
        print_table(conn, "approvals")

        # Joined / human-readable views
        print_pricing_with_names(conn)
        print_bookings_with_names(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
