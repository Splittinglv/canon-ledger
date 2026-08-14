#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .chapter_commit_schema import normalize_accepted_events
from .story_contracts import StoryContractPaths, read_json_if_exists, write_json


class EventLogStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = StoryContractPaths.from_project_root(self.project_root)

    @contextmanager
    def _connect(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """统一 SQLite 连接管理，确保连接始终关闭。"""
        db_path = self.project_root / ".canon-ledger" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def write_events(self, chapter: int, events: Any) -> Path:
        normalized = self.normalize_events(chapter, events)
        path = self.paths.event_json(chapter)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.is_file()
        old_bytes = path.read_bytes() if existed else b""
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self._replace_sqlite_chapter(conn, chapter, normalized)
                # Keep the SQLite transaction open until the atomically
                # written chapter snapshot is visible.  A failure restores
                # the prior file and rolls the transaction back, so callers
                # never receive success for a half-replaced event mirror.
                write_json(path, normalized)
                conn.commit()
        except Exception:
            self._restore_event_file(path, existed=existed, old_bytes=old_bytes)
            raise
        return path

    def read_events(self, chapter: int) -> List[Dict[str, Any]]:
        return list(read_json_if_exists(self.paths.event_json(chapter)) or [])

    def list_recent(self, chapter: int | None = None, limit: int = 200) -> List[Dict[str, Any]]:
        db_path = self.project_root / ".canon-ledger" / "index.db"
        if not db_path.is_file():
            return []
        with self._connect(row_factory=True) as conn:
            try:
                if chapter is not None:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        WHERE chapter = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chapter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        ORDER BY chapter DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "chapter": row["chapter"],
                    "event_type": row["event_type"],
                    "subject": row["subject"],
                    "payload": payload,
                }
            )
        return result

    def health(self) -> Dict[str, Any]:
        db_path = self.project_root / ".canon-ledger" / "index.db"
        file_count = len(list(self.paths.events_dir.glob("chapter_*.events.json")))
        sqlite_rows = 0
        if db_path.is_file():
            with self._connect() as conn:
                try:
                    sqlite_rows = int(
                        conn.execute("SELECT COUNT(*) FROM story_events").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    sqlite_rows = 0
        return {"ok": True, "sqlite_rows": sqlite_rows, "event_files": file_count}

    def normalize_events(self, chapter: int, events: Any) -> List[Dict[str, Any]]:
        return normalize_accepted_events(chapter, events)

    def _replace_sqlite_chapter(
        self,
        conn: sqlite3.Connection,
        chapter: int,
        events: List[Dict[str, Any]],
    ) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS story_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                chapter INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                subject TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_story_events_chapter ON story_events(chapter)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_story_events_type ON story_events(event_type)"
        )
        conn.execute("DELETE FROM story_events WHERE chapter = ?", (int(chapter),))
        conn.executemany(
            """
            INSERT INTO story_events(event_id, chapter, event_type, subject, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    event["event_id"],
                    int(event["chapter"]),
                    event["event_type"],
                    event["subject"],
                    json.dumps(event.get("payload") or {}, ensure_ascii=False),
                )
                for event in events
            ],
        )

    @staticmethod
    def _restore_event_file(path: Path, *, existed: bool, old_bytes: bytes) -> None:
        if not existed:
            path.unlink(missing_ok=True)
            return
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".restore",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(old_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
