class PreviewError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def busy_error() -> PreviewError:
    return PreviewError(429, 'service_busy', 'Another video is being processed. Try again later.')
