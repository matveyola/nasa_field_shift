class NotFoundError(Exception):
    """A requested domain record does not exist."""


class ConflictError(Exception):
    """A request conflicts with the current state of a domain record."""
