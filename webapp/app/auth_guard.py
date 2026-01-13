# webapp/app/auth_guard.py
from functools import wraps
from fastapi import Request
from starlette.responses import RedirectResponse, Response
from app.config import get_settings
import httpx, os
from urllib.parse import urlencode

API_BASE = (os.getenv("API_BASE_URL") or "http://api:8000").strip()

if "://" not in API_BASE:
    API_BASE = "http://" + API_BASE

ACCESS_COOKIE = get_settings().ACCESS_COOKIE

print("[AUTH_GUARD] API_BASE =", API_BASE)

def _same_path_with_flag(request: Request) -> str:
    qs = dict(request.query_params)
    qs["__rf"] = "1"
    return f"{request.url.path}?{urlencode(qs)}" if qs else request.url.path

def login_required(fn):
    @wraps(fn)
    async def _wrap(request: Request, *args, **kwargs):
        settings = get_settings()
        is_ajax = request.headers.get("X-Requested-With") in {"fetch", "XMLHttpRequest"}

        access = request.cookies.get(ACCESS_COOKIE)
        already_refreshed = request.query_params.get("__rf") == "1"

        try:
            async with httpx.AsyncClient(base_url=API_BASE, cookies=request.cookies, timeout=5.0) as client:
                # 1) Pas d'access → tenter un refresh (une seule fois)
                if not access and not already_refreshed:
                    print("[AUTH_GUARD] Aucun access cookie → tentative /auth/refresh-cookie")
                    r = await client.post("/api/v1/auth/refresh-cookie")
                    print("[AUTH_GUARD] refresh from missing access =>", r.status_code)
                    if r.status_code == 200:
                        if is_ajax:
                            # AJAX : 200 OK + X-Auth-Redirect pour rediriger le front
                            resp = Response(status_code=200)
                            for c in r.headers.get_list("set-cookie"):
                                resp.headers.append("set-cookie", c)
                            resp.headers["X-Auth-Refreshed"] = "1"
                            return resp

                        # Page pleine: 303 + __rf=1
                        redir = RedirectResponse(_same_path_with_flag(request), status_code=303)
                        for c in r.headers.get_list("set-cookie"):
                            redir.headers.append("set-cookie", c)
                        return redir

                # 2) Vérifier /auth/me
                me = await client.get("/api/v1/auth/me")
                print("[AUTH_GUARD] /auth/me =>", me.status_code)
    
        except httpx.RequestError as e:
            print("[AUTH_GUARD] httpx.RequestError sur /auth/* :", repr(e))
            # API injoignable → si AJAX, 401 + X-Auth-Redirect, sinon 303 /login
            if is_ajax:
                resp = Response(status_code=401)
                resp.headers["X-Auth-Redirect"] = "1"
                return resp
            return RedirectResponse(settings.LOGIN_PATH, status_code=303)

        if me.status_code == 200:
            request.state.user = me.json()
            return await fn(request, *args, **kwargs)

        # 3) 401 → tenter refresh si pas encore tenté
        if me.status_code == 401 and not already_refreshed:
            print("[AUTH_GUARD] 401 → tentative /auth/refresh-cookie")
            try:
                async with httpx.AsyncClient(base_url=API_BASE, cookies=request.cookies, timeout=5.0) as client:
                    r = await client.post("/api/v1/auth/refresh-cookie")
                    print("[AUTH_GUARD] refresh after 401 =>", r.status_code)
            except httpx.RequestError as e:
                print("[AUTH_GUARD] httpx.RequestError sur refresh after 401 :", repr(e))
                r = None

            if r is not None and r.status_code == 200:
                if is_ajax:
                    # AJAX : 200 OK + X-Auth-Redirect pour rediriger le front
                    resp = Response(status_code=200)
                    for c in r.headers.get_list("set-cookie"):
                        resp.headers.append("set-cookie", c)
                    resp.headers["X-Auth-Redirect"] = "1"
                    return resp
                else:
                    # Page pleine: 303 + __rf=1
                    redir = RedirectResponse(_same_path_with_flag(request), status_code=303)
                    for c in r.headers.get_list("set-cookie"):
                        redir.headers.append("set-cookie", c)
                    return redir

        # 4) Échec d’auth : si AJAX → 401 + X-Auth-Redirect, sinon → 303 /login
        print("[AUTH_GUARD] Échec d’auth → redirection /login (AJAX?", is_ajax, ")")
        if is_ajax:
            resp = Response(status_code=401)
            resp.headers["X-Auth-Redirect"] = "1"
            return resp

        return RedirectResponse(settings.LOGIN_PATH, status_code=303)

    return _wrap
