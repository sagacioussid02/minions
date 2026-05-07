"""Magic-link approval webhook (FastAPI app, deployed to Fly.io).

Entry points:
- ``minions.webhook.app:create_app`` — factory used by tests
- ``minions.webhook.app:app`` — module-level instance used by uvicorn
"""

from minions.webhook.app import app, create_app

__all__ = ["app", "create_app"]
