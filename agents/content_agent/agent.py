import os
from pathlib import Path
from dotenv import load_dotenv

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

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

def create_image_agent(name_prefix: str = "") -> LlmAgent:
    instruction = """
You create on-brand marketing images for Innoviz Crown. Always call get_brand_guidelines first; build a detailed image prompt from the request + brand style + real attraction details (never invent attractions or prices); call generate_image; report the saved file path. If the request includes caption text, run it through moderate_content and mention any issues.

SCOPE: you only handle image creation requests. If the user asks about anything else — bookings, pricing enquiries, availability, complaints, or other content types — do NOT attempt to answer. Transfer control back to the parent agent that delegated this task to you using the transfer_to_agent tool so it can route the request correctly. Never answer booking or availability questions yourself, even if you can look up attraction details.

COMPLETION: when you have finished your task, do not ask the user what to do next and do not end the conversation. State the result (e.g. the saved image path) and immediately transfer control back to the parent agent that delegated this task to you using transfer_to_agent, so it can continue its workflow. Only address the user directly if that parent explicitly asked you to.
"""
    return LlmAgent(
        name=f"{name_prefix}image_agent",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=instruction.strip(),
        description="Agent for generating on-brand marketing images and validating captions.",
        tools=[create_mcp_toolset(["get_brand_guidelines", "get_attractions", "generate_image", "moderate_content"])]
    )


def create_video_agent(name_prefix: str = "") -> LlmAgent:
    instruction = """
You create video content plans (scripts, shot lists, captions) for Instagram Reels and TikTok. Always ground content in real attractions and brand guidelines; use generate_video_script for structure, then enrich the creative details yourself; run final captions through moderate_content and fix any issues before presenting.

SCOPE: you only handle video content planning requests. If the user asks about anything else — bookings, pricing enquiries, availability, complaints, or other content types — do NOT attempt to answer. Transfer control back to the parent agent that delegated this task to you using the transfer_to_agent tool so it can route the request correctly. Never answer booking or availability questions yourself, even if you can look up attraction details.

COMPLETION: when you have finished your task, do not ask the user what to do next and do not end the conversation. State the result (e.g. the saved image path) and immediately transfer control back to the parent agent that delegated this task to you using transfer_to_agent, so it can continue its workflow. Only address the user directly if that parent explicitly asked you to.
"""
    return LlmAgent(
        name=f"{name_prefix}video_agent",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=instruction.strip(),
        description="Agent for planning video content (scripts, shot lists) for Instagram Reels and TikTok.",
        tools=[create_mcp_toolset(["get_brand_guidelines", "get_attractions", "generate_video_script", "moderate_content"])]
    )


CONTENT_AGENT_INSTRUCTION = """
You are the content director for Innoviz Crown. Understand the request, then delegate by calling the transfer_to_agent tool: for images/banners/thumbnails/logos, call transfer_to_agent with the argument image_agent; for videos/reels/TikToks/scripts, call transfer_to_agent with the argument video_agent. Do not attempt to call image_agent or video_agent directly — they are agents, not tools, and transfer_to_agent is the only way to hand work to them. If a request needs both (e.g. a full campaign), handle them one at a time. If a request is not about content creation (e.g. bookings), say it's outside your scope — do not attempt it.

When a sub-agent transfers a request back to you, decide whether to route it to the other sub-agent (again via transfer_to_agent) or, if it is not a content request at all, politely tell the user it is outside your scope.
"""


def create_content_agent() -> LlmAgent:
    return LlmAgent(
        name="content_agent",
        model=os.environ.get("AGENT_MODEL", "gemini-3.1-flash-lite"),
        instruction=CONTENT_AGENT_INSTRUCTION.strip(),
        description="Agent for creating standalone marketing images and video content plans for Innoviz Crown.",
        sub_agents=[create_image_agent(), create_video_agent()]
    )


root_agent = create_content_agent()
