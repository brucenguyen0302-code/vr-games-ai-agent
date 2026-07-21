import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext

try:
    from sales_agent.agent import create_sales_agent
except ImportError:
    from agents.sales_agent.agent import create_sales_agent

try:
    from content_agent.agent import create_content_agent
except ImportError:
    from agents.content_agent.agent import create_content_agent

try:
    from publishing_agent.agent import create_publishing_agent
except ImportError:
    from agents.publishing_agent.agent import create_publishing_agent

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

ORCHESTRATOR_INSTRUCTION = """
You are the coordinator for Innoviz Crown's business automation system. You route requests to the right specialist and never do the work yourself.

Routing: to hand off a request, call the transfer_to_agent tool with the argument set to the specialist's name. Do not attempt to call a specialist directly by name — they are agents, not tools; transfer_to_agent is the only way to hand off work.
- Customer questions, bookings, pricing, availability, DMs, complaints → call transfer_to_agent with the argument sales_agent.
- Standalone creative assets (an image, a video script) with no publishing involved → call transfer_to_agent with the argument content_agent.
- Anything about posting, scheduling, campaigns, or approvals → call transfer_to_agent with the argument publishing_agent.

When a sub-agent transfers control back to you, either route to the next specialist (again via transfer_to_agent) or summarise the result for the user.

If a request spans several specialists (e.g. "make a post about our busiest attraction"), sequence it: get the data need met first, then the content, then publishing.

Never invent business facts yourself — you have no tools; all facts come from specialists.
"""

# Venue timezone (AEST, UTC+10 — matches mcp_server.py's fixed-offset convention).
_VENUE_TZ = timezone(timedelta(hours=10))


def _current_date_line() -> str:
    now = datetime.now(_VENUE_TZ)
    return (
        f"Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}), "
        "Australia/Sydney timezone. Use this to reason about relative dates "
        "when sequencing work across specialists.\n\n"
    )


def build_orchestrator_instruction(context: ReadonlyContext) -> str:
    # Evaluated per-request via ADK's InstructionProvider, so the date is
    # always fresh rather than frozen at module import time.
    return _current_date_line() + ORCHESTRATOR_INSTRUCTION.strip()


def create_orchestrator_agent() -> LlmAgent:
    return LlmAgent(
        name="orchestrator",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=build_orchestrator_instruction,
        description="Root coordinator for Innoviz Crown's business automation system.",
        sub_agents=[create_sales_agent(), create_content_agent(), create_publishing_agent()]
    )


root_agent = create_orchestrator_agent()
