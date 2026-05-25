"""Preflight domain models.

Three classes, all Pydantic:

* :class:`PreflightConfig` — per-project knobs (manifest schema).
* :class:`PreflightStepResult` — outcome of one step (install / typecheck
  / build / test / lint).
* :class:`PreflightReport` — full sweep outcome, ridden on the
  ``EngineerRunRecord`` as serialized JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Steps run in this order; the first failure of a required step aborts.
REQUIRED_STEPS: tuple[str, ...] = ("install", "typecheck", "build")
OPTIONAL_STEPS: tuple[str, ...] = ("test", "lint")
ALL_STEPS: tuple[str, ...] = REQUIRED_STEPS + OPTIONAL_STEPS


class NetworkPosture(StrEnum):
    DENY = "deny"
    ALLOW_INSTALL_ONLY = "allow_install_only"
    ALLOW = "allow"


class PreflightConfig(BaseModel):
    """Per-project preflight knobs.

    Empty command strings trigger autodetect (see ``preflight.autodetect``).
    ``enabled=False`` makes preflight a no-op — used for projects with no
    runnable build (e.g., pure-docs repos).
    """

    enabled: bool = True
    install: str = ""
    typecheck: str = ""
    build: str = ""
    test: str = ""
    lint: str = ""
    timeout_seconds: int = 600
    network: NetworkPosture = NetworkPosture.ALLOW_INSTALL_ONLY
    block_on_test_failure: bool = True

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return v


class PreflightStepResult(BaseModel):
    """Outcome of a single preflight step."""

    step: Literal["install", "typecheck", "build", "test", "lint"]
    command: str
    exit_code: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    ok: bool


class PreflightReport(BaseModel):
    """Full preflight outcome for one engineer attempt."""

    project: str
    commit_sha: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    network_posture: NetworkPosture
    steps: list[PreflightStepResult] = Field(default_factory=list)
    ok: bool
    consultation_role: str | None = None
    consultation_response: str | None = None

    @property
    def failed_step(self) -> PreflightStepResult | None:
        for s in self.steps:
            if not s.ok:
                return s
        return None

    @property
    def total_duration_seconds(self) -> float:
        return sum(s.duration_seconds for s in self.steps)
