import asyncio
from datetime import datetime
from decimal import Decimal
from functools import wraps
import logging
from random import choices
import string

from aiohttp import ClientSession, ClientResponseError
import aiohttp_session as aiosession


API_HOST = "https://api.monzo.com"


log = logging.getLogger(__name__)


def rand_str():
    return "".join(choices(string.ascii_uppercase + string.digits, k=32))


def currency(amount):
    return Decimal(amount) / 100

def date(timestamp):
    try:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")


class NotAuthorisedError(Exception):
    pass


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

    def __init__(self, token=None):
        self._token = token
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
                    raise NotAuthorisedError
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
        self.token = data["access_token"]
        return data

    async def accounts(self):
        return await self("GET", "/accounts", "accounts")

    async def pots(self):
        data = await self("GET", "/pots/listV1", "pots")
        for pot in data:
            pot["balance"] = currency(pot["balance"])
        return data

    async def balance(self, account_id):
        data = await self("GET", "/balance", params={"account_id": account_id})
        data["balance"] = currency(data["balance"])
        data["spend_today"] = currency(data["spend_today"])
        return data

    async def transactions(self, *account_ids):
        tasks = []
        for account_id in account_ids:
            tasks.append(self("GET", "/transactions", "transactions",
                              params={"account_id": account_id,
                                      "expand[]": "merchant"}))
        data = [item for items in (await asyncio.gather(*tasks)) for item in items]
        data.sort(key=lambda t: t["created"])
        for item in data:
            item["amount"] = currency(item["amount"])
            item["local_amount"] = currency(item["local_amount"])
            item["created"] = date(item["created"])
            if item["merchant"]:
                item["merchant"]["created"] = date(item["merchant"]["created"])
        return data


def session(fn):
    @wraps(fn)
    async def session_wrap(request):
        return await fn(request, await aiosession.get_session(request))
    return session_wrap