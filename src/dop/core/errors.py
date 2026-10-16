"""Custom errors for the dop CLI."""


class MCPError(Exception):
    """Base error for the dop CLI."""


class ConfigError(MCPError):
    """Configuration or usage error (exit 2)."""


class ToolMissing(MCPError):
    """A named executable a command needs is not on PATH (exit 2)."""

    def __init__(self, tool: str) -> None:
        super().__init__(f"required tool not found on PATH: {tool}")
        self.tool = tool


class ValidationError(MCPError):
    """Validation error."""


class SecurityViolationError(MCPError):
    """Security policy violation."""


class ProcessError(MCPError):
    """Subprocess execution error."""
