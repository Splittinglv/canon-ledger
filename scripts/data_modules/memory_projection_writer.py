#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from .config import DataModulesConfig
from .memory.writer import MemoryWriter


class MemoryProjectionWriter:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def apply(self, commit_payload: dict) -> dict:
        if commit_payload["meta"]["status"] != "accepted":
            return {"applied": False, "writer": "memory", "reason": "commit_rejected"}
        result = MemoryWriter(DataModulesConfig.from_project_root(self.project_root)).apply_commit_projection(
            commit_payload
        )
        if (result or {}).get("error"):
            return {
                "applied": False,
                "writer": "memory",
                "reason": f"error:{result['error']}",
                **(result or {}),
            }
        return {
            "applied": bool(
                (result or {}).get("items_added")
                or (result or {}).get("items_updated")
                or (result or {}).get("items_resolved")
                or (result or {}).get("items_preserved")
            ),
            "writer": "memory",
            **(result or {}),
        }
