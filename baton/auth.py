"""Sign-in through Supabase Auth's REST API. The app keeps only the account id and email, in a signed cookie."""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class AuthError(ValueError):
    """Shown to the user."""


@dataclass
class User:
    id: str
    email: str


class SupabaseAuth:
    def __init__(self, url: str, anon_key: str, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self.client = client or httpx.Client(timeout=15)

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self.client.post(f"{self.url}/auth/v1/{path}", json=body, headers={"apikey": self.anon_key})
        except httpx.HTTPError as e:
            raise AuthError("The sign-in service could not be reached. Please try again.") from e
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            message = str(data.get("msg") or data.get("error_description") or data.get("message") or "")
            code = str(data.get("error_code") or data.get("error") or "")
            if "invalid" in code or "invalid" in message.lower():
                raise AuthError("That email and password don't match.")
            if "confirm" in code or "not confirmed" in message.lower():
                raise AuthError("Please confirm your email first, using the link we sent you.")
            if r.status_code == 429:
                raise AuthError("Too many attempts. Please wait a minute and try again.")
            raise AuthError(message or "Sign-in failed. Please try again.")
        return data

    def sign_in(self, email: str, password: str) -> User:
        data = self._post("token?grant_type=password", {"email": email, "password": password})
        return User(data["user"]["id"], data["user"]["email"].lower())

    def sign_up(self, email: str, password: str) -> User | None:
        """Returns the user, or None when the project requires email confirmation first."""
        data = self._post("signup", {"email": email, "password": password})
        if data.get("access_token") and data.get("user"):
            return User(data["user"]["id"], data["user"]["email"].lower())
        return None


def auth_from_env() -> SupabaseAuth | None:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_ANON_KEY")
    return SupabaseAuth(url, key) if url and key else None
