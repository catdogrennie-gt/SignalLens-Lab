"""Anonymous fixed fault switch; all analysis belongs to the separate platform."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from lab.toggle_controller import Controller, load_config


class SwitchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    on:StrictBool


def create_app(*,config=None,controller=None,start_controller=True):
    config=config or load_config()
    controller=controller or Controller(config)
    origins={config['public_origin'],*config.get('allowed_origins',[])}
    @asynccontextmanager
    async def lifespan(app):
        if start_controller:controller.start()
        try:yield
        finally:
            if start_controller:controller.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.controller=controller
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=sorted({urlsplit(origin).hostname for origin in origins}))
    @app.middleware('http')
    async def boundaries(request,call_next):
        if request.method in ('PUT','POST','PATCH','DELETE') and request.headers.get('origin') not in origins:
            return JSONResponse({'error':{'code':'origin_rejected'}},status_code=403)
        if request.url.path.startswith('/api/') and request.url.query:
            return JSONResponse({'error':{'code':'extra_query_forbidden'}},status_code=422)
        try:response=await call_next(request)
        except Exception:return JSONResponse({'error':{'code':'showcase_unavailable'}},status_code=503)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response
    @app.get('/api/state')
    def state():return controller.state()
    @app.put('/api/switch')
    def switch(body:SwitchInput):return controller.set_desired(body.on)
    @app.get('/api/health')
    def health():return {'process':'alive'}
    web=Path(__file__).parent/'web'
    @app.get('/')
    def index():return FileResponse(web/'index.html',media_type='text/html')
    @app.get('/app.js')
    def script():return FileResponse(web/'app.js',media_type='text/javascript')
    @app.get('/styles.css')
    def style():return FileResponse(web/'styles.css',media_type='text/css')
    return app
