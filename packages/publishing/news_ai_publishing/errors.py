"""Safe public errors: no provider, database or credential details."""


class PublicationError(Exception):
    def __init__(self, code: str, message: str = "Publication operation is not eligible"):
        self.code = code
        super().__init__(message)
