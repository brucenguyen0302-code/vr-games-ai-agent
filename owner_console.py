import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sys
import shlex

PROJECT_ROOT = Path(__file__).parent.resolve()
DB_PATH = PROJECT_ROOT / "data" / "venue.db"

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def list_pending():
    print("\n--- PENDING APPROVALS ---")
    try:
        conn = _get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT a.id as approval_id, a.requested_at, p.id as post_id, p.platform, p.caption, p.image_path, p.scheduled_datetime
            FROM approvals a
            JOIN scheduled_posts p ON a.item_id = p.id
            WHERE a.status = 'pending' AND a.item_type = 'post'
            ORDER BY a.requested_at ASC
            """
        )
        rows = cur.fetchall()
        
        if not rows:
            print("No pending approvals found.")
            return

        for r in rows:
            print(f"\n[Approval ID: {r['approval_id']}] Requested: {r['requested_at']}")
            print(f"  Platform: {r['platform'].upper()} | Scheduled for: {r['scheduled_datetime']}")
            print(f"  Image:    {r['image_path']}")
            print(f"  Caption:  {r['caption']}")
    except Exception as e:
        print(f"Error fetching approvals: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def decide_approval(approval_id: int, decision: str, reason: str):
    try:
        conn = _get_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,))
        approval = cur.fetchone()
        
        if not approval:
            print(f"Error: Pending approval ID {approval_id} not found.")
            return
            
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None).isoformat()
        
        # Update approvals
        cur.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = 'owner', note = ? WHERE id = ?",
            (decision, now, reason, approval_id)
        )
        
        # Cascade update to scheduled_posts
        post_id = approval["item_id"]
        if decision == 'approved':
            cur.execute("UPDATE scheduled_posts SET status = 'approved' WHERE id = ?", (post_id,))
            print(f"Approval {approval_id} APPROVED. Post {post_id} is ready to be published.")
        else:
            cur.execute("UPDATE scheduled_posts SET status = 'rejected', rejection_reason = ? WHERE id = ?", (reason, post_id))
            print(f"Approval {approval_id} REJECTED. Post {post_id} status updated to rejected.")
            
        conn.commit()
    except Exception as e:
        print(f"Database error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def main():
    print("Welcome to the Innoviz Crown Owner Console")
    print("Commands:")
    print("  list                       - Show pending approvals")
    print("  approve <id> [note]        - Approve a post")
    print("  reject <id> <reason>       - Reject a post")
    print("  quit / exit                - Exit console")
    
    while True:
        try:
            cmd_line = input("\nowner> ").strip()
            if not cmd_line:
                continue
                
            parts = shlex.split(cmd_line)
            cmd = parts[0].lower()
            
            if cmd in ("quit", "exit"):
                print("Goodbye.")
                break
            elif cmd == "list":
                list_pending()
            elif cmd in ("approve", "reject"):
                if len(parts) < 2:
                    print(f"Usage: {cmd} <id> [{ 'note' if cmd == 'approve' else 'reason' }]")
                    continue
                try:
                    approval_id = int(parts[1])
                except ValueError:
                    print("Error: ID must be a number.")
                    continue
                
                reason = " ".join(parts[2:]) if len(parts) > 2 else ""
                
                if cmd == "reject" and not reason:
                    print("Error: A reason is required when rejecting.")
                    continue
                    
                decide_approval(approval_id, f"{cmd}d", reason) # 'approved' or 'rejected'
            else:
                print(f"Unknown command: {cmd}")
        except KeyboardInterrupt:
            print("\nGoodbye.")
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
