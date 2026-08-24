"""Session pool management routes."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    SessionImportRequest,
    SessionItemResponse,
    SessionListResponse,
    SessionUpdateRequest,
)
from fb_crawl.core.session_pool import SessionPool, SessionStatus

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}

SAFE_SESSION_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")


class SessionLaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxy: str | None = Field(default=None, max_length=2048)


def _safe_session_filename(value: str | None, *, fallback: str) -> str:
    raw = (value or fallback).strip()
    if raw.endswith(".json"):
        raw = raw[:-5]
    if raw in {"", ".", ".."} or not SAFE_SESSION_NAME.fullmatch(raw):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session name must use only letters, numbers, dot, dash, or underscore.",
        )
    return f"{raw}.json"


def _session_file(sessions_dir: Path, filename: str) -> Path:
    root = sessions_dir.resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session path must stay inside the configured sessions directory.",
        ) from error
    return path


def _item_response(managed) -> SessionItemResponse:
    return SessionItemResponse(
        name=managed.path.name,
        proxy=managed.proxy,
        status=managed.status.value,
        success_count=managed.success_count,
        failure_count=managed.failure_count,
        is_available=managed.is_available,
    )


def create_sessions_router(
    session_pool: SessionPool,
    sessions_dir: Path,
    auth: ApiKeyAuth,
    *,
    allow_local_browser_login: bool = False,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/sessions",
        tags=["sessions"],
        dependencies=[Depends(auth)],
    )

    @router.get("", response_model=SessionListResponse, responses=ERROR_RESPONSES)
    def list_sessions() -> SessionListResponse:
        return SessionListResponse(
            total_count=session_pool.total_count,
            available_count=len(session_pool.available_sessions),
            items=[_item_response(session) for session in session_pool._sessions],
        )

    @router.post(
        "",
        status_code=status.HTTP_201_CREATED,
        response_model=SessionItemResponse,
        responses=ERROR_RESPONSES,
    )
    def import_session(request: SessionImportRequest) -> SessionItemResponse:
        from fb_crawl.core.session_parser import parse_cookie_input

        inferred_uid, cookies = parse_cookie_input(request.cookies)
        if not cookies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not detect cookies from the submitted session data.",
            )

        fallback = f"fb_{inferred_uid}" if inferred_uid else f"session_{int(time.time())}"
        filename = _safe_session_filename(request.name, fallback=fallback)
        session_file = _session_file(sessions_dir, filename)

        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_file.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        return _item_response(session_pool.add_session(session_file, proxy=request.proxy))

    @router.post(
        "/{session_name}/launch-login",
        status_code=status.HTTP_202_ACCEPTED,
        responses=ERROR_RESPONSES,
    )
    def launch_local_browser_login(
        session_name: str,
        request: SessionLaunchRequest | None = None,
    ):
        if not allow_local_browser_login:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Local browser login is available only when the API is bound to localhost.",
            )

        filename = _safe_session_filename(session_name, fallback="session")
        session_file = _session_file(sessions_dir, filename)
        managed = next(
            (session for session in session_pool._sessions if session.path.name == filename),
            None,
        )
        proxy = (request.proxy.strip() if request and request.proxy else None) or (
            managed.proxy if managed else None
        )

        def _run_login_window() -> None:
            from fb_crawl.adapters.browser.driver import create_browser
            from fb_crawl.adapters.browser.session import (
                FACEBOOK_HOME,
                SessionStore,
                is_authenticated,
            )
            from fb_crawl.config import load_browser_settings

            settings = load_browser_settings()
            object.__setattr__(settings, "headless", False)
            if proxy:
                object.__setattr__(settings, "proxy", proxy)

            driver = None
            try:
                driver = create_browser(settings)
                store = SessionStore(session_file)
                if session_file.is_file():
                    store.restore(driver)
                if not is_authenticated(driver):
                    driver.get(FACEBOOK_HOME)

                deadline = time.monotonic() + settings.verification_timeout_seconds
                while time.monotonic() < deadline:
                    if is_authenticated(driver):
                        store.save(driver)
                        session_pool.add_session(session_file, proxy=proxy)
                        session_pool.mark_success(session_file)
                        return
                    time.sleep(2.0)
            except Exception:
                if managed is not None:
                    session_pool.mark_invalid(managed.path, SessionStatus.CHECKPOINT)
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass

        threading.Thread(target=_run_login_window, daemon=True).start()
        return {
            "status": "accepted",
            "name": filename,
            "message": "Local browser login started. Enter credentials only in the Facebook browser window.",
        }

    @router.patch(
        "/{session_name}",
        response_model=SessionItemResponse,
        responses=ERROR_RESPONSES,
    )
    def update_session(
        session_name: str,
        request: SessionUpdateRequest,
    ) -> SessionItemResponse:
        filename = _safe_session_filename(session_name, fallback="session")
        managed = session_pool.update_session(
            filename,
            proxy=request.proxy,
            status=request.status,
        )
        if not managed:
            raise HTTPException(status_code=404, detail=f"Session '{filename}' was not found.")
        return _item_response(managed)

    @router.delete("/{session_name}", responses=ERROR_RESPONSES)
    def delete_session(session_name: str):
        filename = _safe_session_filename(session_name, fallback="session")
        session_file = _session_file(sessions_dir, filename)
        if session_file.is_file():
            try:
                session_file.unlink()
            except OSError as error:
                raise HTTPException(
                    status_code=500,
                    detail="Could not delete the session file.",
                ) from error
        session_pool.remove_session(filename)
        return {"status": "success", "message": f"Session '{filename}' was deleted."}

    @router.post("/{session_name}/check", responses=ERROR_RESPONSES)
    def check_session_live(session_name: str):
        if not allow_local_browser_login:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Session browser checks are available only when the API is bound to localhost.",
            )

        from fb_crawl.adapters.browser.account_safety import (
            SafetyCode,
            classify_account_safety,
        )
        from fb_crawl.adapters.browser.driver import create_browser
        from fb_crawl.adapters.browser.session import SessionStore
        from fb_crawl.config import load_browser_settings

        filename = _safe_session_filename(session_name, fallback="session")
        session_file = _session_file(sessions_dir, filename)
        if not session_file.is_file():
            raise HTTPException(status_code=404, detail=f"Session '{filename}' was not found.")

        managed = next(
            (session for session in session_pool._sessions if session.path.name == filename),
            None,
        )
        proxy = managed.proxy if managed else None

        settings = load_browser_settings()
        object.__setattr__(settings, "headless", True)
        if proxy:
            object.__setattr__(settings, "proxy", proxy)

        driver = None
        new_status = SessionStatus.EXPIRED
        message = "Session is expired or not authenticated."
        try:
            driver = create_browser(settings)
            is_auth = SessionStore(session_file).restore(driver)
            signal = classify_account_safety(driver)

            if is_auth and signal is None:
                new_status = SessionStatus.HEALTHY
                message = "Session is live."
            elif signal is not None and signal.manual_review:
                new_status = SessionStatus.MANUAL_REVIEW
                message = signal.safe_message
            elif signal is not None and signal.code in {
                SafetyCode.TEMPORARY_BLOCK,
            }:
                new_status = SessionStatus.COOLDOWN
                message = signal.safe_message
        except Exception:
            new_status = SessionStatus.EXPIRED
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

        if managed:
            if new_status == SessionStatus.HEALTHY:
                session_pool.mark_success(managed.path)
            else:
                session_pool.mark_invalid(managed.path, new_status)

        return {
            "name": filename,
            "status": new_status.value,
            "is_live": new_status == SessionStatus.HEALTHY,
            "message": message,
        }

    @router.post("/check-all", responses=ERROR_RESPONSES)
    def check_all_sessions():
        results = [check_session_live(session.path.name) for session in list(session_pool._sessions)]
        return {"total_checked": len(results), "results": results}

    return router
