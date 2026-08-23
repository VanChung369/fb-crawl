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
        PhoneEvidencePageResponse,
        PhoneEvidenceResponse,
        UserPageResponse,
        UserResponse,
        UserUpdateRequest,
    )

    error_responses = {
        400: {"model": ApiErrorResponse},
        404: {"model": ApiErrorResponse},
    }
    router = APIRouter(
        prefix="/api/v1/users",
        tags=["users"],
        dependencies=[Depends(auth)],
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
        request: "UserUpdateRequest",
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
            raise UserNotFound("Không thể cập nhật thông tin khách hàng.")
        return UserResponse(**user_response_values(updated))

    @router.delete(
        "/{user_id}",
        responses=error_responses,
    )
    def delete_user(
        user_id: Annotated[int, Path(gt=0, le=9223372036854775807)],
    ) -> dict[str, object]:
        require_user(user_repository, user_id)
        user_repository.delete_user(user_id)
        return {"status": "success", "message": f"Khách hàng #{user_id} đã được xóa thành công."}

    return router
