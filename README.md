# Roo CLI

A terminal-based AI coding agent that connects to the agentrouter.org API. Roo CLI provides an interactive chat interface for code generation, file operations, command execution, and multi-mode AI assistance.

## Features

- **Interactive Chat Interface** - Natural language conversation with the AI agent
- **File Operations** - Read and write files with support for multiple reading modes
- **Command Execution** - Run shell commands directly from the CLI
- **Multiple AI Modes** - Switch between specialized modes:
  - **Code** mode - Write, modify, or refactor code
  - **Architect** mode - Plan, design, and strategize before implementation
  - **Ask** mode - Get explanations, documentation, and answers to technical questions
  - **Debug** mode - Troubleshoot issues, investigate errors, and diagnose problems
  - **Orchestrator** mode - Coordinate complex, multi-step projects across specialties
- **Tool-Calling Capabilities** - The AI can autonomously use tools to accomplish tasks
- **Line-Numbered File Reading** - View files with line numbers for easy reference
- **Indentation-Based Extraction** - Extract complete semantic code blocks

## Installation

### Prerequisites

- Python 3.6 or higher

### Setup

1. **Install the required dependency:**

```bash
pip install httpx
```

2. **Optional: Set up a virtual environment:**

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependency
pip install httpx
```

3. **Verify installation:**

```bash
python roo_cli.py
```

## Usage

### Starting the CLI

Run the Roo CLI from your terminal:

```bash
python roo_cli.py
```

The CLI will display startup information including the model being used and the current workspace directory.

### Exiting the CLI

Type any of the following commands to exit:

- `exit`
- `quit`
- `q`

### Interactive Session Example

```
============================================================================
  Roo CLI - AI Coding Agent
============================================================================
  Model: deepseek-v3.2
  Workspace: /path/to/your/project
============================================================================
Type 'exit' or 'quit' to exit

You: Create a simple Python function to calculate fibonacci numbers

[Thinking...]

Roo: I'll create a Python function to calculate Fibonacci numbers for you.
```

## Available Tools

The AI agent has access to the following tools:

### execute_command

Execute a CLI command on the system.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | Yes | Shell command to execute |
| `cwd` | string/null | Yes | Optional working directory (relative or absolute) |
| `timeout` | number/null | Yes | Timeout in seconds for long-running processes |

**Example:**
```json
{
  "command": "npm run dev",
  "cwd": null,
  "timeout": 30
}
```

### read_file

Read a file and return its contents with line numbers.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Path to the file (relative to workspace) |
| `mode` | string | No | Reading mode: `"slice"` or `"indentation"` (default: `"slice"`) |
| `offset` | integer | No | 1-based line offset to start reading (slice mode, default: 1) |
| `limit` | integer | No | Maximum lines to return (slice mode, default: 2000) |
| `indentation` | object | No | Indentation mode options (see below) |

**Indentation Mode Options:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `anchor_line` | integer | No | 1-based line number to anchor extraction |
| `max_levels` | integer | No | Maximum indentation levels above anchor (0 = unlimited) |
| `include_siblings` | boolean | No | Include sibling blocks at same indentation level |
| `include_header` | boolean | No | Include file header content (default: true) |
| `max_lines` | integer | No | Hard cap on lines returned |

**Example (slice mode):**
```json
{
  "path": "src/app.ts",
  "mode": "slice",
  "offset": 1,
  "limit": 100
}
```

**Example (indentation mode):**
```json
{
  "path": "src/app.ts",
  "mode": "indentation",
  "indentation": {
    "anchor_line": 42,
    "max_levels": 2,
    "include_siblings": true,
    "include_header": true,
    "max_lines": 200
  }
}
```

### write_to_file

Write content to a file, creating directories as needed.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | Yes | Path to the file (relative to workspace) |
| `content` | string | Yes | Complete file content to write |

**Example:**
```json
{
  "path": "config/settings.json",
  "content": "{\n  \"apiKey\": \"your-key\",\n  \"debug\": true\n}"
}
```

### ask_followup_question

Ask the user a question to gather additional information.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | Yes | Clear, specific question |
| `follow_up` | array | Yes | List of 2-4 suggested answers |

**Follow-up Item Properties:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | Yes | Suggested answer text |
| `mode` | string/null | Yes | Optional mode to switch to |

**Example:**
```json
{
  "question": "What is the path to the configuration file?",
  "follow_up": [
    {"text": "./config/settings.json", "mode": null},
    {"text": "./src/config.json", "mode": null},
    {"text": "Let me search for it", "mode": "code"}
  ]
}
```

### attempt_completion

Present the completion result to the user.

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `result` | string | Yes | Final result message |

**Example:**
```json
{
  "result": "I've created the Fibonacci function in src/fibonacci.py"
}
```

## Configuration

### Environment Variables

The Roo CLI requires the following environment variables to be set before running:

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `ROO_API_KEY` | Yes | Your API bearer token for authentication | `sk-xxxxxxxxxxxx` |
| `ROO_PROXY_URL` | Yes | Full residential proxy URL with authentication | `http://user:pass@proxy:port/` |
| `ROO_MODEL` | Yes | The primary AI model to use | `deepseek-v3.2` |

#### Setting Environment Variables

**Option 1: Using a `.env` file (Recommended)**

Create a `.env` file in the same directory as [`roo_cli.py`](roo_cli.py):

```env
ROO_API_KEY=your_api_key_here
ROO_PROXY_URL=http://user:pass@proxy:port/
ROO_MODEL=deepseek-v3.2
```

Then install the optional `python-dotenv` package:

```bash
pip install python-dotenv
```

**Option 2: Setting in Shell (Linux/macOS)**

```bash
export ROO_API_KEY=your_api_key_here
export ROO_PROXY_URL=http://user:pass@proxy:port/
export ROO_MODEL=deepseek-v3.2
```

To make these persistent, add them to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.).

**Option 3: Setting in PowerShell (Windows)**

```powershell
$env:ROO_API_KEY='your_api_key_here'
$env:ROO_PROXY_URL='http://user:pass@proxy:port/'
$env:ROO_MODEL='deepseek-v3.2'
```

To make these persistent, add them to your PowerShell profile.

**Option 4: Setting in Command Prompt (Windows)**

```cmd
set ROO_API_KEY=your_api_key_here
set ROO_PROXY_URL=http://user:pass@proxy:port/
set ROO_MODEL=deepseek-v3.2
```

### Additional Configuration

The following settings are configured in [`roo_cli.py`](roo_cli.py):

| Setting | Value | Description |
|---------|-------|-------------|
| `API_URL` | `https://agentrouter.org/v1/chat/completions` | API endpoint |
| `FALLBACK_MODEL` | `glm-4.6` | Fallback AI model |
| `temperature` | `0.7` | AI response randomness |
| `timeout` | `120.0` | API request timeout (seconds) |
| `max_iterations` | `20` | Maximum tool iterations per request |

### Workspace Directory

The CLI uses the current working directory as the workspace. All file operations are relative to this directory.

## Security Notes

⚠️ **Important Security Considerations:**

1. **Environment Variables** - Sensitive credentials (API key, proxy URL) are now loaded from environment variables. Never commit your `.env` file to version control. Add `.env` to your `.gitignore` file.

2. **Command Execution** - The `execute_command` tool allows the AI to run arbitrary shell commands. Only use this CLI in trusted environments and review commands before execution.

3. **File Access** - The AI can read and write files within the workspace directory. Ensure you understand what files the AI may access.

4. **Proxy Usage** - The CLI routes traffic through a proxy server. Ensure you trust the proxy provider.

5. **No Input Validation** - User inputs are not extensively validated. Be cautious with special characters and command injection attempts.

### Recommended Security Practices

- Store sensitive credentials in environment variables (not in source code)
- Add `.env` to your `.gitignore` file
- Review the code before running commands suggested by the AI
- Use version control to track changes made by the AI
- Run the CLI in a sandboxed environment for untrusted tasks
- Rotate your API keys regularly
- Use different API keys for different environments (dev, staging, production)

## Examples

### Example 1: Creating a New File

```
You: Create a Python script that prints "Hello, World!"

[Thinking...]

[Written] hello.py

[Task Complete] I've created hello.py with a simple Hello World script.
```

### Example 2: Reading and Modifying Code

```
You: Read the main.py file and add error handling

[Thinking...]

[Executing] cat main.py

[Reading] main.py
    1 | def main():
    2 |     print("Starting...")
    3 |     result = calculate()
    4 |     print(result)

[Written] main.py

[Task Complete] I've added try-except error handling to main.py.
```

### Example 3: Running Commands

```
You: Install the required dependencies and run the tests

[Thinking...]

[Executing] pip install -r requirements.txt

[Executing] pytest tests/

[Task Complete] Dependencies installed and tests passed successfully.
```

### Example 4: Interactive Question

```
You: Set up a database connection

[Thinking...]

[Question] Which database type should I use?

Suggested answers:
  1. PostgreSQL
  2. MySQL
  3. SQLite
  4. MongoDB

Your answer: 1

[Written] database.py

[Task Complete] I've created database.py with PostgreSQL connection setup.
```

## Troubleshooting

### Connection Issues

If you experience connection problems:

1. Check your internet connection
2. Verify the proxy server is accessible
3. Ensure the API endpoint is correct

### Command Execution Failures

If commands fail:

1. Verify the command syntax for your operating system
2. Check that required tools are installed
3. Ensure you have proper permissions

### File Access Errors

If file operations fail:

1. Verify the file path is correct
2. Check file permissions
3. Ensure the workspace directory is accessible

## License

This project is provided as-is for educational and development purposes.

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.
