import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SystemActor:
    """Trusted internal identity. No public request schema accepts this type."""

    name: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_:-]{0,119}", self.name):
            raise ValueError("Invalid system actor identity")
