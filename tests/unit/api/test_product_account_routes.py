from __future__ import annotations

from tests.unit.api.test_product_auth_routes import product_client
from tests.unit.api.product_auth_fakes import INSTALLATION_ID


def _headers(access: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access}",
        "X-Installation-ID": str(INSTALLATION_ID),
    }


def test_account_me_and_devices_expose_no_password_or_session_hashes() -> None:
    client, _repository, _auth_service, access = product_client()

    account = client.get("/api/v1/account/me", headers=_headers(access))
    devices = client.get("/api/v1/devices", headers=_headers(access))

    assert account.status_code == devices.status_code == 200
    assert account.json() == {
        "id": 7,
        "email": "Person@example.com",
        "role": "user",
        "status": "active",
        "email_verified_at": "2026-08-30T08:00:00Z",
        "device_allowed": True,
    }
    assert devices.json()["items"][0]["installation_id"] == str(INSTALLATION_ID)
    combined = account.text + devices.text
    assert "password" not in combined
    assert "refresh" not in combined


def test_revoke_device_is_scoped_to_current_account() -> None:
    client, repository, _auth_service, access = product_client()

    response = client.delete("/api/v1/devices/9", headers=_headers(access))

    assert response.status_code == 200
    assert response.json() == {"status": "revoked", "device_id": 9}
    assert repository.revoked_devices == [9]
