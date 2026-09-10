from copy import deepcopy

from tests.unit.api.product_auth_fakes import INSTALLATION_ID
from tests.unit.api.test_product_auth_routes import product_client


class Leads:
    def __init__(self):
        self.values = {}

    def read(self, owner, identities):
        return [deepcopy(self.values.get((owner, identity.key), {
            "status": "unprocessed", "notes": "", "revision": 0,
        })) for identity in identities]

    def save(self, owner, identity, status, notes, revision):
        value = {"status": status, "notes": notes, "revision": revision + 1}
        self.values[owner, identity.key] = value
        return value


def setup():
    repository = Leads()
    client, _, _, access = product_client(lead_repository=repository)
    return client, repository, {"Authorization": f"Bearer {access}", "X-Installation-ID": str(INSTALLATION_ID)}


def test_lead_is_saved_and_read_under_authenticated_owner():
    client, repository, headers = setup()
    identity = {"facebook_uid": "10012345", "username": "sample.user"}
    result = client.put('/api/v1/history/leads', headers=headers, json={
        "identity": identity, "status": "potential", "notes": "Gọi lại ngày mai", "revision": 0,
    })
    assert result.status_code == 200
    assert (7, "uid:10012345") in repository.values
    response = client.post('/api/v1/history/leads/read', headers=headers, json={"identities": [identity]})
    assert response.json()["items"] == [{"status": "potential", "notes": "Gọi lại ngày mai", "revision": 1}]


def test_lead_routes_require_auth_and_reject_forged_owner_and_invalid_updates():
    client, repository, headers = setup()
    payload = {"identity": {"facebook_uid": "10012345"}, "status": "called", "notes": "", "revision": 0}
    assert client.put('/api/v1/history/leads', json=payload).status_code == 401
    for extra in ({"account_id": 99}, {"status": "bogus"}, {"notes": "x" * 2001}, {"revision": -1}):
        assert client.put('/api/v1/history/leads', headers=headers, json={**payload, **extra}).status_code == 400
    assert not repository.values
