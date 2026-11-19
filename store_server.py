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
    STORE_ICP_ICON      备案号前的中性装饰小图标，设 0 关闭（默认开启）
    STORE_POLICE_BEIAN  公安联网备案号（空则不展示）
    STORE_POLICE_ICON   公安备案警徽图标地址，默认 /static/ghs.png（平台官方素材）
    STORE_UPLOAD_TOKEN  可选上传口令；设置后 POST /api/themes 需带 X-Upload-Token
    STORE_ADMIN_USER    管理员账号（默认 admin）
    STORE_ADMIN_PASSWORD 管理员密码；支持明文或 "sha256$<hex>" 哈希（推荐哈希）。
                        未设置时登录与删除接口一律禁用
    STORE_ADMIN_TOKEN   旧的静态删除口令（兼容保留）；与登录会话任一通过即可删除
    STORE_ADMIN_SESSION_HOURS  登录会话有效期（小时，默认 8）
    STORE_ADMIN_MAX_FAILS      登录失败次数上限（默认 5）
    STORE_ADMIN_LOCK_SECONDS   超限后锁定时长（秒，默认 60）

接口（App 与网页共用）：
    GET  /                       网页商店首页
    GET  /api/health             健康检查 JSON
    GET  /api/categories         主题分类列表与数量
    GET  /api/themes             主题列表（可用 ?category= 过滤）
    GET  /api/themes/{id}        主题详情
    GET  /api/themes/{id}/download  下载主题包 zip
    POST /api/themes             上传主题（multipart，含 category）
    DELETE /api/themes/{id}      删除主题（需管理员登录会话或静态口令）
    POST /api/themes/{id}/cover           自定义封面（管理员上传图片）
    POST /api/themes/{id}/cover/generate  从主题包自动生成封面（管理员）
    POST /api/themes/{id}/previews        追加预览图（管理员）
    DELETE /api/themes/{id}/previews/{f}  删除预览图（管理员）
    GET  /files/{id}/{path}      预览图等静态文件
    GET  /api/admin/status       管理员登录是否启用
    POST /api/admin/login        账号密码登录，签发会话 token
    POST /api/admin/logout       注销会话
    GET  /api/admin/session      校验当前会话是否有效
    POST /api/users/register     注册普通账号（用户名 + 密码，可选昵称），成功即签发会话
    POST /api/users/login        普通账号登录
    POST /api/users/logout       注销普通账号会话
    GET  /api/users/session      校验普通账号会话
    GET  /api/admin/check-user   查询当前登录用户是否为被授予的管理员
    GET  /api/admin/users        管理员名单（仅超级管理员）
    POST /api/admin/users/grant  授予某注册用户管理员权限（仅超级管理员）
    POST /api/admin/users/revoke 撤销管理员权限（仅超级管理员）
    GET  /api/themes/{id}/comments      评论列表（新在前）
    POST /api/themes/{id}/comments      发表评论（需登录，X-User-Token）
    DELETE /api/themes/{id}/comments/{cid}  删除评论（本人或管理员）

数据存储：纯文件系统，无数据库依赖。每个主题一个目录：
    store_data/themes/<id>/meta.json + package.zip + previews/
注意：部分免费托管平台使用临时磁盘，重启会清空上传内容。
      生产使用请挂载持久磁盘（见 README / render.yaml / docker-compose.yml）。
"""

import os
import re
import json
import uuid
import time
import hmac
import hashlib
import secrets
import shutil
import struct
import zipfile
import mimetypes
import threading
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

# 工信部并未下发「ICP 备案图标」，官方只要求标明备案编号并链接备案系统。
# 这里自绘一个中性的「备案文件」小标记，纯装饰、不冒充任何官方徽标；
# 设 STORE_ICP_ICON=0 可关闭。若需换成自己的图片，替换这段 SVG 即可。
ICP_ICON = os.environ.get("STORE_ICP_ICON", "1") != "0"
ICP_ICON_SVG = (
    '<svg class="ba-icon" viewBox="0 0 16 16" width="14" height="14" '
    'aria-hidden="true" focusable="false">'
    '<rect x="1.5" y="1" width="13" height="14" rx="2.5" fill="#eaf0ff" '
    'stroke="#2f6bff" stroke-width="1"/>'
    '<path d="M4.3 5.4h7.4M4.3 8h7.4M4.3 10.6h4.8" stroke="#2f6bff" '
    'stroke-width="1.1" stroke-linecap="round"/></svg>'
)

# 公安联网备案：图标为用户从「全国互联网安全管理服务平台 → 已备案网站 →
# 公安机关备案号详情」下载的官方素材（36x40 PNG），本地托管在 web/ghs.png，
# 不自行绘制警徽。查询链接沿用平台给出的代码片段格式。
POLICE_BEIAN = os.environ.get("STORE_POLICE_BEIAN", "闽公网安备35092202000207号").strip()
_m = re.search(r"(\d{8,})", POLICE_BEIAN)
POLICE_CODE = _m.group(1) if _m else ""
POLICE_ICON = os.environ.get("STORE_POLICE_ICON", "/static/ghs.png").strip()

# 上传口令为「可选」设计：未设置时完全开放（兼容现有 Mac App 上传），
# 设置后网页端 / App 端都需带 X-Upload-Token 头，启用前请同步改 App。
UPLOAD_TOKEN = os.environ.get("STORE_UPLOAD_TOKEN", "")

# ---------------------------------------------------------------------------
# 管理员登录
# ---------------------------------------------------------------------------
# 账号密码只从服务器环境变量读取，绝不写进代码仓库 / 前端。
# 密码建议用哈希形式：printf '%s' '你的密码' | shasum -a 256 得到 hex，
# 设 STORE_ADMIN_PASSWORD="sha256$<hex>"；也兼容明文（便于本地测试）。
ADMIN_USER = os.environ.get("STORE_ADMIN_USER", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("STORE_ADMIN_PASSWORD", "")
ADMIN_SESSION_HOURS = max(1, int(os.environ.get("STORE_ADMIN_SESSION_HOURS", "8")))
ADMIN_MAX_FAILS = max(1, int(os.environ.get("STORE_ADMIN_MAX_FAILS", "5")))
ADMIN_LOCK_SECONDS = max(1, int(os.environ.get("STORE_ADMIN_LOCK_SECONDS", "60")))
# 旧的静态删除口令，保留向后兼容（脚本 / 旧版客户端可不登录直接删）。
ADMIN_TOKEN = os.environ.get("STORE_ADMIN_TOKEN", "")

# 会话与失败限速都是进程内状态：重启即失效，符合这个无数据库后端的定位。
_SESSIONS: dict = {}          # token -> {"exp": float, "user": str}
_FAILS: dict = {}             # client ip -> {"count": int, "until": float}
_ADMIN_LOCK = threading.Lock()

ADMIN_ENABLED = bool(ADMIN_PASSWORD)

ID_RE = re.compile(r"^[A-Za-z0-9_\-]{2,48}$")
SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]")

# 主题分类：完整主题为默认值，兼容早期没有 category 字段的 meta.json。
# 客户端可传 slug（theme/icons/background/fonts/efi）或中文名称。
# efi 包由 Mac App 从已挂载分区生成，内含 oc-efi-manifest.json 与脱敏配置，
# 服务端只按普通 zip 校验，具体脱敏与安装逻辑在 App 端。
CATEGORIES = [
    ("theme", "完整主题"),
    ("icons", "图标区"),
    ("background", "背景区"),
    ("fonts", "字体区"),
    ("efi", "EFI 配置包"),
]
CATEGORY_LABELS = dict(CATEGORIES)
DEFAULT_CATEGORY = "theme"


def normalize_category(value) -> str:
    """把上传 / 旧数据里的分类值统一成 slug；旧主题默认归为完整主题。"""
    v = str(value or "").strip().lower()
    if v in CATEGORY_LABELS:
        return v
    for cid, label in CATEGORIES:
        if v == label:
            return cid
    return DEFAULT_CATEGORY

app = FastAPI(title=TITLE, version="1.4")
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
    cat = normalize_category(meta.get("category"))
    tid = meta.get("id", "")
    # 预览图 URL 列表（详情页轮播用）：自定义封面排最前，其余按文件名排序
    previews = []
    try:
        pdir = theme_dir(tid) / "previews"
        if pdir.is_dir():
            files = [f for f in sorted(pdir.iterdir())
                     if f.is_file()
                     and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp")]
            cover_name = str(meta.get("cover") or "")
            if cover_name:
                files = [f for f in files if f.name == cover_name] + \
                        [f for f in files if f.name != cover_name]
            previews = [f"/files/{tid}/previews/{f.name}" for f in files]
    except Exception:
        pass
    thumbnail = meta.get("thumbnail", "")
    if meta.get("cover"):
        cover = f"/files/{tid}/previews/{meta['cover']}"
        if cover in previews:
            thumbnail = cover
    return {
        "id": tid,
        "name": meta.get("name", ""),
        "author": meta.get("author", ""),
        "description": meta.get("description", ""),
        "version": meta.get("version", ""),
        "tags": meta.get("tags", []),
        "category": cat,
        "categoryLabel": CATEGORY_LABELS[cat],
        "thumbnail": thumbnail,
        "previews": previews,
        "cover": meta.get("cover", ""),
        "download": f"/api/themes/{tid}/download",
        "previewCount": len(previews) or meta.get("previewCount", 0),
        "sizeText": meta.get("sizeText", ""),
        "updated": meta.get("updated", ""),
        "downloads": meta.get("downloads", 0),
        "commentCount": len(read_comments(tid)),
    }


def size_text(n: int) -> str:
    return f"{n/1024:.0f} KB" if n < 1024 * 1024 else f"{n/1024/1024:.1f} MB"


def safe_filename(name: str) -> str:
    return SAFE_NAME_RE.sub("_", Path(name or "theme").name)[:80]


def _icns_png_bytes(data: bytes) -> Optional[bytes]:
    """从 .icns 容器里取出最大的内嵌 PNG 块（ic07/ic08/ic09/ic10/ic13/ic14）。"""
    if len(data) < 16 or data[:4] != b"icns":
        return None
    best = None
    i = 8
    total = min(len(data), struct.unpack(">I", data[4:8])[0] if len(data) >= 8 else len(data))
    while i + 8 <= total:
        typ = data[i:i+4]
        n = struct.unpack(">I", data[i+4:i+8])[0]
        if n < 8 or i + n > total:
            break
        body = data[i+8:i+n]
        if body[:8] == b"\x89PNG\r\n\x1a\n" and typ in (
                b"ic07", b"ic08", b"ic09", b"ic10", b"ic13", b"ic14"):
            if best is None or len(body) > len(best):
                best = body
        i += n
    return best


def build_icon_preview(zf: zipfile.ZipFile, dest: Path, tid: str,
                       out_stem: str = "0") -> Optional[str]:
    """纯图标包（无 PNG/JPG 预览）兜底：从若干 .icns 提取 PNG 拼成一张预览网格。

    out_stem 默认 "0"（上传时的首张预览）；生成封面时传 "cover"。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    icns = [n for n in zf.namelist()
            if n.lower().endswith(".icns") and "__MACOSX" not in n
            and not n.startswith(".")]
    if not icns:
        return None
    # 优先挑有辨识度的立体/彩色系统图标；箭头、圆点、光标、焦点框等纯 UI 小图标排最后
    prefer = ["Apple", "HardDrive", "Windows", "AppleTM", "ExtHardDrive",
              "AppleRecv", "shell", "tool", "ShutDown", "Lock", "Restart", "SetDefault",
              "BtnFocus", "Cursor", "Dot", "Enter", "Left", "Right", "Selected",
              "Selector", "Password"]
    ui_small = {"btnfocus", "cursor", "dot", "enter", "left", "right", "selected",
                "selector", "password"}
    ranked = []
    for key in prefer:
        for n in icns:
            if Path(n).stem.lower() == key.lower() and n not in ranked:
                ranked.append(n)
    # 兜底：未在偏好表里的，主图标（非 UI 小图标）优先
    for n in icns:
        if n not in ranked and Path(n).stem.lower() not in ui_small:
            ranked.append(n)
    for n in icns:
        if n not in ranked:
            ranked.append(n)
    picks = ranked[:8]

    W, H = 1280, 800
    sheet = Image.new("RGB", (W, H), (13, 20, 34))
    d = ImageDraw.Draw(sheet)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(16-6*t), int(24-9*t), int(40-14*t)))
    thumb_tiles = []
    for n in picks:
        try:
            raw = _icns_png_bytes(zf.read(n))
            if not raw:
                continue
            import io
            im = Image.open(io.BytesIO(raw)).convert("RGBA")
            im.thumbnail((128, 128), Image.LANCZOS)
            thumb_tiles.append(im)
        except Exception:
            continue
    if not thumb_tiles:
        return None
    cols, cell = 4, 200
    gx = (W - cols * cell) // 2
    gy = 250
    for i, im in enumerate(thumb_tiles[:8]):
        r, c = divmod(i, cols)
        cx = gx + c * cell + cell // 2
        cy = gy + r * cell + cell // 2
        d.rounded_rectangle([cx-84, cy-84, cx+84, cy+84], radius=18,
                            fill=(30, 42, 60), outline=(55, 74, 102), width=2)
        sheet.paste(im, (cx - im.width//2, cy - im.height//2), im)
    dest_dir = dest / "previews"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{out_stem}.png"
    sheet.save(out, "PNG")
    return f"/files/{tid}/previews/{out.name}"


def extract_preview_from_zip(zf: zipfile.ZipFile, dest: Path, tid: str,
                             out_stem: str = "0") -> Optional[str]:
    """取压缩包里第一张背景/图片作为预览图。out_stem 同上。"""
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
    out_name = out_stem + ext
    try:
        with zf.open(name) as src, open(dest_dir / out_name, "wb") as out:
            shutil.copyfileobj(src, out)
    except Exception:
        return None
    return f"/files/{tid}/previews/{out_name}"


def add_theme_from_zip(path: Path, *, name: str, author: str,
                       description: str, version: str, tags: list,
                       category: str = DEFAULT_CATEGORY,
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
                if not thumbnail:
                    # 纯图标包：从 .icns 拼一张预览
                    thumbnail = build_icon_preview(zf, d, tid)
                if thumbnail:
                    preview_count = max(preview_count, 1)
        except Exception:
            thumbnail = None

    size = path.stat().st_size
    meta = {
        "id": tid, "name": name, "author": author, "description": description,
        "version": version, "tags": tags,
        "category": normalize_category(category), "thumbnail": thumbnail or "",
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
                version="1.0", tags=["示例", "内置"],
                category=DEFAULT_CATEGORY)
        except Exception as e:
            print(f"[seed] 跳过 {z.name}: {e}")
    print(f"[seed] 已从 {SEED_DIR} 导入 {len(zips)} 个示例主题")


@app.on_event("startup")
def on_startup():
    seed_if_needed()


# ---------------------------------------------------------------------------
# 管理员登录：鉴权工具
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    """反代后取真实来源 IP（nginx 已设 X-Forwarded-For）。"""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def password_ok(given: str) -> bool:
    """常量时间比较密码。支持 sha256$<hex> 哈希或明文。"""
    if not ADMIN_ENABLED:
        return False
    given = given or ""
    if ADMIN_PASSWORD.startswith("sha256$"):
        want = ADMIN_PASSWORD[len("sha256$"):].strip().lower()
        got = hashlib.sha256(given.encode("utf-8")).hexdigest()
        return hmac.compare_digest(got, want)
    return hmac.compare_digest(given, ADMIN_PASSWORD)


def new_session(user: str):
    tok = secrets.token_urlsafe(32)
    exp = time.time() + ADMIN_SESSION_HOURS * 3600
    with _ADMIN_LOCK:
        # 顺手清理过期会话，避免长期运行堆积
        now = time.time()
        for k in [k for k, v in _SESSIONS.items() if v["exp"] <= now]:
            _SESSIONS.pop(k, None)
        _SESSIONS[tok] = {"exp": exp, "user": user}
    return tok, exp


def session_user(tok: str) -> Optional[str]:
    if not tok:
        return None
    with _ADMIN_LOCK:
        rec = _SESSIONS.get(tok)
        if not rec:
            return None
        if rec["exp"] <= time.time():
            _SESSIONS.pop(tok, None)
            return None
        return rec["user"]


def drop_session(tok: str):
    with _ADMIN_LOCK:
        _SESSIONS.pop(tok, None)


def admin_token_from(request: Request) -> str:
    """会话 token 来源：X-Admin-Token 或 Authorization: Bearer。"""
    h = request.headers.get("x-admin-token", "").strip()
    if h:
        return h
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def user_is_admin(key: str) -> bool:
    """被授予管理员权限的注册用户；每次动态查表，授权/撤销立即生效。"""
    with _USERS_LOCK:
        rec = load_users().get(key) or {}
    return bool(rec.get("admin"))


def admin_authorized(request: Request) -> bool:
    """管理员判定：超级管理员会话 / 旧静态口令 / 被授予管理员权限的普通用户会话。"""
    tok = admin_token_from(request)
    if tok:
        if session_user(tok) is not None:
            return True
        if ADMIN_TOKEN and hmac.compare_digest(tok, ADMIN_TOKEN):
            return True
    urec = user_session(user_token_from(request))
    return bool(urec and user_is_admin(urec["key"]))


def super_authorized(request: Request) -> bool:
    """超级管理员判定：环境变量配置的账号会话或旧静态口令。
    授予/撤销管理员只认超级管理员，被授予者不能继续授权（防提权）。"""
    tok = admin_token_from(request)
    if not tok:
        return False
    if session_user(tok) == ADMIN_USER:
        return True
    return bool(ADMIN_TOKEN) and hmac.compare_digest(tok, ADMIN_TOKEN)


def locked_seconds(ip: str) -> int:
    """返回剩余锁定秒数；同时清理已过期锁定与过期的失败计数窗口。"""
    with _ADMIN_LOCK:
        st = _FAILS.get(ip)
        if not st:
            return 0
        now = time.time()
        left = st["until"] - now
        if left > 0:
            return int(left) + 1
        # 只在「曾进入锁定且已解锁」或「失败计数窗口过期」时清空；
        # until==0 表示仍在累计失败次数，绝不能在这里清掉。
        if st["until"] > 0 or now - st.get("last", 0) > ADMIN_LOCK_SECONDS:
            _FAILS.pop(ip, None)
        return 0


def note_fail(ip: str) -> int:
    """记一次登录失败；达到上限返回锁定秒数，否则返回 0。"""
    with _ADMIN_LOCK:
        st = _FAILS.get(ip, {"count": 0, "until": 0.0, "last": 0.0})
        st["count"] += 1
        st["last"] = time.time()
        if st["count"] >= ADMIN_MAX_FAILS:
            st["until"] = time.time() + ADMIN_LOCK_SECONDS
            st["count"] = 0
            _FAILS[ip] = st
            return ADMIN_LOCK_SECONDS
        _FAILS[ip] = st
        return 0


def clear_fails(ip: str):
    with _ADMIN_LOCK:
        _FAILS.pop(ip, None)


# ---------------------------------------------------------------------------
# 普通用户账号（注册 / 登录 / 评论）
#
# 与管理员体系完全分离：独立的用户表（users.json，用户名小写为键）、
# 独立的会话表与失败限速。密码只存 sha256$salt$hash，明文不落盘。
# 评论按主题存 comments.json，删除权限为「本人或管理员」。
# ---------------------------------------------------------------------------
USERS_FILE = DATA / "users.json"
_USERS_LOCK = threading.Lock()
_USER_SESSIONS: dict = {}       # token -> {"exp": float, "user": str, "key": str}
USER_SESSION_HOURS = max(1, int(os.environ.get("STORE_USER_SESSION_HOURS", "72")))
USER_MAX_FAILS = max(1, int(os.environ.get("STORE_USER_MAX_FAILS", "8")))
USER_LOCK_SECONDS = max(1, int(os.environ.get("STORE_USER_LOCK_SECONDS", "60")))
# 用户名：中英文数字下划线，2-20 字；密码 6-64 位
USER_NAME_RE = re.compile(r"^[A-Za-z0-9_一-龥]{2,20}$")
USER_FAILS: dict = {}
REG_PER_HOUR = max(1, int(os.environ.get("STORE_REG_PER_HOUR", "5")))
_REG_LOG: dict = {}             # ip -> [timestamp, ...]


def load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        d = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_users(users: dict):
    tmp = DATA / ("users_" + uuid.uuid4().hex[:8] + ".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_FILE)


def hash_password(pwd: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.sha256((salt + pwd).encode("utf-8")).hexdigest()
    return f"sha256${salt}${h}"


def verify_password(pwd: str, stored: str) -> bool:
    parts = (stored or "").split("$")
    if len(parts) != 3 or parts[0] != "sha256":
        return False
    h = hashlib.sha256((parts[1] + (pwd or "")).encode("utf-8")).hexdigest()
    return hmac.compare_digest(h, parts[2])


def new_user_session(display: str, key: str, login: str):
    tok = secrets.token_urlsafe(32)
    exp = time.time() + USER_SESSION_HOURS * 3600
    with _USERS_LOCK:
        now = time.time()
        for k in [k for k, v in _USER_SESSIONS.items() if v["exp"] <= now]:
            _USER_SESSIONS.pop(k, None)
        _USER_SESSIONS[tok] = {"exp": exp, "user": display, "key": key, "login": login}
    return tok, exp


def user_session(tok: str) -> Optional[dict]:
    if not tok:
        return None
    with _USERS_LOCK:
        rec = _USER_SESSIONS.get(tok)
        if not rec:
            return None
        if rec["exp"] <= time.time():
            _USER_SESSIONS.pop(tok, None)
            return None
        return rec


def drop_user_session(tok: str):
    with _USERS_LOCK:
        _USER_SESSIONS.pop(tok, None)


def user_token_from(request: Request) -> str:
    return request.headers.get("x-user-token", "").strip()


def user_locked_seconds(ip: str) -> int:
    with _USERS_LOCK:
        st = USER_FAILS.get(ip)
        if not st:
            return 0
        now = time.time()
        left = st["until"] - now
        if left > 0:
            return int(left) + 1
        if st["until"] > 0 or now - st.get("last", 0) > USER_LOCK_SECONDS:
            USER_FAILS.pop(ip, None)
        return 0


def user_note_fail(ip: str) -> int:
    with _USERS_LOCK:
        st = USER_FAILS.get(ip, {"count": 0, "until": 0.0, "last": 0.0})
        st["count"] += 1
        st["last"] = time.time()
        if st["count"] >= USER_MAX_FAILS:
            st["until"] = time.time() + USER_LOCK_SECONDS
            st["count"] = 0
            USER_FAILS[ip] = st
            return USER_LOCK_SECONDS
        USER_FAILS[ip] = st
        return 0


def user_clear_fails(ip: str):
    with _USERS_LOCK:
        USER_FAILS.pop(ip, None)


def reg_allowed(ip: str) -> bool:
    """每 IP 每小时限注册 N 个，防批量刷号。"""
    now = time.time()
    with _USERS_LOCK:
        log = [t for t in _REG_LOG.get(ip, []) if now - t < 3600]
        _REG_LOG[ip] = log
        return len(log) < REG_PER_HOUR


def reg_note(ip: str):
    with _USERS_LOCK:
        _REG_LOG.setdefault(ip, []).append(time.time())


def comments_path(tid: str) -> Path:
    return theme_dir(tid) / "comments.json"


def read_comments(tid: str) -> list:
    p = comments_path(tid)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def write_comments(tid: str, items: list):
    tmp = theme_dir(tid) / ("comments_" + uuid.uuid4().hex[:8] + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(comments_path(tid))


# ---------------------------------------------------------------------------
# 网页商店
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    f = WEB_DIR / "index.html"
    if not f.exists():
        return HTMLResponse("<h1>OC Theme Studio 主题商店</h1><p>缺少 web/index.html</p>")
    rel = 'target="_blank" rel="noopener noreferrer"'
    parts = []
    if ICP_BEIAN:
        icon = ICP_ICON_SVG if ICP_ICON else ""
        parts.append('<a href="https://beian.miit.gov.cn/" ' + rel + '>'
                     + icon + html_escape(ICP_BEIAN) + '</a>')
    if POLICE_BEIAN:
        badge = ('<img class="ghs" src="' + html_escape(POLICE_ICON) + '" alt="">'
                 if POLICE_ICON else "")
        href = ("https://beian.mps.gov.cn/#/query/webSearch?code=" + POLICE_CODE
                if POLICE_CODE else "https://beian.mps.gov.cn/")
        parts.append('<a href="' + href + '" ' + rel + '>'
                     + badge + html_escape(POLICE_BEIAN) + '</a>')
    beian = '<span class="sep">｜</span>'.join(parts)
    # 占位符在 web/index.html 的页脚，两者都未配置时整行留空
    return HTMLResponse(f.read_text(encoding="utf-8").replace("<!--ICP_BEIAN-->", beian))


@app.get("/static/{filename}")
def serve_web_static(filename: str):
    """页脚备案图标等 web/ 下的静态素材；文件名与后缀白名单防目录穿越。"""
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", filename):
        raise HTTPException(status_code=400, detail="非法文件名")
    if Path(filename).suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    target = (WEB_DIR / filename).resolve()
    if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(target, media_type=mime)


@app.get("/api/health")
def health():
    return {"ok": True, "service": TITLE, "themes": len(list_meta())}


@app.get("/api/categories")
def get_categories():
    """分类列表与数量；「全部」由客户端自行合成，不属于存储字段。"""
    counts = {}
    for m in list_meta():
        c = normalize_category(m.get("category"))
        counts[c] = counts.get(c, 0) + 1
    return [{"id": cid, "label": label, "count": counts.get(cid, 0)}
            for cid, label in CATEGORIES]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/themes")
def get_themes(category: Optional[str] = None):
    """主题列表；?category=theme|icons|background|fonts 或中文名称可过滤。"""
    v = (category or "").strip().lower()
    cat = None
    if v and v not in ("all", "全部"):
        if v in CATEGORY_LABELS:
            cat = v
        else:
            cat = next((cid for cid, label in CATEGORIES if v == label), None)
            if cat is None:
                raise HTTPException(status_code=400, detail="未知分类：" + str(category))
    return [to_public(m) for m in list_meta()
            if cat is None or normalize_category(m.get("category")) == cat]


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
    category: str = Form(DEFAULT_CATEGORY),
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
    cv = (category or "").strip().lower()
    if cv and cv not in CATEGORY_LABELS and cv not in [label for _, label in CATEGORIES]:
        raise HTTPException(status_code=400, detail="未知分类：" + str(category))
    cat = normalize_category(category)
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
            tags=tag_list, category=cat, previews=pv)
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass
    return JSONResponse(to_public(meta), status_code=201)


@app.delete("/api/themes/{tid}")
def delete_theme(tid: str, request: Request):
    """删除主题：需管理员登录会话或旧静态口令；都没配置时接口直接禁用。"""
    if not ADMIN_ENABLED and not ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="未配置管理员账号，删除接口已禁用")
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    if not ID_RE.match(tid) or not theme_dir(tid).exists():
        raise HTTPException(status_code=404, detail="主题不存在")
    shutil.rmtree(theme_dir(tid))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 封面 / 预览图管理（管理员）
# ---------------------------------------------------------------------------
PREVIEW_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
PREVIEW_MAX_BYTES = 8 * 1024 * 1024


def _require_theme(tid: str) -> dict:
    if not ID_RE.match(tid):
        raise HTTPException(status_code=400, detail="非法 id")
    m = read_meta(tid)
    if m is None:
        raise HTTPException(status_code=404, detail="主题不存在")
    return m


def _save_meta(tid: str, m: dict):
    m["updated"] = now_iso()
    meta_path(tid).write_text(json.dumps(m, ensure_ascii=False, indent=2),
                              encoding="utf-8")


async def _read_preview_upload(f: Optional[UploadFile]) -> tuple:
    """校验并读取上传的图片：返回 (bytes, ext)。"""
    if not f or not f.filename:
        raise HTTPException(status_code=400, detail="请选择图片文件")
    ext = Path(f.filename).suffix.lower()
    if ext not in PREVIEW_EXTS:
        raise HTTPException(status_code=400,
                            detail="图片格式必须是 " + " / ".join(PREVIEW_EXTS))
    b = await f.read()
    if not b:
        raise HTTPException(status_code=400, detail="图片为空")
    if len(b) > PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="图片超过 8 MB 上限")
    return b, ext


@app.post("/api/themes/{tid}/cover")
async def set_cover(tid: str, request: Request, file: UploadFile = File(...)):
    """自定义封面：管理员上传一张图片，作为卡片缩略图与详情页首图。"""
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    m = _require_theme(tid)
    data, ext = await _read_preview_upload(file)
    pdir = theme_dir(tid) / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    # 清掉旧封面（cover.* 可能是任意受支持格式）
    for old in pdir.glob("cover.*"):
        try:
            old.unlink()
        except Exception:
            pass
    fname = "cover" + ext
    (pdir / fname).write_bytes(data)
    m["cover"] = fname
    m["thumbnail"] = f"/files/{tid}/previews/{fname}"
    _save_meta(tid, m)
    return JSONResponse(to_public(m))


@app.post("/api/themes/{tid}/cover/generate")
def generate_cover(tid: str, request: Request):
    """自动从主题包生成封面：优先取包内背景图，纯图标包则拼图标网格。"""
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    m = _require_theme(tid)
    pkg = theme_dir(tid) / "package.zip"
    if not pkg.exists():
        raise HTTPException(status_code=404, detail="主题包文件缺失，无法生成封面")
    pdir = theme_dir(tid) / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    for old in pdir.glob("cover.*"):
        try:
            old.unlink()
        except Exception:
            pass
    url = None
    try:
        with zipfile.ZipFile(pkg) as zf:
            url = extract_preview_from_zip(zf, theme_dir(tid), tid, out_stem="cover")
            if not url:
                url = build_icon_preview(zf, theme_dir(tid), tid, out_stem="cover")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"生成封面失败：{e}")
    if not url:
        raise HTTPException(status_code=422,
                            detail="包内没有可用的图片资源，无法自动生成封面，请手动上传")
    m["cover"] = "cover" + Path(url).suffix.lower()
    m["thumbnail"] = url
    _save_meta(tid, m)
    return JSONResponse(to_public(m))


@app.post("/api/themes/{tid}/previews")
async def add_preview(tid: str, request: Request, file: UploadFile = File(...)):
    """追加一张预览图（详情页轮播可翻页）。"""
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    m = _require_theme(tid)
    data, ext = await _read_preview_upload(file)
    pdir = theme_dir(tid) / "previews"
    pdir.mkdir(parents=True, exist_ok=True)
    fname = f"u_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}"
    (pdir / fname).write_bytes(data)
    if not m.get("thumbnail"):
        m["thumbnail"] = f"/files/{tid}/previews/{fname}"
    _save_meta(tid, m)
    return JSONResponse(to_public(m))


@app.delete("/api/themes/{tid}/previews/{fname}")
def delete_preview(tid: str, fname: str, request: Request):
    """删除一张预览图；若删的是封面则回退到剩余第一张。"""
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    m = _require_theme(tid)
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(status_code=400, detail="非法文件名")
    target = theme_dir(tid) / "previews" / fname
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="预览图不存在")
    target.unlink()
    was_cover = m.get("cover") == fname
    if was_cover:
        m.pop("cover", None)
    pdir = theme_dir(tid) / "previews"
    rest = sorted(f for f in pdir.iterdir()
                  if f.is_file() and f.suffix.lower() in PREVIEW_EXTS)
    if was_cover or not m.get("thumbnail"):
        m["thumbnail"] = (f"/files/{tid}/previews/{rest[0].name}"
                          if rest else "")
    _save_meta(tid, m)
    return JSONResponse(to_public(m))


@app.get("/api/admin/status")
def admin_status():
    """给网页用：是否启用了管理员登录（不泄露账号/口令本身）。"""
    return {"enabled": ADMIN_ENABLED, "staticToken": bool(ADMIN_TOKEN),
            "sessionHours": ADMIN_SESSION_HOURS}


@app.post("/api/admin/login")
async def admin_login(request: Request):
    """账号密码登录，成功签发随机会话 token。"""
    if not ADMIN_ENABLED:
        raise HTTPException(status_code=403, detail="未配置管理员密码，登录已禁用")
    ip = client_ip(request)
    wait = locked_seconds(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求格式错误")
    user = str(body.get("username") or "").strip()
    pwd = str(body.get("password") or "")
    # 账号也用常量时间比较，避免通过响应时间枚举有效账号
    user_ok = hmac.compare_digest(user, ADMIN_USER)
    pwd_ok = password_ok(pwd)
    if not (user_ok and pwd_ok):
        lock = note_fail(ip)
        if lock:
            raise HTTPException(status_code=429, detail=f"密码错误次数过多，请 {lock} 秒后再试")
        raise HTTPException(status_code=401, detail="账号或密码不正确")
    clear_fails(ip)
    tok, exp = new_session(ADMIN_USER)
    return {"ok": True, "token": tok, "user": ADMIN_USER,
            "expiresAt": exp, "sessionHours": ADMIN_SESSION_HOURS}


@app.post("/api/admin/logout")
def admin_logout(request: Request):
    tok = admin_token_from(request)
    if tok:
        drop_session(tok)
    return {"ok": True}


@app.get("/api/admin/session")
def admin_session(request: Request):
    """刷新页面时校验会话是否仍有效。"""
    tok = admin_token_from(request)
    user = session_user(tok)
    if user:
        return {"valid": True, "user": user, "static": False}
    if ADMIN_TOKEN and tok and hmac.compare_digest(tok, ADMIN_TOKEN):
        return {"valid": True, "user": ADMIN_USER, "static": True}
    return {"valid": False}


# ---------------------------------------------------------------------------
# 普通用户：注册 / 登录 / 会话
# ---------------------------------------------------------------------------
@app.post("/api/users/register")
async def user_register(request: Request):
    """注册普通账号：用户名 + 密码，成功即登录并签发会话 token。"""
    ip = client_ip(request)
    if not reg_allowed(ip):
        raise HTTPException(status_code=429, detail="本网络注册过于频繁，请 1 小时后再试")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求格式错误")
    name = str(body.get("username") or "").strip()
    pwd = str(body.get("password") or "")
    nick = str(body.get("nickname") or "").strip()
    if not USER_NAME_RE.match(name):
        raise HTTPException(status_code=400,
                            detail="用户名需为 2-20 位中文、字母、数字或下划线")
    if len(pwd) < 6 or len(pwd) > 64:
        raise HTTPException(status_code=400, detail="密码需为 6-64 位")
    if len(nick) > 24:
        raise HTTPException(status_code=400, detail="昵称不能超过 24 字")
    display = nick or name
    key = name.lower()
    with _USERS_LOCK:
        users = load_users()
        if key in users:
            raise HTTPException(status_code=409, detail="该用户名已被注册")
        users[key] = {
            "name": name,
            "nick": nick,
            "pwd": hash_password(pwd),
            "created": now_iso(),
        }
        save_users(users)
    reg_note(ip)
    tok, exp = new_user_session(display, key, name)
    return {"ok": True, "token": tok, "user": display, "login": name,
            "expiresAt": exp, "sessionHours": USER_SESSION_HOURS}


@app.post("/api/users/login")
async def user_login(request: Request):
    ip = client_ip(request)
    wait = user_locked_seconds(ip)
    if wait:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {wait} 秒后再试")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求格式错误")
    name = str(body.get("username") or "").strip()
    pwd = str(body.get("password") or "")
    key = name.lower()
    with _USERS_LOCK:
        users = load_users()
        rec = users.get(key)
    ok = bool(rec) and verify_password(pwd, rec.get("pwd", ""))
    if not ok:
        lock = user_note_fail(ip)
        if lock:
            raise HTTPException(status_code=429, detail=f"密码错误次数过多，请 {lock} 秒后再试")
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    user_clear_fails(ip)
    tok, exp = new_user_session(rec.get("nick") or rec["name"], key, rec["name"])
    return {"ok": True, "token": tok, "user": rec.get("nick") or rec["name"],
            "login": rec["name"],
            "expiresAt": exp, "sessionHours": USER_SESSION_HOURS}


@app.post("/api/users/logout")
def user_logout(request: Request):
    tok = user_token_from(request)
    if tok:
        drop_user_session(tok)
    return {"ok": True}


@app.get("/api/users/session")
def user_session_check(request: Request):
    rec = user_session(user_token_from(request))
    if rec:
        return {"valid": True, "user": rec["user"], "login": rec.get("login", ""),
                "isAdmin": user_is_admin(rec["key"])}
    return {"valid": False}


# ---------------------------------------------------------------------------
# 管理员授权：超级管理员把已注册用户提升 / 降为管理员
# ---------------------------------------------------------------------------
@app.get("/api/admin/check-user")
def check_user_admin(request: Request):
    """查询当前登录的普通用户是否已被授予管理员（前端据此显示管理入口）。"""
    rec = user_session(user_token_from(request))
    if rec and user_is_admin(rec["key"]):
        return {"isAdmin": True, "user": rec["user"], "login": rec.get("login", "")}
    return {"isAdmin": False}


@app.get("/api/admin/users")
def list_admin_users(request: Request):
    """全部注册用户及其管理员标志（仅超级管理员可查）。"""
    if not super_authorized(request):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    with _USERS_LOCK:
        users = load_users()
    return [{"login": u.get("name", k), "nick": u.get("nick", ""),
             "created": u.get("created", ""), "isAdmin": bool(u.get("admin"))}
            for k, u in sorted(users.items())]


@app.post("/api/admin/users/grant")
async def grant_admin(request: Request):
    """授予某注册用户管理员权限（仅超级管理员）。"""
    if not super_authorized(request):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    key = str((body or {}).get("username") or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="请提供用户名")
    with _USERS_LOCK:
        users = load_users()
        rec = users.get(key)
        if rec is None:
            raise HTTPException(status_code=404, detail="用户不存在，请让其先注册")
        if rec.get("admin"):
            return {"ok": True, "login": rec["name"], "isAdmin": True, "changed": False}
        rec["admin"] = True
        save_users(users)
    return {"ok": True, "login": rec["name"], "isAdmin": True, "changed": True}


@app.post("/api/admin/users/revoke")
async def revoke_admin(request: Request):
    """撤销管理员权限（仅超级管理员）。"""
    if not super_authorized(request):
        raise HTTPException(status_code=403, detail="需要超级管理员权限")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    key = str((body or {}).get("username") or "").strip().lower()
    if not key:
        raise HTTPException(status_code=400, detail="请提供用户名")
    with _USERS_LOCK:
        users = load_users()
        rec = users.get(key)
        if rec is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if not rec.get("admin"):
            return {"ok": True, "login": rec["name"], "isAdmin": False, "changed": False}
        rec["admin"] = False
        save_users(users)
    return {"ok": True, "login": rec["name"], "isAdmin": False, "changed": True}


# ---------------------------------------------------------------------------
# 评论
# ---------------------------------------------------------------------------
COMMENT_MAX = 500
COMMENT_PER_MIN = max(1, int(os.environ.get("STORE_COMMENT_PER_MIN", "5")))
_COMMENT_LOG: dict = {}


def comment_allowed(key: str) -> bool:
    now = time.time()
    with _USERS_LOCK:
        log = [t for t in _COMMENT_LOG.get(key, []) if now - t < 60]
        _COMMENT_LOG[key] = log
        return len(log) < COMMENT_PER_MIN


@app.get("/api/themes/{tid}/comments")
def get_comments(tid: str):
    if not ID_RE.match(tid) or not theme_dir(tid).exists():
        raise HTTPException(status_code=404, detail="主题不存在")
    items = read_comments(tid)
    items.sort(key=lambda c: c.get("ts", 0), reverse=True)
    return [{"id": c.get("id"), "user": c.get("user"), "text": c.get("text"),
             "time": c.get("time"), "ts": c.get("ts", 0)} for c in items]


@app.post("/api/themes/{tid}/comments")
async def post_comment(tid: str, request: Request):
    if not ID_RE.match(tid) or not theme_dir(tid).exists():
        raise HTTPException(status_code=404, detail="主题不存在")
    rec = user_session(user_token_from(request))
    if not rec:
        raise HTTPException(status_code=401, detail="请先注册或登录后评论")
    if not comment_allowed(rec["key"]):
        raise HTTPException(status_code=429, detail="评论太快了，请稍后再发")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求格式错误")
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="评论内容不能为空")
    if len(text) > COMMENT_MAX:
        raise HTTPException(status_code=400, detail=f"评论不能超过 {COMMENT_MAX} 字")
    with _USERS_LOCK:
        _COMMENT_LOG.setdefault(rec["key"], []).append(time.time())
    item = {
        "id": "c_" + uuid.uuid4().hex[:10],
        "user": rec["user"],
        "key": rec["key"],
        "text": text,
        "time": now_iso(),
        "ts": int(time.time()),
    }
    items = read_comments(tid)
    items.append(item)
    write_comments(tid, items)
    return JSONResponse({"ok": True, **{k: item[k] for k in
                        ("id", "user", "text", "time", "ts")}}, status_code=201)


@app.delete("/api/themes/{tid}/comments/{cid}")
def delete_comment(tid: str, cid: str, request: Request):
    if not ID_RE.match(tid) or not theme_dir(tid).exists():
        raise HTTPException(status_code=404, detail="主题不存在")
    urec = user_session(user_token_from(request))
    is_admin = admin_authorized(request)
    if not urec and not is_admin:
        raise HTTPException(status_code=401, detail="请先登录")
    items = read_comments(tid)
    target = next((c for c in items if c.get("id") == cid), None)
    if target is None:
        raise HTTPException(status_code=404, detail="评论不存在")
    if not is_admin and target.get("key") != urec["key"]:
        raise HTTPException(status_code=403, detail="只能删除自己的评论")
    items = [c for c in items if c.get("id") != cid]
    write_comments(tid, items)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 应用内更新（App Updater）
#
# 两个更新通道：
#   1) GitHub Release：App 直接请求 GitHub API（无需本服务参与）
#   2) 自有服务器：GET /api/app/update 读 releases/app.json；
#      POST /api/app/release 由管理员上传新版本 DMG 并原子更新 app.json
# 发布产物与主题数据同盘存放（STORE_DATA/releases/），备份策略一致。
# ---------------------------------------------------------------------------

RELEASES_DIR = DATA / "releases"
RELEASES_DIR.mkdir(parents=True, exist_ok=True)
RELEASE_META = RELEASES_DIR / "app.json"
UPDATE_MAX_MB = int(os.environ.get("UPDATE_MAX_MB", "200"))
UPDATE_MAX_BYTES = UPDATE_MAX_MB * 1024 * 1024
SEMVER_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def read_release_meta() -> dict:
    if not RELEASE_META.exists():
        return {}
    try:
        return json.loads(RELEASE_META.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.get("/api/app/update")
def app_update_check():
    """App 启动/手动检查更新时调用：返回最新版本号、更新说明与下载地址。"""
    meta = read_release_meta()
    if not meta:
        return {"hasUpdate": False, "channel": "server"}
    return {
        "hasUpdate": True,
        "channel": "server",
        "version": meta.get("version", ""),
        "notes": meta.get("notes", ""),
        "publishedAt": meta.get("publishedAt", ""),
        "url": "/api/app/release/download",
        "size": meta.get("size", 0),
        "sha256": meta.get("sha256", ""),
    }


@app.get("/api/app/release/download")
def app_release_download():
    meta = read_release_meta()
    fn = meta.get("file", "")
    path = RELEASES_DIR / fn if fn else None
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="暂无发布包")
    return FileResponse(path, filename=fn, media_type="application/octet-stream")


@app.post("/api/app/release")
async def app_release_upload(
    request: Request,
    version: str = Form(...),
    notes: str = Form(""),
    package: UploadFile = File(...),
):
    """管理员发布新版本：上传 DMG + 版本号 + 更新说明，原子替换 app.json。"""
    if not admin_authorized(request):
        raise HTTPException(status_code=401, detail="请先登录管理员账号")
    v = (version or "").strip().lstrip("vV")
    if not SEMVER_RE.match(v):
        raise HTTPException(status_code=400, detail="版本号格式应为 x.y[.z[.w]]，例如 2.5.0")
    fn = (package.filename or "").strip()
    if not fn.lower().endswith(".dmg"):
        raise HTTPException(status_code=400, detail="发布包必须是 .dmg 文件")
    raw = await package.read()
    if not raw:
        raise HTTPException(status_code=400, detail="发布包为空")
    if len(raw) > UPDATE_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"发布包超过 {UPDATE_MAX_MB} MB 上限")

    safe_name = f"OC.Theme.Studio-{v}.dmg"
    tmp = RELEASES_DIR / (uuid.uuid4().hex + ".part")
    try:
        tmp.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        final = RELEASES_DIR / safe_name
        tmp.replace(final)
        # 清理旧版本包（保留最近 3 个）
        olds = sorted(RELEASES_DIR.glob("OC.Theme.Studio-*.dmg"),
                      key=lambda p: p.stat().st_mtime)
        for p in olds[:-3]:
            if p != final:
                try:
                    p.unlink()
                except Exception:
                    pass
        meta = {
            "version": v,
            "notes": (notes or "").strip()[:2000],
            "publishedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "file": safe_name,
            "size": len(raw),
            "sha256": digest,
        }
        tmp_meta = RELEASES_DIR / (uuid.uuid4().hex + ".json")
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_meta.replace(RELEASE_META)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    return JSONResponse({"ok": True, **meta}, status_code=201)


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
