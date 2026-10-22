"""team-notes: a shared team notebook MCP server (upstream #3).

Lets the agent save findings ("Today's orders summary ...") and search them later.
Notes are kept in a small SQLite file (an emptyDir volume in Kubernetes: notes survive
container restarts, not pod deletion, which is fine for a demo upstream).
No credential is required; it relies on NetworkPolicy so only the gateway can reach it.
This shows the gateway's "optional credential" path.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Annotated, Any

from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field

from .common import build_app, new_server, run

DB_PATH = os.environ.get("NOTES_DB_PATH", "/tmp/notes.db")
_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    return conn


_conn = _db()
mcp = new_server("team-notes", "A shared team notebook. Save summaries with add_note and find them with search_notes.")


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "title": r["title"], "body": r["body"],
            "tags": [t for t in r["tags"].split(",") if t], "created_at": r["created_at"]}


@mcp.tool(description="Check that the team-notes server is alive.")
async def ping() -> dict[str, Any]:
    count = _conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    return {"server": "team-notes", "ok": True, "notes": count}


@mcp.tool(description="Save a note to the shared team notebook.")
async def add_note(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    body: Annotated[str, Field(min_length=1, max_length=10_000)],
    tags: Annotated[list[str] | None, Field(description="Optional tags, e.g. ['orders','daily']")] = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    clean_tags = ",".join(t.strip().lower() for t in (tags or []) if t.strip())
    with _lock:
        cur = _conn.execute("INSERT INTO notes(title, body, tags, created_at) VALUES (?,?,?,?)",
                            (title, body, clean_tags, now))
        _conn.commit()
    return {"saved": True, "id": cur.lastrowid}


@mcp.tool(description="List the most recent notes, optionally filtered by a tag.")
async def list_notes(
    tag: Annotated[str | None, Field(description="Only notes with this tag")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    if tag:
        rows = _conn.execute("SELECT * FROM notes WHERE ','||tags||',' LIKE ? ORDER BY id DESC LIMIT ?",
                             (f"%,{tag.lower()},%", limit)).fetchall()
    else:
        rows = _conn.execute("SELECT * FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"count": len(rows), "notes": [_row(r) for r in rows]}


@mcp.tool(description="Full-text search in note titles and bodies.")
async def search_notes(query: Annotated[str, Field(min_length=1)]) -> dict[str, Any]:
    like = f"%{query}%"
    rows = _conn.execute("SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? ORDER BY id DESC LIMIT 50",
                         (like, like)).fetchall()
    return {"count": len(rows), "notes": [_row(r) for r in rows]}


@mcp.tool(description="Delete a note by ID.")
async def delete_note(note_id: Annotated[int, Field(ge=1)]) -> dict[str, Any]:
    with _lock:
        cur = _conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        _conn.commit()
    if cur.rowcount == 0:
        raise ToolError(f"Note {note_id} not found")
    return {"deleted": True, "id": note_id}


app = build_app(mcp)

if __name__ == "__main__":
    run(app)
