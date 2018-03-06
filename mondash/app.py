#!/usr/bin/env python3

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps
from itertools import chain
import logging
import os
import sys
from urllib.parse import urlencode

from aiohttp import web
import aiohttp_jinja2 as aiojinja
import aiohttp_session as aiosession
import jinja2

from .utils import rand_str, currency, date_format, MonzoAPI, session


AUTH_HOST = "https://auth.monzo.com"


log = logging.getLogger(__name__)

cache = {}


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
                return await fn(request, sess, MonzoAPI(token))
            except MonzoAPI.NotAuthorisedError:
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
    sess["token"] = data["access_token"]
    sess["expires"] = datetime.now().timestamp() + data["expires_in"]
    return web.HTTPFound("/")


@auth_redir
async def clear(request, sess, api):
    user = await api.user()
    if user in cache:
        del cache[user]
    return web.HTTPFound("/")

@auth_redir
async def logout(request, sess, api):
    user = await api.user()
    if user in cache:
        del cache[user]
    del sess["token"]
    return web.HTTPFound("/")


@aiojinja.template("base.j2")
@auth_redir
async def base(request, sess, api):
    async with api:
        user = await api.user()
        if user in cache:
            accounts = cache[user]["accounts"]
            pots = cache[user]["pots"]
        else:
            accounts, pots = await asyncio.gather(api.accounts(), api.pots())
        account_ids = [account["id"] for account in accounts]
        default = next(account for account in accounts if not account["closed"])
        balance_data = await asyncio.gather(*(api.balance(id) for id in account_ids))
        balances = dict(zip(account_ids, balance_data))
        items = []
        since = None
        if user in cache:
            items = cache[user]["items"]
            since = items[-1]["created"]
        item_data = await asyncio.gather(*(api.transactions(id, since) for id in account_ids))
        items += list(chain(*item_data))
        items.sort(key=lambda item: item["created"])
        cache[user] = {"accounts": accounts,
                       "pots": pots,
                       "items": items}
    inbounds = defaultdict(int)
    outbounds = defaultdict(int)
    categories = defaultdict(lambda: defaultdict(int))
    merchants = defaultdict(lambda: defaultdict(int))
    matches = {}
    dupes = set()
    for item in items:
        if item["amount"] == 0 or item.get("decline_reason"):
            continue
        month = date_format(item["created"], "%Y-%m")
        merchant = None
        if item["merchant"]:
            merchant = item["merchant"]["name"]
        elif item["counterparty"]:
            merchant = item["counterparty"]["name"]
        amount = item["amount"]
        if amount > 0:
            inbounds[month] += amount
        else:
            outbounds[month] += amount
        if (merchant, -amount) in matches:
            dupes.add(item["id"])
            dupes.add(matches.pop((merchant, -amount)))
        else:
            matches[(merchant, amount)] = item["id"]
        if not merchant and item["is_load"]:
            merchant = "Top-up"
        categories[month][item["category"]] += amount
        merchants[month][merchant or ""] += amount
    return {"accounts": accounts,
            "default": default,
            "pots": pots,
            "balances": balances,
            "items": items,
            "inbounds": inbounds,
            "outbounds": outbounds,
            "categories": categories,
            "merchants": merchants,
            "dupes": dupes}


def init_app(args=()):
    logging.basicConfig(level=logging.DEBUG)
    app = web.Application()
    app["client_id"] = os.getenv("CLIENT_ID")
    app["client_secret"] = os.getenv("CLIENT_SECRET")
    app["client_host"] = os.getenv("CLIENT_HOST")
    env = aiojinja.setup(app, loader=jinja2.FileSystemLoader(
        os.path.join(os.path.dirname(__file__), "templates")))
    env.globals.update({
        "currency": currency,
        "date_format": date_format
    })
    aiosession.setup(app, storage=aiosession.SimpleCookieStorage())  # TODO
    app.router.add_get("/callback", callback, name="callback")
    app.router.add_get("/clear", clear)
    app.router.add_get("/logout", logout)
    app.router.add_get("/", base)
    app.router.add_static("/static", os.path.join(os.path.dirname(__file__), "static"))
    return app


if __name__ == "__main__":
    web.run_app(init_app(sys.argv[1:]))
