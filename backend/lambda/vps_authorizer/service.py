from __future__ import annotations

import secrets

from models import AuthorizationRequest
from repository import TokenRepository


class AuthorizationService:
    def __init__(self, repository: TokenRepository) -> None:
        self.repository = repository

    def authorize(self, request: AuthorizationRequest) -> bool:
        supplied = request.token()
        if not supplied:
            return False
        expected = self.repository.get_expected_token()
        return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
