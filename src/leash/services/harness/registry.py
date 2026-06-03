"""Harness client registry.

Registry of all available harness clients. Provides lookup by name and enumeration.
Register new clients here to extend support for additional AI coding assistants.
"""

from __future__ import annotations

from leash.services.harness.base import HarnessClient


class HarnessClientRegistry:
    """Registry of harness clients keyed by their machine-readable name."""

    def __init__(self, clients: list[HarnessClient] | None = None) -> None:
        self._clients: dict[str, HarnessClient] = {}
        for client in clients or []:
            self._clients[client.name.lower()] = client

    def get(self, name: str) -> HarnessClient | None:
        """Get a client by name (e.g. 'claude', 'copilot'). Returns None if not found."""
        return self._clients.get(name.lower())

    def get_required(self, name: str) -> HarnessClient:
        """Get a client by name, raising ValueError if not found."""
        client = self.get(name)
        if client is None:
            raise ValueError(f"Unknown harness client: {name}")
        return client

    def get_all(self) -> list[HarnessClient]:
        """Return all registered clients."""
        return list(self._clients.values())

    def get_names(self) -> list[str]:
        """Return all registered client names."""
        return list(self._clients.keys())
