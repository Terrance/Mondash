from datetime import datetime
from decimal import Decimal
from functools import wraps
import logging
from random import choices
import string
from urllib.parse import urlparse, urlunparse

from aiohttp import ClientSession, ClientResponseError
import aiohttp_session as aiosession


API_HOST = "https://api.monzo.com"


log = logging.getLogger(__name__)


def rand_str():
    return "".join(choices(string.ascii_uppercase + string.digits, k=32))


def currency(amount, decimal=True):
    if amount % 100 == 0 and not decimal:
        return str(amount / 100)
    else:
        return "{:.2f}".format(Decimal(amount) / 100)

def date_format(timestamp, format):
    try:
        date = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        date = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    return date.strftime(format)

def url(text, display=False):
    parsed = urlparse(text)
    if display:
        if parsed.netloc:
            path = "{}/{}".format(parsed.netloc, parsed.path).replace("//", "/")
            parsed = parsed._replace(netloc="", path=path)
        while parsed.path.endswith("/"):
            parsed = parsed._replace(path=parsed.path[:-1])
        parsed = parsed._replace(scheme="", params="", query="", fragment="")
    else:
        if not parsed.scheme:
            parsed = parsed._replace(scheme="http")
        if parsed.path and not parsed.netloc:
            parts = parsed.path.split("/", 1)
            parsed = parsed._replace(netloc=parts.pop(0), path="/{}".format("/".join(parts)))
    return urlunparse(parsed)


class MonzoAPI:
    """
    A thin asynchronous wrapper around Monzo's API.

    Using an existing token::

        >>> api = MonzoAPI(token)

    Using a new OAuth code::

        >>> api = MonzoAPI()
        >>> with api:
        ...     await api.auth("oauth2client_xyz", "mnzpub.xyzxyz",
        ...                    "https://example.com/callback", "code")
        {"access_token": "xyzxyz", ...}

    Accessing account data (``with api``)::

        >>> await api.accounts()
        [{"id": "acc_xyz", ...}]
        >>> await api.balance("acc_xyz")
        {"balance": 1234, ...}
    """

    class NotAuthorisedError(Exception): pass

    def __init__(self, token=None):
        self._token = token
        self._user = None
        self._sess = ClientSession()

    async def __call__(self, method, path, key=None, **kwargs):
        log.debug("API call: {} {}".format(method, path))
        headers = {}
        if self._token:
            headers["Authorization"] = "Bearer {}".format(self._token)
        async with self._sess.request(method, "{}/{}".format(API_HOST, path),
                                      headers=headers, **kwargs) as resp:
            try:
                resp.raise_for_status()
            except ClientResponseError as e:
                if e.code == 401:
                    raise MonzoAPI.NotAuthorisedError
                raise
            data = await resp.json()
            return data[key] if key else data

    async def __aenter__(self):
        await self._sess.__aenter__()
        return self

    @property
    def __aexit__(self):
        return self._sess.__aexit__

    async def auth(self, client_id, client_secret, redirect_uri, code):
        data = await self("POST", "/oauth2/token",
                          data={"grant_type": "authorization_code",
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "redirect_uri": redirect_uri,
                                "code": code})
        self._token = data["access_token"]
        self._user = data["user_id"]
        return data

    async def whoami(self):
        return await self("GET", "/ping/whoami")

    async def user(self):
        if not self._user:
            self._user = (await self.whoami())["user_id"]
        return self._user

    async def accounts(self):
        return await self("GET", "/accounts", "accounts")

    async def pots(self):
        return []  # No longer available.

    async def balance(self, account_id):
        return await self("GET", "/balance", params={"account_id": account_id})

    async def transactions(self, account_id, since=None):
        data = await self("GET", "/transactions", "transactions",
                          params={"account_id": account_id,
                                  "expand[]": "merchant",
                                  "since": since or ""})
        return [item for item in data if not item["created"] == since]


def session(fn):
    @wraps(fn)
    async def session_wrap(request):
        return await fn(request, await aiosession.get_session(request))
    return session_wrap
