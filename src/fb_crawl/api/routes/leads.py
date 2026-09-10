from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from fb_crawl.api.dependencies import CurrentAccount, ProductAuthenticationError
from fb_crawl.interaction_sessions.models import SessionIdentity, SessionError


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    facebook_uid: str = Field(default="", max_length=20)
    username: str = Field(default="", max_length=100)

    def domain(self):
        return SessionIdentity(facebook_uid=self.facebook_uid, username=self.username)


class ReadLeads(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identities: list[Identity] = Field(min_length=1, max_length=100)


class SaveLead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identity: Identity
    status: Literal['unprocessed', 'potential', 'called', 'closed', 'no_answer']
    notes: str = Field(max_length=2000)
    revision: int = Field(strict=True, ge=0, le=9007199254740991)

    @field_validator('notes')
    @classmethod
    def valid_notes(cls, value):
        if '\x00' in value:
            raise ValueError('Invalid notes.')
        return value


def create_leads_router(repository, current_auth):
    router = APIRouter(prefix='/api/v1/history/leads', tags=['product-leads'])

    async def account(current: CurrentAccount = Depends(current_auth)):
        if current.cookie_authenticated:
            raise ProductAuthenticationError()
        if not current.device_allowed:
            raise SessionError('contact_device_not_allowed', 403)
        return current.account.id

    @router.post('/read')
    def read(payload: ReadLeads, owner=Depends(account)):
        return {"items": repository.read(owner, [value.domain() for value in payload.identities])}

    @router.put('')
    def save(payload: SaveLead, owner=Depends(account)):
        return repository.save(owner, payload.identity.domain(), payload.status, payload.notes, payload.revision)

    return router
