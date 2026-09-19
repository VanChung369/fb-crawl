"""Bounded persisted-user query routes and transport-independent input helpers."""

from typing import TYPE_CHECKING

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import Page
from fb_data_pipeline.repositories.users import (
    EnrichmentAttemptView,
    PhoneEvidenceView,
    UserQuery,
    UserQueryRepository,
    UserSummary,
)

if TYPE_CHECKING:
    from fastapi import APIRouter

    from fb_crawl.api.dependencies import ApiKeyAuth


class UserNotFound(ValidationError):
    """Stable transport-safe missing persisted-user error."""

    code = "user_not_found"


def build_user_query(
    *,
    q: object = None,
    uid: object = None,
    username: object = None,
    phone: object = None,
    phone_origin: object = None,
    has_phone: object = None,
    limit: object = 20,
    cursor: object = None,
) -> UserQuery:
    """Construct the Task 5 typed query before repository access."""

    return UserQuery(
        q=q,
        uid=uid,
        username=username,
        phone=phone,
        phone_origin=phone_origin,
        has_phone=has_phone,
        limit=limit,
        cursor=cursor,
    )


def query_user_page(
    user_repository: UserQueryRepository,
    **filters: object,
) -> Page[UserSummary]:
    """Execute one bounded list query using the closed Task 5 contract."""

    return user_repository.list_users(build_user_query(**filters))


def require_user(user_repository: UserQueryRepository, user_id: int) -> UserSummary:
    user = user_repository.get_user(user_id)
    if user is None:
        raise UserNotFound("Persisted user was not found.")
    return user


def query_phone_evidence_page(
    user_repository: UserQueryRepository,
    user_id: int,
    *,
    limit: object = 20,
    cursor: object = None,
) -> Page[PhoneEvidenceView]:
    """Validate pagination, require the parent user, and return evidence."""

    pagination = UserQuery(limit=limit, cursor=cursor)
    require_user(user_repository, user_id)
    return user_repository.list_phone_evidence(
        user_id,
        limit=pagination.limit,
        cursor=pagination.cursor,
    )


def query_enrichment_attempt_page(
    user_repository: UserQueryRepository,
    user_id: int,
    *,
    limit: object = 20,
    cursor: object = None,
) -> Page[EnrichmentAttemptView]:
    """Validate pagination, require the parent user, and return attempts."""

    pagination = UserQuery(limit=limit, cursor=cursor)
    require_user(user_repository, user_id)
    return user_repository.list_enrichment_attempts(
        user_id,
        limit=pagination.limit,
        cursor=pagination.cursor,
    )


def user_response_values(user: UserSummary) -> dict[str, object]:
    """Allow-list identity, profile, phone-slot, and timestamp fields."""

    return {
        "id": user.id,
        "facebook_uid": user.facebook_uid,
        "username": user.username,
        "name": user.name,
        "profile_url": user.profile_url,
        "phone_1": user.phone_1,
        "phone_2": user.phone_2,
        "address": user.address,
        "birth_date": user.birth_date,
        "gender": user.gender,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def phone_evidence_response_values(evidence: PhoneEvidenceView) -> dict[str, object]:
    """Allow-list stored phone evidence without provider request internals."""

    return {
        "id": evidence.id,
        "normalized_phone": evidence.normalized_phone,
        "display_phone": evidence.display_phone,
        "origin": evidence.origin,
        "source": evidence.source,
        "source_url": evidence.source_url,
        "provider": evidence.provider,
        "confidence": evidence.confidence,
        "first_captured_at": evidence.first_captured_at,
        "last_captured_at": evidence.last_captured_at,
        "evidence_count": evidence.evidence_count,
        "created_at": evidence.created_at,
        "updated_at": evidence.updated_at,
    }


def enrichment_attempt_response_values(
    attempt: EnrichmentAttemptView,
) -> dict[str, object]:
    """Allow-list attempt status while omitting stored error/provider internals."""

    return {
        "id": attempt.id,
        "provider": attempt.provider,
        "status": attempt.status,
        "checked_at": attempt.checked_at,
        "error_code": attempt.error_code or None,
        "values_found": attempt.values_found,
        "created_at": attempt.created_at,
    }


def create_users_router(
    user_repository: UserQueryRepository,
    auth: "ApiKeyAuth",
) -> "APIRouter":
    """Create the authenticated, read-only persisted-user API surface."""

    from typing import Annotated

    from fastapi import APIRouter, Depends, Path, Query

    from fb_crawl.api.schemas import (
        ApiErrorResponse,
        EnrichmentAttemptPageResponse,
        EnrichmentAttemptResponse,
        FBNumberScansItemResponse,
        FBNumberScansSyncRequest,
        FBNumberScansSyncResponse,
        PhoneEvidencePageResponse,
        PhoneEvidenceResponse,
        UserPageResponse,
        UserResponse,
        UserUpdateRequest,
    )
    from fastapi import HTTPException, status

    error_responses = {
        400: {"model": ApiErrorResponse},
        401: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    }
    router = APIRouter(
        prefix="/api/v1/users",
        tags=["users"],
        dependencies=[Depends(auth)],
    )

    @router.post(
        "/sync-fbnumber-scans",
        response_model=FBNumberScansSyncResponse,
        responses=error_responses,
    )
    def sync_fbnumber_scans(
        request: FBNumberScansSyncRequest,
    ) -> FBNumberScansSyncResponse:
        from fb_crawl.api.routes.settings import effective_fbnumber_config
        from fb_data_pipeline.importers.fbnumber_scans import (
            DEFAULT_FBNUMBER_SCANS_URL,
            fetch_fbnumber_scans,
            import_scan_item,
            sync_scans_data_to_repository,
        )
        from fb_data_pipeline.repositories.postgres import PostgresRepository

        config = effective_fbnumber_config()
        token = (request.api_token or config.get("api_token") or "").strip()
        if not token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="FB_NUMBER_API_TOKEN là bắt buộc. Vui lòng cấu hình trong Cài đặt hoặc nhập trực tiếp.",
            )

        api_url = request.api_url or DEFAULT_FBNUMBER_SCANS_URL

        try:
            raw_response = fetch_fbnumber_scans(
                api_token=token,
                page_number=request.page_number,
                page_size=request.page_size,
                filter_query=request.filter,
                api_url=api_url,
            )
        except PermissionError as err:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(err),
            )
        except Exception as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Lỗi khi gọi FBNumber Scans API: {str(err)}",
            )

        data_items = raw_response.get("data") if isinstance(raw_response.get("data"), list) else []
        total_count = int(raw_response.get("totalCount") or len(data_items))

        if request.preview:
            preview_items: list[FBNumberScansItemResponse] = []
            for item in data_items:
                enriched = import_scan_item(
                    item,
                    default_country_code=config.get("default_country_code", "84"),
                )
                if enriched:
                    phones = [ev.normalized_phone for ev in enriched.bundle.evidence]
                    preview_items.append(
                        FBNumberScansItemResponse(
                            uid=enriched.bundle.identity.uid or None,
                            username=enriched.bundle.identity.username or None,
                            name=enriched.bundle.identity.name or None,
                            profile_url=enriched.bundle.identity.profile_url or None,
                            phone_1=phones[0] if len(phones) > 0 else None,
                            phone_2=phones[1] if len(phones) > 1 else None,
                            address=enriched.bundle.profile.address or None,
                            gender=enriched.bundle.profile.gender or None,
                            birthday=enriched.bundle.profile.birth_date or None,
                            scan_at=item.get("scanAt"),
                        )
                    )
            return FBNumberScansSyncResponse(
                success=True,
                total_count=total_count,
                fetched_count=len(data_items),
                imported_count=0,
                skipped_count=max(0, len(data_items) - len(preview_items)),
                preview=True,
                message=f"Đã lấy thành công {len(preview_items)} bản ghi xem trước từ FBNumber (Tổng trên hệ thống: {total_count}).",
                items=preview_items,
            )

        postgres_repo = PostgresRepository(
            user_repository.database_url,
            statement_timeout_seconds=getattr(user_repository, "statement_timeout_ms", 5000) / 1000.0,
        )
        sync_result = sync_scans_data_to_repository(
            data_items,
            postgres_repo,
            default_country_code=config.get("default_country_code", "84"),
        )

        return FBNumberScansSyncResponse(
            success=True,
            total_count=total_count,
            fetched_count=sync_result.fetched_count,
            imported_count=sync_result.imported_count,
            skipped_count=sync_result.skipped_count,
            preview=False,
            message=f"Đã đồng bộ và cập nhật thành công {sync_result.imported_count}/{sync_result.fetched_count} bản ghi vào cơ sở dữ liệu (Tổng trên FBNumber: {total_count}).",
            items=[FBNumberScansItemResponse(**item) for item in sync_result.items],
            errors=sync_result.errors,
        )


    @router.get(
        "",
        response_model=UserPageResponse,
        responses=error_responses,
    )
    def list_users(
        q: Annotated[str | None, Query(max_length=256)] = None,
        uid: Annotated[str | None, Query(max_length=128)] = None,
        username: Annotated[str | None, Query(max_length=256)] = None,
        phone: Annotated[str | None, Query(max_length=64)] = None,
        phone_origin: Annotated[str | None, Query(max_length=32)] = None,
        has_phone: Annotated[bool | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ) -> UserPageResponse:
        page = query_user_page(
            user_repository,
            q=q,
            uid=uid,
            username=username,
            phone=phone,
            phone_origin=phone_origin,
            has_phone=has_phone,
            limit=limit,
            cursor=cursor,
        )
        return UserPageResponse(
            items=[
                UserResponse(**user_response_values(user))
                for user in page.items
            ],
            next_cursor=page.next_cursor,
        )

    @router.delete("/without-phone", responses=error_responses)
    def delete_users_without_phone(
        confirm: Annotated[bool, Query()] = False,
    ) -> dict[str, object]:
        if not confirm:
            raise HTTPException(status_code=400, detail="Confirm bulk deletion with confirm=true.")
        deleted = user_repository.delete_users_without_phone()
        return {"status": "success", "deleted_count": deleted}

    @router.get(
        "/{user_id}/phone-evidence",
        response_model=PhoneEvidencePageResponse,
        responses=error_responses,
    )
    def list_phone_evidence(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ) -> PhoneEvidencePageResponse:
        page = query_phone_evidence_page(
            user_repository,
            user_id,
            limit=limit,
            cursor=cursor,
        )
        return PhoneEvidencePageResponse(
            items=[
                PhoneEvidenceResponse(**phone_evidence_response_values(evidence))
                for evidence in page.items
            ],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/{user_id}/enrichment-attempts",
        response_model=EnrichmentAttemptPageResponse,
        responses=error_responses,
    )
    def list_enrichment_attempts(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    ) -> EnrichmentAttemptPageResponse:
        page = query_enrichment_attempt_page(
            user_repository,
            user_id,
            limit=limit,
            cursor=cursor,
        )
        return EnrichmentAttemptPageResponse(
            items=[
                EnrichmentAttemptResponse(
                    **enrichment_attempt_response_values(attempt)
                )
                for attempt in page.items
            ],
            next_cursor=page.next_cursor,
        )

    @router.get(
        "/{user_id}",
        response_model=UserResponse,
        responses=error_responses,
    )
    def get_user(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    ) -> UserResponse:
        return UserResponse(
            **user_response_values(require_user(user_repository, user_id))
        )

    @router.patch(
        "/{user_id}",
        response_model=UserResponse,
        responses=error_responses,
    )
    def update_user(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
        request: UserUpdateRequest,
    ) -> UserResponse:
        require_user(user_repository, user_id)
        updated = user_repository.update_user(
            user_id,
            name=request.name,
            username=request.username,
            phone_1=request.phone_1,
            phone_2=request.phone_2,
            address=request.address,
            gender=request.gender,
            birth_date=request.birth_date,
        )
        if updated is None:
            raise UserNotFound("Persisted user was not found.")
        return UserResponse(**user_response_values(updated))

    @router.delete(
        "/{user_id}",
        responses=error_responses,
    )
    def delete_user(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    ) -> dict[str, object]:
        require_user(user_repository, user_id)
        deleted = user_repository.delete_user(user_id)
        if deleted is False:
            raise UserNotFound("Persisted user was not found.")
        return {"status": "success", "message": f"User #{user_id} was deleted."}

    return router
