"""
app.py
------
Gradio web UI for the Innoviz Crown business agent system. Designed to run
locally (`python app.py`) or deployed to Hugging Face Spaces (free CPU tier).

Three tabs:
  1. Chat            — talk to the orchestrator agent (ADK Runner + InMemorySessionService),
                        one session per browser tab, with a trace panel for the last turn.
  2. Owner approval   — the owner_console.py workflow as a UI: approve/reject posts
                         pending_approval. No LLM involved — direct SQLite reads/writes.
  3. About            — architecture, approval gate, security model, and deployment notes.

Config is read from the environment (GOOGLE_API_KEY, HF_TOKEN, AGENT_MODEL) — Spaces
injects secrets as env vars; python-dotenv loads a local .env for local development.
"""

from __future__ import annotations

import importlib.util
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import gradio as gr
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")

import os  # noqa: E402  (after load_dotenv, so .env values are visible below)

DB_PATH = PROJECT_ROOT / "data" / "venue.db"
GITHUB_URL = "https://github.com/brucenguyen0302-code/vr-games-ai-agent"

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
HF_TOKEN = os.environ.get("HF_TOKEN")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite")

_VENUE_TZ = timezone(timedelta(hours=10))  # AEST — matches mcp_server.py's convention

logger.info(
    "Storage: %s. On Hugging Face Spaces (free tier) this directory is EPHEMERAL — "
    "it resets to the seed fixture on every Space restart.",
    DB_PATH,
)
if not GOOGLE_API_KEY:
    logger.warning("GOOGLE_API_KEY is not set — the Chat tab will not be able to reach the model.")
if not HF_TOKEN:
    logger.warning("HF_TOKEN is not set — image generation will fall back to a local mock image.")


# ---------------------------------------------------------------------------
# Startup: seed the database if it doesn't exist yet (import, don't shell out)
# ---------------------------------------------------------------------------

def _ensure_db() -> None:
    if DB_PATH.exists():
        logger.info("Found existing database at %s — skipping seed.", DB_PATH)
        return
    logger.warning(
        "No database found at %s — seeding from data/init_db.py now. "
        "NOTE: this data is EPHEMERAL — data/venue.db is not committed to git, so on "
        "Hugging Face Spaces it will be recreated from this same seed fixture every "
        "time the Space restarts. Anything written during this session (bookings, "
        "approvals, scheduled posts) will be lost on restart.",
        DB_PATH,
    )
    spec = importlib.util.spec_from_file_location("_venue_init_db", PROJECT_ROOT / "data" / "init_db.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.main()


_ensure_db()


# ---------------------------------------------------------------------------
# Tab 1 — Chat (orchestrator agent via ADK Runner)
# ---------------------------------------------------------------------------

APP_NAME = "innoviz_crown_ui"
_CHAT_USER_ID = "web_user"

_runner: Any = None
_session_service: Any = None
_agent_init_error: str | None = None


def _get_runner() -> Any:
    """Lazily build the orchestrator agent + Runner once, reused across all
    browser sessions (each session is distinguished by ADK session_id, not
    by a separate Runner/agent instance)."""
    global _runner, _session_service, _agent_init_error
    if _runner is not None or _agent_init_error is not None:
        return _runner
    try:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from agents.orchestrator.agent import create_orchestrator_agent

        root_agent = create_orchestrator_agent()
        _session_service = InMemorySessionService()
        _runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=_session_service)
        logger.info("Orchestrator agent initialised (model=%s).", AGENT_MODEL)
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
        logger.exception("Failed to initialise the orchestrator agent")
        _agent_init_error = str(exc)
    return _runner


def _friendly_api_error(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    if code == 429:
        return (
            "⚠️ The AI model is rate-limited right now (free-tier quota hit). "
            "Please wait a minute and try again."
        )
    if code in (401, 403):
        return "⚠️ The model API rejected the request — check that `GOOGLE_API_KEY` is valid."
    message = getattr(exc, "message", None) or str(exc)
    return f"⚠️ The AI model returned an error{f' ({code})' if code else ''}: {message}"


def _render_trace_line(event: Any) -> list[str]:
    """Extract tool calls and agent transfers (name + arguments) from one ADK event."""
    lines: list[str] = []
    parts = event.content.parts if event.content else []
    for part in parts:
        fc = getattr(part, "function_call", None)
        if fc:
            args = dict(fc.args or {})
            if fc.name == "transfer_to_agent":
                target = args.get("agent_name", "?")
                lines.append(f"- **`{event.author}`** transfers control → **`{target}`**")
            else:
                if args:
                    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                else:
                    args_str = "(no arguments)"
                lines.append(f"- **`{event.author}`** calls tool `{fc.name}`({args_str})")
    return lines


async def chat_respond(message: str, history: list[dict], session_id: str):
    message = (message or "").strip()
    if not message:
        return history, "", "_No turns yet._", session_id

    if not GOOGLE_API_KEY:
        history = history + [
            {"role": "user", "content": message},
            {
                "role": "assistant",
                "content": (
                    "⚠️ `GOOGLE_API_KEY` is not configured, so I can't reach the model. "
                    "Set it in this Space's secrets (or in a local `.env` file) and restart."
                ),
            },
        ]
        return history, "", "_No trace — `GOOGLE_API_KEY` is missing._", session_id

    runner = _get_runner()
    if runner is None:
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": f"⚠️ The agent system failed to start up: {_agent_init_error}"},
        ]
        return history, "", "_No trace — startup error._", session_id

    if await _session_service.get_session(app_name=APP_NAME, user_id=_CHAT_USER_ID, session_id=session_id) is None:
        await _session_service.create_session(app_name=APP_NAME, user_id=_CHAT_USER_ID, session_id=session_id)

    history = history + [{"role": "user", "content": message}]

    from google.genai import errors, types

    trace_lines: list[str] = []
    final_text = ""
    try:
        new_message = types.Content(role="user", parts=[types.Part.from_text(text=message)])
        async for event in runner.run_async(
            user_id=_CHAT_USER_ID, session_id=session_id, new_message=new_message
        ):
            trace_lines.extend(_render_trace_line(event))
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        final_text = part.text
    except errors.APIError as exc:
        history.append({"role": "assistant", "content": _friendly_api_error(exc)})
        trace_md = "\n".join(trace_lines) if trace_lines else "_No tool calls recorded before the error._"
        return history, "", trace_md, session_id
    except Exception as exc:  # noqa: BLE001 — last-resort guard so a bad turn never crashes the UI
        logger.exception("Unexpected error handling a chat turn")
        history.append({"role": "assistant", "content": f"⚠️ Something went wrong: {exc}"})
        trace_md = "\n".join(trace_lines) if trace_lines else "_No trace available._"
        return history, "", trace_md, session_id

    history.append({"role": "assistant", "content": final_text or "(no response)"})
    trace_md = "\n".join(trace_lines) if trace_lines else "_No tool calls or transfers this turn._"
    return history, "", trace_md, session_id


CHAT_EXAMPLES = [
    "How much is the 360 Flight for 2 people?",
    "I'd like to book the VYBOX Large Booth for 6 people this Saturday at 7pm",
    "Check my unreplied Instagram DMs and draft replies",
    "Generate a promotional image for the VR Slide",
    "Schedule an Instagram post about VYBOX for Friday",
]


# ---------------------------------------------------------------------------
# Tab 2 — Owner approval (reproduces owner_console.py — no LLM involved)
# ---------------------------------------------------------------------------

def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_pending_posts() -> list[dict]:
    conn = _get_connection()
    try:
        cur = conn.execute(
            """
            SELECT a.id as approval_id, a.requested_at, p.id as post_id, p.platform,
                   p.caption, p.image_path, p.scheduled_datetime
            FROM approvals a
            JOIN scheduled_posts p ON a.item_id = p.id
            WHERE a.status = 'pending' AND a.item_type = 'post' AND p.status = 'pending_approval'
            ORDER BY a.requested_at ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _decide_approval(approval_id: int, decision: str, note: str) -> str:
    conn = _get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM approvals WHERE id = ? AND status = 'pending'", (approval_id,))
        approval = cur.fetchone()
        if not approval:
            return f"⚠️ Pending approval ID {approval_id} not found (already decided?)."

        now = datetime.now(timezone.utc).astimezone(_VENUE_TZ).replace(tzinfo=None).isoformat()
        cur.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = 'owner', note = ? WHERE id = ?",
            (decision, now, note, approval_id),
        )

        post_id = approval["item_id"]
        if decision == "approved":
            cur.execute("UPDATE scheduled_posts SET status = 'approved' WHERE id = ?", (post_id,))
            msg = f"✅ Approval {approval_id} APPROVED. Post {post_id} is ready to be published."
        else:
            cur.execute(
                "UPDATE scheduled_posts SET status = 'rejected', rejection_reason = ? WHERE id = ?",
                (note, post_id),
            )
            msg = f"❌ Approval {approval_id} REJECTED. Post {post_id} status updated to rejected."
        conn.commit()
        return msg
    except Exception as exc:  # noqa: BLE001
        return f"⚠️ Database error: {exc}"
    finally:
        conn.close()


def refresh_table():
    rows = _fetch_pending_posts()
    display = [
        [r["approval_id"], r["platform"], r["scheduled_datetime"], r["requested_at"], r["caption"]]
        for r in rows
    ]
    return display, rows


def clear_selection():
    return None, None, "", ""


def on_row_select(rows: list[dict], evt: gr.SelectData):
    if not rows or evt.index is None:
        return None, None, "", ""
    idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
    if idx is None or idx >= len(rows):
        return None, None, "", ""
    row = rows[idx]
    image_path = row.get("image_path") or ""
    image_value = image_path if image_path and Path(image_path).is_file() else None
    return image_value, row["approval_id"], row["caption"], image_path


def approve_action(approval_id: float | None, note: str) -> str:
    if not approval_id:
        return "⚠️ Select a pending approval from the table first."
    return _decide_approval(int(approval_id), "approved", (note or "").strip())


def reject_action(approval_id: float | None, note: str) -> str:
    if not approval_id:
        return "⚠️ Select a pending approval from the table first."
    if not note or not note.strip():
        return "⚠️ A reason is required to reject a post."
    return _decide_approval(int(approval_id), "rejected", note.strip())


# ---------------------------------------------------------------------------
# Tab 3 — About
# ---------------------------------------------------------------------------

ABOUT_MD = f"""
## About this system

**Innoviz Crown Business Agent** is a multi-agent system (Google ADK) that automates
marketing content creation and customer engagement for Innoviz Crown, a real VR
entertainment venue in Australia.

### Architecture

- **`orchestrator`** — routes each request to the right specialist via ADK's
  `transfer_to_agent`. It has no tools of its own, so it structurally cannot
  invent a price, a booking, or a post.
- **`sales_agent`** — bookings, pricing, availability, and Instagram DM/comment
  triage. It is the only agent that touches untrusted external input (customer
  messages), and carries its own prompt-injection defenses.
- **`content_agent`** — a thin router to `image_agent` / `video_agent` for
  standalone creative assets, with no publishing involved.
- **`publishing_agent`** — plans, schedules, and (once approved) publishes
  social posts.

All of them call into a shared MCP tool server (`mcp_server/server.py`), which
is the only thing that touches the SQLite database (`data/venue.db`).

### The approval gate

No post reaches Instagram or TikTok without a human saying so:

```
schedule_post → status 'pending_approval' → request_approval
   → owner reviews (this tab, or owner_console.py)
   → approved / rejected → get_approval_status
   → only 'approved' lets publish_instagram_post / publish_tiktok_post succeed
```

This is enforced at **two layers**: the agent's instructions say never to
publish without approval, and — independently — the `publish_*` tools
themselves check the approval status in the database before doing anything,
regardless of what the model believes it's allowed to do.

### Security model

- Instagram DMs/comments are wrapped as `<untrusted_user_content>` and scanned
  for prompt-injection patterns *before* an agent reasons over them.
- Medium/high-severity messages are auto-flagged for owner review by the tool
  layer itself — deterministically, not left to the model to remember.
- Every write tool validates input length/characters and uses parameterized
  SQL (no string-built queries).
- A runtime output guard checks the model's final response against the tool
  calls it actually made this turn, and rewrites any "claimed but not
  performed" narration before it reaches the user.
- Rate limiting on image generation, DM replies, and publishing.

Full detail: **[{GITHUB_URL}]({GITHUB_URL})**

### ⚠️ Important notes about this deployment

- **The database is ephemeral on Hugging Face Spaces (free tier).**
  `data/venue.db` is not committed to git — every Space restart re-seeds it
  from `data/init_db.py`'s fixture data. Bookings, approvals, and posts made
  in this UI will be lost the next time the Space restarts.
- **Instagram/TikTok publishing is simulated.** Nothing is actually posted to
  a real social account from this demo unless the deployer has configured
  real `IG_USER_ID`/`IG_ACCESS_TOKEN` credentials — TikTok publishing is
  always simulated.
"""


# ---------------------------------------------------------------------------
# Build the Blocks app
# ---------------------------------------------------------------------------

def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Innoviz Crown Business Agent") as demo:
        gr.Markdown("# 🎮 Innoviz Crown Business Agent")

        with gr.Tab("Chat"):
            if not GOOGLE_API_KEY:
                gr.Markdown(
                    "⚠️ **`GOOGLE_API_KEY` is not set.** The chat will not be able to reach the "
                    "model until it's configured (Space secrets, or a local `.env` file) and the "
                    "app is restarted."
                )
            session_id_state = gr.State(value=lambda: str(uuid.uuid4()))

            chatbot = gr.Chatbot(height=480, label="Innoviz Crown Assistant")
            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask about pricing, bookings, DMs, content, or scheduling...",
                    label="Message",
                    scale=8,
                    autofocus=True,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)

            gr.Examples(examples=CHAT_EXAMPLES, inputs=msg, label="Example prompts")

            with gr.Accordion("🔍 Trace — tool calls & agent transfers (last turn)", open=False):
                trace_md = gr.Markdown("_No turns yet._")

            clear_btn = gr.Button("Clear conversation")

            chat_inputs = [msg, chatbot, session_id_state]
            chat_outputs = [chatbot, msg, trace_md, session_id_state]
            msg.submit(chat_respond, inputs=chat_inputs, outputs=chat_outputs)
            send_btn.click(chat_respond, inputs=chat_inputs, outputs=chat_outputs)

            def _new_conversation():
                return [], "_No turns yet._", str(uuid.uuid4())

            clear_btn.click(_new_conversation, outputs=[chatbot, trace_md, session_id_state])

        with gr.Tab("Owner approval"):
            gr.Markdown(
                "### Pending post approvals\n"
                "Approve or reject posts awaiting owner sign-off before they can be "
                "published. This mirrors `owner_console.py` exactly (same tables, same "
                "status transitions) — **no AI is involved on this tab.**"
            )
            refresh_btn = gr.Button("🔄 Refresh")
            pending_state = gr.State([])
            table = gr.Dataframe(
                headers=["Approval ID", "Platform", "Scheduled (AEST)", "Requested At", "Caption"],
                datatype=["number", "str", "str", "str", "str"],
                interactive=False,
                wrap=True,
                label="Pending approvals",
            )

            with gr.Row():
                with gr.Column(scale=1):
                    image_preview = gr.Image(label="Image preview", interactive=False)
                with gr.Column(scale=2):
                    selected_id = gr.Number(label="Selected Approval ID", interactive=False)
                    full_caption = gr.Textbox(label="Full caption", interactive=False, lines=4)
                    image_path_box = gr.Textbox(label="Image path", interactive=False)

            note_box = gr.Textbox(label="Note / rejection reason (required to reject)")
            with gr.Row():
                approve_btn = gr.Button("✅ Approve", variant="primary")
                reject_btn = gr.Button("❌ Reject", variant="stop")
            action_status = gr.Markdown("")

            detail_outputs = [image_preview, selected_id, full_caption, image_path_box]

            refresh_btn.click(refresh_table, outputs=[table, pending_state]).then(
                clear_selection, outputs=detail_outputs
            )
            demo.load(refresh_table, outputs=[table, pending_state])
            table.select(on_row_select, inputs=[pending_state], outputs=detail_outputs)

            approve_btn.click(
                approve_action, inputs=[selected_id, note_box], outputs=[action_status]
            ).then(refresh_table, outputs=[table, pending_state]).then(
                clear_selection, outputs=detail_outputs
            )
            reject_btn.click(
                reject_action, inputs=[selected_id, note_box], outputs=[action_status]
            ).then(refresh_table, outputs=[table, pending_state]).then(
                clear_selection, outputs=detail_outputs
            )

        with gr.Tab("About"):
            gr.Markdown(ABOUT_MD)

    return demo


demo = build_demo()

if __name__ == "__main__":
    demo.queue().launch()
