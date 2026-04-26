"""Wiki note schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WikiNoteType = Literal["task", "decision", "bug", "workflow", "project", "source"]
WikiConfidence = Literal["low", "medium", "high"]


class WikiNoteInput(BaseModel):
    repo_root: str
    task_id: str
    type: WikiNoteType
    title: str
    summary: str
    evidence: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    confidence: WikiConfidence = "medium"
    next_steps: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class WikiNoteData(BaseModel):
    created: bool
    path: str | None = None
    wikilink: str | None = None
    disabled: bool = False
    message: str = ""
    warning_code: str | None = None
