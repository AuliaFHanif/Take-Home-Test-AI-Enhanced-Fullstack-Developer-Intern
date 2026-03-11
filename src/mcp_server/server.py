"""
MCP Server for UI File Analysis
Provides tools for the AI agent to securely read local mock UI files.
Uses FastMCP to auto-generate JSON-RPC boilerplate from docstrings.
"""

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── Security: restrict access to the mock_ui sandbox directory only ──────────
BASE_DIR = Path(__file__).parent.parent / "mock_ui"

mcp = FastMCP("ui-analysis-server")


def _safe_path(filename: str) -> Path:
    """
    Resolve a filename relative to BASE_DIR and raise if path traversal
    is attempted (e.g. '../../etc/passwd').
    """
    resolved = (BASE_DIR / filename).resolve()
    if not str(resolved).startswith(str(BASE_DIR.resolve())):
        raise PermissionError(f"Access denied: '{filename}' is outside the sandbox.")
    return resolved


# ── Tool 1: List available mock UI files ─────────────────────────────────────
@mcp.tool()
def list_mock_ui_files() -> dict[str, Any]:
    """
    List all available mock UI files in the sandbox directory.
    Returns a dictionary with a 'files' key containing filenames and their sizes.
    Use this tool first to discover what UI files can be read.
    """
    if not BASE_DIR.exists():
        return {"files": [], "error": f"Mock UI directory not found at {BASE_DIR}"}

    files = []
    for f in BASE_DIR.iterdir():
        if f.suffix in {".json", ".xml", ".html"}:
            files.append({"name": f.name, "size_bytes": f.stat().st_size})

    return {"files": files, "sandbox_dir": str(BASE_DIR)}


# ── Tool 2: Read a mock UI file ───────────────────────────────────────────────
@mcp.tool()
def read_mock_ui(filename: str) -> dict[str, Any]:
    """
    Read the contents of a specific mock UI file from the secure sandbox.
    Supports JSON and XML formats. Returns parsed content for JSON files
    and raw text for XML/HTML files.

    Args:
        filename: The name of the file to read (e.g. 'login_context.json').
                  Must be a file that exists inside the mock_ui sandbox.
    """
    try:
        path = _safe_path(filename)
    except PermissionError as e:
        return {"error": str(e)}

    if not path.exists():
        return {"error": f"File '{filename}' not found. Use list_mock_ui_files() to see available files."}

    raw = path.read_text(encoding="utf-8")

    if path.suffix == ".json":
        try:
            content = json.loads(raw)
            return {"filename": filename, "format": "json", "content": content}
        except json.JSONDecodeError as e:
            return {"filename": filename, "format": "json", "error": f"Invalid JSON: {e}", "raw": raw}

    # XML / HTML — return as raw text
    return {"filename": filename, "format": path.suffix.lstrip("."), "content": raw}


# ── Tool 3: Query interactive elements from a JSON UI file ───────────────────
@mcp.tool()
def get_interactive_elements(filename: str) -> dict[str, Any]:
    """
    Parse a JSON mock UI file and return only the interactive elements
    (inputs, buttons, forms, links, selects, textareas).
    Useful when the agent only needs to locate actionable UI components.

    Args:
        filename: The JSON mock UI file to analyse (e.g. 'login_context.json').
    """
    result = read_mock_ui(filename)
    if "error" in result:
        return result

    if result.get("format") != "json":
        return {"error": "get_interactive_elements only supports JSON UI files."}

    INTERACTIVE_TAGS = {"input", "button", "form", "a", "select", "textarea"}
    elements = result["content"]

    if not isinstance(elements, list):
        return {"error": "Unexpected JSON structure — expected a top-level array of elements."}

    interactive = [el for el in elements if el.get("tag", "").lower() in INTERACTIVE_TAGS]

    return {
        "filename": filename,
        "interactive_element_count": len(interactive),
        "interactive_elements": interactive,
    }


# ── Tool 4: Search elements by attribute ────────────────────────────────────
@mcp.tool()
def find_element_by_attribute(filename: str, attribute: str, value: str) -> dict[str, Any]:
    """
    Search a JSON mock UI file for elements that have a specific attribute
    matching a given value. Returns all matching elements.

    Args:
        filename:  The JSON mock UI file (e.g. 'login_context.json').
        attribute: The attribute key to search (e.g. 'id', 'class', 'placeholder', 'type').
        value:     The value to match against (case-insensitive partial match).
    """
    result = read_mock_ui(filename)
    if "error" in result:
        return result

    if result.get("format") != "json":
        return {"error": "find_element_by_attribute only supports JSON UI files."}

    elements = result["content"]
    if not isinstance(elements, list):
        return {"error": "Unexpected JSON structure — expected a top-level array."}

    matches = [
        el for el in elements
        if value.lower() in str(el.get(attribute, "")).lower()
    ]

    return {
        "filename": filename,
        "query": {"attribute": attribute, "value": value},
        "match_count": len(matches),
        "matches": matches,
    }


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()