"""Preserve API scopes for native sign-in to the dedicated dashboard user pool.

Cognito authenticates the user and supplies group membership. API Gateway still
verifies the token signature, issuer, dashboard client ID, expiry, and scopes.
Never trust ClientMetadata for authorization or log authentication events.
"""

import os


def lambda_handler(event, context):
    if event.get("version") != "2":
        raise ValueError("Dashboard scope customization requires a V2 event")
    # Leave OAuth grants and their requested scopes unchanged during migration.
    if event.get("triggerSource") not in {
        "TokenGeneration_Authentication", "TokenGeneration_NewPasswordChallenge",
        "TokenGeneration_AuthenticateDevice", "TokenGeneration_RefreshTokens",
    }:
        return event
    groups = (event.get("request", {}).get("groupConfiguration") or {}).get("groupsToOverride") or []
    scopes = [os.environ["READ_SCOPE"]]
    write_scope = os.environ["WRITE_SCOPE"]
    admin = os.environ["ADMIN_GROUP"] in groups
    if admin:
        scopes.append(write_scope)
    event.setdefault("response", {})["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {
            "scopesToAdd": scopes,
            "scopesToSuppress": [] if admin else [write_scope],
        },
    }
    return event
