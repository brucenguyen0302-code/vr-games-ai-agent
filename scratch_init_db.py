import re

db_path = '/Users/brucenguyen/vr_games_ai_agent/data/init_db.py'
with open(db_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Update customer_interactions channel CHECK
content = content.replace(
    "CHECK(channel IN ('instagram_dm','instagram_comment','walk_in'))",
    "CHECK(channel IN ('instagram_dm','instagram_comment','walk_in','owner_flag'))"
)

# Add DROPs
drops = """DROP TABLE IF EXISTS customer_interactions;
DROP TABLE IF EXISTS scheduled_posts;
DROP TABLE IF EXISTS instagram_messages;
DROP TABLE IF EXISTS social_comments;
DROP TABLE IF EXISTS approvals;"""
content = content.replace("DROP TABLE IF EXISTS customer_interactions;\nDROP TABLE IF EXISTS scheduled_posts;", drops)

# Add CREATEs
creates = """
CREATE TABLE instagram_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    direction      TEXT    NOT NULL CHECK(direction IN ('inbound', 'outbound')),
    thread_id      TEXT    NOT NULL,
    sender_handle  TEXT    NOT NULL,
    message_text   TEXT    NOT NULL,
    timestamp      TEXT    NOT NULL,  -- ISO 8601
    replied        INTEGER NOT NULL DEFAULT 0  -- boolean: 1 = true
);

CREATE TABLE social_comments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    platform         TEXT    NOT NULL CHECK(platform IN ('instagram', 'tiktok')),
    post_ref         TEXT    NOT NULL,
    commenter_handle TEXT    NOT NULL,
    comment_text     TEXT    NOT NULL,
    timestamp        TEXT    NOT NULL  -- ISO 8601
);

CREATE TABLE approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type    TEXT    NOT NULL CHECK(item_type = 'post'),
    item_id      INTEGER NOT NULL,
    status       TEXT    NOT NULL CHECK(status IN ('pending', 'approved', 'rejected')),
    requested_at TEXT    NOT NULL,  -- ISO 8601
    decided_at   TEXT,              -- ISO 8601
    decided_by   TEXT,
    note         TEXT
);
"""
content = content.replace("CREATE TABLE scheduled_posts (", creates + "\nCREATE TABLE scheduled_posts (")

# Add SEED data variables
seeds = """
# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

INSTAGRAM_MESSAGES = [
    ("inbound", "thread_1", "@user1", "Hey! How much is a ticket for the 360 Flight ride for 2 people?", "2026-07-20T10:00:00Z", 0),
    ("inbound", "thread_2", "@party_planner", "I'm looking to book the VYBOX Large booth for my birthday next week. Do you allow outside cake?", "2026-07-20T11:30:00Z", 0),
    ("inbound", "thread_3", "@late_night", "Are you guys open on Mondays?", "2026-07-20T14:15:00Z", 0),
    ("inbound", "thread_4", "@tourist_guy", "Do we need to book online or do you take walk-ins?", "2026-07-20T16:00:00Z", 0),
    ("inbound", "thread_5", "@angry_cust", "I had a booking for Car Racing but it was cancelled with NO EXPLANATION. I want a refund NOW.", "2026-07-20T18:45:00Z", 0),
]

SOCIAL_COMMENTS = [
    # Instagram (4)
    ("instagram", "post_101", "@vr_fan", "This looks insane! Definitely coming this weekend.", "2026-07-19T09:00:00Z"),
    ("instagram", "post_101", "@curious_mom", "Is there an age limit for the slides?", "2026-07-19T10:30:00Z"),
    ("instagram", "post_102", "@singer_wannabe", "VYBOX is the best karaoke setup in Brisbane hands down.", "2026-07-19T20:15:00Z"),
    ("instagram", "post_102", "@random_guy", "Do you guys serve alcohol?", "2026-07-19T21:00:00Z"),
    
    # TikTok (3)
    ("tiktok", "video_201", "@thrill_seeker", "Bro the drop on that VR ride is wild", "2026-07-18T18:00:00Z"),
    ("tiktok", "video_201", "@cheap_dates", "How much does this cost?", "2026-07-18T19:30:00Z"),
    ("tiktok", "video_202", "@aussie_gamer", "Innoviz always bringing the heat", "2026-07-18T22:10:00Z"),
]
"""
content = content.replace("# ---------------------------------------------------------------------------\n# Seed data\n# ---------------------------------------------------------------------------", seeds)

# Add Insert statements at the bottom of the script
inserts = """
        # Insert instagram messages
        cur.executemany(
            "INSERT INTO instagram_messages (direction, thread_id, sender_handle, message_text, timestamp, replied) VALUES (?, ?, ?, ?, ?, ?)",
            INSTAGRAM_MESSAGES
        )

        # Insert social comments
        cur.executemany(
            "INSERT INTO social_comments (platform, post_ref, commenter_handle, comment_text, timestamp) VALUES (?, ?, ?, ?, ?)",
            SOCIAL_COMMENTS
        )
"""
# find the line with "print(f"  + Inserted {len(BRAND)} brand rows.")" and insert right before it
if "print(f\"  + Inserted {len(BRAND)} brand rows.\")" in content:
    content = content.replace("print(f\"  + Inserted {len(BRAND)} brand rows.\")", inserts + "\n        print(f\"  + Inserted {len(BRAND)} brand rows.\")\n        print(f\"  + Inserted {len(INSTAGRAM_MESSAGES)} inbound DMs.\")\n        print(f\"  + Inserted {len(SOCIAL_COMMENTS)} social comments.\")")

with open(db_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("init_db updated")
