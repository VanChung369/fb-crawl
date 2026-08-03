class FbCrawlError(RuntimeError):
    """Base class for all exceptions raised by fb-crawl."""
    code = "fb_crawl_error"
    exit_code = 1

    def __init__(self, safe_message: str, *, target: str = None) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.target = target

class ConfigurationError(FbCrawlError):
    code = "configuration_error"
    exit_code = 2


class ValidationError(FbCrawlError):
    code = "validation_error"
    exit_code = 2

class FetchError(FbCrawlError):
    code = "public_fetch_failed"
    exit_code = 1

class ParseError(FbCrawlError):
    code = "public_parse_failed"
    exit_code = 1

class ExportError(FbCrawlError):
    code = "export_failed"
    exit_code = 4