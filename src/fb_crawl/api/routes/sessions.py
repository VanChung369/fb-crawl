"""Session pool management routes."""

from __future__ import annotations

import json
from pathlib import Path
from fastapi import APIRouter, Depends, status

from fb_crawl.api.dependencies import ApiKeyAuth
from fb_crawl.api.schemas import (
    ApiErrorResponse,
    SessionExtractRequest,
    SessionImportRequest,
    SessionItemResponse,
    SessionListResponse,
    SessionUpdateRequest,
)
from fb_crawl.core.session_pool import SessionPool

ERROR_RESPONSES = {
    400: {"model": ApiErrorResponse},
    404: {"model": ApiErrorResponse},
}


def create_sessions_router(session_pool: SessionPool, sessions_dir: Path, auth: ApiKeyAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/sessions",
        tags=["sessions"],
        dependencies=[Depends(auth)],
    )

    @router.get("", response_model=SessionListResponse, responses=ERROR_RESPONSES)
    def list_sessions() -> SessionListResponse:
        items = [
            SessionItemResponse(
                name=s.path.name,
                proxy=s.proxy,
                status=s.status.value,
                success_count=s.success_count,
                failure_count=s.failure_count,
                is_available=s.is_available,
            )
            for s in session_pool._sessions
        ]
        return SessionListResponse(
            total_count=session_pool.total_count,
            available_count=len(session_pool.available_sessions),
            items=items,
        )

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionItemResponse, responses=ERROR_RESPONSES)
    def import_session(request: SessionImportRequest) -> SessionItemResponse:
        import time
        from fastapi import HTTPException
        from fb_crawl.core.session_parser import parse_cookie_input

        inferred_uid, cookies = parse_cookie_input(request.cookies)
        if not cookies:
            raise HTTPException(status_code=400, detail="Không thể nhận diện danh sách Cookie từ dữ liệu nhập vào.")

        sessions_dir.mkdir(parents=True, exist_ok=True)
        raw_name = request.name or (f"fb_{inferred_uid}" if inferred_uid else f"session_{int(time.time())}")
        filename = raw_name if raw_name.endswith(".json") else f"{raw_name}.json"
        session_file = sessions_dir / filename
        session_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

        managed = session_pool.add_session(session_file, proxy=request.proxy)
        return SessionItemResponse(
            name=managed.path.name,
            proxy=managed.proxy,
            status=managed.status.value,
            success_count=managed.success_count,
            failure_count=managed.failure_count,
            is_available=managed.is_available,
        )


    @router.post("/extract", status_code=status.HTTP_201_CREATED, response_model=SessionItemResponse, responses=ERROR_RESPONSES)
    def extract_session(request: SessionExtractRequest) -> SessionItemResponse:
        from fb_crawl.adapters.browser.session_extractor import extract_session_from_credentials

        sessions_dir.mkdir(parents=True, exist_ok=True)
        filename = request.name if request.name.endswith(".json") else f"{request.name}.json"
        session_file = sessions_dir / filename

        extract_session_from_credentials(
            email=request.email,
            password=request.password,
            two_factor_code=request.two_factor_code,
            proxy=request.proxy,
            output_path=session_file,
            headless=request.headless,
        )

        managed = session_pool.add_session(session_file, proxy=request.proxy)
        return SessionItemResponse(
            name=managed.path.name,
            proxy=managed.proxy,
            status=managed.status.value,
            success_count=managed.success_count,
            failure_count=managed.failure_count,
            is_available=managed.is_available,
        )

    @router.post("/{session_name}/launch")
    def launch_session_browser(session_name: str):
        import subprocess
        import threading
        from fastapi import HTTPException
        from fb_crawl.config import load_browser_settings
        from fb_crawl.adapters.browser.driver import create_firefox_driver
        from fb_crawl.adapters.browser.session import SessionStore

        clean_name = session_name if session_name.endswith(".json") else f"{session_name}.json"
        session_file = sessions_dir / clean_name
        if not session_file.is_file():
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file session '{clean_name}'.")

        # Find proxy for this session if configured
        managed = next((s for s in session_pool._sessions if s.path.name == clean_name), None)
        proxy_url = managed.proxy if managed else None

        def _open_interactive_browser():
            settings = load_browser_settings()
            # Non-headless, interactive window
            object.__setattr__(settings, "headless", False)
            if proxy_url:
                object.__setattr__(settings, "proxy", proxy_url)
            driver = None
            try:
                driver = create_firefox_driver(settings)
                store = SessionStore(session_file)
                store.restore(driver)
                # Keep browser open for user interaction
            except Exception as err:
                print(f"[ERROR] Launch browser failed: {err}")

        thread = threading.Thread(target=_open_interactive_browser, daemon=True)
        thread.start()

        return {"status": "success", "message": f"Đang khởi động trình duyệt cho nick {clean_name}..."}

    @router.patch("/{session_name}", response_model=SessionItemResponse, responses=ERROR_RESPONSES)
    def update_session(session_name: str, request: SessionUpdateRequest) -> SessionItemResponse:
        from fastapi import HTTPException
        clean_name = session_name if session_name.endswith(".json") else f"{session_name}.json"
        managed = session_pool.update_session(clean_name, proxy=request.proxy, status=request.status)
        if not managed:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy session '{clean_name}'.")
        return SessionItemResponse(
            name=managed.path.name,
            proxy=managed.proxy,
            status=managed.status.value,
            success_count=managed.success_count,
            failure_count=managed.failure_count,
            is_available=managed.is_available,
        )

    @router.delete("/{session_name}", responses=ERROR_RESPONSES)
    def delete_session(session_name: str):
        from fastapi import HTTPException
        clean_name = session_name if session_name.endswith(".json") else f"{session_name}.json"
        session_file = sessions_dir / clean_name
        if session_file.is_file():
            try:
                session_file.unlink()
            except OSError as err:
                raise HTTPException(status_code=500, detail=f"Lỗi khi xóa file session: {err}")
        session_pool.remove_session(clean_name)
        return {"status": "success", "message": f"Session '{clean_name}' đã được xóa thành công."}

    @router.post("/{session_name}/check", responses=ERROR_RESPONSES)
    def check_session_live(session_name: str):
        from fastapi import HTTPException
        from fb_crawl.adapters.browser.account_safety import SafetyCode, classify_account_safety
        from fb_crawl.adapters.browser.driver import create_firefox_driver
        from fb_crawl.adapters.browser.session import SessionStore, is_authenticated
        from fb_crawl.config import load_browser_settings
        from fb_crawl.core.session_pool import SessionStatus

        clean_name = session_name if session_name.endswith(".json") else f"{session_name}.json"
        session_file = sessions_dir / clean_name
        if not session_file.is_file():
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file session '{clean_name}'.")

        # Find managed session for proxy if any
        managed = next((s for s in session_pool._sessions if s.path.name == clean_name), None)
        proxy = managed.proxy if managed else None

        settings = load_browser_settings()
        object.__setattr__(settings, "headless", True)
        if proxy:
            object.__setattr__(settings, "proxy_url", proxy)

        driver = None
        new_status = SessionStatus.EXPIRED
        msg = "Cookie đã hết hạn hoặc phiên đăng nhập không còn hiệu lực."
        try:
            driver = create_firefox_driver(settings)
            store = SessionStore(session_file)
            is_auth = store.restore(driver)
            signal = classify_account_safety(driver)

            if is_auth and signal is None:
                new_status = SessionStatus.HEALTHY
                msg = "Nick đang hoạt động bình thường (Live)."
            elif signal is not None and signal.code in (
                SafetyCode.CHECKPOINT,
                SafetyCode.TWO_FACTOR,
                SafetyCode.CAPTCHA,
                SafetyCode.ACCOUNT_RESTRICTED,
                SafetyCode.ACCOUNT_RECOVERY,
                SafetyCode.UNUSUAL_ACTIVITY,
            ):
                new_status = SessionStatus.CHECKPOINT
                msg = f"Nick bị Checkpoint / Khóa bảo vệ: {signal.safe_message}"
            else:
                new_status = SessionStatus.EXPIRED
                msg = "Cookie đã hết hạn hoặc phiên đăng nhập không còn hiệu lực."
        except Exception as err:
            new_status = SessionStatus.EXPIRED
            msg = f"Lỗi kiểm tra session: {err}"
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

        # Update status in pool
        if managed:
            managed.status = new_status

        return {
            "name": clean_name,
            "status": new_status.value,
            "is_live": new_status == SessionStatus.HEALTHY,
            "message": msg,
        }

    @router.post("/check-all", responses=ERROR_RESPONSES)
    def check_all_sessions():
        results = []
        for s in list(session_pool._sessions):
            res = check_session_live(s.path.name)
            results.append(res)
        return {"total_checked": len(results), "results": results}

    return router

