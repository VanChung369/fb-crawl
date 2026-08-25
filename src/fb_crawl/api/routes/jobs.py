"""Authenticated crawl-job HTTP routes."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    EVENT_COUNTER_NAMES,
    EventCountersResponse,
    GroupBatchCreateRequest,
    GroupBatchCreateResponse,
    JobCreateRequest,
    JobEventPageResponse,
    JobEventResponse,
    JobOptionsResponse,
    JobPageResponse,
    JobResponse,
    JobTargetPageResponse,
    JobTargetResponse,
)
from fb_crawl.core.jobs import (
    CrawlEvent,
    CrawlJob,
    CrawlTarget,
    JobCreateCommand,
    JobNotFound,
    JobStatus,
    SafeJobOptions,
    canonical_job_target,
)
from fb_crawl.core.models import AuthenticatedAction
from fb_crawl.services.jobs import JobService


ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
    409: {"model": ApiErrorResponse},
}


def create_jobs_router(
    job_service: JobService,
    job_repository: Any,
    auth: ApiKeyAuth,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/jobs",
        tags=["jobs"],
        dependencies=[Depends(auth)],
    )

    @router.post(
        "",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
    )
    def create_job(
        request: JobCreateRequest,
        idempotency: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
            ),
        ],
    ) -> JobResponse:
        action = AuthenticatedAction(request.action)
        options = SafeJobOptions.from_mapping(action, request.options)
        targets = tuple(canonical_job_target(action, value) for value in request.targets)
        command = JobCreateCommand(action=action, targets=targets, options=options)
        job, _created = job_service.create(command, idempotency_key=idempotency)
        return _job_response(job)

    @router.get(
        "",
        response_model=JobPageResponse,
        responses=ERROR_RESPONSES,
    )
    def list_jobs(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ) -> JobPageResponse:
        page = job_repository.list_jobs(limit=limit, cursor=cursor)
        return JobPageResponse(
            items=[_job_response(job) for job in page.items],
            next_cursor=page.next_cursor,
        )

    @router.post(
        "/group-batches",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=GroupBatchCreateResponse,
        responses=ERROR_RESPONSES,
    )
    def create_group_batches(
        request: GroupBatchCreateRequest,
        idempotency: Annotated[
            str,
            Header(
                alias="Idempotency-Key",
                min_length=1,
                max_length=128,
            ),
        ],
    ) -> GroupBatchCreateResponse:
        action = AuthenticatedAction.MEMBERS
        options = SafeJobOptions.from_mapping(
            action,
            {
                "max_users": request.batch_size,
                "max_duration_seconds": request.batch_duration_seconds,
                "navigation_delay_seconds": request.navigation_delay_seconds,
                "steps": request.steps,
                "call_fbnumber": request.call_fbnumber,
            },
        )
        target = canonical_job_target(action, request.group_url)
        jobs: list[CrawlJob] = []
        created_count = 0
        for index in range(1, request.batch_count + 1):
            command = JobCreateCommand(
                action=action,
                targets=(target,),
                options=options,
            )
            job, created = job_service.create(
                command,
                idempotency_key=f"{idempotency}:batch:{index}",
            )
            if created:
                created_count += 1
            jobs.append(job)
        return GroupBatchCreateResponse(
            total_requested=request.batch_count,
            total_created=created_count,
            items=[_job_response(job) for job in jobs],
        )

    @router.get(
        "/{job_id}",
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
    )
    def get_job(job_id: UUID) -> JobResponse:
        return _job_response(_require_job(job_repository, job_id))

    @router.delete(
        "/{job_id}",
        status_code=status.HTTP_200_OK,
        responses=ERROR_RESPONSES,
    )
    def delete_job(job_id: UUID) -> dict[str, Any]:
        job = _require_job(job_repository, job_id)
        if job.status in {JobStatus.RUNNING, JobStatus.CANCELLING}:
            try:
                job_service.cancel(job_id)
            except Exception:
                pass
        job_repository.delete_job(job_id)
        return {"status": "success", "message": f"Job {job_id} đã được xóa thành công."}

    @router.get(
        "/{job_id}/targets",
        response_model=JobTargetPageResponse,
        responses=ERROR_RESPONSES,
    )
    def list_targets(
        job_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ) -> JobTargetPageResponse:
        _require_job(job_repository, job_id)
        page = job_repository.list_targets(job_id, limit=limit, cursor=cursor)
        return JobTargetPageResponse(
            items=[_target_response(target) for target in page.items],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/{job_id}/events",
        response_model=JobEventPageResponse,
        responses=ERROR_RESPONSES,
    )
    def list_events(
        job_id: UUID,
        after_id: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
    ) -> JobEventPageResponse:
        _require_job(job_repository, job_id)
        page = job_repository.list_events(job_id, after_id=after_id, limit=limit)
        return JobEventPageResponse(
            items=[_event_response(event) for event in page.items],
            next_cursor=page.next_cursor,
        )

    @router.post(
        "/{job_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
    )
    def cancel_job(job_id: UUID) -> JobResponse:
        return _job_response(job_service.cancel(job_id))

    @router.post(
        "/{job_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=JobResponse,
        responses=ERROR_RESPONSES,
    )
    def retry_job(job_id: UUID) -> JobResponse:
        return _job_response(job_service.retry(job_id))

    return router


def _require_job(job_repository: Any, job_id: UUID) -> CrawlJob:
    job = job_repository.get_job(job_id)
    if job is None:
        raise JobNotFound("Crawl job was not found.")
    return job


def _job_response(job: CrawlJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        mode=job.mode.value,
        action=job.action.value,
        status=job.status,
        options=JobOptionsResponse(**job.request_options.to_canonical_dict()),
        retry_of_job_id=job.retry_of_job_id,
        priority=job.priority,
        attempt=job.attempt,
        cancel_requested_at=job.cancel_requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        requested_targets=job.requested_targets,
        completed_targets=job.completed_targets,
        failed_targets=job.failed_targets,
        discovered_users=job.discovered_users,
        persisted_users=job.persisted_users,
        provider_retries_required=job.provider_retries_required,
        current_target_id=job.current_target_id,
        error_code=job.error_code,
        error_message=job.error_message,
    )


def _target_response(target: CrawlTarget) -> JobTargetResponse:
    return JobTargetResponse(
        id=target.id,
        job_id=target.job_id,
        target_key=target.target_key,
        target_url=target.target_url,
        target_kind=target.target_kind,
        position=target.position,
        status=target.status,
        created_at=target.created_at,
        updated_at=target.updated_at,
        attempt=target.attempt,
        started_at=target.started_at,
        finished_at=target.finished_at,
        steps_completed=target.steps_completed,
        items_discovered=target.items_discovered,
        users_persisted=target.users_persisted,
        provider_retries_required=target.provider_retries_required,
        error_code=target.error_code,
        error_message=target.error_message,
    )


def _event_response(event: CrawlEvent) -> JobEventResponse:
    counters = {
        key: value
        for key, value in event.counters.items()
        if (
            key in EVENT_COUNTER_NAMES
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        )
    }
    return JobEventResponse(
        id=event.id,
        job_id=event.job_id,
        event_type=event.event_type,
        level=event.level,
        created_at=event.created_at,
        target_id=event.target_id,
        safe_message=event.safe_message,
        counters=EventCountersResponse(**counters),
    )
