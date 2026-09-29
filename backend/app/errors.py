"""Shared OilTrace exception types."""
class RealDataUnavailableError(RuntimeError):
    """Raised when a required live provider/model is unavailable and fallback is disabled."""
