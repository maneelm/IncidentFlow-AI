class InvestigationFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        diagnostics: dict | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.code = code
        self.retryable = retryable
        # Callers supply bounded metadata, never exception messages or model text.
        self.diagnostics = diagnostics or {}
        self.retry_after = retry_after
        super().__init__(code)


class LostClaim(Exception):
    """A cancelled, expired or superseded owner must stop without persisting effects."""
