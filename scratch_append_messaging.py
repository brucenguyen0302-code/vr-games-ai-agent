# ---------------------------------------------------------------------------
# Messaging and Approval tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_instagram_dms(unreplied_only: bool = True) -> dict:
    """
    Retrieve inbound Instagram Direct Messages (DMs).
    
    Parameters
    ----------
    unreplied_only : bool
        If True, returns only messages that have not yet been replied to.
        
    Returns
    -------
    dict containing a list of messages or an error.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            if unreplied_only:
                cur.execute("SELECT * FROM instagram_messages WHERE direction = 'inbound' AND replied = 0 ORDER BY timestamp ASC")
            else:
                cur.execute("SELECT * FROM instagram_messages WHERE direction = 'inbound' ORDER BY timestamp ASC")
            return {"messages": [dict(r) for r in cur.fetchall()]}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


def _send_dm_real(thread_id: str, message_text: str, row: dict) -> dict:
    """
    Real Instagram Graph API DM send flow.
    """
    return {"simulated": True, "note": "Real IG DM publishing not fully implemented"}

@mcp.tool()
def reply_instagram_dm(thread_id: str, message_text: str) -> dict:
    """
    Reply to an Instagram DM thread.
    
    Validates that the thread exists and runs the reply text through internal
    content moderation. Inserts an outbound message row and marks the latest
    inbound message for that thread as replied.
    
    If IG credentials are set, it calls the real Instagram Graph API.
    Otherwise, it runs in simulation mode.
    
    Parameters
    ----------
    thread_id : str
        The thread identifier.
    message_text : str
        The reply text.
        
    Returns
    -------
    dict with operation result, including simulation status.
    """
    from datetime import datetime, timezone, timedelta
    
    mod_res = moderate_content(message_text)
    if not mod_res.get("approved"):
        return {"error": "Reply text failed moderation.", "issues": mod_res.get("issues")}

    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
    now_str = now.isoformat()

    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM instagram_messages WHERE thread_id = ? AND direction = 'inbound' ORDER BY timestamp DESC LIMIT 1",
                (thread_id,)
            )
            inbound = cur.fetchone()
            if not inbound:
                return {"error": f"Thread ID {thread_id} not found or no inbound messages."}

            cur.execute(
                "INSERT INTO instagram_messages (direction, thread_id, sender_handle, message_text, timestamp, replied) VALUES ('outbound', ?, 'venue_account', ?, ?, 0)",
                (thread_id, message_text, now_str)
            )
            outbound_id = cur.lastrowid
            
            cur.execute("UPDATE instagram_messages SET replied = 1 WHERE thread_id = ? AND direction = 'inbound'", (thread_id,))
            conn.commit()
            
            ig_user = os.environ.get("IG_USER_ID")
            ig_token = os.environ.get("IG_ACCESS_TOKEN")
            if ig_user and ig_token:
                return _send_dm_real(thread_id, message_text, dict(inbound))
                
            return {"simulated": True, "note": "IG credentials not configured", "status": "replied"}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_instagram_comments() -> dict:
    """
    Retrieve Instagram comments.
    
    Returns
    -------
    dict containing a list of Instagram comments.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM social_comments WHERE platform = 'instagram' ORDER BY timestamp ASC")
            return {"comments": [dict(r) for r in cur.fetchall()]}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_tiktok_comments() -> dict:
    """
    Retrieve TikTok comments.
    
    NOTE: TikTok has no public posting API for third-party apps, so this
    tool is mock-only and reads from the local simulation database.
    
    Returns
    -------
    dict containing a list of TikTok comments.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM social_comments WHERE platform = 'tiktok' ORDER BY timestamp ASC")
            return {"comments": [dict(r) for r in cur.fetchall()]}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def flag_for_owner_review(context: str, reason: str, severity: str) -> dict:
    """
    Flag an issue or request for the human owner to review.
    
    Use this for anything the agent should not handle alone, such as refunds,
    angry customers, or legal questions. Logs the request to the CRM interaction
    history as an 'owner_flag' so the owner can follow up.
    
    Parameters
    ----------
    context : str
        The context of the issue (e.g., customer handle, thread ID, or booking details).
    reason : str
        Why it requires owner review.
    severity : str
        Must be 'low', 'medium', or 'high'.
        
    Returns
    -------
    dict with confirmation.
    """
    if severity not in ("low", "medium", "high"):
        return {"error": "Severity must be 'low', 'medium', or 'high'."}
        
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
    
    summary = f"[{severity.upper()}] Owner review requested for: {context}"
    outcome = reason
    
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO customer_interactions (timestamp, channel, customer_handle, summary, outcome) VALUES (?, 'owner_flag', ?, ?, ?)",
                (now.isoformat(), context, summary, outcome)
            )
            conn.commit()
            return {"status": "success", "message": "Flagged for owner review.", "id": cur.lastrowid}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def request_approval(post_id: int) -> dict:
    """
    Request owner approval for a scheduled post.
    
    This creates an approvals row (pending) for a scheduled post that is currently
    in 'pending_approval' status. This notifies the owner to review the post.
    
    CRITICAL: The agent must NOT approve its own content. The agent can only request
    approval. The owner will review and decide.
    
    Parameters
    ----------
    post_id : int
        The ID of the scheduled post.
        
    Returns
    -------
    dict with the approval ID and status.
    """
    from datetime import datetime, timezone, timedelta
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,))
            row = cur.fetchone()
            if not row:
                return {"error": f"Post ID {post_id} not found."}
            if row["status"] != "pending_approval":
                return {"error": f"Post status must be 'pending_approval', got '{row['status']}'."}
                
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=10))).replace(tzinfo=None)
            cur.execute(
                "INSERT INTO approvals (item_type, item_id, status, requested_at) VALUES ('post', ?, 'pending', ?)",
                (post_id, now.isoformat())
            )
            approval_id = cur.lastrowid
            conn.commit()
            return {"status": "approval_requested", "approval_id": approval_id, "post_id": post_id}
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def get_approval_status(approval_id: int) -> dict:
    """
    Check the status of an approval request.
    
    If the owner has decided on the approval (approved or rejected), this tool
    will also update the linked post's status accordingly and return the full
    result. This is how owner decisions propagate back to the agent.
    
    Parameters
    ----------
    approval_id : int
        The ID of the approval request.
        
    Returns
    -------
    dict with the approval and item details.
    """
    try:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
            approval = cur.fetchone()
            if not approval:
                return {"error": f"Approval ID {approval_id} not found."}
                
            post_id = approval["item_id"]
            appr_status = approval["status"]
            
            # If the approval has been decided, cascade to the post
            if appr_status in ("approved", "rejected"):
                cur.execute("SELECT status FROM scheduled_posts WHERE id = ?", (post_id,))
                post = cur.fetchone()
                if post and post["status"] == "pending_approval":
                    if appr_status == "approved":
                        cur.execute("UPDATE scheduled_posts SET status = 'approved' WHERE id = ?", (post_id,))
                    else:
                        cur.execute("UPDATE scheduled_posts SET status = 'rejected', rejection_reason = ? WHERE id = ?", (approval["note"], post_id))
                    conn.commit()
            
            # Re-fetch post for complete response
            cur.execute("SELECT * FROM scheduled_posts WHERE id = ?", (post_id,))
            post = cur.fetchone()
            
            return {
                "approval_id": approval["id"],
                "approval_status": appr_status,
                "decided_at": approval["decided_at"],
                "decided_by": approval["decided_by"],
                "note": approval["note"],
                "linked_post": dict(post) if post else None
            }
        finally:
            conn.close()
    except Exception as e:
        return {"error": str(e)}
