"""New multi-scenario entrance; the original toggle site remains unchanged."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .scenario_engine import Controller, load_config, RevisionConflict


class Switch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    on:StrictBool
    scenario:str|None=None
    expected_revision:StrictInt|None=Field(default=None,ge=0)
    expected_generation:str|None=Field(default=None,min_length=32,max_length=32)
    lease_seconds:StrictInt|None=Field(default=None,ge=1,le=3600)
    validation_id:str|None=Field(default=None,min_length=32,max_length=32)
    expected_validation_id:str|None=Field(default=None,min_length=32,max_length=32)


def create_app(config=None,controller=None,start_controller=True):
    config=config or load_config();controller=controller or Controller(config)
    origins={config['public_origin'],*config.get('allowed_origins',[])}
    @asynccontextmanager
    async def lifespan(app):
        if start_controller:controller.start()
        try:yield
        finally:
            if start_controller:controller.close()
    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=sorted({urlsplit(o).hostname for o in origins}))
    @app.middleware('http')
    async def boundary(request:Request,call_next):
        if request.method in ('PUT','POST','PATCH','DELETE') and request.headers.get('origin') not in origins:
            return JSONResponse({'error':'origin_rejected'},status_code=403)
        if request.url.path.startswith('/api/') and request.url.query:return JSONResponse({'error':'extra_query_rejected'},status_code=422)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        return response
    @app.get('/api/health')
    def health():return {'ready':True}
    @app.get('/api/state')
    def state():return controller.state()
    @app.get('/api/control-state')
    def direct_state():
        try:return controller.direct_state()
        except (httpx.HTTPError,OSError,ValueError,KeyError):
            return JSONResponse({'error':'direct_state_unavailable'},status_code=503)
    @app.get('/api/targets/{target_key}/snapshot')
    def target_snapshot(target_key:str):
        try:return controller.target_snapshot(target_key)
        except ValueError:return JSONResponse({'error':'target_unavailable'},status_code=422)
        except (httpx.HTTPError,OSError,KeyError):return JSONResponse({'error':'target_snapshot_unavailable'},status_code=503)
    @app.get('/api/targets/{target_key}/control-state')
    def target_control(target_key:str):
        try:return controller.direct_state(target_key)
        except ValueError:return JSONResponse({'error':'target_unavailable'},status_code=422)
        except (httpx.HTTPError,OSError,KeyError):return JSONResponse({'error':'direct_state_unavailable'},status_code=503)
    @app.put('/api/switch')
    def switch(body:Switch):
        try:return controller.set_desired(body.on,body.scenario,body.expected_revision,body.expected_generation,
            body.lease_seconds,body.validation_id,body.expected_validation_id)
        except RevisionConflict:return JSONResponse({'error':'revision_conflict'},status_code=409)
        except ValueError:return JSONResponse({'error':'scenario_unavailable'},status_code=422)
    web=Path(__file__).parent/'web-v2'
    @app.get('/')
    def index():return FileResponse(web/'index.html')
    @app.get('/app.js')
    def js():return FileResponse(web/'app.local-view.js',media_type='text/javascript')
    @app.get('/styles.css')
    def css():return FileResponse(web/'styles.local-view.css',media_type='text/css')
    return app
