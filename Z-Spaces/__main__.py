import asyncio
import json
import os

import httpx
import websockets
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import markdown

from . import space_manager

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

NODES = {
    "omega": {"ssh": "runner@10.1.1.140", "host": "10.1.1.140"},
    "nano": {"ssh": "runner@10.1.1.143", "host": "10.1.1.143"},
}


def load_spaces():
    spaces = []
    used_ports = set()
    for root, dirs, files in sorted(os.walk("spaces")):
        dirs.sort()
        if "config.json" in files:
            with open(os.path.join(root, "config.json")) as f:
                config = json.load(f)
            name = os.path.relpath(root, "spaces")
            node = config.get("node", "omega")
            port = 7000
            while port in used_ports:
                port += 1
            used_ports.add(port)
            spaces.append({
                "name": name,
                "path": root,
                "node": node,
                "port": port,
            })
    return spaces


SPACES = load_spaces()
SPACES_BY_NAME = {r["name"]: r for r in SPACES}


def get_space(author: str, repo: str):
    return SPACES_BY_NAME.get(f"{author}/{repo}")


def render_readme(space: dict) -> str:
    readme_path = os.path.join(space["path"], "README.md")
    if not os.path.exists(readme_path):
        return "<p>There is no README.md for this space.</p>"
    with open(readme_path, encoding="utf-8") as f:
        content = f.read()
    return markdown.markdown(content)


def with_status(space: dict) -> dict:
    node = NODES[space["node"]]
    status = space_manager.status(node["ssh"], space["name"])
    result = {**space, "status": status, "docs": render_readme(space)}
    if status not in ("running", "stopped"):
        result["logs"] = space_manager.logs(node["ssh"], space["name"])
    return result


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})

@app.get("/docs/{author}/{repo}")
def docs(request: Request, author: str, repo: str):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    return templates.TemplateResponse(
        request, "docs.html", {"space": with_status(space), "content": render_readme(space)}
    )

@app.get("/docs_raw/{author}/{repo}")
def docs_raw(request: Request, author: str, repo: str):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    readme_path = os.path.join(space["path"], "README.md")
    if os.path.exists(readme_path):
        with open(readme_path) as f:
            content = f.read()
        return HTMLResponse(content=content)


@app.get("/spaces")
def spaces(request: Request):
    return templates.TemplateResponse(
        request, "spaces.html", {"spaces": [with_status(r) for r in SPACES]}
    )


@app.get("/space/{author}/{repo}")
def space_page(request: Request, author: str, repo: str):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    return templates.TemplateResponse(request, "space.html", {"space": with_status(space)})


@app.post("/space/{author}/{repo}/start")
def space_start(author: str, repo: str):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    node = NODES[space["node"]]
    space_manager.deploy(node["ssh"], space["name"], space["path"], space["port"])
    return RedirectResponse(url=f"/space/{author}/{repo}", status_code=303)


@app.post("/space/{author}/{repo}/stop")
def space_stop(author: str, repo: str):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    node = NODES[space["node"]]
    space_manager.stop(node["ssh"], space["name"])
    return RedirectResponse(url=f"/space/{author}/{repo}", status_code=303)




HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}

RESPONSE_HEADERS_TO_DROP = HOP_BY_HOP_HEADERS | {"content-length", "content-encoding"}


@app.api_route("/space_proxy/{author}/{repo}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def space_proxy(request: Request, author: str, repo: str, path: str = ""):
    space = get_space(author, repo)
    if space is None:
        return Response(status_code=404, content="Unknown space")
    node = NODES[space["node"]]
    target_url = f"http://{node['host']}:{space['port']}/{path}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}

    headers["host"] = request.headers.get("host", target_url)
    body = await request.body()

    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            upstream = await client.request(
                request.method, target_url, params=request.query_params,
                headers=headers, content=body, timeout=60.0,
            )
    except httpx.ConnectError:
        return templates.TemplateResponse(
            request, "loading.html", {"space": with_status(space)}
        )

    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in RESPONSE_HEADERS_TO_DROP
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


@app.websocket("/space_proxy/{author}/{repo}/{path:path}")
async def space_proxy_ws(websocket: WebSocket, author: str, repo: str, path: str = ""):
    space = get_space(author, repo)
    if space is None:
        await websocket.close(code=1008)
        return
    node = NODES[space["node"]]
    target_url = f"ws://{node['host']}:{space['port']}/{path}"

    await websocket.accept()
    try:
        async with websockets.connect(target_url) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        message = await websocket.receive_text()
                        await upstream.send(message)
                except WebSocketDisconnect:
                    await upstream.close()

            async def upstream_to_client():
                async for message in upstream:
                    await websocket.send_text(message)

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
        pass
    except OSError:
        await websocket.close(code=1011)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
