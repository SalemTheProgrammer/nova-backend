"""Pydantic schema for the AI panel's rule-based instant insights."""
from __future__ import annotations

from pydantic import BaseModel


class InsightsRead(BaseModel):
    insights: list[str]
