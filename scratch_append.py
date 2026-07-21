# ---------------------------------------------------------------------------
# Publishing / Scheduling tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_optimal_posting_time(platform: str, target_date: str | None = None) -> dict:
    """
    Get heuristic-based optimal posting times for a specific platform.
    Returns 2-3 recommended posting windows tailored for an entertainment
    venue targeting families and young groups in Australia.

    No external API calls are made.

    Parameters
    ----------
    platform : str
        'instagram' or 'tiktok'
    target_date : str, optional
        Target date to plan the post.

    Returns
    -------
    dict with recommendations and reasons.
    """
    if platform not in ("instagram", "tiktok"):
        return {"error": "platform must be 'instagram' or 'tiktok'"}
        
    if platform == "instagram":
        return {
            "platform": "instagram",
            "target_date": target_date,
            "recommendations": [
                {"window": "11:00-13:00", "reason": "High engagement during lunch breaks on weekdays."},
                {"window": "19:00-21:00", "reason": "Peak activity for families and friend groups planning weekend outings."},
                {"window": "10:00-12:00", "reason": "Best for weekend mornings before people head out."}
            ]
        }
    else:
        return {
            "platform": "tiktok",
            "target_date": target_date,
            "recommendations": [
                {"window": "18:00-22:00", "reason": "Highest TikTok consumption occurs during evening hours."}
            ]
        }


@mcp.tool()
def schedule_post(platform: str, caption: str, image_path: str, scheduled_datetime: str) -> dict:
    """
    Schedule a post for a specific platform.

    Validates that the image exists, the datetime is in the future, and runs
    the caption through the internal moderation logic. Only schedules the post
    if all checks pass.

    NOTE: Posts are never published directly. They are created with a
    'pending_approval' status and require owner approval before publishing.

    Parameters
    ----------
    platform : str
        'instagram' or 'tiktok'
    caption : str
        The caption text for the post.
    image_path : str
        Filename or relative path to the image in the 'generated/' folder.
    scheduled_datetime : str
        ISO 8601 datetime string.

    Returns
    -------
    The inserted row as a dict.
    """
    from datetime import datetime, timezone, timedelta

    if platform not in ("instagram", "tiktok"):
        return {"error": f"platform must be 'instagram' or 'tiktok', got '{platform}'"}
    
    # Validate ISO datetime in the future
    dt = _parse_iso(scheduled_datetime)
    if not dt:
        return {"error": "Invalid scheduled_datetime format. Must be ISO 8601."}
    
    # Local now for AEST
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
    if dt <= now:
        return {"error": "scheduled_datetime must be in the future."}

    # Verify image exists in generated/
    img_path = Path(image_path)
    if not img_path.is_absolute():
        img_path = PROJECT_ROOT / "generated" / image_path
    
    try:
        img_path.resolve().relative_to((PROJECT_ROOT / "generated").resolve())
    except ValueError:
        return {"error": "image_path must reside within the 'generated/' directory."}

    if not img_path.is_file():
        return {"error": f"Image file not found at {img_path}"}

    # Caption passes moderation checks
    mod_res = moderate_content(caption)
    if not mod_res.get("approved"):
        return {"error": "Caption failed moderation.", "issues": mod_res.get("issues")}

    # Insert
    now_str = now.isoformat()
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO scheduled_posts 
                (platform, caption, image_path, scheduled_datetime, status, created_at)
                VALUES (?, ?, ?, ?, 'pending_approval', ?)
                """,
                (platform, caption, str(img_path), dt.isoformat(), now_str)
            )
            post_id = cur.lastrowid
            conn.commit()
            
            cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
            return dict(cur.fetchone())
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_scheduled_posts(status: str | None = None) -> dict:
    """
    Retrieve scheduled posts, optionally filtered by status.
    
    Returns
    -------
    List of scheduled posts wrapped in a dict.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            if status:
                cur.execute("SELECT * FROM scheduled_posts WHERE status = ? ORDER BY scheduled_datetime ASC", (status,))
            else:
                cur.execute("SELECT * FROM scheduled_posts ORDER BY scheduled_datetime ASC")
            return {"posts": [dict(r) for r in cur.fetchall()]}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def cancel_scheduled_post(post_id: int, reason: str) -> dict:
    """
    Cancel a scheduled post.

    Only posts in 'pending_approval' or 'approved' statuses can be cancelled.
    Once published, a post cannot be cancelled through this tool.

    Parameters
    ----------
    post_id : int
        The ID of the scheduled post.
    reason : str
        The reason for cancellation.

    Returns
    -------
    The updated row as a dict.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,))
            row = cur.fetchone()
            if not row:
                return {"error": f"Post ID {post_id} not found."}
            if row["status"] not in ("pending_approval", "approved"):
                return {"error": f"Cannot cancel post with status '{row['status']}'."}
            
            cur.execute(
                "UPDATE scheduled_posts SET status = 'cancelled', rejection_reason = ? WHERE id = ?",
                (reason, post_id)
            )
            conn.commit()
            
            cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
            return dict(cur.fetchone())
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


def _publish_ig_real(post_id: int, row: dict) -> dict:
    """
    Real Instagram Graph API publish flow.
    """
    import urllib.request
    import json
    import urllib.parse
    ig_user = os.environ.get("IG_USER_ID")
    ig_token = os.environ.get("IG_ACCESS_TOKEN")
    
    # 1. Create Media Container
    url = f"https://graph.facebook.com/v19.0/{ig_user}/media"
    data = urllib.parse.urlencode({
        "image_url": "https://example.com/mock-image.png", # Usually requires a public URL
        "caption": row["caption"],
        "access_token": ig_token
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            container_id = res_body.get("id")
            
        # 2. Publish Container
        pub_url = f"https://graph.facebook.com/v19.0/{ig_user}/media_publish"
        pub_data = urllib.parse.urlencode({
            "creation_id": container_id,
            "access_token": ig_token
        }).encode("utf-8")
        req_pub = urllib.request.Request(pub_url, data=pub_data, method="POST")
        with urllib.request.urlopen(req_pub) as pub_resp:
            pub_res_body = json.loads(pub_resp.read().decode("utf-8"))
            
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
        
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE scheduled_posts SET status = 'published', published_at = ? WHERE id = ?",
                (now.isoformat(), post_id)
            )
            conn.commit()
        finally:
            conn.close()
            
        return {"simulated": False, "ig_response": pub_res_body, "status": "published"}
    except Exception as e:
        return {"error": f"IG API Error: {str(e)}"}


@mcp.tool()
def publish_instagram_post(post_id: int) -> dict:
    """
    THE GATE: Publish an Instagram post to the account.
    
    Refuses to publish unless the post's status is exactly 'approved'.
    If IG_USER_ID and IG_ACCESS_TOKEN are configured, calls the Graph API.
    Otherwise, runs in simulation mode and marks the post as published.
    
    Returns
    -------
    dict describing whether it was simulated or real, with any API notes.
    """
    from datetime import datetime, timezone, timedelta
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
            row = cur.fetchone()
            if not row:
                return {"error": f"Post ID {post_id} not found."}
            if row["platform"] != "instagram":
                return {"error": "Post is not for instagram."}
            
            if row["status"] != "approved":
                return {
                    "error": (
                        "Approval is required before publishing. "
                        f"Current status is '{row['status']}', but must be exactly 'approved'."
                    )
                }
            
            ig_user = os.environ.get("IG_USER_ID")
            ig_token = os.environ.get("IG_ACCESS_TOKEN")
            if ig_user and ig_token:
                return _publish_ig_real(post_id, dict(row))
            
            # Simulation mode
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
            cur.execute(
                "UPDATE scheduled_posts SET status = 'published', published_at = ? WHERE id = ?",
                (now.isoformat(), post_id)
            )
            conn.commit()
            return {"simulated": True, "note": "IG credentials not configured", "status": "published"}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def publish_tiktok_post(post_id: int) -> dict:
    """
    THE GATE: Publish a TikTok post to the account.
    
    Refuses to publish unless the post's status is exactly 'approved'.
    Since TikTok does not have a public posting API for third-party apps,
    this always runs in simulation mode.

    Returns
    -------
    dict with simulation result and notes.
    """
    from datetime import datetime, timezone, timedelta
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
            row = cur.fetchone()
            if not row:
                return {"error": f"Post ID {post_id} not found."}
            if row["platform"] != "tiktok":
                return {"error": "Post is not for tiktok."}
            
            if row["status"] != "approved":
                return {
                    "error": (
                        "Approval is required before publishing. "
                        f"Current status is '{row['status']}', but must be exactly 'approved'."
                    )
                }
            
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
            cur.execute(
                "UPDATE scheduled_posts SET status = 'published', published_at = ? WHERE id = ?",
                (now.isoformat(), post_id)
            )
            conn.commit()
            return {"simulated": True, "note": "TikTok has no public posting API for third-party apps", "status": "published"}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}
