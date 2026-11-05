class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def not_found(entity: str) -> DomainError:
    return DomainError(404, "not_found", f"{entity} was not found")


def version_conflict() -> DomainError:
    return DomainError(409, "version_conflict", "Incident changed; retrieve it and retry")
