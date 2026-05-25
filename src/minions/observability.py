"""Langfuse observability — auto-traces every CrewAI LLM call when credentials are present.

## What you get

- One trace per crew run (planning / engineer), grouping all LLM calls inside.
- Per-LLM-call generations: prompt, response, latency, token counts, est. cost.
- Filterable by metadata (project, decision_id, dry_run, etc.).
- Self-hostable (Docker) or hosted (cloud.langfuse.com — free tier ~50k events/mo).

## Setup

Set two env vars (or put them in ``.env`` — orchestrator loads it on startup):

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL (optional)
```

Then ``minions langfuse`` to verify, ``minions plan Demo --no-dry-run`` to
generate your first trace.

## Why a lazy wrapper

The Langfuse SDK prints "Authentication error: client initialized without
public_key" if you call ``get_client()`` without creds. Our ``observe_crew``
decorator checks credentials at *call* time, so when Langfuse isn't
configured, it's a true pass-through — no noise.
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def has_credentials() -> bool:
    """True if Langfuse public + secret keys are present in the environment."""
    return bool(
        os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
    )


def init_langfuse(*, verbose: bool = False) -> bool:
    """Enable LiteLLM auto-tracing if credentials are present.

    CrewAI uses LiteLLM under the hood; flipping these callbacks makes every
    LLM call auto-trace into Langfuse. Idempotent — safe to call repeatedly.

    Returns True if callbacks were registered, False otherwise.
    """
    if not has_credentials():
        if verbose:
            logger.info(
                "Langfuse credentials not set (LANGFUSE_PUBLIC_KEY/SECRET_KEY); "
                "observability disabled."
            )
        return False
    try:
        import litellm  # type: ignore[import-not-found]
    except ImportError:
        if verbose:
            logger.warning("litellm not installed; cannot enable Langfuse callbacks")
        return False

    if "langfuse" not in (litellm.success_callback or []):
        litellm.success_callback = list(litellm.success_callback or []) + ["langfuse"]
    if "langfuse" not in (litellm.failure_callback or []):
        litellm.failure_callback = list(litellm.failure_callback or []) + ["langfuse"]
    if verbose:
        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        logger.info("Langfuse LiteLLM callbacks registered (host: %s)", host)
    return True


def observe_crew(name: str, *, as_type: str = "agent") -> Callable[[F], F]:
    """Decorator: wrap a crew runner as a Langfuse span when credentials are set.

    No-op pass-through when Langfuse isn't configured — no noisy warnings.
    Checks credentials at call time so toggling env vars takes effect on the
    next invocation without re-importing.
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not has_credentials():
                return fn(*args, **kwargs)
            try:
                from langfuse import observe as _observe
            except ImportError:
                return fn(*args, **kwargs)
            wrapped = _observe(name=name, as_type=as_type)(fn)
            return wrapped(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def add_metadata(metadata: dict[str, Any] | None = None, **extra: Any) -> None:
    """Attach metadata to the current Langfuse span.

    Safe no-op when Langfuse isn't configured.
    """
    if not has_credentials():
        return
    try:
        from langfuse import get_client
    except ImportError:
        return
    merged: dict[str, Any] = dict(metadata or {})
    merged.update(extra)
    try:
        get_client().update_current_span(metadata=merged)
    except Exception as e:
        # Observability failure should never break the actual work.
        logger.debug("Langfuse add_metadata failed: %s", e)


def flush() -> None:
    """Flush pending Langfuse events. Safe no-op if disabled."""
    if not has_credentials():
        return
    try:
        from langfuse import get_client
    except ImportError:
        return
    try:
        get_client().flush()
    except Exception as e:
        logger.debug("Langfuse flush failed: %s", e)


def host_url() -> str:
    """Resolve the Langfuse host URL (cloud default)."""
    return os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")


def auth_check() -> tuple[bool, str]:
    """Verify Langfuse credentials by hitting the SDK's auth_check endpoint.

    Returns ``(ok, message)``. Used by ``minions langfuse`` CLI.
    """
    if not has_credentials():
        return (
            False,
            "credentials not set (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)",
        )
    try:
        from langfuse import get_client
    except ImportError:
        return (False, "langfuse package not installed")
    try:
        result = get_client().auth_check()
        # auth_check returns True or raises in v4; treat any truthy as OK.
        if result:
            return (True, f"authenticated to {host_url()}")
        return (False, f"auth_check returned {result!r}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")
