"""Stateless public allowlist over one existing controller; never owns a workload."""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .scenario_catalog import SCENARIOS, public_catalog


class PublicSwitch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    on:StrictBool
    scenario:str=Field(min_length=1,max_length=64)
    expected_revision:StrictInt=Field(ge=0)
    expected_generation:str=Field(pattern=r'^[a-f0-9]{32}$')


def create_app(config=None,client=None):
    if config is None:
        config=json.loads(Path(os.environ['CURATED_SITE_CONFIG']).read_text(encoding='utf-8-sig'))
    upstream=config['controller_origin'].rstrip('/')
    parsed=urlsplit(upstream)
    if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('fixed_loopback_controller_required')
    pins=config['public_scenarios']
    known={s['key']:s for s in SCENARIOS}
    if not isinstance(pins,dict) or not pins or any(k not in known or known[k]['target_key']!=v for k,v in pins.items()):
        raise ValueError('registered_scene_target_required')
    origins={config['public_origin'],*config.get('allowed_origins',[])}
    http=client or httpx.Client(timeout=8,trust_env=False,follow_redirects=False)

    def approved():
        try:
            records=json.loads(Path(config['verification_path']).read_text(encoding='utf-8-sig'))
            if not isinstance(records,dict):return {}
            return {k:v for k,v in records.items() if k in pins and isinstance(v,dict)
                and v.get('status')=='platform_verified' and v.get('analysis_verified') is True
                and isinstance(v.get('verified_stages'),dict)
                and all(v['verified_stages'].get(s) is True for s in
                    ('reproduction','discovery','interpretation','display','cleanup','recovery'))}
        except (OSError,ValueError):return {}

    def fetch(method,path,body=None):
        r=http.request(method,upstream+path,headers={'Origin':config['public_origin']},
            **({'json':body} if body is not None else {}))
        if r.status_code==409:return JSONResponse({'error':'revision_conflict'},status_code=409)
        r.raise_for_status()
        result=r.json()
        if not isinstance(result,dict):raise ValueError('invalid_controller_response')
        return result

    def project(state,acceptance,allow_off=False):
        if state.get('desired_on') and state.get('scenario') not in pins:
            return JSONResponse({'error':'internal_work_in_progress'},status_code=503)
        cleanup_only=state.get('scenario') in pins and (state.get('desired_on') is True
            or state.get('actual_state') in ('on','stopping'))
        if not acceptance and not allow_off and not cleanup_only:
            return JSONResponse({'error':'no_verified_scene_available'},status_code=503)
        catalog_cfg={**config,'services':{known[k]['service']:{} for k in pins}}
        descriptions={r['key']:r for r in public_catalog(catalog_cfg)}
        rows=[]
        for row in state.get('scenarios',[]):
            key=row.get('key')
            if key in acceptance and row.get('available') is True and row.get('target_key')==pins[key]:
                rows.append({**row,**descriptions[key],'status':'analysis_verified','available':True})
        if not rows and (cleanup_only or allow_off):
            key=state.get('scenario')
            if key in pins:
                rows=[{**known[key],'status':'implemented_awaiting_verification','available':False,
                    'platform_clues':'场景当前未获开放许可，仅可关闭已运行负载。'}]
        if not rows:return JSONResponse({'error':'no_verified_scene_available'},status_code=503)
        if state.get('scenario') not in pins:
            state={**state,'scenario':next(iter(pins)),'platform':None,'platform_received_at':None}
        if (state.get('platform') or {}).get('target',{}).get('key')!=pins[state['scenario']]:
            state={**state,'platform':None,'platform_received_at':None}
        return {**state,'scenarios':rows}

    @asynccontextmanager
    async def lifespan(app):
        try:yield
        finally:
            if client is None:http.close()

    app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=sorted({urlsplit(o).hostname for o in origins}))

    @app.middleware('http')
    async def boundary(request:Request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') not in origins:
            return JSONResponse({'error':'origin_rejected'},status_code=403)
        if request.url.path.startswith('/api/') and request.url.query:
            return JSONResponse({'error':'extra_query_rejected'},status_code=422)
        try:response=await call_next(request)
        except (httpx.HTTPError,OSError,ValueError,KeyError,TypeError):
            response=JSONResponse({'error':'controller_unavailable'},status_code=503)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        return response

    @app.get('/api/health')
    def health():return {'ready':True,'mode':'stateless_curated_facade'}

    @app.get('/api/state')
    def state():return project(fetch('GET','/api/state'),approved())

    @app.get('/api/targets/{target_key}/{view}')
    def target(target_key:str,view:str):
        if view not in ('snapshot','control-state') or not any(pins[k]==target_key for k in approved()):
            return JSONResponse({'error':'target_not_public'},status_code=404)
        return fetch('GET','/api/targets/'+target_key+'/'+view)

    @app.put('/api/switch')
    def switch(body:PublicSwitch):
        if body.scenario not in pins or (body.on and body.scenario not in approved()):
            return JSONResponse({'error':'scene_not_public'},status_code=422)
        current=fetch('GET','/api/state')
        if isinstance(current,JSONResponse):return current
        if current.get('desired_on') and current.get('scenario')!=body.scenario:
            return JSONResponse({'error':'other_work_in_progress'},status_code=409)
        if body.on and not any(r.get('key')==body.scenario and r.get('available') is True
            and r.get('target_key')==pins[body.scenario] for r in current.get('scenarios',[])):
            return JSONResponse({'error':'scene_target_unavailable'},status_code=422)
        result=fetch('PUT','/api/switch',body.model_dump())
        return result if isinstance(result,JSONResponse) else project(result,approved(),allow_off=not body.on)

    web=Path(__file__).parent/'web-v2'
    @app.get('/')
    def index():return FileResponse(web/'index.html')
    @app.get('/app.js')
    def script():return FileResponse(web/'app.local-view.js',media_type='text/javascript')
    @app.get('/styles.css')
    def css():return FileResponse(web/'styles.local-view.css',media_type='text/css')
    return app
