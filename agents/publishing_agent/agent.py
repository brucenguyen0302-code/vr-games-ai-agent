import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

try:
    from content_agent.agent import create_image_agent, create_video_agent
except ImportError:
    from agents.content_agent.agent import create_image_agent, create_video_agent

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

mcp_server_path = PROJECT_ROOT / "mcp_server" / "server.py"

def create_mcp_toolset(tool_filter: list[str]) -> McpToolset:
    # We must instantiate a new McpToolset (and thus stdio connection) for each agent
    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="python",
                args=[str(mcp_server_path)],
            ),
            timeout=120.0,
        ),
        tool_filter=tool_filter
    )

PUBLISHING_AGENT_INSTRUCTION = """
You are the publishing manager for Innoviz Crown (Instagram @thrillmates, hours Wed-Sun 11:00-21:00). You plan and schedule social posts; you never post anything the owner hasn't approved.

Standard workflow for a new post:
1. get_brand_guidelines and get_attractions to ground the content.
2. If an image is needed, call the transfer_to_agent tool with the argument publishing_image_agent, then use the returned file path. Do not attempt to call publishing_image_agent directly — it is an agent, not a tool.
3. Draft a caption in brand voice, always tagging @thrillmates, never inventing prices — read them from get_attractions.
4. Run moderate_content on the caption and fix any issues.
5. Call get_optimal_posting_time to pick a slot, explaining your reasoning.
6. schedule_post.
7. request_approval for the scheduled post.
8. Tell the user the post is awaiting owner approval and give them the post id.

If a video content plan is needed, call the transfer_to_agent tool with the argument publishing_video_agent. Do not attempt to call publishing_video_agent directly — it is an agent, not a tool.

publishing_image_agent and publishing_video_agent are sub-agents, not tools. The only way to hand work to them is the transfer_to_agent tool; never write their names as if invoking a function.

You must NEVER call publish_instagram_post or publish_tiktok_post unless get_approval_status confirms the post is approved. If asked to publish something unapproved, refuse and explain the approval requirement.

TikTok publishing is simulated only — say so when relevant.

SCOPE: you handle publishing and scheduling. Bookings, pricing enquiries and customer questions are out of scope — say so rather than answering.

After a sub-agent returns an asset to you, CONTINUE the workflow yourself from the next step — do not stop and ask the user unless you need a decision only they can make (e.g. approving the final caption wording).
"""

# Venue timezone (AEST, UTC+10 — matches mcp_server.py's fixed-offset convention).
_VENUE_TZ = timezone(timedelta(hours=10))


def _current_date_line() -> str:
    now = datetime.now(_VENUE_TZ)
    return (
        f"Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}), "
        "Australia/Sydney timezone. Always compute relative dates like "
        "'this Saturday' or 'tomorrow' from this date, and never schedule a "
        "post more than 14 days out unless explicitly asked.\n\n"
    )


def build_publishing_instruction(context: ReadonlyContext) -> str:
    # Evaluated per-request via ADK's InstructionProvider, so the date is
    # always fresh rather than frozen at module import time.
    return _current_date_line() + PUBLISHING_AGENT_INSTRUCTION.strip()


def create_publishing_agent() -> LlmAgent:
    return LlmAgent(
        name="publishing_agent",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=build_publishing_instruction,
        description="Agent for planning, scheduling, and publishing approved social posts for Innoviz Crown.",
        tools=[create_mcp_toolset([
            "get_optimal_posting_time",
            "get_brand_guidelines",
            "get_attractions",
            "moderate_content",
            "schedule_post",
            "get_scheduled_posts",
            "cancel_scheduled_post",
            "request_approval",
            "get_approval_status",
            "publish_instagram_post",
            "publish_tiktok_post",
        ])],
        sub_agents=[
            create_image_agent(name_prefix="publishing_"),
            create_video_agent(name_prefix="publishing_"),
        ]
    )


root_agent = create_publishing_agent()
