#!/usr/bin/env python3
"""
Roo CLI - A standalone, terminal-based AI coding agent
Connects to agentrouter.org with WAF bypass requirements
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import httpx

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional

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
                },
                "additionalProperties": False
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
                },
                "required": ["regex"],
                "additionalProperties": False
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
                },
                "additionalProperties": False
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
                    "diff": {"type": "string", "description": "The diff to apply in SEARCH/REPLACE format. Each block should have:\\n<<<<<<< SEARCH\\n:start_line:X\\n-------\\ncontent to replace\\n=======\\nnew content\\n>>>>>>> REPLACE"}
                },
                "required": ["path", "diff"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Request to execute a CLI command on the system. Use this when you need to perform system operations or run specific commands to accomplish any step in the user's task. You must tailor your command to the user's system and provide a clear explanation of what the command does. For command chaining, use the appropriate chaining syntax for the user's shell. Prefer to execute complex CLI commands over creating executable scripts, as they are more flexible and easier to run. Prefer relative commands and paths that avoid location sensitivity for terminal consistency.\n\nParameters:\n- command: (required) The CLI command to execute. This should be valid for the current operating system. Ensure the command is properly formatted and does not contain any harmful instructions.\n- cwd: (optional) The working directory to execute the command in\n- timeout: (optional) Timeout in seconds. When exceeded, the command keeps running in the background and you receive the output so far. Set this for commands that may run indefinitely, such as dev servers or file watchers, so you can proceed without waiting for them to exit.\n\nExample: Executing npm run dev\n{ \"command\": \"npm run dev\", \"cwd\": null, \"timeout\": null }\n\nExample: Executing ls in a specific directory if directed\n{ \"command\": \"ls -la\", \"cwd\": \"/home/user/projects\", \"timeout\": null }\n\nExample: Using relative paths\n{ \"command\": \"touch ./testdata/example.file\", \"cwd\": null, \"timeout\": null }\n\nExample: Running a build with a timeout\n{ \"command\": \"npm run build\", \"cwd\": null, \"timeout\": 30 }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "cwd": {"type": ["string", "null"], "description": "Optional working directory for the command, relative or absolute"},
                    "timeout": {"type": ["number", "null"], "description": "Timeout in seconds. When exceeded, the command continues running in the background and output collected so far is returned. Use this for long-running processes like dev servers, file watchers, or any command that may not exit on its own"}
                },
                "required": ["command", "cwd", "timeout"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file and return its contents with line numbers for diffing or discussion. IMPORTANT: This tool reads exactly one file per call. If you need multiple files, issue multiple parallel read_file calls. Supports two modes: 'slice' (default) reads lines sequentially with offset/limit; 'indentation' extracts complete semantic code blocks around an anchor line based on indentation hierarchy. Slice mode is ideal for initial file exploration, understanding overall structure, reading configuration/data files, or when you need a specific line range. Use it when you don't have a target line number. PREFER indentation mode when you have a specific line number from search results, error messages, or definition lookups - it guarantees complete, syntactically valid code blocks without mid-function truncation. IMPORTANT: Indentation mode requires anchor_line to be useful. Without it, only header content (imports) is returned. By default, returns up to 2000 lines per file. Lines longer than 2000 characters are truncated. Supports text extraction from PDF and DOCX files, but may not handle other binary files properly. Example: { path: 'src/app.ts' } Example (indentation mode): { path: 'src/app.ts', mode: 'indentation', indentation: { anchor_line: 42 } }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read, relative to the workspace"},
                    "mode": {"type": "string", "enum": ["slice", "indentation"], "description": "Reading mode. 'slice' (default): read lines sequentially with offset/limit - use for general file exploration or when you don't have a target line number (may truncate code mid-function). 'indentation': extract complete semantic code blocks containing anchor_line - PREFERRED when you have a line number because it guarantees complete, valid code blocks. WARNING: Do not use indentation mode without specifying indentation.anchor_line, or you will only get header content."},
                    "offset": {"type": "integer", "description": "1-based line offset to start reading from (slice mode, default: 1)"},
                    "limit": {"type": "integer", "description": "Maximum number of lines to return (slice mode, default: 2000)"},
                    "indentation": {
                        "type": "object",
                        "description": "Indentation mode options. Only used when mode='indentation'. You MUST specify anchor_line for useful results - it determines which code block to extract.",
                        "properties": {
                            "anchor_line": {"type": "integer", "description": "1-based line number to anchor the extraction. REQUIRED for meaningful indentation mode results. The extractor finds the semantic block (function, method, class) containing this line and returns it completely. Without anchor_line, indentation mode defaults to line 1 and returns only imports/header content. Obtain anchor_line from: search results, error stack traces, definition lookups, codebase_search results, or condensed file summaries (e.g., '14--28 | export class UserService' means anchor_line=14)."},
                            "max_levels": {"type": "integer", "description": "Maximum indentation levels to include above the anchor (indentation mode, 0 = unlimited (default)). Higher values include more parent context."},
                            "include_siblings": {"type": "boolean", "description": "Include sibling blocks at the same indentation level as the anchor block (indentation mode, default: false). Useful for seeing related methods in a class."},
                            "include_header": {"type": "boolean", "description": "Include file header content (imports, module-level comments) at the top of output (indentation mode, default: true)."},
                            "max_lines": {"type": "integer", "description": "Hard cap on lines returned for indentation mode. Acts as a separate limit from the top-level 'limit' parameter."}
                        },
                        "required": [],
                        "additionalProperties": False
                    }
                },
                "required": ["path"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_to_file",
            "description": "Request to write content to a file. This tool is primarily used for creating new files or for scenarios where a complete rewrite of an existing file is intentionally required. If the file exists, it will be overwritten. If it doesn't exist, it will be created. This tool will automatically create any directories needed to write the file.\n\n**Important:** You should prefer using other editing tools over write_to_file when making changes to existing files, since write_to_file is slower and cannot handle large files. Use write_to_file primarily for new file creation.\n\nWhen using this tool, use it directly with the desired content. You do not need to display the content before using the tool. ALWAYS provide the COMPLETE file content in your response. This is NON-NEGOTIABLE. Partial updates or placeholders like '// rest of code unchanged' are STRICTLY FORBIDDEN. Failure to do so will result in incomplete or broken code.\n\nWhen creating a new project, organize all new files within a dedicated project directory unless the user specifies otherwise. Structure the project logically, adhering to best practices for the specific type of project being created.\n\nExample: Writing a configuration file\n{ \"path\": \"frontend-config.json\", \"content\": \"{\\n  \\\"apiEndpoint\\\": \\\"https://api.example.com\\\",\\n  \\\"theme\\\": {\\n    \\\"primaryColor\\\": \\\"#007bff\\\"\\n  }\\n}\" }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The path of the file to write to (relative to the current workspace directory)"},
                    "content": {"type": "string", "description": "The content to write to the file. ALWAYS provide the COMPLETE intended content of the file, without any truncation or omissions. You MUST include ALL parts of the file, even if they haven't been modified. Do NOT include line numbers in the content."}
                },
                "required": ["path", "content"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_followup_question",
            "description": "Ask the user a question to gather additional information needed to complete the task. Use when you need clarification or more details to proceed effectively.\n\nParameters:\n- question: (required) A clear, specific question addressing the information needed\n- follow_up: (required) A list of 2-4 suggested answers. Suggestions must be complete, actionable answers without placeholders. Optionally include mode to switch modes (code/architect/etc.)\n\nExample: Asking for file path\n{ \"question\": \"What is the path to the frontend-config.json file?\", \"follow_up\": [{ \"text\": \"./src/frontend-config.json\", \"mode\": null }, { \"text\": \"./config/frontend-config.json\", \"mode\": null }, { \"text\": \"./frontend-config.json\", \"mode\": null }] }\n\nExample: Asking with mode switch\n{ \"question\": \"Would you like me to implement this feature?\", \"follow_up\": [{ \"text\": \"Yes, implement it now\", \"mode\": \"code\" }, { \"text\": \"No, just plan it out\", \"mode\": \"architect\" }] }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "Clear, specific question that captures the missing information you need"},
                    "follow_up": {
                        "type": "array",
                        "description": "Required list of 2-4 suggested responses; each suggestion must be a complete, actionable answer and may include a mode switch",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Suggested answer the user can pick"},
                                "mode": {"type": ["string", "null"], "description": "Optional mode slug to switch to if this suggestion is chosen (e.g., code, architect)"}
                            },
                            "required": ["text", "mode"],
                            "additionalProperties": False
                        },
                        "minItems": 1,
                        "maxItems": 4
                    }
                },
                "required": ["question", "follow_up"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "attempt_completion",
            "description": "After each tool use, the user will respond with the result of that tool use, i.e. if it succeeded or failed, along with any reasons for failure. Once you've received the results of tool uses and can confirm that the task is complete, use this tool to present the result of your work to the user. The user may respond with feedback if they are not satisfied with the result, which you can use to make improvements and try again.\n\nIMPORTANT NOTE: This tool CANNOT be used until you've confirmed from the user that any previous tool uses were successful. Failure to do so will result in code corruption and system failure. Before using this tool, you must confirm that you've received successful results from the user for any previous tool uses. If not, then DO NOT use this tool.\n\nParameters:\n- result: (required) The result of the task. Formulate this result in a way that is final and does not require further input from the user. Don't end your result with questions or offers for further assistance.\n\nExample: Completing after updating CSS\n{ \"result\": \"I've updated the CSS to use flexbox layout for better responsiveness\" }",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "Final result message to deliver to the user once the task is complete"}
                },
                "required": ["result"],
                "additionalProperties": False
            }
        }
    }
]


def get_system_prompt() -> str:
    """Generate the system prompt with current working directory."""
    cwd = os.getcwd()
    return f"""You are Roo, a strategic workflow orchestrator who coordinates complex tasks by delegating them to appropriate specialized modes. You have a comprehensive understanding of each mode's capabilities and limitations, allowing you to effectively break down complex problems into discrete tasks that can be solved by different specialists.

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
- You can use the execute_command tool to run commands on the user's computer whenever you feel it can help accomplish the user's task. When you need to execute a CLI command, you must provide a clear explanation of what the command does. Prefer to execute complex CLI commands over creating executable scripts, since they are more flexible and easier to run. Interactive and long-running commands are allowed, since the commands are run in the user's VSCode terminal. The user may keep commands running in the background and you will be kept updated on their status along the way. Each command you execute is run in a new terminal instance.
- The list_files tool allows you to explore directory structures efficiently. Use it instead of shell commands like `ls` to get a clean, structured list of files and directories.
- The search_files tool enables regex-based content search across files. Use it to find specific patterns, function names, or text across the codebase. You can filter by file patterns (e.g., '*.py', '*.js') to narrow down the search.
- The list_code_definition_names tool parses source code files and extracts function names, class names, and variable definitions. This is invaluable for understanding code structure in large projects. Supports Python, JavaScript/TypeScript, Java, C/C++, Go, Rust, Ruby, and PHP.
- The apply_diff tool allows you to make targeted changes to files using SEARCH/REPLACE blocks with line numbers. This is more efficient than write_to_file for making small, specific edits to existing files.

====

MODES

- These are the currently available modes:
  * "Code" mode (code) - Use this mode when you need to write, modify, or refactor code. Ideal for implementing features, fixing bugs, creating new files, or making code improvements across any programming language or framework.
  * "Architect" mode (architect) - Use this mode when you need to plan, design, or strategize before implementation. Perfect for breaking down complex problems, creating technical specifications, designing system architecture, or brainstorming solutions before coding.
  * "Ask" mode (ask) - Use this mode when you need explanations, documentation, or answers to technical questions. Best for understanding concepts, analyzing existing code, getting recommendations, or learning about technologies without making changes.
  * "Debug" mode (debug) - Use this mode when you're troubleshooting issues, investigating errors, or diagnosing problems. Specialized in systematic debugging, adding logging, analyzing stack traces, and identifying root causes before applying fixes.
  * "Orchestrator" mode (orchestrator) - Use this mode for complex, multi-step projects that require coordination across different specialties. Ideal when you need to break down large tasks into subtasks, manage workflows, or coordinate work that spans multiple domains or expertise areas.

====

RULES

- The project base directory is: {cwd}
- All file paths must be relative to this directory. However, commands may change directories in terminals, so respect working directory specified by the response to execute_command.
- You cannot `cd` into a different directory to complete a task. You are stuck operating from '{cwd}', so be sure to pass in the correct 'path' parameter when using tools that require a path.
- Do not use the ~ character or $HOME to refer to the home directory.
- Before using the execute_command tool, you must first think about the SYSTEM INFORMATION context provided to understand the user's environment and tailor your commands to ensure they are compatible with their system. You must also consider if the command you need to run should be executed in a specific directory outside of the current working directory '{cwd}', and if so prepend with `cd`'ing into that directory && then executing the command (as one command since you are stuck operating from '{cwd}'). For example, if you needed to run `npm install` in a project outside of '{cwd}', you would need to prepend with a `cd` i.e. pseudocode for this would be `cd (path to project) && (command, in this case npm install)`. Note: Using `&&` for bash command chaining (conditional execution).
- Some modes have restrictions on which files they can edit. If you attempt to edit a restricted file, the operation will be rejected with a FileRestrictionError that will specify which file patterns are allowed for the current mode.
- Be sure to consider the type of project (e.g. Python, JavaScript, web application) when determining the appropriate structure and files to include. Also consider what files may be most relevant to accomplishing the task, for example looking at a project's manifest file would help you understand the project's dependencies, which you could incorporate into any code you write.
  * For example, in architect mode trying to edit app.js would be rejected because architect mode can only edit files matching r"\.md$"
- When making changes to code, always consider the context in which the code is being used. Ensure that your changes are compatible with the existing codebase and that they follow the project's coding standards and best practices.
- Do not ask for more information than necessary. Use the tools provided to accomplish your user's request efficiently and effectively. When you've completed your user's task, you must use the attempt_completion tool to present the result of your task to the user. The user may provide feedback, which you can use to make improvements and try again.
- You are only allowed to ask the user questions using the ask_followup_question tool. Use this tool only when you need additional details to complete a task, and be sure to use a clear and concise question that will help you move forward with the task. When you ask a question, provide the user with 2-4 suggested answers based on your question so they don't need to do so much typing. The suggestions should be specific, actionable, and directly related to the completed task. They should be ordered by priority or logical sequence. However if you can use the available tools to avoid having to ask the user questions, you should do so. For example, if the user mentions a file that may be in an outside directory like the Desktop, you should use the list_files tool to list the files in the Desktop and check if the file they're talking about is there, rather than asking the user to provide the file path themselves.
- When executing commands, if you don't see the expected output, assume the terminal executed the command successfully and proceed with the task. The user's terminal may be unable to stream the output back properly. If you absolutely need to see the actual terminal output, use the ask_followup_question tool to request the user to copy and paste it back to you.
- The user may provide a file's contents directly in their message, in which case you shouldn't use the read_file tool to get the file contents again since you already have it.
- Your goal is to try to accomplish the user's task, NOT engage in a back and back conversation.
- NEVER end attempt_completion result with a question or request to engage in further conversation! Formulate the end of your result in a way that is final and does not require further input from the user.
- You are STRICTLY FORBIDDEN from starting your messages with "Great", "Certainly", "Okay", "Sure". You should NOT be conversational in your responses, but rather direct and to the point. For example you should NOT say "Great, I've updated the CSS" but instead something like "I've updated the CSS". It is important you be clear and technical in your messages.
- When presented with images, utilize your vision capabilities to thoroughly examine them and extract meaningful information. Incorporate these insights into your thought process as you accomplish the user's task.
- At the end of each user message, you will automatically receive environment_details. This information is not written by the user themselves, but is auto-generated to provide potentially relevant context about the project structure and environment. While this information can be valuable for understanding the project context, do not treat it as explicitly part of the user's request or response unless they clearly do so in their message. Use it to inform your actions and decisions, but don't assume the user is explicitly asking about or referring to this information unless they clearly do so in their message. When using environment_details, explain your actions clearly to ensure the user understands, as they may not be aware of these details.
- Before executing commands, check the "Actively Running Terminals" section in environment_details. If present, consider how these active processes might impact your task. For example, if a local development server is already running, you wouldn't need to start it again. If no active terminals are listed, proceed with command execution as normal.
- It is critical you wait for the user's response after each tool use, in order to confirm the success of the tool use. For example, if asked to make a todo app, you would create a file, wait for the user's response it was created successfully, then create another file if needed, wait for the user's response it was created successfully, etc.

====

SYSTEM INFORMATION

Operating System: Linux
Default Shell: /bin/bash
Home Directory: /home/ubuntu
Current Workspace Directory: {cwd}

The Current Workspace Directory is the active VS Code project directory, and is therefore the default directory for all tool operations. New terminals will be created in the current workspace directory, however if you change directories in a terminal it will then have a different working directory; changing directories in a terminal does not modify the workspace directory, because you do not have access to change the workspace directory. If you need to further explore directories such as outside the current workspace directory, you can use the list_files tool. If you pass 'true' for the recursive parameter, it will list files recursively. Otherwise, it will list files at the top level, which is better suited for generic directories where you don't necessarily need the nested structure, like the Desktop.

====

OBJECTIVE

You accomplish a given task iteratively, breaking it down into clear steps and working through them methodically.

1. Analyze the user's task and set clear, achievable goals to accomplish it. Prioritize these goals in a logical order.
2. Work through these goals sequentially, utilizing available tools one at a time as necessary. Each goal should correspond to a distinct step in your problem-solving process. You will be informed on the work completed and what's remaining as you go.
3. Remember, you have extensive capabilities with access to a range of tools that can be used in powerful and clever ways as necessary to accomplish each goal. Before calling a tool, do some analysis. First, analyze the file structure provided in environment_details to gain context and insights for proceeding effectively. Next, think about which of the available tools is the most relevant tool to accomplish the user's task. Go through each of the required parameters of the relevant tool and determine if the user has directly provided or given enough information to infer a value. When deciding if the parameter can be inferred, carefully consider all the context to see if it supports a specific value. If all of the required parameters are present or can be reasonably inferred, proceed with the tool use. BUT, if one of the values for the required parameters is missing, DO NOT invoke the tool (not even with fillers for the missing params) and instead, ask the user to provide the missing parameters using the ask_followup_question tool. DO NOT ask for more information on optional parameters if it is not provided.
4. Once you've completed the user's task, you must use the attempt_completion tool to present the result of your task to the user.
5. The user may provide feedback, which you can use to make improvements and try again. But DO NOT continue in pointless back and forth conversations, i.e. don't end your responses with questions or offers for further assistance."""


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
            timeout=timeout
        )
        
        output = result.stdout if result.stdout else result.stderr
        return json.dumps({
            "success": result.returncode == 0,
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
    
    try:
        full_path = Path(path)
        if not full_path.exists():
            return json.dumps({"error": f"File not found: {path}"})
        
        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        if mode == "indentation":
            anchor_line = indentation.get("anchor_line", 1)
            max_levels = indentation.get("max_levels", 0)
            include_siblings = indentation.get("include_siblings", False)
            include_header = indentation.get("include_header", True)
            max_lines = indentation.get("max_lines", 2000)
            
            # Simple indentation-based extraction
            if anchor_line < 1 or anchor_line > len(lines):
                anchor_line = 1
            
            # Find the indentation level of the anchor line
            anchor_indent = len(lines[anchor_line - 1]) - len(lines[anchor_line - 1].lstrip())
            
            # Extract lines with similar or less indentation
            result_lines = []
            if include_header:
                # Add imports/header (lines before anchor with less indentation)
                for i in range(anchor_line - 1):
                    line_indent = len(lines[i]) - len(lines[i].lstrip())
                    if line_indent < anchor_indent or lines[i].strip() == "":
                        result_lines.append((i + 1, lines[i]))
            
            # Add anchor block
            result_lines.append((anchor_line, lines[anchor_line - 1]))
            
            # Add following lines with same or greater indentation
            for i in range(anchor_line, min(len(lines), anchor_line + max_lines)):
                line_indent = len(lines[i]) - len(lines[i].lstrip())
                if line_indent >= anchor_indent or lines[i].strip() == "":
                    result_lines.append((i + 1, lines[i]))
                elif include_siblings and line_indent == anchor_indent:
                    result_lines.append((i + 1, lines[i]))
                else:
                    break
            
            # Format with line numbers
            output = []
            for line_num, line in result_lines[:max_lines]:
                output.append(f"{line_num:5d} | {line.rstrip()}")
            
            return json.dumps({
                "success": True,
                "content": "\n".join(output),
                "mode": "indentation"
            })
        else:
            # Slice mode
            start = max(0, offset - 1)
            end = min(len(lines), start + limit)
            
            output = []
            for i in range(start, end):
                output.append(f"{i + 1:5d} | {lines[i].rstrip()}")
            
            return json.dumps({
                "success": True,
                "content": "\n".join(output),
                "mode": "slice",
                "offset": offset,
                "limit": limit
            })
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
    
    try:
        target_path = Path(path)
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        if not target_path.is_dir():
            return json.dumps({"error": f"Path is not a directory: {path}"})
        
        files = []
        if recursive:
            # Use os.walk for recursive listing
            for root, dirs, filenames in os.walk(target_path):
                # Sort directories and files for consistent output
                dirs.sort()
                filenames.sort()
                for filename in filenames:
                    full_path = Path(root) / filename
                    rel_path = full_path.relative_to(target_path)
                    files.append(str(rel_path))
        else:
            # Non-recursive listing
            for item in sorted(target_path.iterdir()):
                files.append(item.name)
        
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
    
    try:
        import re
        target_path = Path(path)
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        # Compile the regex pattern
        try:
            pattern = re.compile(regex_pattern)
        except re.error as e:
            return json.dumps({"error": f"Invalid regex pattern: {str(e)}"})
        
        # Try to use ripgrep if available (faster)
        try:
            from fnmatch import fnmatch
            results = []
            
            # Walk through files
            for root, dirs, filenames in os.walk(target_path):
                for filename in filenames:
                    # Apply file pattern filter
                    if not fnmatch(filename, file_pattern):
                        continue
                    
                    full_path = Path(root) / filename
                    try:
                        with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line_num, line in enumerate(f, 1):
                                if pattern.search(line):
                                    rel_path = full_path.relative_to(target_path)
                                    results.append({
                                        "file": str(rel_path),
                                        "line": line_num,
                                        "content": line.rstrip()
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
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        })


def tool_list_code_definition_names(args: Dict[str, Any]) -> str:
    """List code definitions (functions, classes, variables) in source files."""
    path = args.get("path", ".")
    
    try:
        import re
        target_path = Path(path)
        
        if not target_path.exists():
            return json.dumps({"error": f"Path not found: {path}"})
        
        # Language-specific regex patterns for code definitions
        patterns = {
            # Python: def function_name, class ClassName, @decorator
            '.py': [
                (r'^\s*def\s+(\w+)\s*\(', 'function'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*(\w+)\s*=\s*', 'variable'),
                (r'^\s*@(\w+)', 'decorator'),
            ],
            # JavaScript/TypeScript: function name, const name =, class Name, export function
            '.js': [
                (r'^\s*function\s+(\w+)\s*\(', 'function'),
                (r'^\s*const\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)', 'function'),
                (r'^\s*let\s+(\w+)\s*=', 'variable'),
                (r'^\s*var\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*export\s+(?:default\s+)?(?:const|let|var|function|class)\s+(\w+)', 'export'),
            ],
            '.ts': [
                (r'^\s*function\s+(\w+)\s*\(', 'function'),
                (r'^\s*const\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)', 'function'),
                (r'^\s*let\s+(\w+)\s*=', 'variable'),
                (r'^\s*var\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*interface\s+(\w+)', 'interface'),
                (r'^\s*type\s+(\w+)', 'type'),
                (r'^\s*export\s+(?:default\s+)?(?:const|let|var|function|class|interface|type)\s+(\w+)', 'export'),
            ],
            '.jsx': [
                (r'^\s*function\s+(\w+)\s*\(', 'function'),
                (r'^\s*const\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)', 'function'),
                (r'^\s*let\s+(\w+)\s*=', 'variable'),
                (r'^\s*var\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*export\s+(?:default\s+)?(?:const|let|var|function|class)\s+(\w+)', 'export'),
            ],
            '.tsx': [
                (r'^\s*function\s+(\w+)\s*\(', 'function'),
                (r'^\s*const\s+(\w+)\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>)', 'function'),
                (r'^\s*let\s+(\w+)\s*=', 'variable'),
                (r'^\s*var\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*interface\s+(\w+)', 'interface'),
                (r'^\s*type\s+(\w+)', 'type'),
                (r'^\s*export\s+(?:default\s+)?(?:const|let|var|function|class|interface|type)\s+(\w+)', 'export'),
            ],
            # Java: public/private/protected class, method, field
            '.java': [
                (r'^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?class\s+(\w+)', 'class'),
                (r'^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?\w+\s+(\w+)\s*\(', 'method'),
                (r'^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?\w+\s+(\w+)\s*=', 'field'),
            ],
            # C/C++
            '.c': [
                (r'^\s*(?:static\s+)?(?:inline\s+)?\w+\s+(\w+)\s*\(', 'function'),
                (r'^\s*(?:static\s+)?(?:const\s+)?\w+\s+(\w+)\s*=', 'variable'),
            ],
            '.cpp': [
                (r'^\s*(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?\w+\s+(\w+)\s*\(', 'function'),
                (r'^\s*(?:static\s+)?(?:const\s+)?\w+\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
            ],
            '.h': [
                (r'^\s*(?:static\s+)?(?:inline\s+)?\w+\s+(\w+)\s*\(', 'function'),
                (r'^\s*(?:static\s+)?(?:const\s+)?\w+\s+(\w+)\s*=', 'variable'),
                (r'^\s*class\s+(\w+)', 'class'),
            ],
            # Go
            '.go': [
                (r'^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(', 'function'),
                (r'^\s*type\s+(\w+)\s+struct', 'struct'),
                (r'^\s*type\s+(\w+)\s+interface', 'interface'),
                (r'^\s*var\s+(\w+)', 'variable'),
                (r'^\s*const\s+(\w+)', 'constant'),
            ],
            # Rust
            '.rs': [
                (r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(', 'function'),
                (r'^\s*(?:pub\s+)?struct\s+(\w+)', 'struct'),
                (r'^\s*(?:pub\s+)?enum\s+(\w+)', 'enum'),
                (r'^\s*(?:pub\s+)?trait\s+(\w+)', 'trait'),
                (r'^\s*(?:pub\s+)?impl\s+(\w+)', 'impl'),
                (r'^\s*(?:pub\s+)?(?:const|static)\s+(\w+)', 'constant'),
                (r'^\s*let\s+(?:mut\s+)?(\w+)', 'variable'),
            ],
            # Ruby
            '.rb': [
                (r'^\s*def\s+(\w+)', 'method'),
                (r'^\s*def\s+self\.(\w+)', 'class_method'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*module\s+(\w+)', 'module'),
                (r'^\s*(\w+)\s*=', 'variable'),
            ],
            # PHP
            '.php': [
                (r'^\s*(?:public|private|protected)?\s*(?:static\s+)?function\s+(\w+)\s*\(', 'function'),
                (r'^\s*class\s+(\w+)', 'class'),
                (r'^\s*interface\s+(\w+)', 'interface'),
                (r'^\s*trait\s+(\w+)', 'trait'),
                (r'^\s*\$(\w+)\s*=', 'variable'),
            ],
        }
        
        definitions = []
        
        if target_path.is_file():
            # Single file
            files_to_scan = [target_path]
        else:
            # Directory - scan all source files
            files_to_scan = []
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
                    return json.dumps({"error": "Invalid diff format: missing :start_line: in SEARCH block"})
                start_line = int(start_line_match.group(1))
                
                # Skip the separator line
                i += 1
                if i >= len(lines) or lines[i] != '-------':
                    return json.dumps({"error": "Invalid diff format: missing '-------' separator"})
                
                # Collect search content
                i += 1
                search_lines = []
                while i < len(lines) and not lines[i].startswith('======='):
                    search_lines.append(lines[i])
                    i += 1
                
                if i >= len(lines) or not lines[i].startswith('======='):
                    return json.dumps({"error": "Invalid diff format: missing '=======' separator"})
                
                # Skip the separator
                i += 1
                
                # Collect replace content
                replace_lines = []
                while i < len(lines) and not lines[i].startswith('>>>>>>> REPLACE'):
                    replace_lines.append(lines[i])
                    i += 1
                
                if i >= len(lines) or not lines[i].startswith('>>>>>>> REPLACE'):
                    return json.dumps({"error": "Invalid diff format: missing '>>>>>>> REPLACE' marker"})
                
                # Skip the end marker
                i += 1
                
                blocks.append({
                    'start_line': start_line,
                    'search': search_lines,
                    'replace': replace_lines
                })
            else:
                i += 1
        
        if not blocks:
            return json.dumps({"error": "No valid SEARCH/REPLACE blocks found in diff"})
        
        # Apply each block
        modified_lines = original_lines.copy()
        offset = 0  # Track line offset due to previous modifications
        
        for block in blocks:
            start_line = block['start_line'] - 1 + offset  # Convert to 0-based
            search_lines = block['search']
            replace_lines = block['replace']
            
            # Check if start_line is valid
            if start_line < 0 or start_line >= len(modified_lines):
                return json.dumps({
                    "error": f"Invalid start_line {block['start_line']}: file has {len(modified_lines)} lines"
                })
            
            # Check if search content matches
            end_line = start_line + len(search_lines)
            if end_line > len(modified_lines):
                return json.dumps({
                    "error": f"Search block extends beyond file end (line {block['start_line']} + {len(search_lines)} > {len(modified_lines)})"
                })
            
            actual_content = modified_lines[start_line:end_line]
            # Normalize line endings for comparison
            actual_normalized = [line.rstrip('\r\n') + '\n' if line.endswith('\n') else line + '\n' for line in actual_content]
            search_normalized = [line.rstrip('\r\n') + '\n' if line.endswith('\n') else line + '\n' for line in search_lines]
            
            # Handle last line without newline
            if actual_content and not actual_content[-1].endswith('\n'):
                actual_normalized[-1] = actual_content[-1]
            if search_lines and not search_lines[-1].endswith('\n'):
                search_normalized[-1] = search_lines[-1]
            
            if actual_normalized != search_normalized:
                # Show what we found vs what we expected
                return json.dumps({
                    "error": f"Search content mismatch at line {block['start_line']}",
                    "expected": ''.join(search_lines),
                    "found": ''.join(actual_content)
                })
            
            # Apply the replacement
            modified_lines = modified_lines[:start_line] + replace_lines + modified_lines[end_line:]
            
            # Update offset for next block
            offset += len(replace_lines) - len(search_lines)
        
        # Write the modified content back to the file
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


# Tool registry
TOOL_FUNCTIONS = {
    "execute_command": tool_execute_command,
    "read_file": tool_read_file,
    "write_to_file": tool_write_to_file,
    "ask_followup_question": tool_ask_followup_question,
    "attempt_completion": tool_attempt_completion,
    "list_files": tool_list_files,
    "search_files": tool_search_files,
    "list_code_definition_names": tool_list_code_definition_names,
    "apply_diff": tool_apply_diff
}


def execute_tool_call(tool_call: Dict[str, Any]) -> Tuple[str, str]:
    """Execute a tool call and return (tool_name, result_json)."""
    function = tool_call.get("function", {})
    tool_name = function.get("name")
    arguments_str = function.get("arguments", "{}")
    
    try:
        arguments = json.loads(arguments_str) if isinstance(arguments_str, str) else arguments_str
    except json.JSONDecodeError:
        arguments = {}
    
    if tool_name not in TOOL_FUNCTIONS:
        return tool_name, json.dumps({"error": f"Unknown tool: {tool_name}"})
    
    result = TOOL_FUNCTIONS[tool_name](arguments)
    return tool_name, result


# ============================================================================
# Tool Flattening Bypass (CRITICAL)
# ============================================================================

def apply_tool_flattening_bypass(history: List[Dict[str, Any]], tool_name: str, tool_result: str) -> List[Dict[str, Any]]:
    """
    Apply the tool flattening bypass to avoid proxy crashes.
    
    This modifies the history to:
    1. Find the assistant's previous message with tool_calls
    2. DELETE the tool_calls array from that message
    3. Append text note: "[System Note: I executed the tools: tool_name]"
    4. Add a role: "user" message with the tool result
    """
    new_history = []
    tool_calls_found = False
    
    for msg in history:
        if msg.get("role") == "assistant" and "tool_calls" in msg and not tool_calls_found:
            # Found the assistant message with tool calls
            # Create a modified version without tool_calls
            new_msg = msg.copy()
            content = new_msg.get("content", "")
            
            # Append system note to content
            if content:
                new_msg["content"] = f"{content}\n\n[System Note: I executed the tools: {tool_name}]"
            else:
                new_msg["content"] = f"[System Note: I executed the tools: {tool_name}]"
            
            # Remove tool_calls
            if "tool_calls" in new_msg:
                del new_msg["tool_calls"]
            
            new_history.append(new_msg)
            tool_calls_found = True
        else:
            new_history.append(msg)
    
    # Add the tool result as a user message (not as a tool message)
    new_history.append({
        "role": "user",
        "content": f"[Tool Execution Result for {tool_name}]:\n{tool_result}\n\nPlease proceed."
    })
    
    return new_history


# ============================================================================
# Network Layer
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
        with httpx.Client(proxy=ROO_PROXY_URL, timeout=120.0) as client:
            response = client.post(
                API_URL,
                headers=HEADERS,
                json=payload
            )
            response.raise_for_status()
            return response.json()
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
    print_colored(f"  Workspace: {os.getcwd()}", "white")
    print_colored("=" * 60, "cyan")
    print_colored("Type 'exit' or 'quit' to exit\n", "yellow")
    
    # Initialize message history with system prompt
    history = [
        {
            "role": "system",
            "content": get_system_prompt()
        }
    ]
    
    while True:
        try:
            # Get user input
            print_colored("\nYou: ", "green", end="")
            user_input = input().strip()
            
            # Check for exit commands
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
                print_colored("\n[Thinking...]", "yellow")
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
                
                # Add assistant message to history
                history.append(assistant_message)
                
                # Check if there are tool calls
                if tool_calls:
                    # Execute all tool calls
                    tool_results = []
                    for tool_call in tool_calls:
                        tool_name, tool_result = execute_tool_call(tool_call)
                        tool_results.append((tool_name, tool_result))
                    
                    # Display tool results to user
                    for tool_name, tool_result in tool_results:
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
                    
                    # Apply tool flattening bypass for each tool result
                    for tool_name, tool_result in tool_results:
                        history = apply_tool_flattening_bypass(history, tool_name, tool_result)
                    
                    # Continue the loop to get the next response
                    continue
                else:
                    # No tool calls, just text response
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
