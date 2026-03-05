import json
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
import structlog

from src.agent.prompts import MARKET_SNIPER_SYSTEM_PROMPT, build_task_prompt
from src.agent.tools.extract import extract_listing, extract_price
from src.agent.tools.fetch import fetch_url
from src.agent.tools.search import web_search
from src.config import get_settings

log = structlog.get_logger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"
MAX_TOOL_ROUNDS = 5


@dataclass
class AgentResult:
    triggered: bool
    confidence: str  # high | medium | low
    summary: str
    data: dict[str, Any]
    tools_used: list[str]
    next_action: str
    tier_used: str | None = None
    raw_output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# Tool definitions for Anthropic tool use API
TOOL_DEFINITIONS = [
    {
        "name": "fetch_url",
        "description": "Fetch the HTML content of a URL. Returns cleaned text of the page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "context": {"type": "string", "description": "Context about what we're looking for"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for information. Returns a list of results with titles, URLs, and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "num_results": {"type": "integer", "description": "Number of results (1-10)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "extract_price",
        "description": "Use AI to extract price and availability from HTML content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "The HTML content to analyze"},
                "context": {"type": "string", "description": "Context about what we're looking for"},
            },
            "required": ["html", "context"],
        },
    },
    {
        "name": "extract_listing",
        "description": "Use AI to extract structured listing data (title, price, condition, seller) from HTML.",
        "input_schema": {
            "type": "object",
            "properties": {
                "html": {"type": "string", "description": "The HTML content to analyze"},
                "context": {"type": "string", "description": "Context about the listing"},
            },
            "required": ["html", "context"],
        },
    },
]


async def _execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool call and return the result as a string."""
    if tool_name == "fetch_url":
        result = await fetch_url(
            url=tool_input["url"],
            context=tool_input.get("context", ""),
        )
        return json.dumps({
            "text": result.text[:20000],  # cap at 20k chars
            "status": result.status,
            "url": result.url,
            "tier_used": result.tier_used,
        })

    elif tool_name == "web_search":
        results = await web_search(
            query=tool_input["query"],
            num_results=tool_input.get("num_results", 5),
        )
        return json.dumps([
            {"title": r.title, "url": r.url, "description": r.description, "age": r.age}
            for r in results
        ])

    elif tool_name == "extract_price":
        result = await extract_price(
            html=tool_input["html"],
            context=tool_input.get("context", ""),
        )
        return json.dumps({
            "price": result.price,
            "currency": result.currency,
            "available": result.available,
            "confidence": result.confidence,
        })

    elif tool_name == "extract_listing":
        result = await extract_listing(
            html=tool_input["html"],
            context=tool_input.get("context", ""),
        )
        return json.dumps({
            "title": result.title,
            "price": result.price,
            "currency": result.currency,
            "condition": result.condition,
            "seller": result.seller,
            "url": result.url,
            "confidence": result.confidence,
        })

    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


def _parse_agent_output(text: str) -> dict:
    """Parse the JSON output from the agent's final response."""
    # Try to find JSON in the response
    text = text.strip()
    if text.startswith("```"):
        # Strip markdown code fences
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        if text.startswith("json"):
            text = text[4:].strip()

    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    return json.loads(text)


async def _run_agent_loop(snipe: dict, model: str) -> tuple[dict, list[str], str | None]:
    """
    Execute the agent tool-use loop.
    Returns (parsed_output, tools_used, tier_used).
    """
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    task_prompt = build_task_prompt(snipe)
    messages = [{"role": "user", "content": task_prompt}]
    tools_used: list[str] = []
    tier_used: str | None = None

    for round_num in range(MAX_TOOL_ROUNDS):
        log.info("agent.round", round=round_num + 1, model=model, snipe_id=snipe.get("id"))

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=MARKET_SNIPER_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Append assistant's response to messages
        messages.append({"role": "assistant", "content": response.content})

        # Check stop reason
        if response.stop_reason == "end_turn":
            # Extract final text response
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text += block.text
            output = _parse_agent_output(final_text)
            return output, tools_used, tier_used

        if response.stop_reason != "tool_use":
            raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason}")

        # Process all tool calls in this round
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_input = block.input
            tools_used.append(tool_name)

            log.info("agent.tool_call", tool=tool_name, input_keys=list(tool_input.keys()))

            try:
                result_str = await _execute_tool(tool_name, tool_input)
                # Track tier_used from fetch results
                if tool_name == "fetch_url":
                    try:
                        r = json.loads(result_str)
                        if r.get("tier_used"):
                            tier_used = r["tier_used"]
                    except Exception:
                        pass
            except Exception as exc:
                log.warning("agent.tool_error", tool=tool_name, error=str(exc))
                result_str = json.dumps({"error": str(exc)})

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent exceeded max tool rounds ({MAX_TOOL_ROUNDS})")


async def run_snipe(snipe: dict) -> AgentResult:
    """
    Main agent execution loop for a single snipe.

    1. Build task description from snipe config
    2. Call Claude with tools available
    3. Execute tool calls as they come in (tool use loop)
    4. Parse final JSON output
    5. Evaluate against snipe condition
    6. Return AgentResult

    Uses haiku for speed; upgrades to sonnet on failure or low confidence.
    """
    start_time = time.time()
    log.info("agent.run_start", snipe_id=snipe.get("id"), name=snipe.get("name"))

    try:
        output, tools_used, tier_used = await _run_agent_loop(snipe, HAIKU_MODEL)

        # Upgrade to sonnet if confidence is low
        if output.get("confidence") == "low":
            log.info("agent.upgrading_to_sonnet", snipe_id=snipe.get("id"))
            try:
                output, tools_used, tier_used = await _run_agent_loop(snipe, SONNET_MODEL)
            except Exception as exc:
                log.warning("agent.sonnet_upgrade_failed", error=str(exc))
                # Keep haiku result

        elapsed_ms = int((time.time() - start_time) * 1000)
        log.info(
            "agent.run_complete",
            snipe_id=snipe.get("id"),
            triggered=output.get("triggered"),
            confidence=output.get("confidence"),
            duration_ms=elapsed_ms,
        )

        return AgentResult(
            triggered=bool(output.get("triggered", False)),
            confidence=output.get("confidence", "low"),
            summary=output.get("summary", ""),
            data=output.get("data", {}),
            tools_used=list(set(tools_used)),
            next_action=output.get("next_action", "continue_monitoring"),
            tier_used=tier_used,
            raw_output=output,
        )

    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        log.error("agent.run_failed", snipe_id=snipe.get("id"), error=str(exc))
        return AgentResult(
            triggered=False,
            confidence="low",
            summary=f"Agent error: {exc}",
            data={},
            tools_used=[],
            next_action="needs_human_review",
            tier_used=None,
            error=str(exc),
        )
