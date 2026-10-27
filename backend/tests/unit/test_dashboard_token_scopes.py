import copy
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("dashboard_token_scopes", Path(__file__).resolve().parents[2] / "lambda/dashboard_token_scopes/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class DashboardTokenScopesTests(unittest.TestCase):
    def event(self, groups=None, source="TokenGeneration_Authentication"):
        return {"version": "2", "triggerSource": source, "request": {"groupConfiguration": {"groupsToOverride": groups or []}, "clientMetadata": {"admin": "true"}}, "response": {}}

    def invoke(self, event):
        with patch.dict(os.environ, READ_SCOPE="metrics/read", WRITE_SCOPE="metrics/configuration.write", ADMIN_GROUP="configuration-admins"):
            return handler.lambda_handler(event, None)

    def test_readers_cannot_gain_write_from_metadata_or_similar_groups(self):
        for groups in [[], ["configuration-admins-fake"]]:
            result = self.invoke(self.event(groups))["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]
            self.assertEqual(result["scopesToAdd"], ["metrics/read"])
            self.assertEqual(result["scopesToSuppress"], ["metrics/configuration.write"])

    def test_admins_receive_both_scopes_for_auth_password_challenge_and_refresh(self):
        for source in ["TokenGeneration_Authentication", "TokenGeneration_NewPasswordChallenge", "TokenGeneration_RefreshTokens"]:
            result = self.invoke(self.event(["configuration-admins"], source))["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]
            self.assertEqual(result["scopesToAdd"], ["metrics/read", "metrics/configuration.write"])

    def test_hosted_oauth_scopes_are_unchanged(self):
        event = self.event(source="TokenGeneration_HostedAuth")
        self.assertEqual(self.invoke(copy.deepcopy(event)), event)

    def test_missing_groups_are_read_only_and_old_event_versions_fail(self):
        event = self.event(); event["request"]["groupConfiguration"] = None
        self.assertEqual(self.invoke(event)["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]["scopesToAdd"], ["metrics/read"])
        with self.assertRaises(ValueError):
            self.invoke({"version": "1"})
