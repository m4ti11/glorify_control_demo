from __future__ import annotations


class PgStatementsService:
    def __init__(self, repository):
        self.repository = repository

    def get(self, query):
        return self.repository.get(query.key)
