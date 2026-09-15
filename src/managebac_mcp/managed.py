"""Server-owned account authorization, encrypted storage and isolated caches."""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import inspect
import json
import os
import secrets
import threading
import time
import re
import shutil
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import anyio
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .config import load_managebac_config
from .credentials import Credentials
from .db import Database
from .errors import AppError
from .services import ActionService, ReadService, SyncService

selected_account: ContextVar[dict | None] = ContextVar("managebac_account", default=None)
COOKIE = "__Host-managebac_settings"


def school_url(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or not host.endswith(".managebac.com")
        or parsed.username or parsed.password or parsed.port or parsed.query
        or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Enter your school address: https://school.managebac.com")
    return f"https://{host}"


class AccountVault:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()

    def _key(self) -> bytes:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / "accounts.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
        return path.read_bytes()

    def read(self) -> dict:
        path = self.root / "accounts.enc"
        if not path.exists():
            return {"accounts": [], "default_id": None}
        raw = path.read_bytes()
        return json.loads(AESGCM(self._key()).decrypt(raw[:12], raw[12:], b"managebac"))

    def write(self, state: dict):
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key()).encrypt(nonce, json.dumps(state).encode(), b"managebac")
        path = self.root / f".accounts-{secrets.token_hex(6)}.tmp"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(nonce + ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
        path.replace(self.root / "accounts.enc")

    def public(self) -> dict:
        with self.lock:
            state = self.read()
            return {"default_id": state["default_id"], "accounts": [
                {key: a[key] for key in ("id", "label", "username", "school", "verified_at")}
                for a in state["accounts"]]}

    def account(self, identifier: str | None = None) -> dict:
        with self.lock:
            state = self.read()
            identifier = identifier or state["default_id"]
            account = next((a for a in state["accounts"] if a["id"] == identifier), None)
            if not account:
                raise AppError("AUTH_MISSING_CREDENTIALS", "Connect an account on managebac.archik.tech.")
            return dict(account)

    def save(self, account: dict) -> dict:
        with self.lock:
            state = self.read()
            identifier = account.get("id") or secrets.token_hex(16)
            existing = next((a for a in state["accounts"] if a["id"] == identifier), None)
            if account.get("id") and not existing:
                raise ValueError("Account not found")
            entry = dict(account, id=identifier, verified_at=int(time.time()))
            state["accounts"] = [a for a in state["accounts"] if a["id"] != identifier] + [entry]
            state["default_id"] = state["default_id"] or identifier
            self.write(state)
            return entry

    def change(self, identifier: str, *, remove=False):
        with self.lock:
            state = self.read()
            if not any(a["id"] == identifier for a in state["accounts"]):
                raise ValueError("Account not found")
            if remove:
                state["accounts"] = [a for a in state["accounts"] if a["id"] != identifier]
                if state["default_id"] == identifier:
                    state["default_id"] = next((a["id"] for a in state["accounts"]), None)
            else:
                state["default_id"] = identifier
            self.write(state)


class Verifier(TokenVerifier):
    def __init__(self, owner):
        self.owner = owner

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.owner.secret:
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(f"{self.owner.approver}/internal/introspect",
                    headers={"Authorization": f"Bearer {self.owner.secret}"}, json={"token": token})
            value = response.json()
            if (response.status_code != 200 or not value.get("active")
                or value.get("resource") != self.owner.resource or value.get("subject") != "owner"
                or "managebac" not in value.get("scopes", [])):
                return None
            return AccessToken(token=token, client_id=value["client_id"],
                scopes=value["scopes"], resource=self.owner.resource,
                subject="owner", expires_at=value.get("expires_at"))
        except (httpx.HTTPError, ValueError, KeyError):
            return None


class ServiceProxy:
    def __init__(self, owner, index):
        self.owner, self.index = owner, index

    def __getattr__(self, name):
        return getattr(self.owner.services()[self.index], name)


class ManagedAccounts:
    def __init__(self, settings):
        self.settings = settings
        self.config = load_managebac_config(settings.managebac_config_path)
        self.origin = os.getenv("MANAGEBAC_PUBLIC_ORIGIN", "https://managebac.archik.tech").rstrip("/")
        self.resource = f"{self.origin}/mcp"
        self.issuer = os.getenv("OAUTH_ISSUER", "https://auth.archik.tech").rstrip("/")
        self.approver = os.getenv("APPROVER_URL", "http://127.0.0.1:8102").rstrip("/")
        self.secret = os.getenv("APPROVER_INTERNAL_SECRET", "")
        self.root = Path(os.getenv("MANAGEBAC_DATA_DIR", "/var/lib/manageback-mcp"))
        self.vault = AccountVault(self.root)
        self.cache = {}
        self.lock = threading.RLock()

    def credentials(self) -> Credentials | None:
        account = selected_account.get()
        if account:
            return account["username"], account["password"]
        return None

    def services(self):
        account = selected_account.get()
        if account is None:
            raise AppError("AUTH_MISSING_CREDENTIALS", "Choose a connected account.")
        key = account["id"]
        with self.lock:
            if key not in self.cache:
                from .browser import PlaywrightBrowserGateway
                config = self.config.model_copy(update={"base_url": account["school"]})
                directory = self.root / "accounts" / key
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                db = Database(f"sqlite:///{directory / 'cache.sqlite3'}")
                db.create_all()
                browser = PlaywrightBrowserGateway(config, directory / "artifacts")
                self.cache[key] = (db, SyncService(db, browser, config.timezone),
                    ReadService(db, config.timezone), ActionService(db, browser, config.timezone))
            return self.cache[key]

    def tool(self, mcp, **options):
        def decorate(function):
            signature = inspect.signature(function, eval_str=True)
            parameters = list(signature.parameters.values()) + [inspect.Parameter(
                "account_id", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=str | None)]
            @functools.wraps(function)
            async def wrapper(*args, **kwargs):
                token = get_access_token()
                if token is None or token.subject != "owner":
                    raise AppError("AUTH_REQUIRED", "Authorization required.")
                account = self.vault.account(kwargs.pop("account_id", None))
                marker = selected_account.set(account)
                try:
                    return await anyio.to_thread.run_sync(functools.partial(function, *args, **kwargs))
                finally:
                    selected_account.reset(marker)
            wrapper.__signature__ = signature.replace(parameters=parameters)
            wrapper.__annotations__ = dict(function.__annotations__, account_id=str | None)
            mcp.tool(**options)(wrapper)
            return wrapper
        return decorate

    def mcp(self):
        host = urlsplit(self.origin).netloc
        mcp = FastMCP("ManageBac", token_verifier=Verifier(self),
            auth=AuthSettings(issuer_url=AnyHttpUrl(self.issuer),
                resource_server_url=AnyHttpUrl(self.resource), required_scopes=["managebac"]),
            transport_security=TransportSecuritySettings(allowed_hosts=[host, f"{host}:*", "127.0.0.1:8134"],
                allowed_origins=[self.origin]), stateless_http=True, json_response=True)
        for path, methods, handler in [
            ("/", ["GET"], self.page), ("/settings", ["GET"], self.page),
            ("/settings/auth/callback", ["GET"], self.callback),
            ("/settings/api/accounts", ["GET", "POST"], self.accounts),
            ("/settings/api/accounts/{identifier}", ["DELETE"], self.delete),
            ("/settings/api/default", ["POST"], self.default),
            ("/health", ["GET"], self.health),
            ("/settings/app.js", ["GET"], self.script)]:
            mcp.custom_route(path, methods=methods)(handler)
        @mcp.tool(name="disconnect_account", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
        def disconnect_account(external_subject: str) -> dict:
            """Revoke one server-stored account and delete its local cache. The school account itself is unchanged. Idempotent lifecycle operation."""
            return self.remove_account(external_subject)
        return mcp

    def cookie(self) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"app": "managebac", "sub": "owner", "exp": int(time.time()) + 86400}).encode()).decode()
        signature = hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def valid(self, request) -> bool:
        try:
            payload, signature = request.cookies.get(COOKIE, "").split(".", 1)
            value = json.loads(base64.urlsafe_b64decode(payload))
            return (bool(self.secret) and hmac.compare_digest(signature,
                hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest())
                and value.get("app") == "managebac" and value.get("sub") == "owner"
                and int(value.get("exp", 0)) > time.time())
        except (ValueError, TypeError):
            return False

    def deny(self, request, *, mutation=False):
        if not self.valid(request):
            return JSONResponse({"error": "Sign in to manage accounts"}, status_code=401)
        if mutation and request.headers.get("origin") != self.origin:
            return JSONResponse({"error": "Invalid request origin"}, status_code=403)
        return None

    @staticmethod
    def headers():
        return {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}

    async def page(self, request):
        if not self.valid(request):
            destination = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"{self.issuer}/settings/login/managebac?return={quote(destination, safe='')}", status_code=303)
        return FileResponse(Path(__file__).parent / "static" / "settings.html", headers=self.headers())

    async def script(self, _request):
        return FileResponse(Path(__file__).parent / "static" / "settings.js", headers={"Cache-Control": "no-cache"}, media_type="text/javascript")

    async def callback(self, request):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(f"{self.approver}/internal/settings-ticket/redeem",
                    headers={"Authorization": f"Bearer {self.secret}"},
                    json={"app": "managebac", "ticket": request.query_params.get("ticket", "")})
            response.raise_for_status()
            value = response.json()
            if not value.get("owner"):
                raise ValueError("owner required")
            destination = str(value.get("return") or "/settings")
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc or not destination.startswith("/settings") or "\\" in destination:
                destination = "/settings"
        except (httpx.HTTPError, ValueError):
            return HTMLResponse("Sign-in link expired. Open settings again.", status_code=403, headers=self.headers())
        result = RedirectResponse(destination, status_code=303, headers=self.headers())
        result.set_cookie(COOKIE, self.cookie(), secure=True, httponly=True, samesite="lax", path="/", max_age=86400)
        return result

    async def accounts(self, request):
        denied = self.deny(request, mutation=request.method == "POST")
        if denied is not None:
            return denied
        if request.method == "GET":
            return JSONResponse(dict(self.vault.public(), school=self.config.base_url), headers=self.headers())
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Invalid request")
            identifier = str(body.get("id") or "")
            existing = self.vault.account(identifier) if identifier else None
            username = str(body.get("username") or "").strip()
            password = str(body.get("password") or (existing or {}).get("password") or "")
            school = school_url(str(body.get("school") or self.config.base_url))
            if not username or not password or len(username) > 254 or len(password) > 1024:
                raise ValueError("Enter username and password ManageBac")
            if existing and (school != existing["school"] or username != existing["username"]):
                raise ValueError("For another account or school, use Add account»")
            from .browser import PlaywrightBrowserGateway
            browser = PlaywrightBrowserGateway(self.config.model_copy(update={"base_url": school}), self.root / "auth-artifacts")
            await anyio.to_thread.run_sync(browser.login, username, password)
            self.vault.save({"id": identifier or None, "username": username, "password": password,
                "school": school, "label": str(body.get("label") or username).strip()[:80]})
            return JSONResponse(self.vault.public(), headers=self.headers())
        except (AppError, ValueError, TypeError) as exc:
            message = "Unable to sign in to ManageBac. Check your username and password." if isinstance(exc, AppError) else str(exc)
            return JSONResponse({"error": message}, status_code=400, headers=self.headers())

    def remove_account(self, identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid account identifier")
        try:
            self.vault.change(identifier, remove=True)
        except ValueError:
            return {"removed": False}
        with self.lock:
            services = self.cache.pop(identifier, None)
            if services:
                services[0].engine.dispose()
        directory = self.root / "accounts" / identifier
        if directory.is_dir() and not directory.is_symlink():
            shutil.rmtree(directory)
        return {"removed": True}

    async def delete(self, request):
        denied = self.deny(request, mutation=True)
        if denied is not None:
            return denied
        identifier = request.path_params["identifier"]
        try:
            self.remove_account(identifier)
            return JSONResponse(self.vault.public(), headers=self.headers())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404, headers=self.headers())

    async def default(self, request):
        denied = self.deny(request, mutation=True)
        if denied is not None:
            return denied
        try:
            body = await request.json()
            self.vault.change(str(body.get("id") or ""))
            return JSONResponse(self.vault.public(), headers=self.headers())
        except (ValueError, TypeError, AttributeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400, headers=self.headers())

    async def health(self, _request):
        return JSONResponse({"ok": True, "service": "managebac-mcp", "authorization": "external"})
