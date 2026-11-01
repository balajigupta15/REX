"""Typed request / response models and the structured error used by every /ai route."""
from __future__ import annotations
from typing import Any, Literal
from fastapi import status
from pydantic import BaseModel, Field


class AIError(Exception):
    """Structured error: code is machine-readable, message is for people, detail is optional extra text, retryable tells the UI whether to offer a retry."""
    def __init__(self, code: str, message: str, detail: str | None = None, retryable: bool = False, http: int = status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.code, self.message, self.detail, self.retryable, self.http = code, message, detail, retryable, http

    def dump(self) -> dict:
        return dict(code=self.code, message=self.message, detail=self.detail, retryable=self.retryable)


class ErrorOut(BaseModel):
    code: str
    message: str
    detail: str | None = None
    retryable: bool = False


class Target(BaseModel):
    market: Literal["crypto", "nse"] = "crypto"
    symbol: str = "BTCUSDT"
    interval: Literal["1d", "4h", "1h"] = "1h"
    cost_bps: float | None = Field(default=None, ge=0, le=500)


class ChatReq(BaseModel):
    message: str = Field(min_length=2, max_length=1500)
    session_id: str | None = None
    parent_version_id: str | None = None
    target: Target | None = None            # required for the first message of a session (the UI always sends it)


class RunReq(BaseModel):
    goal: str = Field(min_length=3, max_length=600)
    target: Target | None = None            # when absent it is read from the goal text (BTC/ETH/SOL, 1h/4h/daily, NSE names)
    rounds: int = Field(default=3, ge=1, le=4)
    style: Literal["any", "momentum", "mean-reversion"] = "any"
    max_drawdown_pct: float = Field(default=50, gt=0, le=100)
    offline: bool = False                   # force offline mode (no Gemini calls)


class StepOut(BaseModel):
    idx: int
    role: str
    tool: str
    status: Literal["running", "ok", "error", "skipped"]
    summary: str | None = None
    inputs: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: ErrorOut | None = None
    started: float | None = None
    ended: float | None = None


class RunOut(BaseModel):
    id: str
    goal: str
    status: Literal["queued", "running", "done", "failed", "interrupted"]
    mode: Literal["gemini", "offline"]
    session_id: str | None = None
    target: dict[str, Any] = {}
    created: float
    updated: float
    steps: list[StepOut] = []
    experiments: list[dict[str, Any]] = []
    debate: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    final: dict[str, Any] | None = None
    error: ErrorOut | None = None
    max_steps: int = 0
    notices: list[str] = []


class LiveStartReq(BaseModel):
    pass
