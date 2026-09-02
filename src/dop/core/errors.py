"""Custom errors for MCP CLI."""


class MCPError(Exception):
    """Base error for MCP CLI."""


class ConfigError(MCPError):
    """Configuration error."""


class ValidationError(MCPError):
    """Validation error."""


class StateError(MCPError):
    """State management error."""


class SecurityViolationError(MCPError):
    """Security policy violation."""


class ProcessError(MCPError):
    """Subprocess execution error."""
