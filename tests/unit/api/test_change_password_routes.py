from tests.unit.api.test_product_auth_routes import product_client, WEB_ORIGIN
from tests.unit.api.product_auth_fakes import INSTALLATION_ID


def test_change_password_requires_account_auth_and_cookie_csrf():
    client, _repository, service, access = product_client()
    calls=[]
    service.change_password=lambda *args, **kwargs: calls.append(args)
    body={'current_password':'old secure password','new_password':'new secure password'}
    assert client.post('/api/v1/auth/change-password',json=body).status_code==401
    headers={'Authorization':f'Bearer {access}','X-Installation-ID':str(INSTALLATION_ID)}
    assert client.post('/api/v1/auth/change-password',json={**body,'account_id':99},headers=headers).status_code==400
    assert client.post('/api/v1/auth/change-password',json=body,headers=headers).status_code==200
    assert calls[0][:3]==(7,'old secure password','new secure password')
    client.cookies.set('lead_finder_access',access)
    assert client.post('/api/v1/auth/change-password',json=body,headers={'X-Installation-ID':str(INSTALLATION_ID),'Origin':WEB_ORIGIN}).status_code==403
    assert len(calls)==1
