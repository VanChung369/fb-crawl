from unittest.mock import MagicMock, patch

import pytest

from fb_crawl.auth.google import GoogleAuthError, GoogleTokenVerifier


@pytest.mark.parametrize("client_ids", [None, (), ("", " ")])
def test_unconfigured_google_login_fails_before_network(client_ids):
    with patch("fb_crawl.auth.google.httpx.Client") as client:
        with pytest.raises(GoogleAuthError, match="not configured"):
            GoogleTokenVerifier(allowed_client_ids=client_ids).verify_id_token("token")
        client.assert_not_called()


@pytest.mark.parametrize("audience", ["our-client", "foreign-client", None])
def test_google_login_requires_matching_audience(audience):
    response = MagicMock(status_code=200)
    response.json.return_value = {"email": "user@example.com", "email_verified": True,
                                  "sub": "google-user", "aud": audience}
    with patch("fb_crawl.auth.google.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = response
        verifier = GoogleTokenVerifier(allowed_client_ids=("our-client",))
        if audience == "our-client":
            assert verifier.verify_id_token("token").google_user_id == "google-user"
        else:
            with pytest.raises(GoogleAuthError, match="audience mismatch"):
                verifier.verify_id_token("token")
