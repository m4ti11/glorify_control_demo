from __future__ import annotations

from pydantic import BaseModel, Field


class AuthorizationRequest(BaseModel):
    headers: dict[str, str] = Field(default_factory=dict)

    def token(self) -> str | None:
        headers = {name.lower(): value for name, value in self.headers.items()}
        token = headers.get("x-api-token")
        if token:
            return token
        authorization = headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:] or None
        return None


class AuthorizationResult(BaseModel):
    isAuthorized: bool
