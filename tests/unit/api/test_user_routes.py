from __future__ import annotations

import ast
from datetime import UTC, datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from fb_crawl.core.exceptions import ValidationError
from fb_crawl.core.jobs import KeysetCursor, Page, encode_cursor
from fb_data_pipeline.repositories.users import (
    EnrichmentAttemptView,
    PhoneEvidenceView,
    UserQuery,
    UserSummary,
)


ROOT = Path(__file__).parents[3]
USER_ROUTE_PATH = ROOT / "src" / "fb_crawl" / "api" / "routes" / "users.py"
SCHEMAS_PATH = ROOT / "src" / "fb_crawl" / "api" / "schemas.py"
APP_PATH = ROOT / "src" / "fb_crawl" / "api" / "app.py"
API_KEY = "a" * 32

try:
    import fastapi
    import pydantic
except ModuleNotFoundError as error:
    if error.name not in {"fastapi", "pydantic"}:
        raise
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI unavailable")
def test_bulk_delete_requires_auth_and_confirmation() -> None:
    class Repository(RecordingUserRepository):
        def delete_users_without_phone(self):
            self.calls.append(("bulk_delete", None))
            return 12

    repository = Repository(_user())
    client = _client(repository)
    path = "/api/v1/users/without-phone"
    assert client.delete(path + "?confirm=true").status_code == 401
    assert client.delete(path, headers=_headers()).status_code == 400
    assert not repository.calls
    response = client.delete(path + "?confirm=true", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"status": "success", "deleted_count": 12}
    assert repository.calls == [("bulk_delete", None)]

if FASTAPI_AVAILABLE:
    from fastapi.testclient import TestClient

    from fb_crawl.api.app import create_app
    from fb_crawl.api.config import ApiSettings


def _load_users_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_task13_user_routes", USER_ROUTE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("user route module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_user_query_uses_task_5_typed_filters_and_defaults() -> None:
    """Break caught: HTTP filters bypass Task 5 normalization or change page defaults."""

    cursor = encode_cursor(
        KeysetCursor(datetime(2026, 8, 22, 12, tzinfo=UTC), 7)
    )

    query = _load_users_module().build_user_query(
        q="  SẢMple  ",
        uid=" 100 ",
        username=" Sample.User ",
        phone="0901 111 111",
        phone_origin="fbnumber",
        has_phone=True,
        cursor=cursor,
    )

    assert query == UserQuery(
        q="sảmple",
        uid="100",
        username="sample.user",
        phone="+84901111111",
        phone_origin="fbnumber",
        has_phone=True,
        limit=20,
        cursor=cursor,
    )


@pytest.mark.parametrize(
    "filters",
    [
        {"phone": "not-a-phone"},
        {"phone_origin": "private_provider"},
        {"cursor": "not-an-opaque-cursor"},
        {"limit": 0},
        {"limit": 101},
    ],
)
def test_build_user_query_rejects_invalid_filters_before_repository(
    filters: dict[str, object],
) -> None:
    """Break caught: invalid origin, phone, cursor, or bound reaches PostgreSQL."""

    with pytest.raises(ValidationError):
        _load_users_module().build_user_query(**filters)


def _user(user_id: int = 7) -> UserSummary:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    return UserSummary(
        id=user_id,
        facebook_uid="100006902588690",
        username="truongtuandoan",
        name="Truong Tuan Doan",
        profile_url="https://www.facebook.com/truongtuandoan",
        phone_1="+84901111111",
        phone_2="+84902222222",
        address="Ha Noi",
        birth_date="1990-01-02",
        gender="Male",
        created_at=now,
        updated_at=now,
    )


def _evidence() -> PhoneEvidenceView:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    return PhoneEvidenceView(
        id=3,
        normalized_phone="+84901111111",
        display_phone="0901 111 111",
        origin="fbnumber",
        source="provider_lookup",
        source_url="https://www.facebook.com/truongtuandoan",
        provider="fbnumber",
        confidence="provider_result",
        first_captured_at=now,
        last_captured_at=now,
        evidence_count=2,
        created_at=now,
        updated_at=now,
    )


def _attempt(error_code: str = "provider_rate_limited") -> EnrichmentAttemptView:
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    return EnrichmentAttemptView(
        id=4,
        provider="fbnumber",
        status="found",
        checked_at=now,
        error_code=error_code,
        values_found=1,
        created_at=now,
    )


class RecordingUserRepository:
    def __init__(self, user: UserSummary | None = None) -> None:
        self.user = user
        self.calls: list[tuple[str, object]] = []

    def list_users(self, query: UserQuery) -> Page[UserSummary]:
        self.calls.append(("list_users", query))
        return Page((_user(),), "next-users")

    def get_user(self, user_id: int) -> UserSummary | None:
        self.calls.append(("get_user", user_id))
        return self.user

    def list_phone_evidence(
        self, user_id: int, *, limit: int, cursor: str | None
    ) -> Page[PhoneEvidenceView]:
        self.calls.append(("list_phone_evidence", (user_id, limit, cursor)))
        return Page((_evidence(),), "next-evidence")

    def list_enrichment_attempts(
        self, user_id: int, *, limit: int, cursor: str | None
    ) -> Page[EnrichmentAttemptView]:
        self.calls.append(("list_enrichment_attempts", (user_id, limit, cursor)))
        return Page((_attempt(),), "next-attempts")

    def update_user(self, user_id: int, **updates: object) -> UserSummary | None:
        self.calls.append(("update_user", (user_id, updates)))
        if self.user is None:
            return None
        values = {field: getattr(self.user, field) for field in self.user.__slots__}
        values.update({key: value for key, value in updates.items() if value is not None})
        self.user = UserSummary(**values)
        return self.user

    def delete_user(self, user_id: int) -> bool:
        self.calls.append(("delete_user", user_id))
        if self.user is None:
            return False
        self.user = None
        return True


def test_query_user_page_passes_one_typed_query_and_preserves_keyset_cursor() -> None:
    """Break caught: list filters are forwarded separately or pagination is discarded."""

    module = _load_users_module()
    repository = RecordingUserRepository()

    page = module.query_user_page(repository, uid="100006902588690", limit=20)

    assert page.next_cursor == "next-users"
    assert repository.calls == [
        (
            "list_users",
            UserQuery(uid="100006902588690", limit=20),
        )
    ]


@pytest.mark.parametrize(
    "method_name",
    ["query_phone_evidence_page", "query_enrichment_attempt_page"],
)
def test_detail_page_cursor_is_validated_before_any_repository_call(
    method_name: str,
) -> None:
    """Break caught: malformed evidence/attempt cursors trigger even a PII detail read."""

    module = _load_users_module()
    repository = RecordingUserRepository(_user())

    with pytest.raises(ValidationError, match="Invalid pagination cursor"):
        getattr(module, method_name)(repository, 7, cursor="invalid")

    assert repository.calls == []


@pytest.mark.parametrize(
    ("method_name", "expected_calls", "expected_cursor"),
    [
        (
            "query_phone_evidence_page",
            [
                ("get_user", 7),
                ("list_phone_evidence", (7, 20, None)),
            ],
            "next-evidence",
        ),
        (
            "query_enrichment_attempt_page",
            [
                ("get_user", 7),
                ("list_enrichment_attempts", (7, 20, None)),
            ],
            "next-attempts",
        ),
    ],
)
def test_detail_pages_require_the_parent_user_and_remain_bounded(
    method_name: str,
    expected_calls: list[tuple[str, object]],
    expected_cursor: str,
) -> None:
    """Break caught: orphan evidence is exposed or a detail page becomes unbounded."""

    module = _load_users_module()
    repository = RecordingUserRepository(_user())

    page = getattr(module, method_name)(repository, 7)

    assert page.next_cursor == expected_cursor
    assert repository.calls == expected_calls


@pytest.mark.parametrize(
    "method_name",
    ["query_phone_evidence_page", "query_enrichment_attempt_page"],
)
def test_missing_user_has_stable_not_found_error_for_detail_pages(
    method_name: str,
) -> None:
    """Break caught: a missing user returns an empty PII page instead of stable 404 data."""

    module = _load_users_module()
    repository = RecordingUserRepository(None)

    with pytest.raises(module.UserNotFound) as captured:
        getattr(module, method_name)(repository, 404)

    assert captured.value.code == "user_not_found"
    assert captured.value.safe_message == "Persisted user was not found."
    assert repository.calls == [("get_user", 404)]


def test_response_value_whitelists_approved_user_and_evidence_fields() -> None:
    """Break caught: PII responses leak repository/provider-only attributes."""

    module = _load_users_module()
    user = SimpleNamespace(
        **{field: getattr(_user(), field) for field in _user().__slots__},
        api_token="private-token",
        raw_provider_response={"private": True},
    )
    evidence = SimpleNamespace(
        **{field: getattr(_evidence(), field) for field in _evidence().__slots__},
        request_headers={"Authorization": "private-token"},
        raw_provider_response={"private": True},
    )
    attempt = SimpleNamespace(
        **{field: getattr(_attempt(), field) for field in _attempt().__slots__},
        api_token="private-token",
        request_headers={"Authorization": "private-token"},
        raw_provider_response={"private": True},
        error_message="private provider exception text",
        exception_text="private stack trace",
    )

    user_values = module.user_response_values(user)
    evidence_values = module.phone_evidence_response_values(evidence)
    attempt_values = module.enrichment_attempt_response_values(attempt)

    assert user_values == {
        "id": 7,
        "facebook_uid": "100006902588690",
        "username": "truongtuandoan",
        "name": "Truong Tuan Doan",
        "profile_url": "https://www.facebook.com/truongtuandoan",
        "phone_1": "+84901111111",
        "phone_2": "+84902222222",
        "address": "Ha Noi",
        "birth_date": "1990-01-02",
        "gender": "Male",
        "created_at": datetime(2026, 8, 22, 12, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 22, 12, tzinfo=UTC),
    }
    assert set(evidence_values) == {
        "id",
        "normalized_phone",
        "display_phone",
        "origin",
        "source",
        "source_url",
        "provider",
        "confidence",
        "first_captured_at",
        "last_captured_at",
        "evidence_count",
        "created_at",
        "updated_at",
    }
    assert attempt_values == {
        "id": 4,
        "provider": "fbnumber",
        "status": "found",
        "checked_at": datetime(2026, 8, 22, 12, tzinfo=UTC),
        "error_code": "provider_rate_limited",
        "values_found": 1,
        "created_at": datetime(2026, 8, 22, 12, tzinfo=UTC),
    }
    assert module.enrichment_attempt_response_values(_attempt(""))["error_code"] is None
    combined = repr((user_values, evidence_values, attempt_values))
    assert "private-token" not in combined
    assert "raw_provider_response" not in combined
    assert "private provider exception text" not in combined
    assert "private stack trace" not in combined


def _class_fields(tree: ast.Module, class_name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            }
    raise AssertionError(f"missing schema class {class_name}")


def _response_schema_fields(tree: ast.Module) -> set[str]:
    fields: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.endswith("Response"):
            fields.update(
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            )
    return fields


def test_closed_response_schemas_whitelist_every_persisted_user_payload() -> None:
    """Break caught: response models omit approved fields or expose repository internals."""

    source = SCHEMAS_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert _class_fields(tree, "UserResponse") == {
        "id",
        "facebook_uid",
        "username",
        "name",
        "profile_url",
        "phone_1",
        "phone_2",
        "address",
        "birth_date",
        "gender",
        "created_at",
        "updated_at",
    }
    assert _class_fields(tree, "PhoneEvidenceResponse") == {
        "id",
        "normalized_phone",
        "display_phone",
        "origin",
        "source",
        "source_url",
        "provider",
        "confidence",
        "first_captured_at",
        "last_captured_at",
        "evidence_count",
        "created_at",
        "updated_at",
    }
    assert _class_fields(tree, "EnrichmentAttemptResponse") == {
        "id",
        "provider",
        "status",
        "checked_at",
        "error_code",
        "values_found",
        "created_at",
    }
    assert _class_fields(tree, "UserPageResponse") == {"items", "next_cursor"}
    assert _class_fields(tree, "PhoneEvidencePageResponse") == {
        "items",
        "next_cursor",
    }
    assert _class_fields(tree, "EnrichmentAttemptPageResponse") == {
        "items",
        "next_cursor",
    }
    assert source.count('from_attributes=True') >= 3
    response_fields = _response_schema_fields(tree)
    assert "api_token" not in response_fields
    assert "request_headers" not in response_fields
    assert "raw_provider_response" not in response_fields
    assert "correlation_id" not in response_fields


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_user_router_has_exact_ordered_bounded_authenticated_read_surface() -> None:
    """Break caught: a dynamic route shadows a subresource or an unsafe export is added."""

    source = USER_ROUTE_PATH.read_text(encoding="utf-8")
    app = _client(RecordingUserRepository(_user())).app
    registered_routes = [
        route
        for included in app.routes
        for route in getattr(getattr(included, "original_router", None), "routes", ())
    ]
    user_routes = [
        route
        for route in registered_routes
        if route.path.startswith("/api/v1/users")
    ]
    assert [
        route.path.removeprefix("/api/v1/users")
        for route in user_routes
        if "GET" in route.methods
    ] == [
        "",
        "/{user_id}/phone-evidence",
        "/{user_id}/enrichment-attempts",
        "/{user_id}",
    ]
    assert all(
        route.response_model is not None
        for route in user_routes
        if route.methods & {"GET", "POST", "PATCH"}
    )
    assert 'prefix="/api/v1/users"' in source
    assert "dependencies=[Depends(auth)]" in source
    assert "Query(ge=1, le=100)" in source
    assert "Path(gt=0, le=9223372036854775807)" in source
    assert "OFFSET" not in source.upper()
    assert "export" not in source.casefold()
    assert "download" not in source.casefold()


def test_app_registers_user_router_and_maps_only_user_not_found_to_404() -> None:
    """Break caught: persisted-user routes are absent or all validation errors become 404."""

    source = APP_PATH.read_text(encoding="utf-8")

    assert "create_users_router" in source
    assert "app.include_router(create_users_router(user_repository, auth))" in source
    assert "UserNotFound" in source


def _client(repository: RecordingUserRepository):
    app = create_app(
        ApiSettings(api_key=API_KEY),
        job_service=object(),
        job_repository=object(),
        user_repository=repository,
        readiness=lambda _migration: True,
    )
    return TestClient(app, raise_server_exceptions=False)


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_all_user_endpoints_require_api_key() -> None:
    """Break caught: any PII user route is reachable without authentication."""

    client = _client(RecordingUserRepository(_user()))

    for path in (
        "/api/v1/users",
        "/api/v1/users/7",
        "/api/v1/users/7/phone-evidence",
        "/api/v1/users/7/enrichment-attempts",
    ):
        assert client.get(path).status_code == 401


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_user_list_serializes_all_approved_fields_and_next_cursor() -> None:
    """Break caught: list pages lose phone/profile identity fields or keyset progress."""

    repository = RecordingUserRepository(_user())
    response = _client(repository).get(
        "/api/v1/users?uid=100006902588690&limit=20",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": 7,
                "facebook_uid": "100006902588690",
                "username": "truongtuandoan",
                "name": "Truong Tuan Doan",
                "profile_url": "https://www.facebook.com/truongtuandoan",
                "phone_1": "+84901111111",
                "phone_2": "+84902222222",
                "address": "Ha Noi",
                "birth_date": "1990-01-02",
                "gender": "Male",
                "created_at": "2026-08-22T12:00:00Z",
                "updated_at": "2026-08-22T12:00:00Z",
            }
        ],
        "next_cursor": "next-users",
    }
    assert repository.calls == [
        ("list_users", UserQuery(uid="100006902588690", limit=20))
    ]


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
@pytest.mark.parametrize(
    "query",
    [
        "phone=not-a-phone",
        "phone_origin=private_provider",
        "cursor=not-an-opaque-cursor",
        "limit=0",
        "limit=101",
    ],
)
def test_invalid_user_list_filters_return_stable_400_before_repository(
    query: str,
) -> None:
    """Break caught: malformed user search input reaches SQL or becomes a 500 response."""

    repository = RecordingUserRepository(_user())
    response = _client(repository).get(
        f"/api/v1/users?{query}",
        headers=_headers(),
    )

    assert response.status_code == 400
    assert response.json()["code"] in {
        "request_validation_failed",
        "validation_error",
    }
    assert repository.calls == []


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users/404",
        "/api/v1/users/404/phone-evidence",
        "/api/v1/users/404/enrichment-attempts",
    ],
)
def test_missing_user_is_stable_404_for_detail_and_subresources(path: str) -> None:
    """Break caught: a missing persisted user becomes an empty page or generic error."""

    response = _client(RecordingUserRepository(None)).get(path, headers=_headers())

    assert response.status_code == 404
    assert response.json() == {
        "code": "user_not_found",
        "message": "Persisted user was not found.",
    }


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_evidence_and_attempt_pages_are_bounded_and_response_whitelisted() -> None:
    """Break caught: provider secrets/error internals leak through PII subresources."""

    repository = RecordingUserRepository(_user())
    client = _client(repository)

    evidence = client.get(
        "/api/v1/users/7/phone-evidence?limit=100", headers=_headers()
    )
    attempts = client.get(
        "/api/v1/users/7/enrichment-attempts?limit=100", headers=_headers()
    )

    assert evidence.status_code == attempts.status_code == 200
    assert evidence.json()["next_cursor"] == "next-evidence"
    assert attempts.json()["next_cursor"] == "next-attempts"
    assert set(evidence.json()["items"][0]) == {
        "id",
        "normalized_phone",
        "display_phone",
        "origin",
        "source",
        "source_url",
        "provider",
        "confidence",
        "first_captured_at",
        "last_captured_at",
        "evidence_count",
        "created_at",
        "updated_at",
    }
    assert attempts.json()["items"] == [
        {
            "id": 4,
            "provider": "fbnumber",
            "status": "found",
            "checked_at": "2026-08-22T12:00:00Z",
            "error_code": "provider_rate_limited",
            "values_found": 1,
            "created_at": "2026-08-22T12:00:00Z",
        }
    ]
    combined = evidence.text + attempts.text
    assert "private provider exception text" not in combined
    assert "private stack trace" not in combined
    assert "api_token" not in combined
    assert "request_headers" not in combined
    assert "raw_provider_response" not in combined
    assert repository.calls == [
        ("get_user", 7),
        ("list_phone_evidence", (7, 100, None)),
        ("get_user", 7),
        ("list_enrichment_attempts", (7, 100, None)),
    ]


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_enrichment_attempt_empty_error_code_serializes_as_json_null() -> None:
    """Break caught: the database no-error sentinel leaks as an ambiguous empty string."""

    class NoErrorRepository(RecordingUserRepository):
        def list_enrichment_attempts(
            self, user_id: int, *, limit: int, cursor: str | None
        ) -> Page[EnrichmentAttemptView]:
            self.calls.append(("list_enrichment_attempts", (user_id, limit, cursor)))
            return Page((_attempt(""),), None)

    response = _client(NoErrorRepository(_user())).get(
        "/api/v1/users/7/enrichment-attempts",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["error_code"] is None


@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI/Pydantic extra unavailable")
def test_user_update_and_delete_routes_support_dashboard_management() -> None:
    """Break caught: dashboard lead edit/delete buttons lose their API backing."""

    repository = RecordingUserRepository(_user())
    client = _client(repository)

    updated = client.patch(
        "/api/v1/users/7",
        headers=_headers(),
        json={"name": "Updated Name", "address": "Da Nang"},
    )
    deleted = client.delete("/api/v1/users/7", headers=_headers())

    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated Name"
    assert updated.json()["address"] == "Da Nang"
    assert deleted.status_code == 200
    assert deleted.json() == {
        "status": "success",
        "message": "User #7 was deleted.",
    }
    assert repository.calls[-4:] == [
        ("get_user", 7),
        (
            "update_user",
            (
                7,
                {
                    "name": "Updated Name",
                    "username": None,
                    "phone_1": None,
                    "phone_2": None,
                    "address": "Da Nang",
                    "gender": None,
                    "birth_date": None,
                },
            ),
        ),
        ("get_user", 7),
        ("delete_user", 7),
    ]
