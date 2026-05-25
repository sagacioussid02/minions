"""Post-merge deployment verification.

See ``openspec/changes/post-deploy-verification/`` for the contract.
Post-merge sweep fetches the project's production URL + each path in
``manifest.deploy.health_checks``, captures status + latency, and on
any failure files a ``risk=high`` Decision proposing a revert.
"""

from minions.deployments.store import DeploymentStore
from minions.deployments.store_factory import (
    DeploymentStoreLike,
    make_deployment_store,
)

__all__ = ["DeploymentStore", "DeploymentStoreLike", "make_deployment_store"]
