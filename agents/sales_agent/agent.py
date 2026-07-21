import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Load environment variables (e.g. GOOGLE_API_KEY) from the project root
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

INSTRUCTION = """
You are the sales & customer assistant for Innoviz Crown, a VR venue in Australia. Instagram: @thrillmates. Hours: Wed–Sun 11:00–21:00.
Tone: friendly, energetic, concise — like a great front-desk person. Match the customer's language.
NEVER invent prices, availability, or policies: always read them from tools. If a tool errors, say you'll check and get back, and log the interaction.
Booking flow: check availability before creating any booking; confirm details (attraction, time, party size, total price) with the customer before calling create_booking.
Always log meaningful interactions with log_customer_interaction.
Upsell naturally when relevant: groups → VYBOX booths or multiple rides; couples → 360 Flight/Paraglider (2 seats). Never pushy.
ESCALATE via flag_for_owner_review (and tell the customer the owner will follow up): refund requests, injuries/safety issues, media/partnership enquiries, legal threats, anything you're unsure about. Never promise refunds or discounts yourself.
"""

# Venue timezone (AEST, UTC+10 — matches mcp_server.py's fixed-offset convention).
_VENUE_TZ = timezone(timedelta(hours=10))


def _current_date_line() -> str:
    now = datetime.now(_VENUE_TZ)
    return (
        f"Today is {now.strftime('%Y-%m-%d')} ({now.strftime('%A')}), "
        "Australia/Sydney timezone. Always compute relative dates like "
        "'this Saturday' or 'tomorrow' from this date when checking "
        "availability or creating bookings.\n\n"
    )


def build_sales_instruction(context: ReadonlyContext) -> str:
    # Evaluated per-request via ADK's InstructionProvider, so the date is
    # always fresh rather than frozen at module import time.
    return _current_date_line() + INSTRUCTION.strip()


def create_sales_agent() -> LlmAgent:
    return LlmAgent(
        name="sales_agent",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=build_sales_instruction,
        description="Agent for customer bookings, pricing, availability, and Instagram DM/comment handling for Innoviz Crown.",
        tools=[create_mcp_toolset([
            "get_attractions",
            "get_pricing",
            "check_availability",
            "create_booking",
            "get_upcoming_bookings",
            "log_customer_interaction",
            "get_instagram_dms",
            "reply_instagram_dm",
            "get_instagram_comments",
            "flag_for_owner_review"
        ])]
    )


root_agent = create_sales_agent()
