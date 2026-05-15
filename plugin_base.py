from abc import ABC, abstractmethod
from typing import Any


class ClockApp(ABC):
    def __init__(self) -> None:
        self.enabled = True

    @abstractmethod
    def update(self) -> Any:
        """Fetch or compute the plugin payload."""

    @abstractmethod
    def send(self, data: Any) -> None:
        """Send processed data to AWTRIX."""
