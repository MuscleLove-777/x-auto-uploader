"""One bounded, read-only X OAuth1 authentication probe. Never publishes."""

import argparse
import json
import os
from pathlib import Path

from requests.adapters import HTTPAdapter
from requests_oauthlib import OAuth1Session


EXPECTED_HANDLE = "MuscleGirlLove7"
ENDPOINT = "https://api.x.com/2/users/me"
SECRET_NAMES = (
    "X_CONSUMER_KEY",
    "X_CONSUMER_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)
KNOWN_ERROR_CODES = frozenset({32, 34, 64, 88, 89, 99, 135, 179, 215, 220, 261, 326, 453})


def result(status, *, calls=0, http_status=None, error_codes=None, matched=False):
    value = {
        "status": status,
        "endpoint": "users_me_v2",
        "api_requests": calls,
        "public_posts": 0,
        "http_status": http_status,
        "account_match": matched,
    }
    if matched:
        value["verified_handle"] = EXPECTED_HANDLE
    if error_codes:
        value["known_error_codes"] = sorted(set(error_codes))
    return value


def create_session():
    """Native signing session; credentials come directly from process environment."""
    return OAuth1Session(
        os.environ["X_CONSUMER_KEY"],
        client_secret=os.environ["X_CONSUMER_SECRET"],
        resource_owner_key=os.environ["X_ACCESS_TOKEN"],
        resource_owner_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def auth_probe(env=None, session_factory=create_session):
    """Inspect only allowlisted response fields; never return response/error text."""
    env = os.environ if env is None else env
    if env.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        return result("HOLD_RERUN")
    if any(not env.get(name, "").strip() for name in SECRET_NAMES):
        return result("HOLD_CREDENTIALS_MISSING")
    calls = 0
    http_status = None
    try:
        with session_factory() as session:
            session.trust_env = False
            session.mount("https://", HTTPAdapter(max_retries=0))
            calls = 1
            response = session.get(
                ENDPOINT,
                allow_redirects=False,
                timeout=(10, 30),
                verify=True,
            )
            http_status = response.status_code
            if not isinstance(http_status, int) or isinstance(http_status, bool) or not 100 <= http_status <= 599:
                return result("HOLD_INVALID_RESPONSE", calls=calls)
            try:
                body = response.json()
            except (ValueError, TypeError):
                return result("HOLD_INVALID_RESPONSE", calls=calls, http_status=http_status)
            if not isinstance(body, dict):
                return result("HOLD_INVALID_RESPONSE", calls=calls, http_status=http_status)
            errors = body.get("errors")
            codes = [item["code"] for item in errors if isinstance(item, dict)
                     and type(item.get("code")) is int and item["code"] in KNOWN_ERROR_CODES] if isinstance(errors, list) else []
            if http_status != 200 or errors:
                return result("HOLD_API_RESPONSE", calls=calls, http_status=http_status, error_codes=codes)
            data = body.get("data")
            if not isinstance(data, dict):
                return result("HOLD_INVALID_RESPONSE", calls=calls, http_status=http_status)
            handle = data.get("username")
            user_id = data.get("id")
            if not isinstance(handle, str) or not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdigit():
                return result("HOLD_INVALID_RESPONSE", calls=calls, http_status=http_status)
            if handle.casefold() != EXPECTED_HANDLE.casefold():
                return result("HOLD_ACCOUNT_MISMATCH", calls=calls, http_status=http_status)
            return result("PASS_AUTH_ONLY", calls=calls, http_status=http_status, matched=True)
    except Exception:
        # OAuth/network exceptions can contain signed URLs or headers. Do not log them.
        return result("HOLD_REQUEST_FAILED", calls=calls, http_status=http_status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-auth-check", action="store_true")
    parser.add_argument("--output", default="cloud_once_result.json")
    args = parser.parse_args(argv)
    outcome = auth_probe() if args.live_auth_check else result("DRY_RUN_NO_NETWORK")
    encoded = json.dumps(outcome, ensure_ascii=True, indent=2) + "\n"
    Path(args.output).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if outcome["status"] in {"PASS_AUTH_ONLY", "DRY_RUN_NO_NETWORK"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
