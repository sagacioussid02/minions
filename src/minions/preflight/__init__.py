"""Engineer preflight execution — sandboxed build/test before a PR opens.

See ``openspec/changes/engineer-preflight-execution/`` for the contract.

Engineer crew flow with preflight wired in:

    produce FilePatch[]
        → filter_files (forbidden-path safety)
        → run_preflight(patches, manifest, repo_clone)
            ├─ ok=True  → open PR with verified patches
            └─ ok=False → one retry with failure in prompt
                          (engineer may consult QA / SR_ENGINEER / etc.)
                ├─ ok=True  → open PR
                └─ ok=False → file Question Record, skip PR

No new Decision Records are filed by preflight. Failures land as Question
Records (operator action surface) or as ``EngineerResult(skipped=True)``.
"""

from minions.preflight.models import (
    PreflightConfig,
    PreflightReport,
    PreflightStepResult,
)

__all__ = ["PreflightConfig", "PreflightReport", "PreflightStepResult"]
