# Mondash

A web dashboard for [Monzo](https://monzo.com) banking.

## Requirements

* Python 3.5+
* [`aiohttp`](https://github.com/aio-libs/aiohttp)
* [`aiohttp_jinja2`](https://github.com/aio-libs/aiohttp-jinja2)
* [`aiohttp_session`](https://github.com/aio-libs/aiohttp-session)
* an [OAuth client](https://developers.monzo.com/apps) for Monzo's APIs

## Configuration

Use the following environment variables:

* `CLIENT_ID`
* `CLIENT_SECRET`
* `CLIENT_HOST`

Client ID and secret belong to the OAuth client.  Host is the full URL of the
app, as seen from the browser.

You'll need to add `$CLIENT_HOST/callback` as a redirect URL for your client.

## Startup

```
$ python -m aiohttp.web -P $PORT mondash.app:init_app
```

Then open `http://localhost:$PORT` in your browser.
