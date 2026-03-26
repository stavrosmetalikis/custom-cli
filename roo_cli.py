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
import urllib.parse
import urllib.request
import html
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

    Strategy: always preserve the system prompt (first message if role==system)
    and remove the OLDEST non-system messages from the middle until the
    estimated token count is within the target budget.

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

    removed = 0
    while rest and estimate_tokens(system_msg + rest) > CONTEXT_TRUNCATE_TO:
        # Remove the oldest message from the non-system portion
        rest.pop(0)
        removed += 1
        # Safety: if rest is empty and still over limit, return as-is
        # (cannot truncate further without losing the system prompt)
        if not rest:
            break

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
            "description": "Apply a diff or search/replace block to a file. This is more efficient than write_to_file for making targeted changes to existing files. The diff format uses SEARCH/REPLACE blocks.\n\nParameters:\n- path: (required) Path to the file to modify (relative to workspace).\n- diff: (required) The diff to apply in SEARCH/REPLACE format. Each block should have:\n<<<<<<< SEARCH\n:start_line:X\n-------\ncontent to replace\n=======\nnew content\n>>>>>>> REPLACE\n\nExample: Apply a simple change\n{ \"path\": \"src/app.py\", \"diff\": \"<<<<<<< SEARCH\\n:start_line:10\\n-------\\nold code\\n=======\\nnew code\\n>>>>>>> REPLACE\" }\n\nMultiple blocks can be included in a single diff.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to modify (relative to workspace)."},
                    "diff": {"type": "string", "description": "The diff to apply in SEARCH/REPLACE format. Each block should have:\n<<<<<<< SEARCH\n:start_line:X\n-------\ncontent to replace\n=======\nnew content\n>>>>>>> REPLACE"}
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
            "description": "After each tool use, the user will respond with the result of that tool use, i.e. if it succeeded or failed, along with any reasons for failure. Once you've received the results of tool uses and can confirm that the task is complete, use this tool to present the result of your work to the user. The user may respond with feedback if they are not satisfied with the result, which you can use to make improvements and try again.\n\nIMPORTANT NOTE: This tool CANNOT be used  [truncated...]",
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
    Mode.ORCHESTRATOR: [
        "list_files", "read_file", "ask_followup_question", "attempt_completion",
        "web_search", "web_fetch"
    ],
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

MARKDOWN RULES:
Show filenames and code constructs as clickable links:
[`filename`](relative/path.ext) or [`function()`](file.py:line)
"""

    mode_instructions = {
        Mode.ORCHESTRATOR: """
ORCHESTRATOR MODE INSTRUCTIONS:
You are the strategic planner and coordinator. Your job is to:

Read the user's request carefully.
If the request contains explicit step-by-step instructions, follow them
DIRECTLY — do NOT search the web, do NOT ask for clarification, do NOT
research best practices. Just execute the steps.
Break the task into subtasks and delegate each to the correct mode by
switching immediately.
Stay in the delegated mode until that entire subtask is fully done.
Do NOT switch back to Orchestrator between minor steps within a subtask.
Only use web_search when the user explicitly asks to research something,
or when you genuinely lack information needed to proceed.
Call attempt_completion ONLY when ALL steps in the user's request are
fully complete — not after each individual step.
Never write code or run commands yourself — delegate to Code or Debug.

SPEED RULES (critical):

Do not search the web unless the user asks for research.
Do not switch modes more than necessary — batch related work in one mode.
Do not call attempt_completion mid-task.
Do not re-enter Orchestrator mode between steps unless you need to
plan the next major phase. Minor sequential steps stay in the same mode.
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
""",
    }

    return base + mode_instructions[mode]


def get_tools_for_mode(mode: Mode) -> List[Dict[str, Any]]:
    """Return the TOOLS list filtered to only tools allowed in this mode."""
    allowed = set(MODE_TOOLS[mode])
    return [t for t in TOOLS if t["function"]["name"] in allowed]


def parse_mode_switch(content: str) -> Optional[Mode]:
    """
    Check if the assistant's content contains a mode switch instruction.
    Returns the new Mode if found, or None if no switch requested.

    Looks for:  SWITCH_MODE: {"mode": "code"}
    anywhere in the content string, case-insensitive mode name.
    """
    if not content:
        return None
    match = re.search(r'SWITCH_MODE:\s*\{"mode":\s*"(\w+)"\}', content)
    if not match:
        return None
    mode_name = match.group(1).lower()
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
        
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=timeout if timeout else 60
        )
        
        output = result.stdout
        if result.stderr:
            output += result.stderr
        
        return json.dumps({
            "success": True,
            "output": output,
            "returncode": result.returncode
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
            # List only top-level files
            for item in target_path.iterdir():
                item_path = target_path / item
                if item_path.is_file():
                    files.append(item)
        
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
        for item in target_path.rglob(f"*{file_pattern}"):
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
    """Apply a SEARCH/REPLACE diff to a file."""
    path = args.get("path")
    diff = args.get("diff")
    
    if not path:
        return json.dumps({"error": "Missing required parameter: path"})
    if not diff:
        return json.dumps({"error": "Missing required parameter: diff"})
    
    # Validate path is within workspace
    if not validate_path_in_workspace(path):
        return json.dumps({"error": f"Path outside workspace: {path}"})
    
    try:
        full_path = Path(path)
        if not full_path.exists():
            return json.dumps({"error": f"File not found: {path}"})
        
        # Read the original file
        with open(full_path, 'r', encoding='utf-8') as f:
            original_lines = f.readlines()
        
        # Parse the diff into SEARCH/REPLACE blocks
        blocks = []
        lines = diff.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            if line.startswith('<<<<<<< SEARCH'):
                # Found a SEARCH block start
                # Extract start_line
                start_line_match = re.search(r':start_line:(\d+)', line)
                if not start_line_match:
                    return json.dumps({"error": "Invalid diff format: missing start_line"})
                start_line = int(start_line_match.group(1))
                
                # Move to separator
                i += 1
                if i >= len(lines) or not lines[i].startswith('-------'):
                    return json.dumps({"error": "Invalid diff format: missing '-------' separator"})
                i += 1
                
                # Collect search lines
                search_lines = []
                while i < len(lines) and not lines[i].startswith('======='):
                    search_lines.append(lines[i])
                    i += 1
                
                if i >= len(lines) or not lines[i].startswith('======='):
                    return json.dumps({"error": "Invalid diff format: missing '=======' separator"})
                i += 1
                
                # Collect replace lines
                replace_lines = []
                while i < len(lines) and not lines[i].startswith('>>>>>>> REPLACE'):
                    replace_lines.append(lines[i])
                    i += 1
                
                if i >= len(lines) or not lines[i].startswith('>>>>>>> REPLACE'):
                    return json.dumps({"error": "Invalid diff format: missing '>>>>>>> REPLACE' marker"})
                i += 1
                
                blocks.append({
                    "start_line": start_line,
                    "search": search_lines,
                    "replace": replace_lines
                })
        
        if not blocks:
            return json.dumps({"error": "No valid SEARCH/REPLACE blocks found in diff"})
        
        # Apply each block
        modified_lines = original_lines.copy()
        
        for block in reversed(blocks):
            start_line = block["start_line"]
            search_lines = block["search"]
            replace_lines = block["replace"]
            
            # Adjust start_line to 0-based
            start_idx = start_line - 1
            
            # Check if search matches
            actual_search = ''.join(modified_lines[start_idx:start_idx + len(search_lines)])
            normalized_search = '\n'.join(line.rstrip('\r\n') + '\n' for line in search_lines)
            
            if actual_search != normalized_search:
                return json.dumps({
                    "error": "Search content does not match file content",
                    "expected": ''.join(search_lines),
                    "found": actual_search
                })
            
            # Replace the lines
            modified_lines[start_idx:start_idx + len(search_lines)] = replace_lines
        
        # Write the modified file
        with open(full_path, 'w', encoding='utf-8') as f:
            f.writelines(modified_lines)
        
        print_colored(f"\n[Diff Applied] {path}", "green")
        return json.dumps({
            "success": True,
            "path": path,
            "blocks_applied": len(blocks)
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

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_html = resp.read().decode("utf-8", errors="ignore")

        # Extract results using regex — no external HTML parser needed
        results = []

        # DuckDuckGo HTML results are in <div class="result"> blocks
        # Extract result links and snippets
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

    except urllib.error.HTTPError as e:
        return json.dumps({
            "success": False,
            "error": f"HTTP error {e.code}: {e.reason}",
            "query": query
        })
    except urllib.error.URLError as e:
        return json.dumps({
            "success": False,
            "error": f"Network error: {str(e.reason)}",
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
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,text/plain",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", errors="ignore")

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

    except urllib.error.HTTPError as e:
        return json.dumps({
            "success": False,
            "error": f"HTTP error {e.code}: {e.reason}",
            "url": url
        })
    except urllib.error.URLError as e:
        return json.dumps({
            "success": False,
            "error": f"Network error: {str(e.reason)}",
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
    
    if not tool_name:
        return ("unknown", json.dumps({"error": "Tool name not found in tool call"}))
    
    if tool_name not in TOOL_FUNCTIONS:
        return (tool_name, json.dumps({"error": f"Unknown tool: {tool_name}"}))
    
    try:
        tool_args = json.loads(tool_args_str)
    except json.JSONDecodeError:
        return (tool_name, json.dumps({"error": f"Invalid JSON arguments for tool {tool_name}"}))
    
    # Execute the tool function
    tool_func = TOOL_FUNCTIONS[tool_name]
    tool_result = tool_func(tool_args)
    
    return (tool_name, tool_result)

# ============================================================================
# Tool Flattening Bypass (CRITICAL)
# ============================================================================

def apply_tool_flattening_bypass_batch(history: List[Dict[str, Any]], tool_results: List[Tuple[str, str, str]]) -> List[Dict[str, Any]]:
    """
    Apply the tool flattening bypass for multiple tool results at once to avoid proxy crashes.
    
    This modifies the history to:
    1. Find the assistant's previous message with tool_calls
    2. Keep the tool_calls array (required for API validation)
    3. Append text note: "[System Note: I executed the tools: tool_name1, tool_name2, ...]"
    4. Add role: "tool" messages with the tool results
    
    Args:
        history: The message history
        tool_results: List of tuples (tool_name, tool_result, tool_call_id)
    
    Returns:
        Updated history with tool result messages
    """
    new_history = []
    
    # First, find the assistant message with tool_calls (if any)
    assistant_msg_idx = -1
    assistant_msg = None
    for i, msg in enumerate(history):
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            assistant_msg_idx = i  # keep updating — no break
            assistant_msg = msg
    # No break: loop runs to the end, so assistant_msg_idx is the LAST match
    
    # Process each message in history
    for i, msg in enumerate(history):
        if i == assistant_msg_idx and assistant_msg is not None:
            # This is the assistant message we need to process
            if "tool_calls" in assistant_msg:
                # Create a modified version with system note (keep tool_calls)
                new_msg = assistant_msg.copy()
                content = new_msg.get("content", "")
                
                # Build tool names list for system note
                tool_names = [tr[0] for tr in tool_results]
                tool_names_str = ", ".join(tool_names)
                
                # Append system note to content
                if content:
                    new_msg["content"] = f"{content}\n\n[System Note: I executed the tools: {tool_names_str}]"
                else:
                    new_msg["content"] = f"[System Note: I executed the tools: {tool_names_str}]"
                
                # Keep tool_calls (do not delete)
                new_history.append(new_msg)
                
                # Add all tool result messages
                for tool_name, tool_result, tool_call_id in tool_results:
                    new_history.append({
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": tool_call_id if tool_call_id else f"call_{tool_name}"
                    })
            else:
                # Assistant message already processed (no tool_calls)
                # Just add the tool result messages
                new_history.append(assistant_msg)
                
                for tool_name, tool_result, tool_call_id in tool_results:
                    new_history.append({
                        "role": "tool",
                        "content": tool_result,
                        "tool_call_id": tool_call_id if tool_call_id else f"call_{tool_name}"
                    })
        else:
            new_history.append(msg)
    
    # If we didn't find an assistant message at all, we still need to add tool messages
    if assistant_msg_idx == -1:
        for tool_name, tool_result, tool_call_id in tool_results:
            new_history.append({
                "role": "tool",
                "content": tool_result,
                "tool_call_id": tool_call_id if tool_call_id else f"call_{tool_name}"
            })
    
    return new_history


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

def send_chat_request(messages: List[Dict[str, Any]], model: str = ROO_MODEL, mode: Mode = Mode.ORCHESTRATOR) -> Optional[Dict[str, Any]]:
    """Send a chat completion request to the API."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": get_tools_for_mode(mode),
        "stream": False,
        "temperature": 0.7
    }
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(proxy=ROO_PROXY_URL, timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0)) as client:
                response = client.post(
                    API_URL,
                    headers=HEADERS,
                    json=payload
                )
                response.raise_for_status()
                response_data = response.json()
                
                # Debug mode: print raw message fields
                if os.getenv("ROO_DEBUG"):
                    msg = response_data.get("choices", [{}])[0].get("message", {})
                    debug_keys = {k: str(v)[:120] for k, v in msg.items() if k != "tool_calls"}
                    print_colored(f"\n[DEBUG] Message fields: {debug_keys}", "yellow")
                
                return response_data  # success — exit immediately
                
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if attempt < MAX_RETRIES and should_retry(status_code=status):
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] HTTP {status} — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[HTTP Error] {status}: {e.response.text}", "red")
            return None
        except httpx.TimeoutException as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] Timeout — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[Timeout] Request timed out after {MAX_RETRIES} attempts.", "red")
            return None
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] Network error — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[Request Error] {str(e)}", "red")
            return None
        except Exception as e:
            # Non-retryable — fail immediately
            print_colored(f"\n[Unexpected Error] {type(e).__name__}: {str(e)}", "red")
            return None

    return None  # exhausted all retries


def send_chat_request_stream(messages: List[Dict[str, Any]], model: str = ROO_MODEL, mode: Mode = Mode.ORCHESTRATOR) -> Optional[Dict[str, Any]]:
    """Send a chat completion request with streaming to the API."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": get_tools_for_mode(mode),
        "stream": True,
        "temperature": 0.7
    }
    
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
                    
                    # Print "Roo: " before streaming starts
                    print_colored(f"\nRoo({MODE_LABELS[mode]}): ", MODE_COLORS[mode], end="")
                    
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
                                
                                # Strip SWITCH_MODE lines from buffer before printing
                                content_buffer = re.sub(
                                    r'SWITCH_MODE:\s*\{"mode":\s*"\w+"\}\n?', '', content_buffer
                                )
                                
                                # Print buffer when it reaches size threshold or has natural boundary
                                should_flush = False
                                if len(content_buffer) >= BUFFER_SIZE:
                                    should_flush = True
                                elif content.endswith(' ') or content.endswith('\n'):
                                    should_flush = True
                                
                                if should_flush:
                                    print(content_buffer, end="", flush=True)
                                    content_buffer = ""
                            
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
                    
                    # Flush any remaining content in buffer
                    if content_buffer:
                        print(content_buffer, end="", flush=True)
                        content_buffer = ""
                    
                    # Print newline after streaming content
                    print()
                    
                    # Print reasoning if present
                    if full_reasoning:
                        print_thinking(full_reasoning, source="reasoning")
                    
                    # Reconstruct tool_calls list from map (sorted by index)
                    tool_calls_list = []
                    if tool_calls_map:
                        for index in sorted(tool_calls_map.keys()):
                            tool_calls_list.append(tool_calls_map[index])
                    
                    # Build the response dict in the same shape as non-streaming
                    message_dict = {
                        "role": "assistant",
                        "content": full_content,
                        "reasoning_content": full_reasoning
                    }
                    
                    # Only include tool_calls if there are any
                    if tool_calls_list:
                        message_dict["tool_calls"] = tool_calls_list
                    
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
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] HTTP {status} — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[HTTP Error] {status}: {e.response.text}", "red")
            return None
        except httpx.TimeoutException as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] Timeout — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[Timeout] Request timed out after {MAX_RETRIES} attempts.", "red")
            return None
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print_colored(
                    f"\n[Retry {attempt}/{MAX_RETRIES}] Network error — "
                    f"retrying in {delay:.0f}s...", "yellow"
                )
                time.sleep(delay)
                continue
            print_colored(f"\n[Request Error] {str(e)}", "red")
            return None
        except Exception as e:
            # Non-retryable — fail immediately
            print_colored(f"\n[Unexpected Error] {type(e).__name__}: {str(e)}", "red")
            return None

    return None  # exhausted all retries


# ============================================================================
# Main Agent Loop
# ============================================================================

def main():
    """Main agent loop."""
    current_mode = Mode.ORCHESTRATOR
    
    print_colored("=" * 60, "cyan")
    print_colored("  Roo CLI - AI Coding Agent", "cyan")
    print_colored("=" * 60, "cyan")
    print_colored(f"  Model: {ROO_MODEL}", "white")
    print_colored(f"  Mode: {MODE_LABELS[current_mode]}", MODE_COLORS[current_mode])
    print_colored("=" * 60, "cyan")
    print_colored("Type 'exit' or 'quit' to exit\n", "yellow")
    
    # Create or find workspace folder (after environment validation)
    # Get the script's directory to avoid nesting workspaces
    script_dir = Path(__file__).parent.resolve()
    workspace_num = 1
    while True:
        workspace_dir = script_dir / f"workspace_{workspace_num}"
        if not workspace_dir.exists():
            workspace_dir.mkdir(parents=True, exist_ok=True)
            break
        workspace_num += 1
    
    # Change to workspace directory
    os.chdir(workspace_dir)
    
    print_colored(f"  Workspace: {workspace_dir.absolute()}", "white")
    print_colored("=" * 60, "cyan")
    
    # Initialize message history with system prompt
    history = [
        {
            "role": "system",
            "content": get_system_prompt(current_mode)
        }
    ]
    
    def update_system_message(history, mode):
        """Replace the system message in history with one for the new mode."""
        new_prompt = get_system_prompt(mode)
        if history and history[0].get("role") == "system":
            history[0]["content"] = new_prompt
        else:
            history.insert(0, {"role": "system", "content": new_prompt})
        return history
    
    while True:
        try:
            # Get user input
            mode_label = MODE_LABELS[current_mode]
            user_input = input(f"\nYou({mode_label}): ").strip()
            
            # Check for exit commands on the complete input
            if user_input.lower() in ['exit', 'quit', 'q']:
                print_colored("\nGoodbye!", "cyan")
                break
            
            if not user_input:
                continue
            
            # Add user message to history
            history.append({
                "role": "user",
                "content": user_input
            })
            
            # Agent loop - handle tool calls
            max_iterations = 40  # Prevent infinite loops
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                print_colored(f"\n[Step {iteration}]", "yellow", end=" ")
                
                # Truncate history if needed before API request
                history = truncate_history(history)
                
                # Send request to API (with streaming)
                response = send_chat_request_stream(history, mode=current_mode)
                
                if not response:
                    print_colored(
                        "\n[Failed] Could not reach API after all retry attempts. "
                        "Check your connection or try again.", "red"
                    )
                    # Remove the last user message to allow retry
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
                
                # Check for mode switch instruction
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
                
                # Add assistant message to history
                history.append(assistant_message)
                
                # Check if there are tool calls
                if tool_calls:
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
                        
                        # Execute all tool calls
                        tool_results = []
                        for tool_call in tool_calls:
                            tool_name, tool_result = execute_tool_call(tool_call)
                            tool_call_id = tool_call.get("id")
                            tool_results.append((tool_name, tool_result, tool_call_id))
                        
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
                        
                        # Apply tool flattening bypass (batch all results at once)
                        history = apply_tool_flattening_bypass_batch(history, tool_results)
                        
                        # Continue loop to get next response
                        continue
                else:
                    # No tool calls, just text response
                    # Content was already streamed, just break
                    break
            
            # Display token count after completed turn
            token_estimate = estimate_tokens(history)
            print_colored(
                f"\n[Context] ~{token_estimate:,} tokens used in history "
                f"({100 * token_estimate // CONTEXT_MAX_TOKENS}% of limit)",
                "magenta"
            )
            
            if iteration >= max_iterations:
                print_colored("\n[Warning] Maximum tool iterations reached.", "yellow")
        
        except KeyboardInterrupt:
            print_colored("\n\nInterrupted. Type 'exit' to quit or continue.", "yellow")
        except EOFError:
            print_colored("\n\nGoodbye!", "cyan")
            break
        except Exception as e:
            print_colored(f"\n[Error] {type(e).__name__}: {str(e)}", "red")


if __name__ == "__main__":
    main()
