class AgentFrameworkError(Exception):
    """Base exception for framework-level execution failures."""


class DuplicateAgentError(AgentFrameworkError):
    pass


class HopLimitExceeded(AgentFrameworkError):
    pass


class ToolPermissionDenied(AgentFrameworkError):
    pass


class DuplicateExecution(AgentFrameworkError):
    pass


class RetryableFailure(AgentFrameworkError):
    """An operation failure that the configured retry policy may retry."""


class NonRetryableFailure(AgentFrameworkError):
    """An operation failure that must fail immediately."""
