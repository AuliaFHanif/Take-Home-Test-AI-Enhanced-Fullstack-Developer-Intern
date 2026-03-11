"""
FastAPI Backend — AI UI Analysis Agent
Acts as the MCP Host: orchestrates the agentic loop between the user,
the Claude API, and the local MCP server.
"""

import asyncio
import json
import os
import subprocess
from typing import AsyncIterator

import anthropic
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "YOUR_API_KEY_HERE")
MODEL = "claude-3-5-sonnet-20241022"
MCP_SERVER_PATH = os.path.join(os.path.dirname(__file__), "../mcp_server/server.py")

# ── Prompt Engineering ────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert UI analyst agent. You have access to tools that let you
read local mock UI files and analyse their structure.

Your job is to answer user questions about UI interfaces by:
1. Using tools to read and inspect the relevant UI files
2. Thinking step-by-step about what the user needs
3. Generating structured, numbered procedural instructions

CRITICAL OUTPUT RULES:
- Always call a tool first before answering questions about UI files
- Structure final answers as a JSON object with this exact schema:
  {
    "reasoning_steps": ["step1", "step2", ...],
    "instructions": [
      {"step": 1, "action": "ACTION_VERB", "description": "Detailed human-readable instruction"},
      ...
    ]
  }
- Generate EXACTLY 7 instructions when asked for a procedural task
- Base every instruction on evidence from the UI file — never hallucinate elements

FEW-SHOT EXAMPLE:
User: "How do I submit this form?"
Thought: I need to read the UI file to find the submit button.
Tool call: get_interactive_elements("login_context.json")
Observation: Found input#login-button with type=submit value="Login"
Answer:
{
  "reasoning_steps": [
    "Read the UI file to identify interactive elements",
    "Located a submit input with id=login-button",
    "Mapped user intent 'submit form' to clicking the Login button"
  ],
  "instructions": [
    {"step": 1, "action": "NAVIGATE", "description": "Open https://www.saucedemo.com/ in your browser"},
    ...
    {"step": 7, "action": "CLICK", "description": "Click the Login button (id='login-button') to submit"}
  ]
}"""

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(title="AI UI Analysis Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Request / Response schemas ────────────────────────────────────────────────
class TaskRequest(BaseModel):
    prompt: str
    ui_file: str = "login_context.json"


class HealthResponse(BaseModel):
    status: str
    model: str


# ── MCP tool definitions (sent to Claude so it knows what tools exist) ────────
MCP_TOOLS = [
    {
        "name": "list_mock_ui_files",
        "description": "List all available mock UI files in the sandbox directory.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_mock_ui",
        "description": "Read the full contents of a mock UI file (JSON/XML).",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "e.g. 'login_context.json'"}
            },
            "required": ["filename"],
        },
    },
    {
        "name": "get_interactive_elements",
        "description": "Return only interactive elements (inputs, buttons, forms) from a JSON UI file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"}
            },
            "required": ["filename"],
        },
    },
    {
        "name": "find_element_by_attribute",
        "description": "Find elements by attribute value (e.g. id='login-button').",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "attribute": {"type": "string", "description": "e.g. 'id', 'placeholder', 'type'"},
                "value": {"type": "string"},
            },
            "required": ["filename", "attribute", "value"],
        },
    },
]


# ── Local MCP tool executor (simulated — calls server.py functions directly) ──
def execute_mcp_tool(tool_name: str, tool_input: dict) -> str:
    """
    In production this would communicate with the MCP server over stdio/SSE.
    For this prototype we import and call the functions directly.
    """
    import sys
    sys.path.insert(0, os.path.dirname(MCP_SERVER_PATH))

    from server import list_mock_ui_files, read_mock_ui, get_interactive_elements, find_element_by_attribute  # type: ignore

    dispatch = {
        "list_mock_ui_files": lambda: list_mock_ui_files(),
        "read_mock_ui": lambda: read_mock_ui(**tool_input),
        "get_interactive_elements": lambda: get_interactive_elements(**tool_input),
        "find_element_by_attribute": lambda: find_element_by_attribute(**tool_input),
    }

    fn = dispatch.get(tool_name)
    if fn is None:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    return json.dumps(fn(), ensure_ascii=False)


# ── Agentic loop (streaming SSE) ──────────────────────────────────────────────
async def run_agent(prompt: str, ui_file: str) -> AsyncIterator[str]:
    """
    Implements the Think → Act → Observe agentic loop.
    Yields Server-Sent Event (SSE) strings consumed by the React frontend.
    """

    messages = [{"role": "user", "content": f"UI file context: {ui_file}\n\nUser request: {prompt}"}]

    def sse(event_type: str, data: dict) -> str:
        return f"data: {json.dumps({'type': event_type, **data})}\n\n"

    yield sse("status", {"message": "Agent initialised. Starting reasoning loop..."})

    # Agentic loop — max 5 iterations to prevent infinite loops
    for iteration in range(5):
        yield sse("status", {"message": f"Iteration {iteration + 1}: calling Claude..."})

        # Call Claude (non-streaming for simplicity in tool-use loop)
        response = await asyncio.to_thread(
            client.messages.create,
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=MCP_TOOLS,
            messages=messages,
        )

        # Collect text and tool-use blocks
        tool_calls = []
        text_parts = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(block)

        # Stream any reasoning text the model produced
        if text_parts:
            combined_text = "\n".join(text_parts)
            yield sse("thought", {"content": combined_text})

        # If no tool calls, we have the final answer
        if response.stop_reason == "end_turn" or not tool_calls:
            final_text = "\n".join(text_parts)
            # Try to extract JSON from the response
            try:
                # Find JSON block in text
                start = final_text.find("{")
                end = final_text.rfind("}") + 1
                if start != -1 and end > start:
                    parsed = json.loads(final_text[start:end])
                    yield sse("final_answer", {"structured": parsed, "raw": final_text})
                else:
                    yield sse("final_answer", {"structured": None, "raw": final_text})
            except json.JSONDecodeError:
                yield sse("final_answer", {"structured": None, "raw": final_text})
            break

        # Execute each tool call and collect results
        tool_results = []
        for tc in tool_calls:
            yield sse("tool_call", {
                "tool": tc.name,
                "input": tc.input,
                "message": f"Calling tool: {tc.name}({tc.input})"
            })

            result_str = await asyncio.to_thread(execute_mcp_tool, tc.name, tc.input)
            result_data = json.loads(result_str)

            yield sse("tool_result", {
                "tool": tc.name,
                "result_preview": result_str[:300] + ("..." if len(result_str) > 300 else ""),
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result_str,
            })

        # Append assistant turn + tool results to message history
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    yield sse("done", {"message": "Agent task complete."})


# ── API Endpoints ─────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "model": MODEL}


@app.post("/api/v1/agent/task")
async def execute_task(request: TaskRequest):
    """
    Main endpoint. Streams SSE events representing the agent's thought process
    and final structured answer.
    """
    return StreamingResponse(
        run_agent(request.prompt, request.ui_file),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/v1/ui-files")
async def list_ui_files():
    """List available mock UI files for the frontend file picker."""
    from server import list_mock_ui_files  # type: ignore
    import sys
    sys.path.insert(0, os.path.dirname(MCP_SERVER_PATH))
    from server import list_mock_ui_files
    return list_mock_ui_files()