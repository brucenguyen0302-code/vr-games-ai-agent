import sys
import json
sys.path.insert(0, '.')

from mcp_server.server import get_optimal_posting_time, schedule_post, get_scheduled_posts, cancel_scheduled_post, publish_instagram_post, publish_tiktok_post

print("\n--- get_optimal_posting_time ---")
print(json.dumps(get_optimal_posting_time("instagram"), indent=2))
print(json.dumps(get_optimal_posting_time("tiktok"), indent=2))

print("\n--- schedule_post ---")
# Need an image to test with.
import pathlib
(pathlib.Path(".") / "generated").mkdir(exist_ok=True)
with open("generated/test.png", "wb") as f:
    f.write(b"fake image")

res = schedule_post("instagram", "Hey this is a cool ride at Innoviz Crown! @thrillmates #VR", "test.png", "2027-10-10T12:00:00Z")
print(json.dumps(res, indent=2))
if "error" not in res:
    post_id = res["id"]
else:
    post_id = None

print("\n--- get_scheduled_posts ---")
print(json.dumps(get_scheduled_posts(), indent=2))

if post_id is not None:
    print("\n--- publish_instagram_post (should fail - not approved) ---")
    print(json.dumps(publish_instagram_post(post_id), indent=2))
    
    print("\n--- manual approve ---")
    import sqlite3
    conn = sqlite3.connect("data/venue.db")
    conn.execute("UPDATE scheduled_posts SET status = 'approved' WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()

    print("\n--- publish_instagram_post (simulated) ---")
    print(json.dumps(publish_instagram_post(post_id), indent=2))
