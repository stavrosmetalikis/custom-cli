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
    }
]

# ============================================================================
# System Prompt
# ============================================================================

def get_system_prompt() -> str:
    """Generate the system prompt with current working directory."""
    cwd = os.getcwd()
    return f"""You are Roo, a strategic workflow orchestrator who coordinates complex tasks by delegating them to appropriate specialized modes. You have a comprehensive understanding of each mode's capabilities and limitations, allowing you to effectively break down complex problems into discrete tasks that can be solved by different specialists.

WORKSPACE SYSTEM
You are working in a dedicated workspace directory: {cwd}
All file operations, commands, and tool usage must be performed within this workspace directory. Do not attempt to access files or directories outside of this workspace.

====

MARKDOWN RULES

ALL responses MUST show ANY `language construct` OR filename reference as clickable, exactly as [`filename OR language.declaration()`](relative/file/path.ext:line); line is required for `syntax` and optional for filename links. This applies to ALL markdown responses and ALSO those in attempt_completion

====

TOOL USE

You have access to a set of tools that are executed upon the user's approval. Use the provider-native tool-calling mechanism. Do not include XML markup or examples. You must call at least one tool per assistant response. Prefer calling as many tools as are reasonably needed in a single response to reduce back-and-forth and complete tasks faster.

	# Tool Use Guidelines

	1. Assess what information you already have and what information you need to proceed with the task.
	2. Choose the most appropriate tool based on the task and the tool descriptions provided. Assess if you need additional information to proceed, and which of the available tools would be most effective for gathering this information. For example using the list_files tool is more effective than running a command like `ls` in the terminal. It's critical that you think about each of the available tools and use the one that best fits the current step in your problem-solving process.
	3. If multiple actions are needed, you may use multiple tools in a single message when appropriate, or use tools iteratively across messages. Each tool use should be informed by the results of previous tool uses. Do not assume the outcome of any tool use. Each step must be informed by the previous step's result.

	By carefully considering the user's response after tool executions, you can react accordingly and make informed decisions about how to proceed with the task. This iterative process helps ensure the overall success and accuracy of your work.

====

CAPABILITIES

- You have access to tools that let you execute CLI commands on the user's computer, list files, view source code definitions, regex search, read and write files, apply diffs, and ask follow-up questions. These tools help you effectively accomplish a wide range of tasks, such as writing code, making edits or improvements to existing files, understanding the current state of a project, performing system operations, and much more.
- When the user initially gives you a task, a recursive list of all filepaths in the current workspace directory ('{cwd}') will be included in environment_details. This provides an overview of the project's file structure, offering key insights into the project from directory/file names (how developers conceptualize and organize their code) and file extensions (the language used). This can guide decision-making on which files to explore further. If you need to further explore directories such as outside the current workspace directory, you can use the list_files tool. If you pass 'true' for the recursive parameter, it will list files recursively. Otherwise, it will list files at the top level, which is better suited for generic directories where you don't necessarily need the nested structure, like the Desktop.

====

SYSTEM INFORMATION

Operating System: Linux
Default Shell: /bin/bash
Home Directory: /home/ubuntu
Current Workspace Directory is the active VS Code project directory, and is therefore the default directory for all tool operations. New terminals will be created in the current workspace directory, however if you change directories in a terminal it will then have a different working directory; changing directories in a terminal does not modify the workspace directory, because you do not have access to change the workspace directory. If you need to further explore directories such as outside the current workspace directory, you can use the list_files tool. If you pass 'true' for the recursive parameter, it will list files recursively. Otherwise, it will list files at the top level, which is better suited for generic directories where you don't necessarily need the nested structure, like the Desktop.

====

OBJECTIVE

You accomplish a given task iteratively, breaking it down into clear steps and working through them methodically.

1. Analyze the user's task and set clear, achievable goals to accomplish it. Prioritize these goals in a logical order.
2. Work through these goals sequentially, utilizing available tools one at a time as necessary. Each goal should correspond to a distinct step in your problem-solving process. You will be informed on the work completed and what's remaining as you go.
3. Remember, you have extensive capabilities with access to a range of tools that can be used in powerful and clever ways as necessary to accomplish each goal. Before calling a tool, do some analysis. First, analyze the file structure provided in environment_details to gain context and insights for proceeding effectively. Next, think about which of the available tools is the most relevant tool to accomplish the user's task. Go through each of the required parameters of the relevant tool and determine if the user has directly provided or given enough information to infer a value. When deciding if the parameter can be inferred, carefully consider all the context to see if it supports a specific value. If all of the required parameters are present or can be reasonably inferred, proceed with the tool use. BUT, if one of the values for the required parameters is missing, DO NOT invoke the tool (not even with fillers for the missing params) and instead, ask the user to provide the missing parameters using the ask_followup_question tool. DO NOT ask for more information on optional parameters if it is not provided.
4. Once you've completed the user's task, you must use the attempt_completion tool to present the result of your task to the user.
5. The user may provide feedback, which you can use to make improvements and try again. But DO NOT continue in pointless back and forth conversations, i.e. don't end your responses with questions or offers for further assistance."""

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
    "attempt_completion": tool_attempt_completion
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

def send_chat_request(messages: List[Dict[str, Any]], model: str = ROO_MODEL) -> Optional[Dict[str, Any]]:
    """Send a chat completion request to the API."""
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        "temperature": 0.7
    }
    
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
            
            return response_data
    except httpx.HTTPStatusError as e:
        print_colored(f"\n[HTTP Error] {e.response.status_code}: {e.response.text}", "red")
        return None
    except httpx.RequestError as e:
        print_colored(f"\n[Request Error] {str(e)}", "red")
        return None
    except json.JSONDecodeError as e:
        print_colored(f"\n[JSON Error] Failed to parse response: {str(e)}", "red")
        return None
    except Exception as e:
        print_colored(f"\n[Unexpected Error] {type(e).__name__}: {str(e)}", "red")
        return None


# ============================================================================
# Main Agent Loop
# ============================================================================

def main():
    """Main agent loop."""
    print_colored("=" * 60, "cyan")
    print_colored("  Roo CLI - AI Coding Agent", "cyan")
    print_colored("=" * 60, "cyan")
    print_colored(f"  Model: {ROO_MODEL}", "white")
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
            "content": get_system_prompt()
        }
    ]
    
    while True:
        try:
            # Get user input (handles multi-line paste and single-line input)
            print_colored("\nYou: ", "green", end="")
            
            # Read input - handle both single-line (enter) and multi-line paste (Ctrl+D)
            lines = []
            try:
                # Read first line
                line = input()
                lines.append(line)
                
                # Check if user wants to paste multi-line content (Ctrl+D to end)
                # If the line ends with a backslash, continue reading
                while line.endswith('\\'):
                    line = input('... ')
                    lines.append(line[:-1])  # Remove the backslash
                
            except EOFError:
                # Ctrl+D pressed - end of input
                pass
            
            user_input = '\n'.join(lines).strip()
            
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
            max_iterations = 20  # Prevent infinite loops
            iteration = 0
            
            while iteration < max_iterations:
                iteration += 1
                
                # Send request to API
                response = send_chat_request(history)
                
                if not response:
                    print_colored("\nFailed to get response from API. Please try again.", "red")
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
                reasoning_content = assistant_message.get("reasoning_content", "")
                
                # Add assistant message to history
                history.append(assistant_message)
                
                # Check if there are tool calls
                if tool_calls:
                    # Check if ask_followup_question is being called
                    has_question_tool = any(tc.get("function", {}).get("name") == "ask_followup_question" for tc in tool_calls)
                    
                    if has_question_tool:
                        # Handle question specially - display and get user answer
                        print_thinking(assistant_content, source="planning")
                        
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
                        print_thinking(assistant_content, source="planning")
                        
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
                                    # Show relevant fields from result
                                    if "path" in result_data:
                                        print_colored(f"  Path: {result_data['path']}", "white")
                                    if "content" in result_data:
                                        print_colored(f"  Content: {str(result_data['content'])[:200]}...", "white")
                                    if "files" in result_data:
                                        print_colored(f"  Files: {len(result_data['files'])} found", "white")
                                    if "definitions" in result_data:
                                        print_colored(f"  Definitions: {len(result_data['definitions'])} found", "white")
                                    if "matches" in result_data:
                                        print_colored(f"  Matches: {len(result_data['matches'])} found", "white")
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
                    print_thinking(reasoning_content)   # still try reasoning_content (for R1 models)
                    if assistant_content:
                        print_colored(f"\nRoo: {assistant_content}", "cyan")
                    break
            
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
