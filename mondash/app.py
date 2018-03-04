#!/usr/bin/env python3

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
import logging
import os
import sys
from urllib.parse import urlencode

from aiohttp import web
import aiohttp_jinja2 as aiojinja
import aiohttp_session as aiosession
import jinja2

from .utils import rand_str, NotAuthorisedError, MonzoAPI, session


AUTH_HOST = "https://auth.monzo.com"


log = logging.getLogger(__name__)


def start_auth(request, sess):
    sess["state"] = rand_str()
    callback_url = request.app.router.named_resources()["callback"].url_for()
    data = {"client_id": request.app["client_id"],
            "redirect_uri": "{}{}".format(request.app["client_host"], callback_url),
            "response_type": "code",
            "state": sess["state"]}
    return web.HTTPFound("{}/?{}".format(AUTH_HOST, urlencode(data)))

def auth_redir(fn):
    @wraps(fn)
    @session
    async def auth_redir_wrap(request, sess):
        try:
            token = sess["token"]
            expires = sess["expires"]
            if expires < datetime.now().timestamp():
                raise KeyError
        except KeyError:
            return start_auth(request, sess)
        else:
            try:
                return await fn(request, MonzoAPI(token))
            except NotAuthorisedError:
                return start_auth(request, sess)
    return auth_redir_wrap


@session
async def callback(request, sess):
    code = request.query["code"]
    state = request.query["state"]
    if not state == sess["state"]:
        raise web.HTTPBadRequest
    async with MonzoAPI() as api:
        data = await api.auth(request.app["client_id"],
                              request.app["client_secret"],
                              "{}/callback".format(request.app["client_host"]),
                              code)
    print(data)
    sess["token"] = data["access_token"]
    sess["expires"] = datetime.now().timestamp() + data["expires_in"]
    return web.HTTPFound("/")


@session
async def logout(request, sess):
    del sess["token"]
    return web.HTTPFound("/")


@aiojinja.template("base.j2")
@auth_redir
async def base(request, api):
    async with api:
        accounts, pots = await asyncio.gather(api.accounts(), api.pots())
        account_ids = [account["id"] for account in accounts]
        default = next(account for account in accounts if not account["closed"])
        tasks = (api.balance(id) for id in account_ids)
        balances = dict(zip(account_ids, await asyncio.gather(*tasks)))
        items = await api.transactions(*account_ids)
    amounts = defaultdict(lambda: (0, 0))
    matches = {}
    dupes = set()
    for item in items:
        if item["amount"] == 0 or item.get("decline_reason") or not item["merchant"]:
            continue
        amount = item["amount"]
        if (item["merchant"]["name"], -amount) in matches:
            dupes.add(item["id"])
            dupes.add(matches.pop((item["merchant"]["name"], -amount)))
        else:
            matches[(item["merchant"]["name"], amount)] = item["id"]
    return {"accounts": accounts,
            "default": default,
            "pots": pots,
            "balances": balances,
            "items": items,
            "dupes": dupes}


def init_app(args=()):
    logging.basicConfig(level=logging.DEBUG)
    app = web.Application()
    app["client_id"] = os.getenv("CLIENT_ID")
    app["client_secret"] = os.getenv("CLIENT_SECRET")
    app["client_host"] = os.getenv("CLIENT_HOST")
    aiojinja.setup(app, loader=jinja2.FileSystemLoader(
        os.path.join(os.path.dirname(__file__), "templates")))
    aiosession.setup(app, storage=aiosession.SimpleCookieStorage())  # TODO
    app.router.add_get("/callback", callback, name="callback")
    app.router.add_get("/logout", logout)
    app.router.add_get("/", base)
    app.router.add_static("/static", os.path.join(os.path.dirname(__file__), "static"))
    return app


if __name__ == "__main__":
    web.run_app(init_app(sys.argv[1:]))
