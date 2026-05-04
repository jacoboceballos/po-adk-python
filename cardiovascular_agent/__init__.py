"""
cardiovascular_agent — Cardiovascular risk assessment agent package.

Bootstraps logging and re-exports root_agent for ADK discovery.
"""
import warnings

# Suppress noisy gRPC/protobuf deprecation warnings during startup.
warnings.filterwarnings(
    "ignore",
    message=".*deprecated.*",
    category=UserWarning,
)

from shared.logging_utils import configure_logging  # noqa: E402
configure_logging("cardiovascular_agent")

from .agent import root_agent  # noqa: E402, F401

__all__ = ["root_agent"]