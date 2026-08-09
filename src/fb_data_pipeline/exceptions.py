from fb_crawl.core.exceptions import FbCrawlError


class PipelineExecutionError(FbCrawlError):
    code = "pipeline_execution_failed"
    exit_code = 5
