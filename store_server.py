#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OC Theme Studio · 主题商店后端（可自托管 / 可部署到任意 Python 托管平台）

为什么需要服务器：
    主题商店要支持「多用户上传 + 其他用户下载」，属于共享后端场景，
    纯本地 App 无法跨用户共享数据。

两种使用方式：
    1) macOS 原生 App：在「主题商店」窗口地址栏填入本服务地址即可。
    2) 浏览器：直接打开本服务首页，任何平台的用户都能浏览 / 上传 / 下载主题。

本地运行：
    pip install -r requirements.txt
    python store_server.py                 # http://0.0.0.0:8000
    STORE_PORT=9000 python store_server.py

部署（平台会注入 PORT 环境变量，自动监听）：
    uvicorn store_server:app --host 0.0.0.0 --port $PORT
    # 或 gunicorn -k uvicorn.workers.UvicornWorker store_server:app --bind 0.0.0.0:$PORT

环境变量：
    PORT / STORE_PORT   监听端口（PORT 优先，供托管平台注入）
    STORE_HOST          监听地址，默认 0.0.0.0
    STORE_DATA          数据目录，默认 ./store_data
    STORE_SEED_DIR      示例主题 zip 目录，默认 ./samples
    STORE_TITLE         网页商店标题
    STORE_MAX_MB        单个主题包大小上限（MB），默认 50
    STORE_ICP_BEIAN     首页底部展示的 ICP 备案号（空则不展示）
    STORE_UPLOAD_TOKEN  可选上传口令；设置后 POST /api/themes 需带 X-Upload-Token
    STORE_ADMIN_TOKEN   删除口令；未设置时 DELETE 接口直接禁用

接口（App 与网页共用）：
    GET  /                       网页商店首页
    GET  /api/health             健康检查 JSON
    GET  /api/themes             主题列表
    GET  /api/themes/{id}        主题详情
    GET  /api/themes/{id}/download  下载主题包 zip
    POST /api/themes             上传主题（multipart）
    GET  /files/{id}/{path}      预览图等静态文件

数据存储：纯文件系统，无数据库依赖。每个主题一个目录：
    store_data/themes/<id>/meta.json + package.zip + previews/
注意：部分免费托管平台使用临时磁盘，重启会清空上传内容。
      生产使用请挂载持久磁盘（见 README / render.yaml / docker-compose.yml）。
"""

import os
import re
import json
import uuid
import shutil
import zipfile
import mimetypes
from html import escape as html_escape
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STORE_DATA", ROOT / "store_data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
THEMES_DIR = DATA / "themes"
THEMES_DIR.mkdir(parents=True, exist_ok=True)
HOST = os.environ.get("STORE_HOST", "0.0.0.0")
# 托管平台通常注入 PORT；本地可用 STORE_PORT 覆盖；最后兜底 8000
PORT = int(os.environ.get("PORT") or os.environ.get("STORE_PORT") or "8000")
SEED_DIR = Path(os.environ.get("STORE_SEED_DIR", ROOT / "samples"))
TITLE = os.environ.get("STORE_TITLE", "OC Theme Studio 主题商店")
MAX_MB = int(os.environ.get("STORE_MAX_MB", "50"))
MAX_BYTES = MAX_MB * 1024 * 1024
WEB_DIR = ROOT / "web"

# ICP 备案号：依法需在网站主页显著位置标明并链接到工信部备案系统。
# 可用 STORE_ICP_BEIAN 覆盖；设为空字符串则不展示。
ICP_BEIAN = os.environ.get("STORE_ICP_BEIAN", "闽ICP备2024073090号-1").strip()
# 上传口令为「可选」设计：未设置时完全开放（兼容现有 Mac App 上传），
# 设置后网页端 / App 端都需带 X-Upload-Token 头，启用前请同步改 App。
UPLOAD_TOKEN = os.environ.get("STORE_UPLOAD_TOKEN", "")
ADMIN_TOKEN = os.environ.get("STORE_ADMIN_TOKEN", "")

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{2,48}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]")

app = FastAPI(title=TITLE, version="1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def theme_dir(tid: str) -> Path:
    return THEMES_DIR / tid


def meta_path(tid: str) -> Path:
    return theme_dir(tid) / "meta.json"


def read_meta(tid: str) -> Optional[dict]:
    p = meta_path(tid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_meta() -> list:
    out = []
    for d in sorted(THEMES_DIR.iterdir(), reverse=True):
        mp = d / "meta.json"
        if d.is_dir() and mp.exists():
            try:
                out.append(json.loads(mp.read_text(encoding="utf-8")))
            except Exception:
                continue
    return out


def to_public(meta: dict) -> dict:
    """内部元数据 -> 客户端 StoreTheme 需要的字段。"""
    return {
        "id": meta.get("id", ""),
        "name": meta.get("name", ""),
        "author": meta.get("author", ""),
        "description": meta.get("description", ""),
        "version": meta.get("version", ""),
        "tags": meta.get("tags", []),
        "thumbnail": meta.get("thumbnail", ""),
        "download": f"/api/themes/{meta.get('id','')}/download",
        "previewCount": meta.get("previewCount", 0),
        "sizeText": meta.get("sizeText", ""),
        "updated": meta.get("updated", ""),
        "downloads": meta.get("downloads", 0),
    }


def size_text(n: int) -> str:
    return f"{n/1024:.0f} KB" if n < 1024 * 1024 else f"{n/1024/1024:.1f} MB"


def safe_filename(name: str) -> str:
    return SAFE_NAME_RE.sub("_", Path(name or "theme").name)[:80]


def extract_preview_from_zip(zf: zipfile.ZipFile, dest: Path, tid: str) -> Optional[str]:
    """取压缩包里第一张背景/图片作为预览图。"""
    names = [n for n in zf.namelist()
             if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
             and "/__MACOSX/" not in n and not n.startswith("__MACOSX")
             and not n.startswith(".")]
    bg = [n for n in names if "background" in n.lower()] or names
    if not bg:
        return None
    bg.sort(key=len)
    name = bg[0]
    dest_dir = dest / "previews"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(name).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    out_name = "0" + ext
    try:
        with zf.open(name) as src, open(dest_dir / out_name, "wb") as out:
            shutil.copyfileobj(src, out)
    except Exception:
        return None
    return f"/files/{tid}/previews/{out_name}"


def add_theme_from_zip(path: Path, *, name: str, author: str,
                       description: str, version: str, tags: list,
                       previews: Optional[list] = None) -> dict:
    """从 zip 建立主题记录。previews 为 [(filename, bytes), ...]。"""
    tid = "t_" + uuid.uuid4().hex[:12]
    d = theme_dir(tid)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, d / "package.zip")

    preview_count = 0
    preview_dir = d / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    if previews:
        for i, (fname, data) in enumerate(previews):
            if not data:
                continue
            ext = Path(fname).suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            (preview_dir / f"{i}{ext}").write_bytes(data)
            preview_count += 1

    thumbnail = None
    first = next(iter(sorted(preview_dir.glob("0.*"))), None)
    if first:
        thumbnail = f"/files/{tid}/previews/{first.name}"
    if thumbnail is None:
        try:
            with zipfile.ZipFile(path) as zf:
                thumbnail = extract_preview_from_zip(zf, d, tid)
                if thumbnail:
                    preview_count = max(preview_count, 1)
        except Exception:
            thumbnail = None

    size = path.stat().st_size
    meta = {
        "id": tid, "name": name, "author": author, "description": description,
        "version": version, "tags": tags, "thumbnail": thumbnail or "",
        "previewCount": preview_count,
        "sizeText": size_text(size), "updated": now_iso(), "downloads": 0,
    }
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    return meta


def seed_if_needed():
    """首次启动时从 samples/*.zip 播种示例主题，让商店一开始就有内容。"""
    if not SEED_DIR.exists() or list_meta():
        return
    zips = sorted(SEED_DIR.glob("*.zip"))
    if not zips:
        return
    for z in zips:
        try:
            add_theme_from_zip(
                z, name=z.stem, author="OC Theme Studio",
                description=f"内置示例主题：{z.stem}。可直接下载并在 App 中应用。",
                version="1.0", tags=["示例", "内置"])
        except Exception as e:
            print(f"[seed] 跳过 {z.name}: {e}")
    print(f"[seed] 已从 {SEED_DIR} 导入 {len(zips)} 个示例主题")


@app.on_event("startup")
def on_startup():
    seed_if_needed()


# ---------------------------------------------------------------------------
# 网页商店
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    f = WEB_DIR / "index.html"
    if not f.exists():
        return HTMLResponse("<h1>OC Theme Studio 主题商店</h1><p>缺少 web/index.html</p>")
    beian = ""
    if ICP_BEIAN:
        beian = ('<a href="https://beian.miit.gov.cn/" target="_blank" '
                 'rel="noopener noreferrer">' + html_escape(ICP_BEIAN) + '</a>')
    # 占位符在 web/index.html 的页脚，未配置备案号时整行留空
    return HTMLResponse(f.read_text(encoding="utf-8").replace("<!--ICP_BEIAN-->", beian))


@app.get("/api/health")
def health():
    return {"ok": True, "service": TITLE, "themes": len(list_meta())}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/themes")
def get_themes():
    return [to_public(m) for m in list_meta()]


@app.get("/api/themes/{tid}")
def get_theme(tid: str):
    m = read_meta(tid) if ID_RE.match(tid) else None
    if m is None:
        raise HTTPException(status_code=404, detail="主题不存在")
    return to_public(m)


@app.get("/api/themes/{tid}/download")
def download_theme(tid: str):
    m = read_meta(tid) if ID_RE.match(tid) else None
    if m is None:
        raise HTTPException(status_code=404, detail="主题不存在")
    pkg = theme_dir(tid) / "package.zip"
    if not pkg.exists():
        raise HTTPException(status_code=404, detail="主题包文件缺失")
    m["downloads"] = m.get("downloads", 0) + 1
    meta_path(tid).write_text(json.dumps(m, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    return FileResponse(pkg, media_type="application/zip",
                        filename=safe_filename(m.get("name", "theme")) + ".zip")


@app.post("/api/themes")
async def upload_theme(
    request: Request,
    name: str = Form(...),
    author: str = Form(""),
    description: str = Form(""),
    version: str = Form("1.0"),
    tags: str = Form(""),
    package: UploadFile = File(...),
    previews: List[UploadFile] = File(None),
):
    # 可选上传口令：未配置 STORE_UPLOAD_TOKEN 时保持开放，兼容现有 App
    if UPLOAD_TOKEN and request.headers.get("X-Upload-Token") != UPLOAD_TOKEN:
        raise HTTPException(status_code=401, detail="上传口令不正确")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="主题名称不能为空")
    if len(name) > 80:
        name = name[:80]
    if not package.filename or not package.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="主题包必须是 .zip 文件")

    raw = await package.read()
    if not raw:
        raise HTTPException(status_code=400, detail="主题包为空")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"主题包超过 {MAX_MB} MB 上限")
    if not zipfile.is_zipfile(__import__("io").BytesIO(raw)):
        raise HTTPException(status_code=400, detail="不是有效的 zip 文件")

    # 临时落盘，交给统一的建主题逻辑（与播种共用）
    tmp = DATA / ("upload_" + uuid.uuid4().hex[:10] + ".zip")
    tmp.write_bytes(raw)

    pv = []
    if previews:
        for p in previews:
            if not p or not p.filename:
                continue
            b = await p.read()
            if b and len(b) <= 8 * 1024 * 1024:
                pv.append((p.filename, b))

    tag_list = [t.strip()[:24] for t in (tags or "").split(",") if t.strip()][:8]
    try:
        meta = add_theme_from_zip(
            tmp, name=name,
            author=(author or "匿名").strip()[:60],
            description=(description or "").strip()[:1000],
            version=(version or "1.0").strip()[:24],
            tags=tag_list, previews=pv)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return JSONResponse(to_public(meta), status_code=201)


@app.delete("/api/themes/{tid}")
def delete_theme(tid: str, request: Request):
    """删除接口：未配置 STORE_ADMIN_TOKEN 时直接禁用，避免任何人匿名删除主题。"""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="未配置 STORE_ADMIN_TOKEN，删除接口已禁用")
    if request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="无权删除")
    if not ID_RE.match(tid) or not theme_dir(tid).exists():
        raise HTTPException(status_code=404, detail="主题不存在")
    shutil.rmtree(theme_dir(tid))
    return {"ok": True}


@app.get("/files/{tid}/{rest:path}")
def serve_file(tid: str, rest: str):
    if not ID_RE.match(tid):
        raise HTTPException(status_code=400, detail="非法 id")
    rel = Path(rest)
    if ".." in rel.parts or rest.startswith("/"):
        raise HTTPException(status_code=400, detail="非法路径")
    target = (theme_dir(tid) / rel).resolve()
    if not str(target).startswith(str(theme_dir(tid).resolve())):
        raise HTTPException(status_code=400, detail="非法路径")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=mime)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"{TITLE} 启动： http://{HOST}:{PORT}")
    print(f"数据目录： {DATA}")
    uvicorn.run(app, host=HOST, port=PORT)
