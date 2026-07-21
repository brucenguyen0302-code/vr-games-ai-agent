import os
from pathlib import Path
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

# Load environment variables (e.g. GOOGLE_API_KEY) from the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
load_dotenv(PROJECT_ROOT / ".env")

mcp_server_path = PROJECT_ROOT / "mcp_server" / "server.py"

# Connect to the local MCP server via stdio and filter tools to just what sales needs.
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="python",
            args=[str(mcp_server_path)],
        ),
    ),
    tool_filter=[
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
    ]
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

root_agent = LlmAgent(
    name="sales_agent",
    model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
    instruction=INSTRUCTION.strip(),
    tools=[mcp_toolset]
)
