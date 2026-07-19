"""
mcp_server/server.py
--------------------
MCP server for the Innoviz Crown VR entertainment venue.

This server exposes venue business tools over the Model Context Protocol (MCP)
using stdio transport, allowing AI agents and MCP-compatible clients to query
live venue data — attractions, pricing, bookings, customer interactions, and
brand information — directly from the SQLite database.

Run standalone:
    python mcp_server/server.py

Or via the MCP CLI (from the project root):
    mcp run mcp_server/server.py
"""

import sqlite3
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP("innoviz-venue")

# Resolve the database path relative to the project root (one level above this
# file's directory), regardless of the working directory when the server runs.
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DB_PATH = PROJECT_ROOT / "data" / "venue.db"


def _get_connection() -> sqlite3.Connection:
    """Open a database connection with a dict-style row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def get_attractions() -> list[dict]:
    """
    Return all attractions at the venue together with their available pricing
    options.

    Each element in the returned list represents one attraction and contains:
      - id, name, category ('vr_ride' or 'vr_karaoke'), capacity, tagline,
        description
      - pricing: list of dicts, each with duration_minutes (None for rides),
        price_aud, and per_person (bool)

    Rides are priced per ride (duration_minutes is null); karaoke booths are
    priced per session length (15, 30, or 60 minutes).
    """
    conn = _get_connection()
    try:
        cur = conn.cursor()

        # Fetch all attractions
        cur.execute("SELECT * FROM attractions ORDER BY id")
        attractions = [dict(row) for row in cur.fetchall()]

        # Fetch all pricing rows
        cur.execute("SELECT * FROM pricing ORDER BY attraction_id, duration_minutes")
        all_pricing = [dict(row) for row in cur.fetchall()]

        # Group pricing by attraction_id
        pricing_by_att: dict[int, list[dict]] = {}
        for p in all_pricing:
            att_id = p["attraction_id"]
            pricing_by_att.setdefault(att_id, [])
            pricing_by_att[att_id].append({
                "duration_minutes": p["duration_minutes"],
                "price_aud": p["price_aud"],
                "per_person": bool(p["per_person"]),
            })

        # Embed pricing into each attraction dict
        for att in attractions:
            att["pricing"] = pricing_by_att.get(att["id"], [])

        return attractions

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Shared helpers (not exposed as tools)
# ---------------------------------------------------------------------------

# Venue opens Wed(2)–Sun(6); closed Mon(0) and Tue(1).
_OPEN_DAYS   = {2, 3, 4, 5, 6}   # weekday() values
_OPEN_HOUR   = 11                  # 11:00 local
_CLOSE_HOUR  = 21                  # 21:00 local (last session must start before this)

# Slot occupied by a ride booking (rides have no duration in the DB)
_RIDE_SLOT_MINUTES = 15


def _parse_iso(dt_str: str):
    """
    Parse an ISO 8601 datetime string and return a naive datetime in venue
    local time (AEST, UTC+10).  Returns None on parse failure.
    """
    from datetime import datetime, timezone, timedelta

    dt_str = dt_str.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(dt_str, fmt)
            if dt.tzinfo is not None:
                venue_tz = timezone(timedelta(hours=10))
                dt = dt.astimezone(venue_tz).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    return None


def _resolve_attraction(cur: sqlite3.Cursor, attraction_name: str) -> dict | None:
    """
    Case-insensitive substring match on attraction name.
    Exact match wins over partial match.
    """
    cur.execute("SELECT * FROM attractions ORDER BY id")
    rows = cur.fetchall()
    needle = attraction_name.strip().lower()
    for row in rows:
        if row["name"].lower() == needle:
            return dict(row)
    for row in rows:
        if needle in row["name"].lower():
            return dict(row)
    return None


def _all_attraction_names(cur: sqlite3.Cursor) -> list[str]:
    cur.execute("SELECT name FROM attractions ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _slot_duration(attraction: dict, duration_minutes: int | None) -> int:
    """Return the number of minutes a booking occupies on the schedule."""
    if attraction["category"] == "vr_karaoke":
        return duration_minutes or 0
    return _RIDE_SLOT_MINUTES


def _check_availability_inner(
    cur: sqlite3.Cursor,
    attraction: dict,
    start_dt,
    duration_minutes: int | None,
) -> dict:
    """
    Core availability logic used by both check_availability and create_booking.
    Returns {available, reason, suggestions?}.
    `start_dt` must be a naive datetime already in venue local time.
    """
    from datetime import datetime, timedelta

    slot_mins = _slot_duration(attraction, duration_minutes)
    end_dt    = start_dt + timedelta(minutes=slot_mins)

    # Opening day
    if start_dt.weekday() not in _OPEN_DAYS:
        return {
            "available": False,
            "reason": (
                f"Venue is closed on {start_dt.strftime('%A')}s. "
                "Open Wednesday–Sunday."
            ),
        }

    # Opening hours — start
    if start_dt.hour < _OPEN_HOUR:
        return {
            "available": False,
            "reason": (
                f"Start time {start_dt.strftime('%H:%M')} is before opening "
                f"({_OPEN_HOUR:02d}:00)."
            ),
        }
    if start_dt.hour >= _CLOSE_HOUR:
        return {
            "available": False,
            "reason": (
                f"Start time {start_dt.strftime('%H:%M')} is at or after closing "
                f"({_CLOSE_HOUR:02d}:00)."
            ),
        }

    # Opening hours — end
    if end_dt.hour > _CLOSE_HOUR or (end_dt.hour == _CLOSE_HOUR and end_dt.minute > 0):
        return {
            "available": False,
            "reason": (
                f"Session would end at {end_dt.strftime('%H:%M')}, after closing "
                f"({_CLOSE_HOUR:02d}:00)."
            ),
        }

    # Overlapping bookings
    cur.execute(
        """
        SELECT id, customer_name, start_datetime, duration_minutes
        FROM   bookings
        WHERE  attraction_id = ?
          AND  status != 'cancelled'
        """,
        (attraction["id"],),
    )
    existing = cur.fetchall()

    for row in existing:
        b_start = _parse_iso(row["start_datetime"])
        if b_start is None:
            continue
        b_dur = _slot_duration(attraction, row["duration_minutes"])
        b_end = b_start + timedelta(minutes=b_dur)

        if b_start < end_dt and b_end > start_dt:   # overlap detected
            # Suggest up to 3 alternative same-day slots
            suggestions = []
            candidate = datetime(
                start_dt.year, start_dt.month, start_dt.day,
                _OPEN_HOUR, 0,
            )
            while len(suggestions) < 3:
                if candidate.hour >= _CLOSE_HOUR:
                    break
                cand_end = candidate + timedelta(minutes=slot_mins)
                if cand_end.hour > _CLOSE_HOUR or (
                    cand_end.hour == _CLOSE_HOUR and cand_end.minute > 0
                ):
                    break
                overlap = False
                for chk in existing:
                    chk_start = _parse_iso(chk["start_datetime"])
                    if chk_start is None:
                        continue
                    chk_end = chk_start + timedelta(
                        minutes=_slot_duration(attraction, chk["duration_minutes"])
                    )
                    if chk_start < cand_end and chk_end > candidate:
                        overlap = True
                        break
                if not overlap and candidate != start_dt:
                    suggestions.append(candidate.strftime("%H:%M"))
                candidate += timedelta(minutes=slot_mins)

            result: dict = {
                "available": False,
                "reason": (
                    f"{attraction['name']} is already booked at that time "
                    f"(booking #{row['id']} for {row['customer_name']})."
                ),
            }
            if suggestions:
                result["alternative_times_same_day"] = suggestions
            return result

    return {"available": True, "reason": "Time slot is available."}


# ---------------------------------------------------------------------------

@mcp.tool()
def get_pricing(attraction_name: str | None = None) -> list[dict] | dict:
    """
    Return pricing information joined with attraction names.

    Parameters
    ----------
    attraction_name : str or None
        - None (default): returns all pricing rows for every attraction.
        - Provided: case-insensitive / partial match, e.g. "slide" matches
          "VR Slide", "vybox large" matches "VYBOX Large Booth".
          If no match is found, returns an error dict with valid_names listed.

    Each returned dict contains:
        attraction_id, attraction_name, category,
        duration_minutes (None for per-ride; 15/30/60 for karaoke),
        price_aud, per_person (bool).
    """
    conn = _get_connection()
    try:
        cur = conn.cursor()

        if attraction_name is None:
            cur.execute(
                """
                SELECT p.id, a.id AS attraction_id, a.name AS attraction_name,
                       a.category, p.duration_minutes, p.price_aud, p.per_person
                FROM   pricing p
                JOIN   attractions a ON a.id = p.attraction_id
                ORDER  BY a.id, p.duration_minutes
                """
            )
        else:
            att = _resolve_attraction(cur, attraction_name)
            if att is None:
                return {
                    "error": f"No attraction found matching '{attraction_name}'.",
                    "valid_names": _all_attraction_names(cur),
                }
            cur.execute(
                """
                SELECT p.id, a.id AS attraction_id, a.name AS attraction_name,
                       a.category, p.duration_minutes, p.price_aud, p.per_person
                FROM   pricing p
                JOIN   attractions a ON a.id = p.attraction_id
                WHERE  p.attraction_id = ?
                ORDER  BY p.duration_minutes
                """,
                (att["id"],),
            )

        result = []
        for r in cur.fetchall():
            d = dict(r)
            d["per_person"] = bool(d["per_person"])
            result.append(d)
        return result

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


@mcp.tool()
def check_availability(
    attraction_name: str,
    start_datetime: str,
    duration_minutes: int | None = None,
) -> dict:
    """
    Check whether a specific attraction is available at a given date and time.

    Parameters
    ----------
    attraction_name : str
        Partial or full attraction name (case-insensitive), e.g. "VR Slide",
        "paraglider", "vybox large".
    start_datetime : str
        Proposed start time in ISO 8601 format, e.g. "2026-07-25T14:00:00+10:00"
        or "2026-07-25T14:00". Offset-aware strings are converted to venue
        local time (AEST, UTC+10) before checking.
    duration_minutes : int or None
        Required for karaoke bookings (must be 15, 30, or 60). Ignored for
        rides (which always occupy a 15-minute slot on the schedule).

    Returns
    -------
    dict with:
        available (bool)
        reason (str)
        alternative_times_same_day (list[str], optional) — up to 3 "HH:MM"
            suggestions on the same day, only present when unavailable due to
            an overlapping booking.

    Checks performed (in order):
        1. Attraction name resolves to a known attraction.
        2. Day of week is Wed–Sun (closed Mon–Tue).
        3. Start time >= 11:00 and < 21:00.
        4. Session end time <= 21:00.
        5. No overlapping non-cancelled booking on that attraction.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()

            att = _resolve_attraction(cur, attraction_name)
            if att is None:
                return {
                    "available": False,
                    "reason": f"Unknown attraction '{attraction_name}'.",
                    "valid_names": _all_attraction_names(cur),
                }

            start_dt = _parse_iso(start_datetime)
            if start_dt is None:
                return {
                    "available": False,
                    "reason": (
                        f"Could not parse '{start_datetime}' as an ISO 8601 datetime. "
                        "Expected format: 'YYYY-MM-DDTHH:MM' or "
                        "'YYYY-MM-DDTHH:MM:SS+HH:MM'."
                    ),
                }

            if att["category"] == "vr_karaoke" and duration_minutes is None:
                return {
                    "available": False,
                    "reason": (
                        "duration_minutes is required for karaoke bookings "
                        "(choose 15, 30, or 60)."
                    ),
                }

            return _check_availability_inner(cur, att, start_dt, duration_minutes)

        finally:
            conn.close()

    except Exception as e:
        return {"available": False, "reason": f"Internal error: {e}"}


@mcp.tool()
def create_booking(
    customer_name: str,
    contact: str,
    attraction_name: str,
    start_datetime: str,
    party_size: int,
    duration_minutes: int | None = None,
) -> dict:
    """
    Create a new booking and insert it into the database with status 'pending'.

    Parameters
    ----------
    customer_name : str
        Full name of the customer making the booking.
    contact : str
        Email address, phone number, or Instagram handle.
    attraction_name : str
        Partial or full attraction name (case-insensitive), e.g. "car racing",
        "VYBOX Small", "360 flight".
    start_datetime : str
        Desired start time in ISO 8601 format, e.g. "2026-07-25T14:00:00+10:00".
        Offset-aware strings are converted to venue local time (AEST, UTC+10).
    party_size : int
        Number of people. Must be >= 1 and <= the attraction's capacity.
    duration_minutes : int or None
        Required for karaoke bookings; must be 15, 30, or 60. Leave None for
        rides (slot size is always 15 minutes for scheduling purposes).

    Validations
    -----------
    - Attraction must exist.
    - Karaoke: duration_minutes must be 15, 30, or 60.
    - party_size >= 1 and <= attraction capacity.
    - Time slot must be available (passes check_availability logic).
    - Price is always read from the pricing table — never invented.

    Returns
    -------
    On success: full booking record dict including id, attraction_name,
        total_price_aud (= price_per_person × party_size), status ('pending'),
        and all input fields.
    On failure: {"error": str, ...} with an explanatory message. May include
        alternative_times_same_day if the slot is already taken.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()

            # Resolve attraction
            att = _resolve_attraction(cur, attraction_name)
            if att is None:
                return {
                    "error": f"Unknown attraction '{attraction_name}'.",
                    "valid_names": _all_attraction_names(cur),
                }

            # Karaoke duration validation
            if att["category"] == "vr_karaoke":
                if duration_minutes not in (15, 30, 60):
                    return {
                        "error": (
                            "Karaoke bookings require duration_minutes of 15, 30, or 60. "
                            f"Got: {duration_minutes!r}."
                        )
                    }
            else:
                duration_minutes = None  # rides don't use duration

            # Party size validation
            if party_size < 1:
                return {"error": "party_size must be at least 1."}
            if party_size > att["capacity"]:
                return {
                    "error": (
                        f"{att['name']} has a maximum capacity of {att['capacity']}. "
                        f"Requested party size: {party_size}."
                    )
                }

            # Parse datetime
            start_dt = _parse_iso(start_datetime)
            if start_dt is None:
                return {
                    "error": (
                        f"Could not parse '{start_datetime}' as an ISO 8601 datetime. "
                        "Expected format: 'YYYY-MM-DDTHH:MM' or "
                        "'YYYY-MM-DDTHH:MM:SS+HH:MM'."
                    )
                }

            # Availability check (reuses shared logic)
            avail = _check_availability_inner(cur, att, start_dt, duration_minutes)
            if not avail["available"]:
                return {
                    "error": avail["reason"],
                    **{k: v for k, v in avail.items() if k not in ("available", "reason")},
                }

            # Look up price from the pricing table — never invent
            cur.execute(
                """
                SELECT price_aud FROM pricing
                WHERE  attraction_id = ?
                  AND  (duration_minutes IS ? OR duration_minutes = ?)
                """,
                (att["id"], duration_minutes, duration_minutes),
            )
            price_row = cur.fetchone()
            if price_row is None:
                return {
                    "error": (
                        f"No pricing found for {att['name']} with "
                        f"duration_minutes={duration_minutes}."
                    )
                }
            total_price = round(price_row["price_aud"] * party_size, 2)

            # Insert
            cur.execute(
                """
                INSERT INTO bookings
                    (customer_name, contact, attraction_id, start_datetime,
                     duration_minutes, party_size, status, total_price_aud)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    customer_name, contact, att["id"],
                    start_dt.isoformat(),
                    duration_minutes, party_size,
                    total_price,
                ),
            )
            conn.commit()

            return {
                "id": cur.lastrowid,
                "customer_name": customer_name,
                "contact": contact,
                "attraction_id": att["id"],
                "attraction_name": att["name"],
                "start_datetime": start_dt.isoformat(),
                "duration_minutes": duration_minutes,
                "party_size": party_size,
                "status": "pending",
                "total_price_aud": total_price,
            }

        finally:
            conn.close()

    except Exception as e:
        return {"error": f"Internal error: {e}"}


@mcp.tool()
def get_upcoming_bookings(days_ahead: int = 7) -> list[dict] | dict:
    """
    Return all non-cancelled bookings from now until now + days_ahead days,
    joined with attraction names, ordered by start time ascending.

    Parameters
    ----------
    days_ahead : int
        How many days into the future to look (default 7). Use a larger value
        (e.g. 30) for a broader schedule view.

    Returns
    -------
    List of booking dicts, each containing:
        id, customer_name, contact, attraction_id, attraction_name,
        start_datetime (ISO 8601 naive local), duration_minutes (None for
        rides), party_size, status ('confirmed' or 'pending'), total_price_aud.

    Returns an empty list if no upcoming non-cancelled bookings exist in the
    given window.
    """
    try:
        from datetime import datetime, timedelta, timezone

        venue_tz = timezone(timedelta(hours=10))
        now     = datetime.now(tz=venue_tz).replace(tzinfo=None)
        cutoff  = now + timedelta(days=days_ahead)

        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT b.id, b.customer_name, b.contact,
                       b.attraction_id, a.name AS attraction_name,
                       b.start_datetime, b.duration_minutes,
                       b.party_size, b.status, b.total_price_aud
                FROM   bookings b
                JOIN   attractions a ON a.id = b.attraction_id
                WHERE  b.status != 'cancelled'
                  AND  b.start_datetime >= ?
                  AND  b.start_datetime <  ?
                ORDER  BY b.start_datetime ASC
                """,
                (now.isoformat(), cutoff.isoformat()),
            )
            return [dict(row) for row in cur.fetchall()]

        finally:
            conn.close()

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def log_customer_interaction(
    channel: str,
    summary: str,
    outcome: str,
    customer_handle: str | None = None,
) -> dict:
    """
    Record a customer interaction in the database and return the saved row.

    Call this tool whenever the agent responds to a customer enquiry, or when
    a staff member needs to log a walk-in encounter, so that all touchpoints
    are captured for future reference and follow-up.

    Parameters
    ----------
    channel : str
        Where the interaction occurred. Must be exactly one of:
            'instagram_dm'       — a direct message on Instagram
            'instagram_comment'  — a public comment on an Instagram post or reel
            'walk_in'            — in-person at the venue front desk
    summary : str
        A concise description of what the customer asked or said.
    outcome : str
        What response was given or what action was taken. Be specific enough
        that a future agent or staff member can understand what happened
        without additional context.
    customer_handle : str or None
        Instagram handle (e.g. '@jessroams') or any customer identifier.
        Pass None for anonymous walk-in encounters.

    Returns
    -------
    On success: the full interaction record as a dict, including:
        id, timestamp (ISO 8601 with AEST offset), channel,
        customer_handle, summary, outcome.
    On failure: {"error": str} with an explanatory message.
    """
    _VALID_CHANNELS = {"instagram_dm", "instagram_comment", "walk_in"}

    try:
        if channel not in _VALID_CHANNELS:
            return {
                "error": (
                    f"Invalid channel '{channel}'. "
                    f"Must be one of: {sorted(_VALID_CHANNELS)}."
                )
            }

        from datetime import datetime, timedelta, timezone

        venue_tz = timezone(timedelta(hours=10))
        now_str = datetime.now(tz=venue_tz).isoformat()

        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO customer_interactions
                    (timestamp, channel, customer_handle, summary, outcome)
                VALUES (?, ?, ?, ?, ?)
                """,
                (now_str, channel, customer_handle, summary, outcome),
            )
            conn.commit()

            return {
                "id": cur.lastrowid,
                "timestamp": now_str,
                "channel": channel,
                "customer_handle": customer_handle,
                "summary": summary,
                "outcome": outcome,
            }

        finally:
            conn.close()

    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
