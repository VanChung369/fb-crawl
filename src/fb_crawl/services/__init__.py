from importlib import import_module

__all__ = [
    "AuthenticatedService",
    "CheckpointingService",
    "IdentityRepairService",
    "DataMergeService",
    "DataPlanService",
    "PhoneEvidenceMergeService",
]

_PUBLIC_ATTRIBUTES = {
    "AuthenticatedService": ("fb_crawl.services.authenticated", "AuthenticatedService"),
    "CheckpointingService": ("fb_crawl.services.checkpoint", "CheckpointingService"),
    "IdentityRepairService": ("fb_crawl.services.identity_repair", "IdentityRepairService"),
    "DataMergeService": ("fb_crawl.services.data_merge", "DataMergeService"),
    "DataPlanService": ("fb_crawl.services.data_plan", "DataPlanService"),
    "PhoneEvidenceMergeService": (
        "fb_crawl.services.phone_evidence_merge",
        "PhoneEvidenceMergeService",
    ),
    "CrawlWorker": ("fb_crawl.services.worker", "CrawlWorker"),
    "JobExecutionControl": ("fb_crawl.services.worker", "JobExecutionControl"),
    "LeaseHeartbeat": ("fb_crawl.services.worker", "LeaseHeartbeat"),
    "LeaseLost": ("fb_crawl.services.worker", "LeaseLost"),
    "WorkerPolicy": ("fb_crawl.services.worker", "WorkerPolicy"),
}


def __getattr__(name: str):
    try:
        module_name, attribute_name = _PUBLIC_ATTRIBUTES[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *__all__])
