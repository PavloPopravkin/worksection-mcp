#!/usr/bin/env python3
"""
Worksection MCP Server
Provides tools to interact with Worksection API for project management
"""

import os
import hashlib
import httpx
import urllib.parse
from typing import Optional, Any
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("worksection")

# Configuration from environment variables
WORKSECTION_DOMAIN = os.getenv("WORKSECTION_DOMAIN", "")  # e.g., "youraccount.worksection.com"
WORKSECTION_API_KEY = os.getenv("WORKSECTION_API_KEY", "")


def get_admin_hash(query_params: str) -> str:
    """Generate MD5 hash for admin API authentication"""
    return hashlib.md5(f"{query_params}{WORKSECTION_API_KEY}".encode()).hexdigest()


def build_url(action: str, **params) -> str:
    """Build URL for Worksection API request.

    Hash is computed on raw (unencoded) query string so the server can verify it.
    Values in the URL itself are URL-encoded so special chars (& — \\n etc.) don't
    break query-string parsing.
    """
    # Drop None values
    clean = {k: str(v) for k, v in params.items() if v is not None}

    # Raw query string used only for hash computation (order: action first, then params)
    raw_parts = [f"action={action}"] + [f"{k}={v}" for k, v in clean.items()]
    raw_query = "&".join(raw_parts)
    hash_value = get_admin_hash(raw_query)

    # URL-encoded query string for the actual HTTP request
    ordered = {"action": action}
    ordered.update(clean)
    ordered["hash"] = hash_value
    encoded_query = urllib.parse.urlencode(ordered, quote_via=urllib.parse.quote)

    return f"https://{WORKSECTION_DOMAIN}/api/admin/v2/?{encoded_query}"


async def make_request(
    action: str,
    method: str = "GET",
    files: Optional[dict] = None,
    **params,
) -> dict[str, Any]:
    """Make a request to Worksection API"""
    if not WORKSECTION_DOMAIN or not WORKSECTION_API_KEY:
        raise ValueError("WORKSECTION_DOMAIN and WORKSECTION_API_KEY must be set in environment variables")

    url = build_url(action, **params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            response = await client.get(url)
        elif method == "POST":
            if files:
                response = await client.post(url, files=files)
            else:
                response = await client.post(url)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_projects(page: Optional[int] = None) -> dict[str, Any]:
    """
    Get list of all projects in Worksection

    Args:
        page: Optional page number for pagination
    """
    params = {}
    if page is not None:
        params["page"] = page
    return await make_request("get_projects", **params)


@mcp.tool()
async def get_project(project_id: str) -> dict[str, Any]:
    """
    Get detailed information about a specific project

    Args:
        project_id: The ID of the project
    """
    return await make_request("get_project", id_project=project_id)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_tasks(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """
    Get list of tasks from Worksection

    Args:
        project_id: Optional project ID to filter tasks
        status: Optional task status filter (active, done, all)
        page: Optional page number for pagination
    """
    params = {}
    if project_id:
        params["id_project"] = project_id
    if status:
        params["status"] = status
    if page:
        params["page"] = page
    return await make_request("get_tasks", **params)


@mcp.tool()
async def get_task(task_id: str) -> dict[str, Any]:
    """
    Get detailed information about a specific task

    Args:
        task_id: The ID of the task
    """
    return await make_request("get_task", id_task=task_id)


@mcp.tool()
async def post_task(
    project_id: str,
    title: str,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    user_to: Optional[str] = None,
    date_begin: Optional[str] = None,
    date_end: Optional[str] = None,
    tags: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a new task in a project

    Args:
        project_id: The ID of the project
        title: Task title
        description: Optional task description (supports HTML)
        priority: Optional priority (low, normal, high, urgent)
        user_to: Optional executor email or user ID
        date_begin: Optional start date (DD.MM.YYYY)
        date_end: Optional deadline date (DD.MM.YYYY)
        tags: Optional comma-separated tag names
    """
    params: dict[str, Any] = {"id_project": project_id, "title": title}
    if description:
        params["description"] = description
    if priority:
        params["priority"] = priority
    if user_to:
        params["user_to"] = user_to
    if date_begin:
        params["date_begin"] = date_begin
    if date_end:
        params["date_end"] = date_end
    if tags:
        params["tags"] = tags
    return await make_request("post_task", method="POST", **params)


@mcp.tool()
async def update_task(
    project_id: str,
    task_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    date_begin: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update an existing task's fields

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        title: New title
        description: New description (supports HTML)
        priority: New priority (low, normal, high, urgent)
        date_begin: New start date (DD.MM.YYYY)
        date_end: New deadline date (DD.MM.YYYY)
    """
    params: dict[str, Any] = {"id_project": project_id, "id_task": task_id}
    if title:
        params["title"] = title
    if description:
        params["description"] = description
    if priority:
        params["priority"] = priority
    if date_begin:
        params["date_begin"] = date_begin
    if date_end:
        params["date_end"] = date_end
    return await make_request("update_task", method="POST", **params)


@mcp.tool()
async def assign_task(
    project_id: str,
    task_id: str,
    user_to: str,
) -> dict[str, Any]:
    """
    Assign an executor to a task

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        user_to: Executor email or user ID
    """
    return await make_request(
        "update_task", method="POST",
        id_project=project_id, id_task=task_id, user_to=user_to,
    )


@mcp.tool()
async def complete_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Mark a task as completed

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
    """
    return await make_request(
        "complete_task", method="POST",
        id_project=project_id, id_task=task_id,
    )


@mcp.tool()
async def reopen_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Reopen a completed task (set back to active)

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
    """
    return await make_request(
        "reopen_task", method="POST",
        id_project=project_id, id_task=task_id,
    )


@mcp.tool()
async def delete_task(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Delete a task permanently

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
    """
    return await make_request(
        "delete_task", method="POST",
        id_project=project_id, id_task=task_id,
    )


# ---------------------------------------------------------------------------
# Subtasks
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_subtasks(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Get subtasks of a task

    Args:
        project_id: The ID of the project
        task_id: The ID of the parent task
    """
    return await make_request("get_tasks", id_project=project_id, id_task=task_id)


@mcp.tool()
async def post_subtask(
    project_id: str,
    parent_task_id: str,
    title: str,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    user_to: Optional[str] = None,
    date_begin: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict[str, Any]:
    """
    Create a subtask under an existing task

    Args:
        project_id: The ID of the project
        parent_task_id: The ID of the parent task
        title: Subtask title
        description: Optional description (supports HTML)
        priority: Optional priority (low, normal, high, urgent)
        user_to: Optional executor email or user ID
        date_begin: Optional start date (DD.MM.YYYY)
        date_end: Optional deadline date (DD.MM.YYYY)
    """
    params: dict[str, Any] = {
        "id_project": project_id,
        "id_parent": parent_task_id,
        "title": title,
    }
    if description:
        params["description"] = description
    if priority:
        params["priority"] = priority
    if user_to:
        params["user_to"] = user_to
    if date_begin:
        params["date_begin"] = date_begin
    if date_end:
        params["date_end"] = date_end
    return await make_request("post_task", method="POST", **params)


@mcp.tool()
async def update_subtask(
    project_id: str,
    task_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    priority: Optional[str] = None,
    date_begin: Optional[str] = None,
    date_end: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update an existing subtask (same as update_task, subtasks are tasks with a parent)

    Args:
        project_id: The ID of the project
        task_id: The ID of the subtask
        title: New title
        description: New description
        priority: New priority (low, normal, high, urgent)
        date_begin: New start date (DD.MM.YYYY)
        date_end: New deadline date (DD.MM.YYYY)
    """
    params: dict[str, Any] = {"id_project": project_id, "id_task": task_id}
    if title:
        params["title"] = title
    if description:
        params["description"] = description
    if priority:
        params["priority"] = priority
    if date_begin:
        params["date_begin"] = date_begin
    if date_end:
        params["date_end"] = date_end
    return await make_request("update_task", method="POST", **params)


# ---------------------------------------------------------------------------
# Costs / Time tracking  (action names: add_costs, update_costs, delete_costs, get_costs)
# ---------------------------------------------------------------------------

@mcp.tool()
async def add_costs(
    project_id: str,
    task_id: str,
    user_id: str,
    time: str,
    date: Optional[str] = None,
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """
    Add a time/cost entry to a task (was incorrectly called post_time before)

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        user_id: The ID of the user who spent the time
        time: Time spent in minutes (e.g. "90") or hours:minutes (e.g. "1:30")
        date: Date of work in DD.MM.YYYY format (defaults to today)
        comment: Optional comment for the time entry
    """
    params: dict[str, Any] = {
        "id_project": project_id,
        "id_task": task_id,
        "id_user": user_id,
        "time": time,
    }
    if date:
        params["date"] = date
    if comment:
        params["comment"] = comment
    return await make_request("add_costs", **params)


@mcp.tool()
async def get_costs(
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get cost/time entries

    Args:
        project_id: Optional project ID filter
        task_id: Optional task ID filter
        user_id: Optional user ID filter
        date_from: Optional start date (DD.MM.YYYY)
        date_to: Optional end date (DD.MM.YYYY)
    """
    params: dict[str, Any] = {}
    if project_id:
        params["id_project"] = project_id
    if task_id:
        params["id_task"] = task_id
    if user_id:
        params["id_user"] = user_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return await make_request("get_costs", **params)


@mcp.tool()
async def update_costs(
    project_id: str,
    task_id: str,
    cost_id: str,
    time: Optional[str] = None,
    date: Optional[str] = None,
    comment: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Update an existing time/cost entry

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        cost_id: The ID of the cost entry to update
        time: New time value in minutes or hours:minutes
        date: New date (DD.MM.YYYY)
        comment: New comment
        user_id: New user ID
    """
    params: dict[str, Any] = {
        "id_project": project_id,
        "id_task": task_id,
        "id_cost": cost_id,
    }
    if time:
        params["time"] = time
    if date:
        params["date"] = date
    if comment:
        params["comment"] = comment
    if user_id:
        params["id_user"] = user_id
    return await make_request("update_costs", **params)


@mcp.tool()
async def delete_costs(
    project_id: str,
    task_id: str,
    cost_id: str,
) -> dict[str, Any]:
    """
    Delete a time/cost entry

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        cost_id: The ID of the cost entry to delete
    """
    return await make_request(
        "delete_costs",
        id_project=project_id, id_task=task_id, id_cost=cost_id,
    )


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_files(
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """
    Get files attached to a task or project

    Args:
        project_id: Optional project ID
        task_id: Optional task ID
        page: Optional page number for pagination
    """
    params: dict[str, Any] = {}
    if project_id:
        params["id_project"] = project_id
    if task_id:
        params["id_task"] = task_id
    if page:
        params["page"] = page
    return await make_request("get_files", **params)


@mcp.tool()
async def upload_file(
    project_id: str,
    task_id: str,
    file_path: str,
    comment: Optional[str] = None,
) -> dict[str, Any]:
    """
    Upload and attach a file to a task

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        file_path: Absolute path to the local file to upload
        comment: Optional comment to attach with the file
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    params: dict[str, Any] = {"id_project": project_id, "id_task": task_id}
    if comment:
        params["comment"] = comment

    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        file_data = fh.read()

    files = {"file": (file_name, file_data)}
    return await make_request("upload_file", method="POST", files=files, **params)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_users(page: Optional[int] = None) -> dict[str, Any]:
    """
    Get list of all users in the account

    Args:
        page: Optional page number for pagination
    """
    params: dict[str, Any] = {}
    if page:
        params["page"] = page
    return await make_request("get_users", **params)


@mcp.tool()
async def get_user(user_id: str) -> dict[str, Any]:
    """
    Get information about a specific user

    Args:
        user_id: The ID of the user
    """
    return await make_request("get_user", id_user=user_id)


# ---------------------------------------------------------------------------
# Tags / Labels
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_tags() -> dict[str, Any]:
    """Get list of all tags (labels) in the account"""
    return await make_request("get_tags")


@mcp.tool()
async def get_task_tags(project_id: str, task_id: str) -> dict[str, Any]:
    """
    Get tags assigned to a specific task

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
    """
    return await make_request("get_task_tags", id_project=project_id, id_task=task_id)


@mcp.tool()
async def add_task_tags(
    project_id: str,
    task_id: str,
    tags: str,
) -> dict[str, Any]:
    """
    Add tags to a task (existing tags are kept)

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        tags: Comma-separated list of tag names to add
    """
    return await make_request(
        "add_task_tags", method="POST",
        id_project=project_id, id_task=task_id, tags=tags,
    )


@mcp.tool()
async def update_task_tags(
    project_id: str,
    task_id: str,
    tags: str,
) -> dict[str, Any]:
    """
    Replace all tags on a task with the given list

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        tags: Comma-separated list of tag names (replaces existing tags)
    """
    return await make_request(
        "update_task_tags", method="POST",
        id_project=project_id, id_task=task_id, tags=tags,
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@mcp.tool()
async def post_comment(
    project_id: str,
    task_id: str,
    text: str,
) -> dict[str, Any]:
    """
    Add a comment to a task

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        text: Comment text (supports HTML)
    """
    return await make_request(
        "post_comment", method="POST",
        id_project=project_id, id_task=task_id, text=text,
    )


@mcp.tool()
async def get_comments(
    project_id: str,
    task_id: str,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """
    Get comments for a task

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        page: Optional page number for pagination
    """
    params: dict[str, Any] = {"id_project": project_id, "id_task": task_id}
    if page:
        params["page"] = page
    return await make_request("get_comments", **params)


@mcp.tool()
async def update_comment(
    project_id: str,
    task_id: str,
    comment_id: str,
    text: str,
) -> dict[str, Any]:
    """
    Edit an existing comment

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        comment_id: The ID of the comment to update
        text: New comment text (supports HTML)
    """
    return await make_request(
        "update_comment", method="POST",
        id_project=project_id, id_task=task_id, id_comment=comment_id, text=text,
    )


@mcp.tool()
async def delete_comment(
    project_id: str,
    task_id: str,
    comment_id: str,
) -> dict[str, Any]:
    """
    Delete a comment

    Args:
        project_id: The ID of the project
        task_id: The ID of the task
        comment_id: The ID of the comment to delete
    """
    return await make_request(
        "delete_comment", method="POST",
        id_project=project_id, id_task=task_id, id_comment=comment_id,
    )


# ---------------------------------------------------------------------------
# Reporting / Time reports (legacy alias kept for compatibility)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_time_reports(
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict[str, Any]:
    """
    Get time tracking reports (alias for get_costs with YYYY-MM-DD date format support)

    Args:
        project_id: Optional project ID filter
        user_id: Optional user ID filter
        date_from: Optional start date (DD.MM.YYYY or YYYY-MM-DD)
        date_to: Optional end date (DD.MM.YYYY or YYYY-MM-DD)
    """
    params: dict[str, Any] = {}
    if project_id:
        params["id_project"] = project_id
    if user_id:
        params["id_user"] = user_id
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return await make_request("get_costs", **params)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_statuses(project_id: Optional[str] = None) -> dict[str, Any]:
    """
    Get list of custom task statuses

    Args:
        project_id: Optional project ID to get project-specific statuses
    """
    params: dict[str, Any] = {}
    if project_id:
        params["id_project"] = project_id
    return await make_request("get_statuses", **params)


@mcp.tool()
async def search_tasks(
    query: str,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    page: Optional[int] = None,
) -> dict[str, Any]:
    """
    Search for tasks by text query

    Args:
        query: Search query text
        project_id: Optional project ID filter
        status: Optional status filter (active, done, all)
        page: Optional page number for pagination
    """
    params: dict[str, Any] = {"query": query}
    if project_id:
        params["id_project"] = project_id
    if status:
        params["status"] = status
    if page:
        params["page"] = page
    return await make_request("search_tasks", **params)


@mcp.tool()
async def get_account_info() -> dict[str, Any]:
    """Get information about the Worksection account (limits, settings, etc.)"""
    return await make_request("get_account")


if __name__ == "__main__":
    mcp.run()
