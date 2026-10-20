"""Real AstrBot dashboard routes and authentication with an isolated test runtime."""

import json
import secrets
import time
from pathlib import Path
from types import MethodType, SimpleNamespace

import jwt
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata
from astrbot.dashboard.api import plugins as routes
from astrbot.dashboard.plugin_page_auth import PluginPageAuth
from astrbot.dashboard.responses import ApiError
from astrbot.dashboard.server import AstrBotDashboard
from astrbot.dashboard.services.plugin_page_service import PluginPageService
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


def make_app(plugin, include_host=False):
    context = SimpleNamespace(
        registered_web_apis=[], platform_manager=SimpleNamespace(get_insts=lambda: [])
    )
    context.register_web_api = MethodType(Context.register_web_api, context)
    plugin.context = context
    plugin.panel.register()
    root = Path(__file__).resolve().parents[1]
    metadata = StarMetadata(
        name="astrbot_plugin_human_style",
        display_name="好好说话 · 表达学习",
        root_dir_name=root.name,
        activated=True,
    )
    page_manager = SimpleNamespace(
        plugin_store_path=str(root.parent),
        reserved_plugin_path=str(root.parent),
        context=SimpleNamespace(get_all_stars=lambda: [metadata]),
    )
    secret = secrets.token_urlsafe(40)
    page_service = PluginPageService(page_manager, config={"dashboard": {"jwt_secret": secret}})
    app = FastAPI()
    app.state.jwt_secret = secret
    app.state.core_lifecycle = SimpleNamespace(star_context=context)
    app.dependency_overrides[routes.get_page_service] = lambda: page_service
    app.include_router(routes.router, prefix="/api/v1")
    app.include_router(routes.legacy_router)

    @app.middleware("http")
    async def asset_auth(current_request, call_next):
        # The real dashboard's outer middleware authenticates only the bridge SDK
        # with a scoped asset token; retain its exact token validation here.
        if PluginPageAuth.is_protected_path(current_request.url.path):
            payload, _ = AstrBotDashboard._validate_dashboard_token(
                SimpleNamespace(_jwt_secret=secret),
                current_request.query_params.get("asset_token", ""),
                current_request.url.path,
            )
            if payload is not None:
                current_request.state.dashboard_g = SimpleNamespace(username=payload["username"])
        return await call_next(current_request)

    @app.exception_handler(ApiError)
    async def api_error(_request, error):
        return JSONResponse(
            {"status": "error", "message": error.message}, status_code=error.status_code
        )

    def headers(username="test-admin"):
        token = jwt.encode(
            {"username": username, "exp": int(time.time()) + 3600}, secret, algorithm="HS256"
        )
        return {"Authorization": f"Bearer {token}"}

    if include_host:
        # Emulate only the dashboard's iframe transport. All requests hit real API routes.
        # This host is test-only; no plugin code depends on it.
        @app.get("/test-host", response_class=HTMLResponse)
        async def host():
            token = headers()["Authorization"]
            return """<!doctype html><html><head><meta charset='utf-8'><title>Panel integration host</title>
<style>body{margin:0}iframe{width:100%;height:100vh;border:0}</style></head><body><iframe title='学习控制台' sandbox='allow-scripts allow-forms allow-downloads'></iframe>
<script>
const auth=AUTH_VALUE, base='/api/v1/plugins/extensions/astrbot_plugin_human_style/';
const frame=document.querySelector('iframe');
async function getEntry(){const r=await fetch('/api/v1/plugins/astrbot_plugin_human_style/views/learning',{headers:{Authorization:auth}});const j=await r.json();frame.src=j.data.content_path;}
window.addEventListener('message',async e=>{
 if(e.source!==frame.contentWindow || ![location.origin,'null'].includes(e.origin) || e.data?.channel!=='astrbot-plugin-page')return;
 const m=e.data; const send=data=>frame.contentWindow.postMessage({channel:'astrbot-plugin-page',...data},e.origin==='null'?'*':location.origin);
 if(m.kind==='ready'){send({kind:'context',context:{pluginName:'astrbot_plugin_human_style',pageName:'learning',locale:'zh-CN',isDark:false}});return;}
 if(m.kind!=='request')return;
 try{
  let url=base+m.endpoint, options={headers:{Authorization:auth}};
  if(m.action==='api:get')url+='?'+new URLSearchParams(m.params||{});
  else if(m.action==='api:post'){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(m.body||{});}
  else if(m.action==='files:upload'){options.method='POST';const form=new FormData();form.append('file',new Blob([m.fileBuffer],{type:m.fileType}),m.fileName);options.body=form;}
  else if(m.action==='files:download'){const r=await fetch(url,options);if(!r.ok)throw new Error('下载失败');const b=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=m.filename||'export.json';a.click();URL.revokeObjectURL(a.href);send({kind:'response',requestId:m.requestId,ok:true,data:{}});return;}
  else throw new Error('Unsupported bridge action');
  const r=await fetch(url,options), j=await r.json();if(!r.ok||j.status==='error')throw new Error(j.message||'请求失败');
  send({kind:'response',requestId:m.requestId,ok:true,data:j.data??j});
 }catch(error){send({kind:'response',requestId:m.requestId,ok:false,error:error.message});}
});
getEntry();
</script></body></html>""".replace("AUTH_VALUE", json.dumps(token))

    return app, headers, context
