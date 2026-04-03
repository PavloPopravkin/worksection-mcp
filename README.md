# Worksection MCP Server

[![MCP](https://img.shields.io/badge/MCP-Compatible-blue)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/Python-3.10+-green)](https://www.python.org)

MCP server for [Worksection API](https://worksection.com) integration that allows AI assistants (like Claude) to interact with your projects, tasks, and team in Worksection.

## Features

### 📋 Project Management
- View all projects
- Get detailed project information
- View task statuses in projects

### ✅ Task Operations
- Create new tasks
- Update tasks
- Assign tasks to users
- Change statuses (complete, reopen)
- Delete tasks
- Search tasks
- Manage subtasks

### 👥 Team Management
- View users
- Get specific user information

### 💬 Comments
- Add comments to tasks
- View comments
- Update and delete comments

### 📊 Additional Features
- Manage costs
- Work with tags
- Upload files
- View time reports
- Get account information

## Installation

### Requirements
- Python 3.10 or newer
- Worksection account
- Worksection Admin API key

### 1. Clone the Repository

```bash
git clone https://github.com/PavloPopravkin/worksection-mcp.git
cd worksection-mcp
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Edit the `.env` file with your credentials:

```env
# Worksection Admin API (https://worksection.com/faq/api-admin.html)
WORKSECTION_DOMAIN=yourcompany.worksection.com
WORKSECTION_API_KEY=your_admin_api_key

# Worksection OAuth 2.0 app (optional, for web interface)
OAUTH_CLIENT_ID=your_oauth_client_id
OAUTH_CLIENT_SECRET=your_oauth_client_secret
OAUTH_REDIRECT_URI=https://your-domain.com/oauth/callback

# Random secret for session cookies — generate with: python -c "import secrets; print(secrets.token_hex(32))"
SESSION_SECRET=change_me_generate_a_random_secret
```

#### How to Get Worksection API Key:

1. Log in to your Worksection account
2. Go to account settings
3. Find the "API" section
4. Generate a new Admin API key
5. Copy your Worksection account domain (e.g., `yourcompany.worksection.com`)

Detailed instructions: https://worksection.com/faq/api-admin.html

## Usage

### Running the MCP Server

```bash
python server.py
```

### Configuration in Claude Desktop

Add the following configuration to your `claude_desktop_config.json`:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "worksection": {
      "command": "python",
      "args": ["/absolute/path/to/worksection-mcp/server.py"],
      "env": {
        "WORKSECTION_DOMAIN": "yourcompany.worksection.com",
        "WORKSECTION_API_KEY": "your_admin_api_key"
      }
    }
  }
}
```

Or use `uv` to run:

```json
{
  "mcpServers": {
    "worksection": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/worksection-mcp",
        "run",
        "server.py"
      ],
      "env": {
        "WORKSECTION_DOMAIN": "yourcompany.worksection.com",
        "WORKSECTION_API_KEY": "your_admin_api_key"
      }
    }
  }
}
```

### Using with Docker

```bash
# Build the image
docker-compose build

# Run the server
docker-compose up
```

## Web Interface

The project also includes a web interface for testing and demonstration:

```bash
python web_server.py
```

Then open your browser and navigate to `http://localhost:8000`

The web interface supports:
- OAuth 2.0 authentication
- Interactive chat with MCP tools
- Tool call visualization
- Message history viewing

## Usage Examples

### Creating a Task

```python
# In Claude Desktop, simply type:
"Create a task 'Develop new feature' in project 123456 with description 'Need to add a new button'"
```

### Searching Tasks

```python
# In Claude Desktop:
"Find all tasks with the word 'bug' in the title"
```

### Assigning a Task

```python
# In Claude Desktop:
"Assign task 78910 to user with ID 123"
```

## Available Tools

All available MCP tools:

- `get_projects` - Get list of projects
- `get_project` - Get project information
- `get_tasks` - Get list of tasks
- `get_task` - Get task information
- `post_task` - Create a new task
- `update_task` - Update a task
- `assign_task` - Assign a task
- `complete_task` - Complete a task
- `reopen_task` - Reopen a task
- `delete_task` - Delete a task
- `get_subtasks` - Get subtasks
- `post_subtask` - Create a subtask
- `update_subtask` - Update a subtask
- `add_costs` - Add costs
- `get_costs` - Get costs
- `update_costs` - Update costs
- `delete_costs` - Delete costs
- `get_files` - Get files
- `upload_file` - Upload a file
- `get_users` - Get users
- `get_user` - Get user information
- `get_tags` - Get tags
- `get_task_tags` - Get task tags
- `add_task_tags` - Add tags to task
- `update_task_tags` - Update task tags
- `post_comment` - Add a comment
- `get_comments` - Get comments
- `update_comment` - Update a comment
- `delete_comment` - Delete a comment
- `get_time_reports` - Get time reports
- `get_statuses` - Get statuses
- `search_tasks` - Search tasks
- `get_account_info` - Get account information

## Technical Details

### Architecture

- **FastMCP**: Used to create the MCP server
- **httpx**: Asynchronous HTTP requests to Worksection API
- **Admin API v2**: Integration through Worksection Admin API
- **MD5 hashing**: For request authentication

### Authentication

The server supports two types of authentication:

1. **Admin API** - Uses MD5 hash of the key for authentication (`server.py`)
2. **OAuth 2.0** - For the web interface (`web_server.py`)

## Troubleshooting

### Error: "WORKSECTION_DOMAIN and WORKSECTION_API_KEY must be set"

Make sure you have properly configured environment variables or the `.env` file.

### API Authentication Error

Check:
1. Domain format is correct (should be `yourcompany.worksection.com`)
2. API key is valid
3. Your API key has the necessary permissions

### Docker Issues

Make sure the `.env` file exists and contains the correct credentials.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is open source. Use at your own discretion.

## Resources

- [Worksection API Documentation](https://worksection.com/faq/api-admin.html)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Claude Desktop](https://claude.ai/download)

## Author

Pavlo Popravkin - [GitHub](https://github.com/PavloPopravkin)

## Support

If you have questions or issues, please create an [issue](https://github.com/PavloPopravkin/worksection-mcp/issues) on GitHub.