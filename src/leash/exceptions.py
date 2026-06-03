"""Custom exception types for Leash."""


class ConfigurationException(Exception):
    """Raised when configuration loading or validation fails."""


class StorageException(Exception):
    """Raised when session or configuration storage operations fail."""
