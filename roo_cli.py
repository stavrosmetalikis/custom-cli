#!/usr/bin/env python3
"""
Roo CLI - A standalone, terminal-based AI coding agent
Connects to agentrouter.org with WAF bypass requirements
"""

import os
import sys
import json
import subprocess
import re
import time
import select
import urllib.parse
import html
import argparse
import datetime
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import httpx

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    # Load .env from the script's directory before changing directories
    script_dir = Path(__file__).parent.resolve()
    load_dotenv(script_dir / '.env')
    # Also load from current working directory (workspace) to allow overrides
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional

# ============================================================================
# Utility Functions
# ============================================================================

def print_colored(text: str, color: str = "white", end: str = "\n") -> None:
    """Print colored text to terminal."""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "magenta": "\033[95m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "reset": "\033[0m"
    }
    print(f"{colors.get(color, colors['white'])}{text}{colors['reset']}", end=end)


def print_separator() -> None:
    """Print a visual separator."""
    print_colored("-" * 60, "cyan")


def print_thinking(thinking: str, source: str = "reasoning") -> None:
    """Display the model's thinking/planning process."""
    if not thinking or not thinking.strip():
        return
    label = "💭 Thinking" if source == "reasoning" else "💬 Planning"
    print_colored(f"\n{label}:", "magenta")
    print_colored("-" * 40, "magenta")
    for line in thinking.strip().splitlines():
        print_colored(f"  {line}", "magenta")
    print_colored("-" * 40, "magenta")


# ============================================================================
# Environment Variable Validation
# ============================================================================

def validate_environment() -> Tuple[str, str, str]:
    """
    Validate that all required environment variables are set.
    
    Returns:
        Tuple of (api_key, proxy_url, model)
    
    Exits with error message if any required variable is missing.
    """
    # Attempt to load .env file if variables are missing (first-run fix)
    if not all([os.getenv("ROO_API_KEY"), os.getenv("ROO_PROXY_URL"), os.getenv("ROO_MODEL")]):
        try:
            from dotenv import load_dotenv
            script_dir = Path(__file__).parent.resolve()
            load_dotenv(script_dir / '.env')
            load_dotenv()  # Also load from current working directory
        except ImportError:
            pass  # python-dotenv not installed
    
    api_key = os.getenv("ROO_API_KEY")
    proxy_url = os.getenv("ROO_PROXY_URL")
    model = os.getenv("ROO_MODEL")
    
    missing_vars = []
    if not api_key:
        missing_vars.append("ROO_API_KEY")
    if not proxy_url:
        missing_vars.append("ROO_PROXY_URL")
    if not model:
        missing_vars.append("ROO_MODEL")
    
    if missing_vars:
        print_colored("\n[ERROR] Missing required environment variables:", "red")
        for var in missing_vars:
            print_colored(f"  - {var}", "yellow")
        print_colored("\nPlease set these environment variables before running the script.", "yellow")
        print_colored("\nYou can set them in your shell or create a .env file:", "cyan")
        print_colored("\n  ROO_API_KEY=your_api_key_here", "white")
        print_colored("  ROO_PROXY_URL=http://user:pass@proxy:port/", "white")
        print_colored("  ROO_MODEL=deepseek-v3.2", "white")
        print_colored("\nOr set them in your shell:", "cyan")
        print_colored("\n  export ROO_API_KEY=your_api_key_here", "white")
        print_colored("  export ROO_PROXY_URL=http://user:pass@proxy:port/", "white")
        print_colored("  export ROO_MODEL=deepseek-v3.2", "white")
        print_colored("\nOn Windows (PowerShell):", "cyan")
        print_colored("\n  $env:ROO_API_KEY='your_api_key_here'", "white")
        print_colored("  $env:ROO_PROXY_URL='http://user:pass@proxy:port/'", "white")
        print_colored("  $env:ROO_MODEL='deepseek-v3.2'", "white")
        sys.exit(1)
    
    return api_key, proxy_url, model

# Configuration
API_URL = "https://agentrouter.org/v1/chat/completions"
FALLBACK_MODEL = "glm-4.6"

# Get configuration from environment variables
ROO_API_KEY, ROO_PROXY_URL, ROO_MODEL = validate_environment()

# Spoofed Headers (MUST be exact - OS spoofing headers remain unchanged)
HEADERS = {
    "Host": "agentrouter.org",
    "Accept": "application/json",
    "X-Stainless-Retry-Count": "0",
    "X-Stainless-Lang": "js",
    "X-Stainless-Package-Version": "5.12.2",
    "X-Stainless-OS": "Windows",
    "X-Stainless-Arch": "x64",
    "X-Stainless-Runtime": "node",
    "X-Stainless-Runtime-Version": "v22.22.0",
    "Authorization": f"Bearer {ROO_API_KEY}",
    "HTTP-Referer": "https://github.com/RooVetGit/Roo-Cline",
    "X-Title": "Roo Code",
    "User-Agent": "RooCode/3.51.1",
    "sec-fetch-mode": "cors",
    "Content-Type": "application/json"
}

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0      # seconds — doubles each attempt (2, 4, 8)
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}  # HTTP codes worth retrying

# Context window management
CONTEXT_MAX_TOKENS = 60_000       # conservative limit (deepseek-v3.2 is 128k,
                                   # but leave headroom for tools + response)
CONTEXT_TRUNCATE_TO = 40_000      # truncate down to this when limit is hit
CONTEXT_CHARS_PER_TOKEN = 4       # rough approximation: 1 token ≈ 4 chars


def should_retry(exc: Exception = None, status_code: int = None) -> bool:
    """Return True if the error is transient and worth retrying."""
    if status_code and status_code in RETRY_STATUS_CODES:
        return True
    if exc and isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                                httpx.RemoteProtocolError, httpx.ReadError)):
        return True
    return False


def estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    """
    Estimate total token count of the message history.
    Uses character count / 4 as a rough approximation.
    Accounts for message content, tool call arguments, and tool results.
    """
    total_chars = 0
    for msg in messages:
        # Count content
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            # Some messages have content as a list of parts
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
                else:
                    total_chars += len(str(part))
        else:
            total_chars += len(content)

        # Count tool call arguments
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {})
            total_chars += len(fn.get("name", ""))
            total_chars += len(fn.get("arguments", ""))

        # Count role label overhead (small but consistent)
        total_chars += 16

    return total_chars // CONTEXT_CHARS_PER_TOKEN


def truncate_history(
    history: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Truncate history to fit within CONTEXT_TRUNCATE_TO tokens.

    Strategy:
    - Always preserve the system prompt (first message if role==system).
    - Remove the OLDEST non-system messages until under budget.
    - Never remove a message that is a flattened tool result (a user message
      whose content starts with "[System: You successfully invoked"). Removing
      these without their preceding assistant turn would leave the model seeing
      a tool result with no corresponding request, which confuses it badly.

    Prints a warning to the user when truncation occurs.
    """
    if estimate_tokens(history) <= CONTEXT_MAX_TOKENS:
        return history

    # Separate system prompt from the rest
    if history and history[0].get("role") == "system":
        system_msg = [history[0]]
        rest = list(history[1:])
    else:
        system_msg = []
        rest = list(history)

    _TOOL_RESULT_PREFIX = "[System: You successfully invoked"

    removed = 0
    while rest and estimate_tokens(system_msg + rest) > CONTEXT_TRUNCATE_TO:
        # Skip tool-result messages — removing them without their paired
        # assistant call would orphan the result and confuse the model.
        # Find the first removable message.
        remove_idx = None
        for i, msg in enumerate(rest):
            content = msg.get("content") or ""
            if isinstance(content, str) and content.startswith(_TOOL_RESULT_PREFIX):
                # This is a tool result — skip it AND its preceding assistant turn
                continue
            remove_idx = i
            break

        if remove_idx is None:
            # Everything left is tool results — cannot truncate further safely
            break

        rest.pop(remove_idx)
        removed += 1

    if removed:
        print_colored(
            f"\n[Context] Conversation history truncated: removed {removed} oldest "
            f"message(s) to stay within the context limit. "
            f"(~{estimate_tokens(system_msg + rest):,} tokens remaining)",
            "yellow"
        )
    elif not rest:
        print_colored(
            "\n[Context] Warning: context limit exceeded but cannot truncate "
            "further without losing the system prompt.",
            "red"
        )

    return system_msg + rest

# ============================================================================
# Session Management
# ============================================================================

SESSIONS_DIR = Path(__file__).parent.resolve() / "sessions"


def get_session_path(name: str) -> Path:
    return SESSIONS_DIR / name


def list_sessions() -> List[Dict[str, Any]]:
    """Return list of sessions sorted by last_saved descending."""
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for session_dir in sorted(SESSIONS_DIR.iterdir()):
        meta_file = session_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            sessions.append(meta)
        except Exception:
            pass
    sessions.sort(key=lambda s: s.get("last_saved", ""), reverse=True)
    return sessions


def load_session(name: str) -> Optional[Tuple[List, str, Path]]:
    """
    Load a session by name.
    Returns (history, mode_value, workspace_dir) or None if not found.
    """
    session_path = get_session_path(name)
    history_file = session_path / "history.json"
    meta_file = session_path / "meta.json"
    workspace_dir = session_path / "workspace"

    if not history_file.exists():
        return None
    try:
        with open(history_file, "r", encoding="utf-8") as f:
            history = json.load(f)
        mode_value = "code"
        if meta_file.exists():
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            mode_value = meta.get("mode", "code")
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return history, mode_value, workspace_dir
    except Exception as e:
        print_colored(f"[Error loading session '{name}': {e}]", "red")
        return None


def save_session(name: str, history: List, mode: "Mode",
                 workspace_dir: Path, total_steps: int) -> None:
    """Save session history and metadata."""
    session_path = get_session_path(name)
    session_path.mkdir(parents=True, exist_ok=True)

    # Save history
    history_file = session_path / "history.json"
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print_colored(f"\n[Warning] Failed to save history: {e}", "red")
        return

    # Save metadata
    meta_file = session_path / "meta.json"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    meta = {}
    if meta_file.exists():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    meta.update({
        "name": name,
        "mode": mode.value,
        "last_saved": now,
        "total_steps": total_steps,
        "workspace": str(workspace_dir.absolute()),
    })
    if "created" not in meta:
        meta["created"] = now
    try:
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        print_colored(f"\n[Warning] Failed to save metadata: {e}", "red")


def print_sessions_table() -> None:
    """Print a formatted table of all sessions."""
    sessions = list_sessions()
    if not sessions:
        print_colored("  No sessions found.", "yellow")
        return
    print_colored(f"\n  {'NAME':<20} {'MODE':<14} {'STEPS':<8} {'LAST SAVED'}", "cyan")
    print_colored("  " + "─" * 58, "cyan")
    for s in sessions:
        name = s.get("name", "?")[:19]
        mode = s.get("mode", "?")[:13]
        steps = str(s.get("total_steps", "?"))[:7]
        saved = s.get("last_saved", "?")[:19]
        print_colored(f"  {name:<20} {mode:<14} {steps:<8} {saved}", "white")
    print()


def make_session_name() -> str:
    """Generate a default session name from current datetime."""
    return datetime.datetime.now().strftime("session_%Y%m%d_%H%M%S")

# Tool Definitions (OpenAI Format - MUST match RooCode exactly)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use this to explore the project structure and understand what files are available. The recursive parameter controls whether to list files in subdirectories.\n\nParameters:\n- path: (optional) Path to the directory to list (relative to workspace). If not provided, lists the current workspace directory.\n- recursive: (optional) Whether to list files recursively in subdirectories. Default is false.\n\nExample: List top-level files\n{ \"path\": \".\", \"recursive\": false }\n\nExample: List all files recursively\n{ \"path\": \"src\", \"recursive\": true }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the directory to list (relative to workspace). If not provided, lists the current workspace directory."},
                    "recursive": {"type": "boolean", "description": "Whether to list files recursively in subdirectories. Default is false."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex pattern across all files in a directory. Use this to find specific code patterns, function names, or text across the codebase.\n\nParameters:\n- path: (optional) Path to the directory to search (relative to workspace). If not provided, searches the current workspace directory.\n- regex: (required) The regex pattern to search for.\n- file_pattern: (optional) File pattern to filter files (e.g., '*.py', '*.js'). If not provided, searches all files.\n\nExample: Search for function definitions\n{ \"path\": \"src\", \"regex\": \"def\\\\s+\\\\w+\", \"file_pattern\": \"*.py\" }\n\nExample: Search for TODO comments\n{ \"path\": \".\", \"regex\": \"TODO|FIXME\" }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the directory to search (relative to workspace). If not provided, searches the current workspace directory."},
                    "regex": {"type": "string", "description": "The regex pattern to search for."},
                    "file_pattern": {"type": "string", "description": "Optional file pattern to filter files (e.g., '*.py', '*.js'). If not provided, searches all files."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_code_definition_names",
            "description": "List code definitions (functions, classes, variables) in source files. Use this to understand the structure of code files and find specific definitions.\n\nParameters:\n- path: (optional) Path to the file or directory to analyze (relative to workspace). If a directory, analyzes all source files in it. If not provided, analyzes the current workspace directory.\n\nExample: List definitions in a file\n{ \"path\": \"src/app.py\" }\n\nExample: List definitions in a directory\n{ \"path\": \"src\" }\n\nSupported languages: Python (.py), JavaScript/TypeScript (.js, .ts, .jsx, .tsx), Java (.java), C/C++ (.c, .cpp, .h), Go (.go), Rust (.rs), Ruby (.rb), PHP (.php)",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file or directory to analyze (relative to workspace). If a directory, analyzes all source files in it."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "apply_diff",
            "description": "Apply a search/replace block to a file. This is MORE EFFICIENT than write_to_file for modifying existing files.\n\nCRITICAL FORMATTING RULES:\nYou MUST provide diff string in exact following format. Do not use line numbers. Ensure SEARCH block exactly matches existing file content, including indentation and whitespace.\n\n<<<<<<< SEARCH\ndef old_function():\n    print(\"old\")\n=======\ndef new_function():\n    print(\"new\")\n>>>>>>> REPLACE\n\nParameters:\n- path: (required) Path to file to modify.\n- diff: (required) The strictly formatted diff string.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file to modify."},
                    "diff": {"type": "string", "description": "The diff string in SEARCH/REPLACE format."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Request to execute a CLI command on the system. Use this when you need to perform system operations or run specific commands to accomplish any step in your user's task. You must tailor your command to the user's system and provide a clear explanation of what the command does. For command chaining, use the appropriate chaining syntax for the user's shell. Prefer to execute complex CLI commands over creating executable scripts, as they are more flexible and easier to run [truncated...]",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The CLI command to execute"},
                    "cwd": {"type": ["string", "null"], "description": "Optional working directory (relative or absolute)"},
                    "timeout": {"type": ["number", "null"], "description": "Timeout in seconds for long-running processes"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file and return its contents with line numbers. Use this when you need to examine the contents of a file to understand its structure, find specific code patterns, or gather information needed to accomplish your task.\n\nParameters:\n- path: (required) The path to the file to read (relative to workspace).\n- mode: (optional) The mode to read the file. Can be \"slice\" (default) or \"indentation\". In \"slice\" mode, you can specify offset and limit to read a specific range of lines. In \"indentation\" mode, you can specify an anchor_line to read a code block based on indentation.\n- offset: (optional) The 1-based line offset for slice mode (default: 1).\n- limit: (optional) The maximum number of lines to return for slice mode (default: 2000).\n- indentation: (optional) Options for indentation mode. Requires anchor_line. Can include max_levels (default: 0, unlimited), include_siblings (default: false), include_header (default: true), and max_lines (default: 100).\n\nExample: Read a file\n{ \"path\": \"src/app.py\" }\n\nExample: Read specific lines\n{ \"path\": \"src/app.py\", \"mode\": \"slice\", \"offset\": 10, \"limit\": 20 }\n\nExample: Read a function based on indentation\n{ \"path\": \"src/app.py\", \"mode\": \"indentation\", \"indentation\": { \"anchor_line\": 42, \"max_levels\": 2, \"include_siblings\": true } }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path to the file to read (relative to workspace)."},
                    "mode": {"type": "string", "description": "The mode to read the file. Can be \"slice\" (default) or \"indentation\"."},
                    "offset": {"type": "integer", "description": "The 1-based line offset for slice mode (default: 1)."},
                    "limit": {"type": "integer", "description": "The maximum number of lines to return for slice mode (default: 2000)."},
                    "indentation": {"type": "object", "description": "Options for indentation mode. Requires anchor_line."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_to_file",
            "description": "Request to write content to a file. This tool is primarily used for creating new files or for scenarios where a complete rewrite of an existing file is intentionally required. If the file exists, it will be overwritten. If it doesn't exist, it will be created. This tool will automatically create any directories needed to write the file.\n\n**Important:** You should prefer using other editing tools over write_to_file when making changes to existing files, since write_t [truncated...]",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path to the file to write (relative to workspace)."},
                    "content": {"type": "string", "description": "The complete file content to write."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_followup_question",
            "description": "Ask the user a question to gather additional information needed to complete the task. Use when you need clarification or more details to proceed effectively.\n\nParameters:\n- question: (required) A clear, specific question addressing the information needed\n- follow_up: (required) A list of 2-4 suggested answers. Suggestions must be complete, actionable answers without placeholders. Optionally include mode to switch modes (code/architect/etc.)\n\nExample: Asking for  [truncated...]",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "A clear, specific question addressing the information needed"},
                    "follow_up": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "mode": {"type": "string"}}}, "description": "A list of 2-4 suggested answers. Suggestions must be complete, actionable answers without placeholders. Optionally include mode to switch modes (code/architect/etc.)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "attempt_completion",
            "description": "After each tool use, the user will respond with the result of that tool use, i.e. if it succeeded or failed, along with any reasons for failure. Once you've received the results of tool uses and can confirm that the task is complete, use this tool to present the result of your work to the user. The user may respond with feedback if they are not satisfied with the result, which you can use to make improvements and try again.\n\nCRITICAL: Do NOT call this tool until you have explicitly completed and verified every single step requested by user in exact order. Calling this prematurely is a failure.\n\nIMPORTANT NOTE: This tool CANNOT be used  [truncated...]",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "The result of the task. Formulate this result in a way that is final and does not require further input from the user. Don't end your result with questions or offers for further assistance.\n\nExample: Completing after updating CSS\n{ \"result\": \"I've updated the CSS to use flexbox layout for better responsiveness\" }"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using DuckDuckGo. Returns a list of results with "
                "title, URL, and snippet for each. Use this to find documentation, "
                "look up error messages, find packages, or research any topic. "
                "Always prefer this over guessing when current information is needed.\n\n"
                "Parameters:\n"
                "- query: (required) The search query string.\n"
                "- max_results: (optional) Number of results to return. Default 8, max 20.\n\n"
                "Example:\n"
                '{ "query": "python httpx streaming docs", "max_results": 5 }'
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query string."
                    },
                    "max_results": {
                        "type": ["integer", "null"],
                        "description": "Number of results to return (default 8, max 20)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch the contents of a web page and return it as clean readable text. "
                "Use this after web_search to read the full content of a result URL, or "
                "to fetch any documentation page, GitHub file, or API reference directly.\n\n"
                "Parameters:\n"
                "- url: (required) The full URL to fetch.\n"
                "- max_chars: (optional) Maximum characters to return. Default 8000, max 32000.\n\n"
                "Example:\n"
                '{ "url": "https://www.python.org/doc/", "max_chars": 5000 }'
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch."
                    },
                    "max_chars": {
                        "type": ["integer", "null"],
                        "description": "Maximum characters to return (default 8000, max 32000)."
                    }
                },
                "required": ["url"]
            }
        }
    }
]

# ============================================================================
# Mode System
# ============================================================================

from enum import Enum

class Mode(Enum):
    ORCHESTRATOR = "orchestrator"
    ARCHITECT    = "architect"
    CODE         = "code"
    ASK          = "ask"
    DEBUG        = "debug"

# Tools available per mode (subset of TOOL_FUNCTIONS keys)
MODE_TOOLS = {
    Mode.ORCHESTRATOR: [],
    Mode.ARCHITECT: [
        "list_files", "read_file", "write_to_file", "ask_followup_question",
        "attempt_completion", "web_search", "web_fetch"
    ],
    Mode.CODE: [
        "list_files", "read_file", "write_to_file", "apply_diff",
        "execute_command", "search_files", "list_code_definition_names",
        "ask_followup_question", "attempt_completion", "web_search", "web_fetch"
    ],
    Mode.ASK: [
        "list_files", "read_file", "search_files", "list_code_definition_names",
        "ask_followup_question", "attempt_completion", "web_search", "web_fetch"
    ],
    Mode.DEBUG: [
        "list_files", "read_file", "write_to_file", "apply_diff",
        "execute_command", "search_files", "list_code_definition_names",
        "ask_followup_question", "attempt_completion", "web_search", "web_fetch"
    ],
}

MODE_LABELS = {
    Mode.ORCHESTRATOR: "Orchestrator",
    Mode.ARCHITECT:    "Architect",
    Mode.CODE:         "Code",
    Mode.ASK:          "Ask",
    Mode.DEBUG:        "Debug",
}

MODE_COLORS = {
    Mode.ORCHESTRATOR: "magenta",
    Mode.ARCHITECT:    "blue",
    Mode.CODE:         "green",
    Mode.ASK:          "cyan",
    Mode.DEBUG:        "red",
}

# ============================================================================
# System Prompt
# ============================================================================

def get_system_prompt(mode: Mode = Mode.ORCHESTRATOR) -> str:
    cwd = os.getcwd()
    workspace_note = f"Workspace: {cwd}. All file operations must stay within this directory."

    base = f"""You are Roo, an AI coding agent operating in {MODE_LABELS[mode]} mode.
{workspace_note}

CRITICAL RULE — MODE SWITCHING:
You can switch modes at any time by including this exact JSON on its own line in
your response (before any other text or tool calls):
    SWITCH_MODE: {{"mode": "<mode_name>"}}

Valid mode names: orchestrator, architect, code, ask, debug.

Switch rules:
- Always switch BEFORE attempting work that belongs to another mode.
- Never ask the user for permission to switch. Just switch.
- After switching, the next response will be in the new mode.
- You will see your current mode label in every turn.

CHAIN OF THOUGHT AND PACING RULES:
If user provides a multi-step task, you MUST execute it strictly one step at a time.
Do NOT combine steps or anticipate future steps. Wait for tool result of Step N before initiating Step N+1.
If asked to explain or describe something, you MUST output that text in your standard response content immediately. Do not defer explanations to attempt_completion tool.

MARKDOWN RULES:
Show filenames and code constructs as clickable links:
[`filename`](relative/path.ext) or [`function()`](file.py:line)
"""

    mode_instructions = {
        Mode.ORCHESTRATOR: """
ORCHESTRATOR MODE INSTRUCTIONS:
- Do NOT call list_files or any other tool. You have no useful tools. Switch modes immediately on your first response.

Your only job is to switch to the correct mode. Do it immediately.

User wants to build, code, create files, or run commands → switch to code mode
User has a bug or error → switch to debug mode
User asks a question → switch to ask mode
User wants system design → switch to architect mode

To switch, output this on its own line:
SWITCH_MODE: {"mode": "code"}
Replace "code" with the target mode name.
Rules:

Do NOT use any tools. Do NOT list files. Do NOT search the web.
Do NOT explain what you're about to do. Just switch.
Your entire response should be one line: SWITCH_MODE: {"mode": "code"}
If the workspace is empty, that is fine — switch to code to build it.
""",
        Mode.ARCHITECT: """
ARCHITECT MODE INSTRUCTIONS:
You are the system designer. Your job is to:
1. Design directory structures, module boundaries, and data flows.
2. Write specs, READMEs, and planning documents.
3. Make technology and architecture decisions.
4. You may read files and write documentation files.
5. Do NOT execute terminal commands or write implementation code.
6. When design work is complete, switch back to Orchestrator.
""",
        Mode.CODE: """
CODE MODE INSTRUCTIONS:
You are the implementer. Your job is to:
1. Write, create, and edit code files.
2. Run terminal commands to install dependencies, run scripts, compile, test.
3. Apply diffs to make targeted changes to existing files.
4. Focus purely on implementation — do not redesign or re-plan.
5. If you encounter an error or bug, switch to Debug mode.
6. Complete ALL implementation steps you were given before switching
   back to Orchestrator. Do not switch back after each file — finish
   the whole delegated task first.

CHAIN OF THOUGHT AND PACING RULES:
- Execute strictly one step at a time. Wait for the tool result of Step N before Step N+1.
- Do NOT combine steps or anticipate future steps.
- NEVER write analysis, reasoning, or planning as plain text before a tool call.
  If you need to think through a problem, put it in a SINGLE brief sentence in your
  response content (max 1 line), then IMMEDIATELY call the tool. No multi-paragraph
  explanations before tool calls — ever.
- After reading a file, your NEXT action must be a tool call (write/diff/execute).
  Do not summarize what you read. Act on it.
- If you encounter test failures: read the error → call apply_diff or write_to_file
  to fix it → call execute_command to re-run. No prose diagnosis between steps.
- If pytest shows an ImportError (cannot import name 'X' from 'module'):
  call read_file on that module to see what names exist, then either add the
  missing function with apply_diff OR fix the import in the test file — whichever
  makes more sense. Then re-run pytest.
- When pytest shows all tests PASSED: immediately call attempt_completion. Do NOT
  run extra verification steps, do NOT query the database, do NOT run main.py unless
  the task explicitly asked for it. Tests passing is the completion signal.
- When running Python one-liners with -c, you cannot use for/if/with statements
  separated by semicolons. Write a temp script file instead:
  echo "import sqlite3; conn=sqlite3.connect('x.db'); ..." > /tmp/q.py && python3 /tmp/q.py
""",
        Mode.ASK: """
ASK MODE INSTRUCTIONS:
You are the explainer. Your job is to:
1. Answer questions clearly and accurately.
2. Explain code, concepts, errors, and documentation.
3. Read files to provide grounded answers.
4. Do NOT modify any files or run any commands.
5. When the question is answered, switch back to Orchestrator.
""",
        Mode.DEBUG: """
DEBUG MODE INSTRUCTIONS:
You are the debugger. Your job is to:
1. Read error messages, logs, and stack traces carefully.
2. Identify the root cause of the problem.
3. Apply the minimal fix needed using apply_diff or write_to_file.
4. Run commands to verify the fix worked.
5. Do NOT refactor or add features — fix only what is broken.
6. When the bug is confirmed fixed, switch back to Orchestrator.

CRITICAL: Do NOT write multi-line prose diagnoses. The pattern is:
  read_file → apply_diff/write_to_file → execute_command (verify) → repeat if needed.
One brief sentence of context is fine. Paragraphs of analysis are NOT — act on the
error immediately with a tool call. Every response must end with a tool invocation.
""",
    }

    return base + mode_instructions[mode]


def get_tools_for_mode(mode: Mode) -> List[Dict[str, Any]]:
    """Return the TOOLS list filtered to only tools allowed in this mode."""
    allowed = set(MODE_TOOLS[mode])
    return [t for t in TOOLS if t["function"]["name"] in allowed]


def parse_mode_switch(content: str) -> Optional[Mode]:
    """Detect mode switch from explicit instruction or prose intent."""
    if not content:
        return None

    # 1. Explicit SWITCH_MODE instruction (strict)
    match = re.search(r'SWITCH_MODE:\s*\{"mode":\s*"(\w+)"\}', content)
    if not match:
        # Tolerant — handles missing closing brace or extra whitespace
        match = re.search(r'SWITCH_MODE[^"]*"(\w+)"', content)
    if match:
        mode_name = match.group(1).lower()
        for mode in Mode:
            if mode.value == mode_name:
                return mode

    # 2. Prose intent detection — model says it wants to switch
    # Only trigger on Orchestrator responses (checked by caller)
    content_lower = content.lower()

    # Patterns like "switch to code mode", "I need to switch to Code mode",
    # "let me switch to code", "switching to code mode now"
    prose_patterns = [
        (r'\bswitch\s+to\s+(code|architect|ask|debug|orchestrator)\b', 1),
        (r'\bswitching\s+to\s+(code|architect|ask|debug|orchestrator)\b', 1),
        (r'\b(code|architect|ask|debug|orchestrator)\s+mode\b', 1),
        (r'\buse\s+(code|architect|ask|debug|orchestrator)\s+mode\b', 1),
        (r'\benter\s+(code|architect|ask|debug|orchestrator)\s+mode\b', 1),
        (r'\bdelegate\s+to\s+(code|architect|ask|debug|orchestrator)\b', 1),
    ]

    for pattern, group in prose_patterns:
        match = re.search(pattern, content_lower)
        if match:
            mode_name = match.group(group).lower()
            for mode in Mode:
                if mode.value == mode_name:
                    return mode

    return None


def print_mode_switch(old_mode: Mode, new_mode: Mode) -> None:
    """Display a visual indicator when the mode changes."""
    old_label = MODE_LABELS[old_mode]
    new_label = MODE_LABELS[new_mode]
    new_color = MODE_COLORS[new_mode]
    print_colored(f"\n{'─' * 60}", new_color)
    print_colored(
        f"  ⟳ Mode Switch: {old_label} → {new_label}",
        new_color
    )
    print_colored(f"{'─' * 60}", new_color)


def print_separator() -> None:
    """Print a visual separator."""
    print_colored("-" * 60, "cyan")


def validate_path_in_workspace(path: str) -> bool:
    """Validate that a path is within the workspace directory."""
    workspace_dir = Path.cwd()
    try:
        target_path = Path(path).resolve()
        # Check if target_path is within workspace directory
        target_path.relative_to(workspace_dir)
        return True
    except ValueError:
        return False


# ============================================================================
# Ubuntu-Native Tool Implementations
# ============================================================================

def tool_execute_command(args: Dict[str, Any]) -> str:
    """Execute a CLI command on the system using bash."""
    command = args.get("command")
    cwd = args.get("cwd")
    timeout = args.get("timeout")
    
    if not command:
        return json.dumps({"error": "Missing required parameter: command"})
    
    try:
        working_dir = cwd if cwd else os.getcwd()
        print_colored(f"\n[Executing] {command}", "yellow")

        # Append a CWD sentinel to the command so we capture the shell's final
        # working directory in one execution — no re-running, no regex guessing.
        # The sentinel echoes after the main command completes (regardless of exit
        # code), so we always know where the shell ended up.
        _SENTINEL = "__ROO_CWD__"
        instrumented = f"{command}; echo {_SENTINEL}:$(pwd)"

        result = subprocess.run(
            instrumented,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout if timeout else 60
        )

        # Strip sentinel line from stdout before returning to model
        raw_stdout = result.stdout
        sentinel_prefix = f"{_SENTINEL}:"
        clean_lines = []
        final_cwd = None
        for line in raw_stdout.splitlines():
            if line.startswith(sentinel_prefix):
                final_cwd = line[len(sentinel_prefix):].strip()
            else:
                clean_lines.append(line)
        output = "\n".join(clean_lines)
        if output:
            output += "\n"
        if result.stderr:
            output += result.stderr

        # Sync Python process CWD to wherever the shell ended up
        if final_cwd and os.path.isdir(final_cwd) and final_cwd != os.getcwd():
            try:
                os.chdir(final_cwd)
            except OSError:
                pass

        return json.dumps({
            "success": True,
            "output": output,
            "returncode": result.returncode,
            "cwd": os.getcwd()
        })
    except subprocess.TimeoutExpired:
        return json.dumps({
            "success": False,
            "output": "Command timed out",
            "error": "timeout"
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "output": str(e),
            "error": type(e).__name__
        })


def tool_read_file(args: Dict[str, Any]) -> str:
    """Read a file and return its contents with line numbers."""
    path = args.get("path")
    mode = args.get("mode", "slice")
    offset = args.get("offset", 1)
    limit = args.get("limit", 2000)
    indentation = args.get("indentation", {})
    
    if not path:
        return json.dumps({"error": "Missing required parameter: path"})
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        full_path = Path(path)
        if not full_path.exists():
            return json.dumps({"error": f"File not found: {path}"})
        
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        if mode == "slice":
            # Slice mode: return specific range of lines
            start = max(0, offset - 1)
            end = min(len(lines), start + limit)
            output_lines = lines[start:end]
            
            output = []
            for i, line in enumerate(output_lines, start=start+1):
                output.append(f"{i:5d} | {line.rstrip()}")
            
            return json.dumps({
                "success": True,
                "content": "\n".join(output),
                "mode": "slice",
                "offset": offset,
                "limit": limit
            })
        
        elif mode == "indentation":
            # Indentation mode: return code block based on indentation
            anchor_line = indentation.get("anchor_line")
            if not anchor_line:
                return json.dumps({"error": "Missing required parameter: indentation.anchor_line"})
            
            max_levels = indentation.get("max_levels", 0)
            include_siblings = indentation.get("include_siblings", False)
            include_header = indentation.get("include_header", True)
            max_lines = indentation.get("max_lines", 100)
            
            if anchor_line < 1 or anchor_line > len(lines):
                return json.dumps({"error": f"Invalid anchor_line: {anchor_line}"})
            
            # Get indentation of anchor line
            anchor_indent = len(lines[anchor_line - 1]) - len(lines[anchor_line - 1].lstrip())
            
            # Find block boundaries
            start_line = anchor_line
            end_line = anchor_line
            
            # Find start (go up until indentation decreases)
            for i in range(anchor_line - 2, -1, -1):
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                if line_indent < anchor_indent:
                    start_line = i + 1
                    break
            
            # Find end (go down until indentation decreases below anchor)
            for i in range(anchor_line, len(lines)):
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                if line_indent < anchor_indent:
                    end_line = i
                    break
            
            # Include siblings if requested
            if include_siblings:
                # Find all lines at same indentation level
                sibling_lines = []
                for i in range(start_line, len(lines)):
                    line_indent = len(lines[i]) - len(lines[i].lstrip())
                    if line_indent == anchor_indent:
                        sibling_lines.append(i)
                
                if sibling_lines:
                    end_line = max(end_line, max(sibling_lines))
            
            # Apply max_levels limit
            if max_levels > 0:
                for i in range(start_line, end_line):
                    line_indent = len(lines[i]) - len(lines[i].lstrip())
                    if line_indent < anchor_indent - max_levels:
                        end_line = i
                        break
            
            # Apply max_lines limit
            end_line = min(end_line, start_line + max_lines - 1)
            
            # Include header if requested
            if include_header:
                # Find imports and module-level code
                header_end = start_line
                for i in range(start_line - 1, -1, -1):
                    if lines[i].strip().startswith(('import', 'from', 'class', 'def', '#')):
                        header_end = i + 1
                        break
                start_line = header_end
            
            output_lines = lines[start_line:end_line]
            
            output = []
            for i, line in enumerate(output_lines, start=start_line+1):
                output.append(f"{i:5d} | {line.rstrip()}")
            
            return json.dumps({
                "success": True,
                "content": "\n".join(output),
                "mode": "indentation",
                "offset": start_line + 1,
                "limit": end_line - start_line
            })
        
        else:
            return json.dumps({"error": f"Unknown mode: {mode}"})
    
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_write_to_file(args: Dict[str, Any]) -> str:
    """Write content to a file, creating directories as needed."""
    path = args.get("path")
    content = args.get("content")
    
    if not path:
        return json.dumps({"error": "Missing required parameter: path"})
    if content is None:
        return json.dumps({"error": "Missing required parameter: content"})
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        full_path = Path(path)
        # Create parent directories if they don't exist
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print_colored(f"\n[Written] {path}", "green")
        return json.dumps({
            "success": True,
            "path": path,
            "bytes_written": len(content.encode('utf-8'))
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_ask_followup_question(args: Dict[str, Any]) -> str:
    """Ask the user a question interactively."""
    question = args.get("question")
    follow_up = args.get("follow_up", [])
    
    if not question:
        return json.dumps({"error": "Missing required parameter: question"})
    
    try:
        print_colored(f"\n[Question] {question}", "yellow")
        
        if follow_up:
            print_colored("\nSuggested answers:", "cyan")
            for i, suggestion in enumerate(follow_up, 1):
                mode_info = f" (mode: {suggestion.get('mode')})" if suggestion.get('mode') else ""
                print(f"  {i}. {suggestion.get('text')}{mode_info}")
        
        print_colored("\nYour answer: ", "green", end="")
        answer = input().strip()
        
        return json.dumps({
            "success": True,
            "answer": answer
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_attempt_completion(args: Dict[str, Any]) -> str:
    """Present completion result to the user."""
    result = args.get("result")
    
    if not result:
        return json.dumps({"error": "Missing required parameter: result"})
    
    print_colored(f"\n[Task Complete] {result}", "green")
    print_separator()
    
    return json.dumps({
        "success": True,
        "result": result
    })


def tool_list_files(args: Dict[str, Any]) -> str:
    """List files in a directory with optional recursive listing."""
    path = args.get("path", ".")
    recursive = args.get("recursive", False)
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        target_path = Path(path)
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        if not target_path.is_dir():
            return json.dumps({"error": f"Path is not a directory: {path}"})
        
        files = []
        if recursive:
            # List all files recursively
            for item in target_path.rglob("*"):
                if item.is_file():
                    files.append(str(item.relative_to(target_path)))
        else:
            # List only top-level files and directories
            for item in sorted(target_path.iterdir()):
                if item.is_file():
                    files.append(str(item.name))
                elif item.is_dir():
                    files.append(str(item.name) + "/")
        
        return json.dumps({
            "success": True,
            "path": path,
            "recursive": recursive,
            "files": files
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_search_files(args: Dict[str, Any]) -> str:
    """Search for a regex pattern across files in a directory."""
    path = args.get("path", ".")
    regex_pattern = args.get("regex")
    file_pattern = args.get("file_pattern", "*")
    
    if not regex_pattern:
        return json.dumps({"error": "Missing required parameter: regex"})
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        import re
        target_path = Path(path)
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        # Compile regex pattern
        try:
            pattern = re.compile(regex_pattern)
        except re.error as e:
            return json.dumps({"error": f"Invalid regex pattern: {str(e)}"})
        
        results = []
        files_to_search = []
        
        # Collect files to search
        # Normalize file_pattern — strip leading * to avoid "**.ext"
        if file_pattern and file_pattern != "*":
            clean_pattern = file_pattern.lstrip("*")
            glob_pattern = f"*{clean_pattern}" if clean_pattern else "*"
        else:
            glob_pattern = "*"
        for item in target_path.rglob(glob_pattern):
            if item.is_file():
                files_to_search.append(item)
        
        # Search each file
        for file_path in files_to_search:
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        match = pattern.search(line)
                        if match:
                            rel_path = file_path.relative_to(target_path)
                            results.append({
                                "file": str(rel_path),
                                "line": line_num,
                                "match": match.group(0)
                            })
            except (IOError, UnicodeDecodeError):
                # Skip files that can't be read as text
                pass
        
        return json.dumps({
            "success": True,
            "path": path,
            "regex": regex_pattern,
            "file_pattern": file_pattern,
            "matches": results
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_list_code_definition_names(args: Dict[str, Any]) -> str:
    """List code definitions (functions, classes, variables) in source files."""
    path = args.get("path", ".")
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        target_path = Path(path)
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        # Language-specific patterns
        patterns = {
            ".py": [
                (r'^\s*def\s+(\w+)', 'function'),
                (r'^\s*def\s+self\.(\w+)', 'class_method'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*=\s*[^=\n]', 'variable')
            ],
            ".js": [
                (r'^\s*function\s+(\w+)', 'function'),
                (r'^\s*const\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*=\s*[^=\n]', 'variable')
            ],
            ".ts": [
                (r'^\s*function\s+(\w+)', 'function'),
                (r'^\s*const\s+(\w+)\s*:', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*:\s*[^=\n]', 'variable')
            ],
            ".jsx": [
                (r'^\s*function\s+(\w+)', 'function'),
                (r'^\s*const\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*=\s*[^=\n]', 'variable')
            ],
            ".tsx": [
                (r'^\s*function\s+(\w+)', 'function'),
                (r'^\s*const\s+(\w+)\s*:', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*:\s*[^=\n]', 'variable')
            ],
            ".java": [
                (r'^\s*public\s+class\s+(\w+)', 'class'),
                (r'^\s*private\s+class\s+(\w+)', 'class'),
                (r'^\s*protected\s+class\s+(\w+)', 'class'),
                (r'^\s*public\s+static\s+(\w+)\s+', 'variable'),
                (r'^\s*private\s+static\s+(\w+)\s+', 'variable'),
                (r'^\s*protected\s+static\s+(\w+)\s+', 'variable'),
                (r'^\s*public\s+(\w+)\s+\(', 'variable'),
                (r'^\s*private\s+(\w+)\s+\(', 'variable'),
                (r'^\s*protected\s+(\w+)\s+\(', 'variable'),
                (r'^\s*public\s+(\w+)\s+\s*\(', 'method'),
                (r'^\s*private\s+(\w+)\s+\s*\(', 'method'),
                (r'^\s*protected\s+(\w+)\s+\s*\(', 'method')
            ],
            ".c": [
                (r'^\s*int\s+(\w+)\s+\(', 'variable'),
                (r'^\s*void\s+(\w+)\s*\(', 'function'),
                (r'^\s*struct\s+(\w+)\s*{', 'struct')
            ],
            ".cpp": [
                (r'^\s*int\s+(\w+)\s+\(', 'variable'),
                (r'^\s*void\s+(\w+)\s*\(', 'function'),
                (r'^\s*class\s+(\w+)\s*{', 'class'),
                (r'^\s*struct\s+(\w+)\s*{', 'struct')
            ],
            ".h": [
                (r'^\s*int\s+(\w+)\s+\(', 'variable'),
                (r'^\s*void\s+(\w+)\s*\(', 'function'),
                (r'^\s*struct\s+(\w+)\s*{', 'struct')
            ],
            ".go": [
                (r'^\s*func\s+(\w+)\s*\(', 'function'),
                (r'^\s*type\s+(\w+)\s+struct', 'struct'),
                (r'^\s*var\s+(\w+)\s+', 'variable'),
                (r'^\s*const\s+(\w+)\s+', 'variable')
            ],
            ".rs": [
                (r'^\s*fn\s+(\w+)\s*\(', 'function'),
                (r'^\s*struct\s+(\w+)\s*{', 'struct'),
                (r'^\s*let\s+mut\s+(\w+)\s+', 'variable'),
                (r'^\s*const\s+(\w+)\s*:', 'variable')
            ],
            ".rb": [
                (r'^\s*def\s+(\w+)', 'function'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*=\s*[^=\n]', 'variable')
            ],
            ".php": [
                (r'^\s*function\s+(\w+)\s*\(', 'function'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*\$(\w+)\s*=', 'variable')
            ]
        }
        
        definitions = []
        files_to_scan = []
        
        # Collect files to scan
        if target_path.is_file():
            files_to_scan = [target_path]
        else:
            for ext in patterns.keys():
                files_to_scan.extend(target_path.rglob(f"*{ext}"))
        
        for file_path in files_to_scan:
            ext = file_path.suffix.lower()
            if ext not in patterns:
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        for pattern, def_type in patterns[ext]:
                            match = re.search(pattern, line)
                            if match:
                                name = match.group(1)
                                rel_path = file_path.relative_to(target_path.parent if target_path.is_file() else target_path)
                                definitions.append({
                                    "file": str(rel_path),
                                    "line": line_num,
                                    "name": name,
                                    "type": def_type
                                })
            except (IOError, UnicodeDecodeError):
                # Skip files that can't be read as text
                pass
        
        return json.dumps({
            "success": True,
            "path": path,
            "definitions": definitions
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_apply_diff(args: Dict[str, Any]) -> str:
    """Apply a SEARCH/REPLACE diff to a file without requiring line numbers."""
    path = args.get("path")
    diff = args.get("diff")
    
    if not path:
        return json.dumps({"error": "Missing required parameter: path"})
    if not diff:
        return json.dumps({"error": "Missing required parameter: diff"})
    
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        full_path = Path(path)
        if not full_path.exists():
            return json.dumps({"error": f"File not found: {path}"})
        
        with open(full_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
        
        # Parse SEARCH/REPLACE blocks using regex
        pattern = re.compile(
            r'<<<<<<< SEARCH\n(.*?)\n?=======\n(.*?)\n?>>>>>>> REPLACE',
            re.DOTALL
        )
        matches = pattern.findall(diff)
        
        if not matches:
            return json.dumps({"error": "No valid SEARCH/REPLACE blocks found. Ensure you are using exact <<<<<<< SEARCH, =======, and >>>>>>> REPLACE markers."})
        
        # Pre-validation pass: check ALL search blocks exist before applying any.
        # Without this, block 1 could succeed while block 2 fails, leaving the
        # file in a half-modified state and confusing the model on retry.
        for i, (search_block, _) in enumerate(matches):
            if search_block not in file_content:
                norm_search = '\n'.join([line.strip() for line in search_block.splitlines() if line.strip()])
                norm_content = '\n'.join([line.strip() for line in file_content.splitlines()])
                if norm_search in norm_content:
                    return json.dumps({
                        "error": f"Block {i+1}/{len(matches)}: search block found but whitespace/indentation did not match exactly. Please use the 'write_to_file' tool to rewrite the file completely to avoid corrupting indentation.",
                        "search_block_preview": search_block[:100] + "..."
                    })
                else:
                    return json.dumps({
                        "error": f"Block {i+1}/{len(matches)}: search block not found in file. No changes were made. Ensure exact whitespace and indentation.",
                        "search_block_preview": search_block[:100] + "..."
                    })

        # All blocks validated — now apply them in sequence
        modified_content = file_content
        applied_count = 0
        for search_block, replace_block in matches:
            modified_content = modified_content.replace(search_block, replace_block)
            applied_count += 1
                
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
            
        print_colored(f"\n[Diff Applied] {path} ({applied_count} blocks)", "green")
        return json.dumps({
            "success": True,
            "path": path,
            "blocks_applied": applied_count
        })
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_web_search(args: Dict[str, Any]) -> str:
    """Search the web using DuckDuckGo HTML endpoint (no API key required)."""
    query = args.get("query")
    max_results = min(int(args.get("max_results") or 8), 20)

    if not query:
        return json.dumps({"error": "Missing required parameter: query"})

    try:
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }

        with httpx.Client(proxy=ROO_PROXY_URL, timeout=15.0) as client:
            response = client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            raw_html = response.text

        # Extract results using regex — no external HTML parser needed
        results = []

        # DuckDuckGo HTML results are in <div class="result"> blocks
        result_blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            raw_html,
            re.DOTALL
        )
        snippet_blocks = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span|div)>',
            raw_html,
            re.DOTALL
        )

        for i, (href, title_html) in enumerate(result_blocks):
            if i >= max_results:
                break

            # Clean title
            title = re.sub(r'<[^>]+>', '', title_html)
            title = html.unescape(title).strip()

            # Resolve DuckDuckGo redirect URLs
            if href.startswith("//duckduckgo.com/l/?"):
                uddg_match = re.search(r'uddg=([^&]+)', href)
                if uddg_match:
                    href = urllib.parse.unquote(uddg_match.group(1))
            elif href.startswith("/"):
                href = "https://duckduckgo.com" + href

            # Get matching snippet
            snippet = ""
            if i < len(snippet_blocks):
                snippet = re.sub(r'<[^>]+>', '', snippet_blocks[i])
                snippet = html.unescape(snippet).strip()
                snippet = re.sub(r'\s+', ' ', snippet)

            if title and href.startswith("http"):
                results.append({
                    "index": i + 1,
                    "title": title,
                    "url": href,
                    "snippet": snippet
                })

        if not results:
            return json.dumps({
                "success": False,
                "error": "No results found. Try a different query.",
                "query": query
            })

        return json.dumps({
            "success": True,
            "query": query,
            "result_count": len(results),
            "results": results
        })

    except httpx.HTTPStatusError as e:
        return json.dumps({
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.reason_phrase}",
            "query": query
        })
    except httpx.RequestError as e:
        return json.dumps({
            "success": False,
            "error": f"Network error: {str(e)}",
            "query": query
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}",
            "query": query
        })


def tool_web_fetch(args: Dict[str, Any]) -> str:
    """Fetch a web page and return clean readable text."""
    url = args.get("url")
    max_chars = min(int(args.get("max_chars") or 8000), 32000)

    if not url:
        return json.dumps({"error": "Missing required parameter: url"})

    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": f"Invalid URL (must start with http/https): {url}"})

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,text/plain",
            "Accept-Language": "en-US,en;q=0.9",
        }

        with httpx.Client(proxy=ROO_PROXY_URL, timeout=20.0, follow_redirects=True) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            raw = response.text

        # If plain text (e.g. raw GitHub files), return directly
        if "text/plain" in content_type or url.endswith(
            (".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
             ".toml", ".cfg", ".ini", ".sh", ".rs", ".go")
        ):
            text = raw[:max_chars]
            return json.dumps({
                "success": True,
                "url": url,
                "content_type": "text",
                "char_count": len(text),
                "content": text
            })

        # Strip scripts, styles, nav, footer — noisy for LLMs
        raw = re.sub(r'<script[^>]*>.*?</script>', '', raw, flags=re.DOTALL)
        raw = re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=re.DOTALL)
        raw = re.sub(r'<nav[^>]*>.*?</nav>', '', raw, flags=re.DOTALL)
        raw = re.sub(r'<footer[^>]*>.*?</footer>', '', raw, flags=re.DOTALL)
        raw = re.sub(r'<header[^>]*>.*?</header>', '', raw, flags=re.DOTALL)

        # Convert common block elements to newlines for readability
        raw = re.sub(r'<br\s*/?>', '\n', raw)
        raw = re.sub(r'</(p|div|h[1-6]|li|tr|blockquote)>', '\n', raw)
        raw = re.sub(r'<hr\s*/?>', '\n---\n', raw)

        # Strip all remaining HTML tags
        text = re.sub(r'<[^>]+>', '', raw)

        # Decode HTML entities
        text = html.unescape(text)

        # Collapse excessive whitespace while preserving paragraph breaks
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = '\n'.join(line.strip() for line in text.splitlines())
        text = text.strip()

        # Truncate to max_chars
        truncated = len(text) > max_chars
        text = text[:max_chars]

        return json.dumps({
            "success": True,
            "url": url,
            "content_type": "html",
            "char_count": len(text),
            "truncated": truncated,
            "content": text
        })

    except httpx.HTTPStatusError as e:
        return json.dumps({
            "success": False,
            "error": f"HTTP error {e.response.status_code}: {e.response.reason_phrase}",
            "url": url
        })
    except httpx.RequestError as e:
        return json.dumps({
            "success": False,
            "error": f"Network error: {str(e)}",
            "url": url
        })
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"{type(e).__name__}: {str(e)}",
            "url": url
        })


# ============================================================================
# Tool Registry
# ============================================================================

TOOL_FUNCTIONS = {
    "list_files": tool_list_files,
    "search_files": tool_search_files,
    "list_code_definition_names": tool_list_code_definition_names,
    "apply_diff": tool_apply_diff,
    "execute_command": tool_execute_command,
    "read_file": tool_read_file,
    "write_to_file": tool_write_to_file,
    "ask_followup_question": tool_ask_followup_question,
    "attempt_completion": tool_attempt_completion,
    "web_search": tool_web_search,
    "web_fetch": tool_web_fetch
}


def execute_tool_call(tool_call: Dict[str, Any]) -> Tuple[str, str]:
    """
    Execute a tool call and return the tool name and result.
    
    Args:
        tool_call: A tool call dictionary from the API response
        
    Returns:
        Tuple of (tool_name, tool_result_json)
    """
    function = tool_call.get("function", {})
    tool_name = function.get("name", "")
    tool_args_str = function.get("arguments", "{}")
    
    # DEBUG: Log tool call details
    if os.getenv("ROO_DEBUG"):
        print_colored(f"[DEBUG] execute_tool_call: {tool_name}", "yellow")
        print_colored(f"[DEBUG] tool_args_str: {tool_args_str[:200]}...", "yellow")
    
    if not tool_name:
        return ("unknown", json.dumps({"error": "Tool name not found in tool call"}))
    
    if tool_name not in TOOL_FUNCTIONS:
        return (tool_name, json.dumps({"error": f"Unknown tool: {tool_name}"}))
    
    try:
        tool_args = json.loads(tool_args_str)
    except json.JSONDecodeError as e:
        if os.getenv("ROO_DEBUG"):
            print_colored(f"[DEBUG] JSON decode error: {e}", "red")
            print_colored(f"[DEBUG] Problematic JSON: {tool_args_str}", "red")
        # Give tool-specific guidance for apply_diff failures — the most common cause
        # is a large diff containing backticks, unescaped quotes, or newlines that
        # break the JSON encoding during streaming assembly.
        if tool_name == "apply_diff":
            hint = (
                f"Invalid JSON in apply_diff arguments: {str(e)}. "
                "This usually means the diff string contained backticks, unescaped quotes, "
                "or other characters that broke JSON encoding. "
                "Use write_to_file to rewrite the entire file instead — it is safer for large changes."
            )
        else:
            hint = f"Invalid JSON arguments for tool {tool_name}: {str(e)}"
        return (tool_name, json.dumps({"error": hint}))
    # Execute the tool function
    tool_func = TOOL_FUNCTIONS[tool_name]
    tool_result = tool_func(tool_args)
    
    if os.getenv("ROO_DEBUG"):
        print_colored(f"[DEBUG] Tool result length: {len(tool_result)} chars", "yellow")
        if len(tool_result) < 500:
            print_colored(f"[DEBUG] Tool result: {tool_result}", "yellow")
    
    return (tool_name, tool_result)

# ============================================================================
# Tool Flattening Bypass (CRITICAL)
# ============================================================================

def apply_tool_flattening_bypass_batch(history: List[Dict[str, Any]], tool_results: List[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    """
    Apply the tool flattening bypass for multiple tool results at once.

    The agentrouter.org proxy crashes on role:"tool" messages and tool_calls arrays.
    We work around this by:
      1. Finding the last assistant message that has tool_calls and patching it
         in-place to strip tool_calls (replacing with its plain-text content).
      2. Appending a single role:"user" message containing all tool results.

    Previously this rebuilt the entire history list on every call (O(n) copy per
    tool call = O(n²) total). Now we find the target index once and patch it
    directly, which is O(n) once regardless of history length.
    """
    # Find the LAST assistant message that has tool_calls
    assistant_msg_idx = -1
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "assistant" and history[i].get("tool_calls"):
            assistant_msg_idx = i
            break

    if assistant_msg_idx != -1:
        msg = history[assistant_msg_idx]
        original_content = (msg.get("content") or "").strip() or " "
        # Patch in-place: replace the dict with a flat version (no tool_calls)
        history[assistant_msg_idx] = {
            "role": "assistant",
            "content": original_content
        }

    # Build a single user message containing all tool results
    parts = []
    for tool_name, tool_result, tool_call_id in tool_results:
        parts.append(f"[System: You successfully invoked the '{tool_name}' tool via the native API. Result:]\n{tool_result}")

    combined = "\n\n".join(parts)
    combined += f"\n\n(System Reminder: You MUST continue using native JSON tool calls to execute your next action. Do not output your intended actions as plain text. Current working directory: {os.getcwd()})"

    history.append({"role": "user", "content": combined})
    return history


def apply_tool_flattening_bypass(history: List[Dict[str, Any]], tool_name: str, tool_result: str, tool_call_id: str = None) -> List[Dict[str, Any]]:
    """
    Apply the tool flattening bypass for a single tool result (legacy function).
    
    This is a wrapper around apply_tool_flattening_bypass_batch for backward compatibility.
    
    Args:
        history: The message history
        tool_name: The name of the tool that was executed
        tool_result: The result of the tool execution
        tool_call_id: The ID of the tool call (if known)
    
    Returns:
        Updated history with tool result message
    """
    return apply_tool_flattening_bypass_batch(history, [(tool_name, tool_result, tool_call_id)])


# ============================================================================
# API Communication
# ============================================================================

# ============================================================================
# API Communication
# ============================================================================

def _api_post_with_retry(payload: Dict[str, Any]) -> Optional[httpx.Response]:
    """
    POST to the API with exponential-backoff retry on transient errors.
    Returns the raw httpx.Response on success, or None after all retries fail.
    This is the single shared retry path — both streaming and non-streaming
    calls go through here so the retry logic only lives in one place.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = httpx.Client(
                proxy=ROO_PROXY_URL,
                timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0)
            )
            with client:
                if payload.get("stream"):
                    # For streaming callers: return the response object before reading
                    # so the caller can iterate lines. The client stays open for the
                    # duration of the with-block in the caller.
                    # We re-enter a stream context there; here we just open and return.
                    resp = client.send(
                        client.build_request("POST", API_URL, headers=HEADERS, json=payload),
                        stream=True
                    )
                else:
                    resp = client.post(API_URL, headers=HEADERS, json=payload)
                resp.raise_for_status()
                return resp  # success

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if attempt < MAX_RETRIES and should_retry(status_code=status):
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(f"\n[Retry {attempt}/{MAX_RETRIES}] HTTP {status} — retrying in {delay:.0f}s...", "yellow")
                time.sleep(delay)
                continue
            try:
                error_text = e.response.text
            except Exception:
                error_text = e.response.reason_phrase or "No error text available"
            print_colored(f"\n[HTTP Error] {status}: {error_text}", "red")
            return None

        except (httpx.TimeoutException, httpx.ConnectError,
                httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                label = "Timeout" if isinstance(e, httpx.TimeoutException) else "Network error"
                print_colored(f"\n[Retry {attempt}/{MAX_RETRIES}] {label} — retrying in {delay:.0f}s...", "yellow")
                time.sleep(delay)
                continue
            label = "Timeout" if isinstance(e, httpx.TimeoutException) else "Request Error"
            print_colored(f"\n[{label}] {str(e)}", "red")
            return None

        except Exception as e:
            print_colored(f"\n[Unexpected Error] {type(e).__name__}: {str(e)}", "red")
            return None

    return None  # exhausted all retries


def send_chat_request_stream(messages: List[Dict[str, Any]], model: str = ROO_MODEL, mode: Mode = Mode.ORCHESTRATOR) -> Optional[Dict[str, Any]]:
    """Send a chat completion request with streaming to the API."""
    tools = get_tools_for_mode(mode)
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7
    }
    if tools:
        payload["tools"] = tools

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Accumulators for the full response
            full_content = ""
            full_reasoning = ""
            tool_calls_map = {}  # Indexed by tool call index
            finish_reason = "stop"

            # Buffer for smoother streaming output
            content_buffer = ""
            BUFFER_SIZE = 12  # Print when buffer reaches this size

            with httpx.Client(proxy=ROO_PROXY_URL, timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0)) as client:
                with client.stream("POST", API_URL, headers=HEADERS, json=payload) as response:
                    response.raise_for_status()
                    
                    for line in response.iter_lines():
                        # Skip empty lines
                        if not line.strip():
                            continue
                        
                        # Check for stream terminator
                        if line.strip() == "data: [DONE]":
                            break
                        
                        # Strip "data: " prefix and parse JSON
                        if line.startswith("data: "):
                            json_str = line[6:]  # Remove "data: " prefix
                            try:
                                chunk = json.loads(json_str)
                            except json.JSONDecodeError:
                                continue
                            
                            # Extract delta from choices
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            
                            delta = choices[0].get("delta", {})
                            
                            # Accumulate content (buffer for smoother output)
                            content = delta.get("content")
                            if content:
                                full_content += content
                                content_buffer += content
                                
                                # Print buffer when it reaches size threshold or has natural boundary
                                should_flush = False
                                if len(content_buffer) >= BUFFER_SIZE:
                                    should_flush = True
                                elif content.endswith(' ') or content.endswith('\n'):
                                    should_flush = True
                                
                                if should_flush:
                                    content_buffer = ""  # just discard the buffer, don't print
                            
                            # Accumulate reasoning (don't print yet)
                            reasoning = delta.get("reasoning_content")
                            if reasoning:
                                full_reasoning += reasoning
                            
                            # Accumulate tool calls
                            tool_calls = delta.get("tool_calls", [])
                            for tool_call in tool_calls:
                                index = tool_call.get("index")
                                if index is None:
                                    continue
                                
                                if index not in tool_calls_map:
                                    # First chunk for this tool call - initialize
                                    tool_calls_map[index] = {
                                        "id": tool_call.get("id", ""),
                                        "type": tool_call.get("type", "function"),
                                        "function": {
                                            "name": tool_call.get("function", {}).get("name", ""),
                                            "arguments": ""
                                        }
                                    }
                                
                                # Append arguments (streamed incrementally)
                                func_args = tool_call.get("function", {}).get("arguments", "")
                                if func_args:
                                    tool_calls_map[index]["function"]["arguments"] += func_args
                            
                            # Capture finish reason from last chunk
                            chunk_finish_reason = choices[0].get("finish_reason")
                            if chunk_finish_reason:
                                finish_reason = chunk_finish_reason
                    
                    content_buffer = ""  # discard
                    
                    # Strip <think> blocks that leaked into standard content
                    full_content = re.sub(r'<think>.*?</think>', '', full_content, flags=re.DOTALL)
                    # Also catch dangling closing tags just in case
                    full_content = full_content.replace('</think>', '')
                    
                    # Strip SWITCH_MODE patterns from full_content
                    full_content_cleaned = re.sub(
                        r'SWITCH_MODE:\s*\{"mode":\s*"\w+"\}\n?', '', full_content
                    ).strip()
                    
                    # Print label + content together only if non-empty after SWITCH_MODE removal
                    if full_content_cleaned:
                        print_colored(f"\nRoo({MODE_LABELS[mode]}): ", MODE_COLORS[mode], end="")
                        print(full_content_cleaned)
                    
                    # Print reasoning if present
                    if full_reasoning:
                        print_thinking(full_reasoning, source="reasoning")
                    
                    # Reconstruct tool_calls list from map (sorted by index)
                    tool_calls_list = []
                    if tool_calls_map:
                        for index in sorted(tool_calls_map.keys()):
                            tool_calls_list.append(tool_calls_map[index])
                    
                    # DEBUG: Log tool calls
                    if os.getenv("ROO_DEBUG"):
                        print_colored(f"\n[DEBUG] tool_calls_map keys: {list(tool_calls_map.keys())}", "yellow")
                        print_colored(f"[DEBUG] tool_calls_list length: {len(tool_calls_list)}", "yellow")
                        if tool_calls_list:
                            for tc in tool_calls_list:
                                print_colored(f"[DEBUG] Tool call: {tc.get('function', {}).get('name', 'unknown')}", "yellow")
                    
                    # Build the response dict in the same shape as non-streaming
                    message_dict = {
                        "role": "assistant",
                        "content": full_content,
                        "reasoning_content": full_reasoning
                    }
                    
                    # Only include tool_calls if there are any
                    if tool_calls_list:
                        message_dict["tool_calls"] = tool_calls_list
                    elif os.getenv("ROO_DEBUG"):
                        print_colored("[DEBUG] No tool_calls_list - tool_calls not included in message", "yellow")
                    
                    reconstructed_response = {
                        "choices": [{
                            "message": message_dict,
                            "finish_reason": finish_reason
                        }]
                    }
                    
                    return reconstructed_response  # success — exit immediately
                    
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if attempt < MAX_RETRIES and should_retry(status_code=status):
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(f"\n[Retry {attempt}/{MAX_RETRIES}] HTTP {status} — retrying in {delay:.0f}s...", "yellow")
                time.sleep(delay)
                continue
            try:
                error_text = e.response.text
            except Exception:
                error_text = e.response.reason_phrase or "No error text available"
            print_colored(f"\n[HTTP Error] {status}: {error_text}", "red")
            return None
        except (httpx.TimeoutException, httpx.ConnectError,
                httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                label = "Timeout" if isinstance(e, httpx.TimeoutException) else "Network error"
                print_colored(f"\n[Retry {attempt}/{MAX_RETRIES}] {label} — retrying in {delay:.0f}s...", "yellow")
                time.sleep(delay)
                continue
            label = "Timeout" if isinstance(e, httpx.TimeoutException) else "Request Error"
            print_colored(f"\n[{label}] {str(e)}", "red")
            return None
        except Exception as e:
            print_colored(f"\n[Unexpected Error] {type(e).__name__}: {str(e)}", "red")
            return None

    return None  # exhausted all retries


# ============================================================================
# Main Agent Loop
# ============================================================================

def main():
    """Main agent loop."""

    # ── CLI argument parsing ──────────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="roo",
        description="Roo CLI - AI Coding Agent",
        add_help=True,
    )
    parser.add_argument(
        "--session", "-s",
        metavar="NAME",
        help="Resume or create a named session (e.g. --session myproject)",
    )
    parser.add_argument(
        "--new", "-n",
        action="store_true",
        help="Force a new session even if a named session exists",
    )
    parser.add_argument(
        "--list-sessions", "-l",
        action="store_true",
        help="List all saved sessions and exit",
    )
    args = parser.parse_args()

    # Handle --list-sessions
    if args.list_sessions:
        print_colored("\n  Saved Sessions:", "cyan")
        print_sessions_table()
        sys.exit(0)

    # ── Banner ────────────────────────────────────────────────────────
    current_mode = Mode.ORCHESTRATOR
    print_colored("=" * 60, "cyan")
    print_colored("  Roo CLI - AI Coding Agent", "cyan")
    print_colored("=" * 60, "cyan")
    print_colored(f"  Model: {ROO_MODEL}", "white")
    print_colored(f"  Mode:  {MODE_LABELS[current_mode]}", MODE_COLORS[current_mode])
    print_colored("=" * 60, "cyan")
    print_colored("Type 'exit' or 'quit' to exit", "yellow")
    print_colored("Commands: /mode <name>  /modes  /clear  /undo  /sessions", "yellow")
    print()

    # ── Session setup ─────────────────────────────────────────────────
    session_name = args.session or make_session_name()
    session_path = get_session_path(session_name)
    workspace_dir = session_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    history = None

    # Try to resume if --session was given and --new was NOT set
    if args.session and not args.new:
        result = load_session(session_name)
        if result:
            loaded_history, mode_value, workspace_dir = result
            # Restore mode
            for m in Mode:
                if m.value == mode_value:
                    current_mode = m
                    break
            history = loaded_history
            # Always refresh the system prompt for the restored mode
            if history and history[0].get("role") == "system":
                history[0]["content"] = get_system_prompt(current_mode)
            print_colored(f"  Session: {session_name} (resumed)", "green")
            # Print session info
            sessions = list_sessions()
            for s in sessions:
                if s.get("name") == session_name:
                    print_colored(
                        f"  Steps:   {s.get('total_steps', 0)}  |  "
                        f"Saved: {s.get('last_saved', 'unknown')}",
                        "white"
                    )
                    break
        else:
            print_colored(f"  Session: {session_name} (new)", "cyan")
    else:
        if args.new and args.session:
            print_colored(f"  Session: {session_name} (new, forced)", "cyan")
        elif args.session:
            print_colored(f"  Session: {session_name} (new)", "cyan")
        else:
            print_colored(f"  Session: {session_name} (auto)", "cyan")

    # Fresh history if nothing loaded
    if history is None:
        history = [{"role": "system", "content": get_system_prompt(current_mode)}]

    os.chdir(workspace_dir)
    print_colored(f"  Workspace: {workspace_dir.absolute()}", "white")
    print_colored("=" * 60, "cyan")

    # ── Helpers defined inside main ───────────────────────────────────
    def update_system_message(history, mode):
        new_prompt = get_system_prompt(mode)
        if history and history[0].get("role") == "system":
            history[0]["content"] = new_prompt
        else:
            history.insert(0, {"role": "system", "content": new_prompt})
        return history

    def do_save():
        save_session(session_name, history, current_mode, workspace_dir, total_steps)

    # ── State ─────────────────────────────────────────────────────────
    total_steps = 0
    pending_rerun = False
    # undo_stack: list of history snapshots before each user turn
    undo_stack: List[List] = []

    # ── Main loop ─────────────────────────────────────────────────────
    try:
        while True:
            try:
                if pending_rerun:
                    pending_rerun = False
                    # Mode switch just happened. History ends with the user's task.
                    # Skip input() and run the inner loop directly in the new mode.
                else:
                    mode_label = MODE_LABELS[current_mode]
                    print_colored(f"\nYou({mode_label}): ", MODE_COLORS[current_mode], end="")
                    sys.stdout.flush()

                    first_line = sys.stdin.readline()
                    if not first_line:
                        raise EOFError

                    lines = [first_line.rstrip('\n')]
                    while True:
                        try:
                            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if not ready:
                                break
                            next_line = sys.stdin.readline()
                            if not next_line:
                                break
                            lines.append(next_line.rstrip('\n'))
                        except Exception:
                            break

                    user_input = '\n'.join(lines).strip()

                    if user_input.lower() in ['exit', 'quit', 'q']:
                        print_colored("\nGoodbye!", "cyan")
                        break

                    if not user_input:
                        continue

                    # ── Slash commands ────────────────────────────────
                    if user_input.startswith("/mode "):
                        requested = user_input[6:].strip().lower()
                        matched = None
                        for m in Mode:
                            if m.value.strip() == requested:
                                matched = m
                                break
                        if matched:
                            if matched != current_mode:
                                print_mode_switch(current_mode, matched)
                                current_mode = matched
                                history = update_system_message(history, current_mode)
                            else:
                                print_colored(f"  Already in {MODE_LABELS[current_mode]} mode.", "yellow")
                        else:
                            valid = ", ".join(m.value for m in Mode)
                            print_colored(f"  Unknown mode. Valid modes: {valid}", "red")
                        continue

                    if user_input.strip() == "/modes":
                        print_colored("\n  Available modes:", "cyan")
                        for m in Mode:
                            marker = " ◄ current" if m == current_mode else ""
                            print_colored(f"    /mode {m.value}{marker}", MODE_COLORS[m])
                        continue

                    if user_input.strip() == "/clear":
                        undo_stack.append([msg.copy() for msg in history])
                        history = [{"role": "system", "content": get_system_prompt(current_mode)}]
                        total_steps = 0
                        print_colored("\n[Conversation cleared. Use /undo to restore.]", "green")
                        continue

                    if user_input.strip() == "/undo":
                        if undo_stack:
                            history = undo_stack.pop()
                            # Refresh system prompt for current mode
                            if history and history[0].get("role") == "system":
                                history[0]["content"] = get_system_prompt(current_mode)
                            print_colored(
                                f"\n[Undone. History restored to {len(history)-1} "
                                f"messages. ({len(undo_stack)} undo levels remaining)]",
                                "green"
                            )
                            do_save()
                        else:
                            print_colored("\n[Nothing to undo.]", "yellow")
                        continue

                    if user_input.strip() == "/sessions":
                        print_colored("\n  Saved Sessions:", "cyan")
                        print_sessions_table()
                        print_colored(
                            f"  Current: {session_name}  |  "
                            f"Resume with: python roo_cli.py --session <name>",
                            "yellow"
                        )
                        continue

                    if user_input.strip() == "/session":
                        print_colored(f"\n  Current session: {session_name}", "cyan")
                        print_colored(f"  Workspace: {workspace_dir.absolute()}", "white")
                        print_colored(f"  Steps: {total_steps}  |  Mode: {MODE_LABELS[current_mode]}", "white")
                        print_colored(f"  History: {len(history)-1} messages", "white")
                        continue

                    # ── Save undo snapshot before each user turn ──────
                    undo_stack.append([msg.copy() for msg in history])
                    # Keep at most 20 undo levels
                    if len(undo_stack) > 20:
                        undo_stack.pop(0)

                    # Add user message to history
                    history.append({"role": "user", "content": user_input})

                # ── Agent inner loop ──────────────────────────────────
                max_iterations = int(os.getenv("ROO_MAX_ITERATIONS", "100"))
                iteration = 0
                consecutive_intercepts = 0

                while iteration < max_iterations:
                    iteration += 1
                    total_steps += 1
                    print_colored(f"\n[Step {total_steps}]", "yellow", end=" ")

                    # Truncate history if needed before API request
                    history = truncate_history(history)

                    # Send request to API (with streaming)
                    response = send_chat_request_stream(history, mode=current_mode)

                    if not response:
                        print_colored(
                            "\n[Failed] Could not reach API after all retry attempts. "
                            "Check your connection or try again.", "red"
                        )
                        # Only remove the last message on the first iteration (original
                        # user message). On later iterations the last message is an
                        # injected tool-result — popping it would corrupt history.
                        if iteration == 1:
                            history.pop()
                        break

                    # Extract assistant message
                    choices = response.get("choices", [])
                    if not choices:
                        print_colored("\nNo response choices received.", "red")
                        break

                    assistant_message = choices[0].get("message", {})
                    assistant_content = assistant_message.get("content", "")
                    tool_calls = assistant_message.get("tool_calls", [])

                    # DEBUG: Log what we received
                    if os.getenv("ROO_DEBUG"):
                        print_colored(f"\n[DEBUG] assistant_content length: {len(assistant_content)}", "yellow")
                        print_colored(f"[DEBUG] tool_calls count: {len(tool_calls)}", "yellow")
                        if tool_calls:
                            for tc in tool_calls:
                                print_colored(f"[DEBUG] Tool call in response: {tc.get('function', {}).get('name', 'unknown')}", "yellow")

                    # Check for mode switch instruction
                    mode_switched = False
                    requested_mode = parse_mode_switch(assistant_content)
                    if requested_mode and requested_mode != current_mode:
                        print_mode_switch(current_mode, requested_mode)
                        current_mode = requested_mode
                        history = update_system_message(history, current_mode)
                        # Strip the SWITCH_MODE line from content before displaying
                        assistant_content = re.sub(
                            r'SWITCH_MODE:\s*\{"mode":\s*"\w+"\}\n?', '', assistant_content
                        ).strip()
                        # Update assistant message with cleaned content
                        assistant_message["content"] = assistant_content
                        mode_switched = True
    
                    # If a mode switch happened without tool calls, break out of the
                    # inner loop and re-run in the new mode. We do NOT add the assistant's
                    # SWITCH_MODE message to history — it's noise, and leaving it causes
                    # the new mode to respond to "continue" rather than to the actual task.
                    # The history already ends with the user's original task message, so
                    # the new mode's inner loop will respond to that directly.
                    if mode_switched and not tool_calls:
                        pending_rerun = True   # must be set BEFORE break — line after inner while is never reached
                        break
    
                    # DEBUG: Log empty response condition
                    if os.getenv("ROO_DEBUG"):
                        if not assistant_content and not tool_calls:
                            print_colored("[DEBUG] Empty response - both content and tool_calls are empty", "yellow")
                        elif not tool_calls:
                            print_colored("[DEBUG] No tool calls - will break inner loop", "yellow")
    
                    # Check if the content is effectively empty (e.g., just stripped <think> tags)
                    is_empty_content = not assistant_content.strip()

                    if not tool_calls:
                        # Don't append to history when there are no tool calls.
                        # An unresolved assistant turn (assistant message with no tool
                        # result following it) confuses the model on retry and causes it
                        # to repeat the same text-only response indefinitely.
                        # The intercept message injected below is the correction signal.
                        pass
                    else:
                        # Add assistant message to history only when it has tool calls
                        history.append(assistant_message)

                    # DEBUG: Trace execution path
                    if os.getenv("ROO_DEBUG"):
                        print_colored(f"[DEBUG] Added assistant message to history, now checking tool_calls: {len(tool_calls) if tool_calls else 0}", "yellow")
                        print_colored(f"[DEBUG] History length: {len(history)}", "yellow")

                    # Check if there are tool calls
                    if tool_calls:
                        consecutive_intercepts = 0
                        # DEBUG: Log tool calls before execution
                        if os.getenv("ROO_DEBUG"):
                            print_colored(f"\n[DEBUG] Found {len(tool_calls)} tool calls", "yellow")
                            for i, tc in enumerate(tool_calls):
                                func = tc.get("function", {})
                                name = func.get("name", "unknown")
                                args = func.get("arguments", "{}")
                                print_colored(f"[DEBUG] Tool call {i}: {name} with args: {args[:100]}...", "yellow")
                        
                        # Check if ask_followup_question is being called
                        has_question_tool = any(tc.get("function", {}).get("name") == "ask_followup_question" for tc in tool_calls)

                        if has_question_tool:
                            # Handle question specially - display and get user answer

                            # Execute question tool
                            tool_results = []
                            for tool_call in tool_calls:
                                tool_name, tool_result = execute_tool_call(tool_call)
                                tool_call_id = tool_call.get("id")
                                tool_results.append((tool_name, tool_result, tool_call_id))

                            # Get user's answer from the question tool result
                            user_answer = None
                            for tool_name, tool_result, tool_call_id in tool_results:
                                if tool_name == "ask_followup_question":
                                    try:
                                        result_data = json.loads(tool_result)
                                        if result_data.get("success"):
                                            user_answer = result_data.get("answer", "")
                                    except json.JSONDecodeError:
                                        pass

                            # Apply tool flattening bypass (batch all results at once)
                            history = apply_tool_flattening_bypass_batch(history, tool_results)

                            # Add user's answer to history (after tool messages)
                            if user_answer:
                                history.append({
                                    "role": "user",
                                    "content": user_answer
                                })

                            # Continue loop to get next response
                            continue
                        else:
                            # Regular tool execution
                            if os.getenv("ROO_DEBUG"):
                                print_colored("[DEBUG] Executing regular tool calls", "yellow")

                            # Execute all tool calls
                            tool_results = []
                            for tool_call in tool_calls:
                                tool_name, tool_result = execute_tool_call(tool_call)
                                tool_call_id = tool_call.get("id")
                                tool_results.append((tool_name, tool_result, tool_call_id))
                            
                            if os.getenv("ROO_DEBUG"):
                                print_colored(f"[DEBUG] Executed {len(tool_results)} tools", "yellow")
                                for tr in tool_results:
                                    print_colored(f"[DEBUG] Tool result: {tr[0]} - {len(tr[1])} chars", "yellow")
                                    if len(tr[1]) < 200:
                                        print_colored(f"[DEBUG] Tool result content: {tr[1]}", "yellow")

                            # Display tool results to user
                            for tool_name, tool_result, tool_call_id in tool_results:
                                try:
                                    result_data = json.loads(tool_result)
                                    if "error" in result_data:
                                        print_colored(f"\n[Error in {tool_name}] {result_data.get('error', 'Unknown error')}", "red")
                                    elif result_data.get("success", True):
                                        # Success - display the result data
                                        print_colored(f"\n[{tool_name}]", "cyan")

                                        # Specific handling for execute_command
                                        if tool_name == "execute_command":
                                            output = result_data.get("output", "").strip()
                                            returncode = result_data.get("returncode", 0)
                                            if output:
                                                # Show up to 20 lines of output
                                                lines = output.splitlines()
                                                shown = lines[:20]
                                                for line in shown:
                                                    print_colored(f"  {line}", "white")
                                                if len(lines) > 20:
                                                    print_colored(
                                                        f"  ... ({len(lines) - 20} more lines)", "yellow"
                                                    )
                                            if returncode != 0:
                                                print_colored(f"  Exit code: {returncode}", "red")
                                            if result_data.get("cwd"):
                                                print_colored(f"  cwd: {result_data['cwd']}", "cyan")

                                        # Cleaner label for write_to_file
                                        if tool_name == "write_to_file" and "path" in result_data:
                                            print_colored(f"  Written: {result_data['path']}", "green")

                                        # Show relevant fields from result
                                        if "path" in result_data and tool_name != "write_to_file":
                                            print_colored(f"  Path: {result_data['path']}", "white")
                                        # web_fetch result (has url + char_count — check before generic content)
                                        if "url" in result_data and "char_count" in result_data:
                                            char_count = result_data.get("char_count", 0)
                                            truncated = result_data.get("truncated", False)
                                            trunc_note = " (truncated)" if truncated else ""
                                            print_colored(f"  URL: {result_data['url']}", "white")
                                            print_colored(f"  Fetched: {char_count:,} chars{trunc_note}", "white")
                                        # generic content preview (read_file, write_to_file, etc)
                                        elif "content" in result_data:
                                            print_colored(f"  Content: {str(result_data['content'])[:200]}...", "white")
                                        if "files" in result_data:
                                            print_colored(f"  Files: {len(result_data['files'])} found", "white")
                                        if "definitions" in result_data:
                                            print_colored(f"  Definitions: {len(result_data['definitions'])} found", "white")
                                        if "matches" in result_data:
                                            print_colored(f"  Matches: {len(result_data['matches'])} found", "white")
                                        # web_search results
                                        if "results" in result_data and "query" in result_data:
                                            print_colored(
                                                f"  Query: {result_data['query']} "
                                                f"({result_data.get('result_count', 0)} results)",
                                                "white"
                                            )
                                            for r in result_data.get("results", [])[:3]:  # show top 3 in terminal
                                                print_colored(f"  [{r['index']}] {r['title']}", "white")
                                                print_colored(f"      {r['url']}", "cyan")
                                                if r.get("snippet"):
                                                    snippet = r['snippet'][:120] + "..." if len(r['snippet']) > 120 else r['snippet']
                                                    print_colored(f"      {snippet}", "white")
                                    else:
                                        # Display result content
                                        if "content" in result_data:
                                            print_colored(f"\n[{tool_name} Result]", "cyan")
                                            print_colored(str(result_data["content"]), "white")
                                except json.JSONDecodeError:
                                    # Not JSON, display as-is
                                    print_colored(f"\n[{tool_name}]", "cyan")
                                    print_colored(str(tool_result), "white")

                            # Apply tool flattening bypass
                            history = apply_tool_flattening_bypass_batch(history, tool_results)

                            # If task was completed, stop the loop
                            if any(tr[0] == "attempt_completion" for tr in tool_results):
                                # Automatically return to Orchestrator mode after completing a task
                                if current_mode != Mode.ORCHESTRATOR:
                                    print_mode_switch(current_mode, Mode.ORCHESTRATOR)
                                    current_mode = Mode.ORCHESTRATOR
                                    history = update_system_message(history, current_mode)
                                break

                            # Otherwise continue to next step
                            continue
                    else:
                        consecutive_intercepts += 1

                        # Detect stall: empty or only dots — model is spinning with no content
                        content_stripped = (assistant_content or "").strip().strip(".")
                        is_stall = not content_stripped

                        # One free gentle nudge per stall cluster, only when the model wrote
                        # something meaningful (not a stall). This handles deepseek's habit of
                        # writing a single preamble sentence before its tool call JSON.
                        # We do NOT reset consecutive_intercepts here — the counter keeps
                        # climbing so the circuit breaker still fires if the model never acts.
                        if consecutive_intercepts == 1 and not is_stall:
                            nudge = "(System: Please now invoke the appropriate tool to continue.)"
                            history.append({"role": "assistant", "content": assistant_content})
                            history.append({"role": "user", "content": nudge})
                            continue

                        # Hard enforcement: fire on 2nd+ attempt, or immediately on stall.
                        if consecutive_intercepts >= 6:
                            print_colored("\n[Circuit Breaker] AI failed to output a tool 6 times in a row. Returning control to user.", "red")
                            break

                        print_colored("\n[System Intercept] AI forgot to call a tool, forcing JSON response...", "yellow")

                        # Build a context-aware hint by scanning recent history
                        last_result_hint = ""
                        for msg in reversed(history):
                            content = msg.get("content", "")
                            if not isinstance(content, str):
                                continue
                            # Check for failed execute_command
                            if "[System: You successfully invoked the 'execute_command'" in content:
                                try:
                                    result_json = content.split("Result:]\n", 1)[1].split("\n\n(System Reminder", 1)[0]
                                    result_data = json.loads(result_json)
                                    output = result_data.get("output", "")
                                    if result_data.get("returncode", 0) != 0:
                                        if "ImportError: cannot import name" in output:
                                            m = re.search(r"cannot import name '(\w+)' from '(\w+)'", output)
                                            if m:
                                                missing, module = m.group(1), m.group(2)
                                                last_result_hint = (
                                                    f" ImportError: '{missing}' not found in '{module}.py'. "
                                                    f"Call read_file on {module}.py to see what functions exist, "
                                                    f"then either add '{missing}' to {module}.py with apply_diff, "
                                                    f"or fix the import in test_main.py to use the correct name."
                                                )
                                            else:
                                                last_result_hint = (
                                                    " ImportError detected. Call read_file on the relevant module "
                                                    "to see what names are defined, then fix the import."
                                                )
                                        else:
                                            last_result_hint = (
                                                " The last command failed. Call read_file on the relevant "
                                                "source file, apply_diff to fix it, then re-run."
                                            )
                                except Exception:
                                    pass
                                break
                            # Model read a file but didn't act on it
                            if "[System: You successfully invoked the 'read_file'" in content:
                                last_result_hint = (
                                    " You just read a file. Now ACT: call apply_diff to fix the code "
                                    "or write_to_file to rewrite it. Do not write more analysis."
                                )
                                break

                        nudge = (
                            "[System Error: text response with no tool call. "
                            "Stop and ACT immediately." + last_result_hint + " "
                            "Call one of: apply_diff, write_to_file, read_file, execute_command. "
                            "JSON tool call only — no more text.]"
                        )

                        _MAX_INTERCEPT_CONTENT = 300
                        stored_content = assistant_content or "..."
                        if len(stored_content) > _MAX_INTERCEPT_CONTENT:
                            stored_content = stored_content[:_MAX_INTERCEPT_CONTENT] + "... [truncated]"
                        history.append({"role": "assistant", "content": stored_content})
                        history.append({"role": "user", "content": nudge})
                        continue

                    # Display token count after completed turn
                    token_estimate = estimate_tokens(history)
                    print_colored(
                        f"\n[Context] ~{token_estimate:,} tokens used in history "
                        f"({100 * token_estimate // CONTEXT_MAX_TOKENS}% of limit)",
                        "magenta"
                    )

                    # Auto-save session
                    do_save()

                    if iteration >= max_iterations:
                        print_colored("\n[Warning] Maximum tool iterations reached.", "yellow")
            except KeyboardInterrupt:
                print_colored("\n\nInterrupted. Type 'exit' to quit or continue.", "yellow")
                if current_mode != Mode.ORCHESTRATOR:
                    print_mode_switch(current_mode, Mode.ORCHESTRATOR)
                    current_mode = Mode.ORCHESTRATOR
                    history = update_system_message(history, current_mode)
                continue
            except EOFError:
                print_colored("\n\nGoodbye!", "cyan")
                break
            except Exception as e:
                print_colored(f"\n[Error] {type(e).__name__}: {str(e)}", "red")
    finally:
        do_save()
        print_colored(f"\n[Session saved: {session_name}]", "green")
        print_colored(f"  Resume with: python roo_cli.py --session {session_name}", "cyan")


if __name__ == "__main__":
    main()