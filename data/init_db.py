"""
data/init_db.py
---------------
Creates (or recreates) the Innoviz Crown SQLite database at data/venue.db.
Idempotent: drops and recreates all tables on every run.
Uses only the Python standard library (sqlite3).

Run from the project root:
    python data/init_db.py
"""

import sqlite3
import os
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "venue.db")

# Reference timestamp: 2026-07-17 14:14:58 AEST (UTC+10)
NOW = datetime(2026, 7, 17, 14, 14, 58, tzinfo=timezone(timedelta(hours=10)))


def dt(days: int, hour: int, minute: int = 0) -> str:
    """Return an ISO 8601 datetime string offset from NOW by the given days."""
    d = NOW + timedelta(days=days)
    return d.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
DROP TABLE IF EXISTS customer_interactions;
DROP TABLE IF EXISTS scheduled_posts;
DROP TABLE IF EXISTS bookings;
DROP TABLE IF EXISTS pricing;
DROP TABLE IF EXISTS attractions;
DROP TABLE IF EXISTS brand;

CREATE TABLE attractions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    category    TEXT    NOT NULL CHECK(category IN ('vr_ride', 'vr_karaoke')),
    capacity    INTEGER NOT NULL,
    tagline     TEXT,
    description TEXT
);

CREATE TABLE pricing (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    attraction_id    INTEGER NOT NULL REFERENCES attractions(id),
    duration_minutes INTEGER,          -- NULL for per-ride pricing
    price_aud        REAL    NOT NULL,
    per_person       INTEGER NOT NULL DEFAULT 1  -- boolean: 1 = true
);

CREATE TABLE bookings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name    TEXT    NOT NULL,
    contact          TEXT,
    attraction_id    INTEGER NOT NULL REFERENCES attractions(id),
    start_datetime   TEXT    NOT NULL,  -- ISO 8601
    duration_minutes INTEGER,           -- NULL for rides; session length for karaoke
    party_size       INTEGER NOT NULL DEFAULT 1,
    status           TEXT    NOT NULL CHECK(status IN ('confirmed','pending','cancelled')),
    total_price_aud  REAL    NOT NULL
);

CREATE TABLE customer_interactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,  -- ISO 8601
    channel         TEXT    NOT NULL CHECK(channel IN ('instagram_dm','instagram_comment','walk_in')),
    customer_handle TEXT,
    summary         TEXT,
    outcome         TEXT
);

CREATE TABLE brand (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE scheduled_posts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT    NOT NULL CHECK(platform IN ('instagram', 'tiktok')),
    caption            TEXT    NOT NULL,
    image_path         TEXT    NOT NULL,
    scheduled_datetime TEXT    NOT NULL,  -- ISO 8601
    status             TEXT    NOT NULL CHECK(status IN ('pending_approval', 'approved', 'published', 'cancelled', 'rejected')),
    created_at         TEXT    NOT NULL,  -- ISO 8601
    published_at       TEXT,              -- ISO 8601 (NULL until published)
    rejection_reason   TEXT               -- NULL unless rejected
);
"""

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

ATTRACTIONS = [
    # (name, category, capacity, tagline, description)
    (
        "VR Slide", "vr_ride", 1,
        "Sit. Slide. Survive.",
        "UFO-style motion seat with 360 VR that twists, turns and drops through wild virtual worlds. "
        "A 2-5 minute experience packed with thrills -- fun for all ages."
    ),
    (
        "VR Machine Gun", "vr_ride", 1,
        None,
        "Realistic mounted machine-gun shooter that puts you right in the action -- it feels like "
        "firing in real life. Action-packed and great for thrill-seekers."
    ),
    (
        "Car Racing", "vr_ride", 1,
        "Feel the speed. Live the thrill.",
        "Full racing simulator with realistic cars and tracks across multiple modes. "
        "Easy to pick up, hard to master -- perfect for competitive groups and solo speed demons."
    ),
    (
        "360 Flight", "vr_ride", 2,
        "Spin. Soar. Dive. Feel everything.",
        "Full-motion flight simulator that soars over epic landscapes with immersive surround sound. "
        "Seats two side-by-side for a truly shared adventure."
    ),
    (
        "Paraglider", "vr_ride", 2,
        None,
        "Experience the sensation of real paragliding -- floating through open skies with "
        "stomach-dropping climbs, dives and sweeping turns that feel completely authentic."
    ),
    (
        "VYBOX Small Booth", "vr_karaoke", 4,
        "Let's get loud!",
        "Private soundproof karaoke booth with thousands of songs, hi-fi sound, smart sync lighting "
        "and the ability to record and share your performance. Perfect for small groups."
    ),
    (
        "VYBOX Large Booth", "vr_karaoke", 8,
        "Let's get loud!",
        "Private soundproof karaoke booth with thousands of songs, hi-fi sound, smart sync lighting "
        "and the ability to record and share your performance. Roomier layout perfect for larger groups "
        "and celebrations."
    ),
]

# Attraction name -> 1-based ID (matches insertion order)
ATT_ID = {name: idx + 1 for idx, (name, *_) in enumerate(ATTRACTIONS)}


PRICING = [
    # (attraction_name, duration_minutes, price_aud)
    # Rides -- priced per ride (duration NULL)
    ("VR Slide",          None,  10.0),
    ("VR Machine Gun",    None,  10.0),
    ("Car Racing",        None,  15.0),
    ("360 Flight",        None,  25.0),
    ("Paraglider",        None,  25.0),
    # VYBOX Small Booth
    ("VYBOX Small Booth",   15,  10.0),
    ("VYBOX Small Booth",   30,  15.0),
    ("VYBOX Small Booth",   60,  25.0),
    # VYBOX Large Booth
    ("VYBOX Large Booth",   15,  12.0),
    ("VYBOX Large Booth",   30,  18.0),
    ("VYBOX Large Booth",   60,  30.0),
]

# (attraction_id, duration_minutes) -> price_aud
PRICE_LOOKUP = {
    (ATT_ID[name], dur): price for (name, dur, price) in PRICING
}


def booking_price(attraction_name: str, party_size: int,
                  duration_minutes=None) -> float:
    """Compute total_price_aud = price_per_person x party_size."""
    att_id = ATT_ID[attraction_name]
    price = PRICE_LOOKUP[(att_id, duration_minutes)]
    return round(price * party_size, 2)


BOOKINGS = [
    # (customer_name, contact, attraction_name, duration_minutes,
    #  start_datetime, party_size, status)
    ("Liam Chen",       "liam.chen@gmail.com",      "Car Racing",        None, dt(1,  10, 30), 1, "confirmed"),
    ("Sofia Nguyen",    "@sofiang_au",               "VR Slide",          None, dt(1,  11,  0), 2, "confirmed"),
    ("Marcus Webb",     "marcus.w@outlook.com",      "VR Machine Gun",    None, dt(2,  13, 15), 1, "confirmed"),
    ("Priya Sharma",    "@priya.adventures",         "360 Flight",        None, dt(3,  15,  0), 2, "confirmed"),
    ("Jake & Emma T.",  "jake.emma.t@icloud.com",    "VYBOX Small Booth",   30, dt(4,  18,  0), 3, "confirmed"),
    ("Riley Patel",     "riley_patel@hotmail.com",   "Paraglider",        None, dt(5,  14, 45), 2, "confirmed"),
    ("Zoe Martin",      "@zoemartin_bne",            "VYBOX Large Booth",   60, dt(7,  19,  0), 7, "confirmed"),
    ("Tom & Sarah K.",  "tksydney@gmail.com",        "360 Flight",        None, dt(8,  12,  0), 2, "pending"),
    ("Aiden Brooks",    "aiden.brooks@live.com",     "VYBOX Small Booth",   15, dt(10, 17, 30), 4, "pending"),
    ("Hannah Liu",      "@hannahl_travels",          "Car Racing",        None, dt(12, 11,  0), 1, "cancelled"),
]


CUSTOMER_INTERACTIONS = [
    # (timestamp, channel, customer_handle, summary, outcome)
    (
        (NOW - timedelta(days=6)).isoformat(),
        "instagram_dm", "@jessroams",
        "Asked for pricing on the Car Racing simulator and whether there are group discounts.",
        "Replied with full pricing ($15/person per ride). Advised no group discount currently but "
        "suggested VYBOX booths for group value. Customer thanked us and said they'd visit this weekend."
    ),
    (
        (NOW - timedelta(days=5)).isoformat(),
        "instagram_dm", "@_birthday_boss_",
        "Enquired about booking VYBOX Large Booth for a 21st birthday party of 6 people on a Saturday night.",
        "Provided 60-minute pricing ($30/person), confirmed availability, and sent booking link. "
        "Customer proceeded to book -- confirmed reservation created."
    ),
    (
        (NOW - timedelta(days=4)).isoformat(),
        "instagram_comment", "@tourist_tim_au",
        "Comment on a reel asking what the opening hours are and whether they need to book in advance.",
        "Replied publicly: open Wed-Sun 11am-9pm; walk-ins welcome but booking recommended on weekends. "
        "Directed to website for the booking form."
    ),
    (
        (NOW - timedelta(days=3)).isoformat(),
        "walk_in", None,
        "Customer complained about a 25-minute wait for the VR Slide despite having a booking. "
        "Expressed frustration in person at the front desk.",
        "Staff apologised sincerely and offered a complimentary extra ride. Customer accepted, "
        "left satisfied, and said they would return."
    ),
    (
        (NOW - timedelta(days=2)).isoformat(),
        "instagram_comment", "@familyfunday_mel",
        "Left a glowing comment on a post: said it was the best outing they had in months, "
        "kids absolutely loved the VR Slide and Paraglider, will 100% be back.",
        "Liked and replied with a warm thank-you. Reposted the comment to Stories for social proof."
    ),
    (
        (NOW - timedelta(days=1)).isoformat(),
        "instagram_dm", "@dad_of_three",
        "Asked whether kids under 10 are allowed on the VR rides and if there is a minimum height requirement.",
        "Advised that most rides are suitable for children 6+ (supervised) with no strict height "
        "requirement, but parents should use discretion for motion-sensitive kids. VR Slide and "
        "Paraglider are most family-friendly. Customer was reassured and said they'd bring the family next week."
    ),
]


BRAND = [
    ("venue_name",       "Innoviz Crown"),
    ("instagram_handle", "@thrillmates"),
    ("website",          "innovizcrown.com.au"),
    ("karaoke_brand",    "VYBOX"),
    ("visual_style",     "neon arcade, retro-pixel, vibrant purple/pink/blue on dark backgrounds"),
    ("taglines",         "Feel the speed. Live the thrill. | Spin. Soar. Dive. Feel everything. | Let's get loud!"),
    ("audience",         "families, friend groups, couples, tourists -- adults and children"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Initialising database at: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")

    try:
        # ---- Schema (drop + recreate) ----
        conn.executescript(SCHEMA_SQL)
        print("  + Schema created (all tables dropped and recreated).")

        cur = conn.cursor()

        # ---- Attractions ----
        cur.executemany(
            "INSERT INTO attractions (name, category, capacity, tagline, description) "
            "VALUES (?, ?, ?, ?, ?)",
            ATTRACTIONS,
        )
        print(f"  + Inserted {len(ATTRACTIONS)} attractions.")

        # ---- Pricing ----
        pricing_rows = [
            (ATT_ID[name], dur, price, 1)
            for (name, dur, price) in PRICING
        ]
        cur.executemany(
            "INSERT INTO pricing (attraction_id, duration_minutes, price_aud, per_person) "
            "VALUES (?, ?, ?, ?)",
            pricing_rows,
        )
        print(f"  + Inserted {len(pricing_rows)} pricing rows.")

        # ---- Bookings ----
        booking_rows = [
            (
                cname, contact,
                ATT_ID[att_name],
                start_dt, dur, party,
                status,
                booking_price(att_name, party, dur),
            )
            for (cname, contact, att_name, dur, start_dt, party, status) in BOOKINGS
        ]
        cur.executemany(
            "INSERT INTO bookings "
            "(customer_name, contact, attraction_id, start_datetime, "
            " duration_minutes, party_size, status, total_price_aud) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            booking_rows,
        )
        print(f"  + Inserted {len(booking_rows)} bookings.")

        # ---- Customer interactions ----
        interaction_rows = [
            (ts, channel, handle, summary, outcome)
            for (ts, channel, handle, summary, outcome) in CUSTOMER_INTERACTIONS
        ]
        cur.executemany(
            "INSERT INTO customer_interactions "
            "(timestamp, channel, customer_handle, summary, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            interaction_rows,
        )
        print(f"  + Inserted {len(interaction_rows)} customer interactions.")

        # ---- Brand ----
        cur.executemany(
            "INSERT INTO brand (key, value) VALUES (?, ?)",
            BRAND,
        )
        print(f"  + Inserted {len(BRAND)} brand rows.")

        conn.commit()
        print("\nDatabase initialised successfully.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
