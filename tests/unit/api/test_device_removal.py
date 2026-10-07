from unittest.mock import Mock
import pytest

from fb_crawl.accounts.repository import DeviceNotFound
from fb_crawl.core.exceptions import ValidationError
from tests.unit.api.test_product_auth_routes import product_client
from tests.unit.api.test_product_account_routes import _headers


@pytest.mark.parametrize('error, status', [(None,200),(DeviceNotFound('Device was not found.'),404),(ValidationError('Only revoked devices can be removed.'),400)])
def test_removing_a_revoked_device_is_scoped_to_authenticated_account(error,status):
    client,repository,_auth,access=product_client()
    repository.delete_revoked_device=Mock(side_effect=error)
    response=client.delete('/api/v1/devices/10/record',headers=_headers(access))
    assert response.status_code==status
    repository.delete_revoked_device.assert_called_once()
    assert repository.delete_revoked_device.call_args.args[:2]==(7,10)
    if status==200:
        assert response.json()=={'status':'deleted','device_id':10}


def test_removing_device_requires_authentication():
    client,repository,_auth,_access=product_client()
    repository.delete_revoked_device=Mock()
    assert client.delete('/api/v1/devices/10/record').status_code==401
    repository.delete_revoked_device.assert_not_called()
