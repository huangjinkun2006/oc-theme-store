# OC Theme Studio · 主题商店后端

给 [OC Theme Studio](https://github.com/huangjinkun2006/oc-theme-store) macOS 原生 App 用的主题共享后端。
支持**多用户上传主题**（名称 / 作者 / 版本 / 标签 / 简介 / 主题压缩包 / 预览图）与**其他用户下载并一键应用**。

- 纯 Python + FastAPI，**无数据库**（主题以文件夹形式存储：`meta.json` + `package.zip` + `previews/`）
- 自带**网页版商店**：没装 Mac App 的 Windows / Linux 用户也能用浏览器浏览、上传、下载
- App 与网页共用同一套 HTTP 接口
- 首次启动自动从 `samples/*.zip` 播种 3 个内置示例主题，商店不会空

---

## 快速开始（本地）

```bash
pip install -r requirements.txt
python store_server.py
# 打开 http://localhost:8000
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` / `STORE_PORT` | `8000` | 监听端口，`PORT` 优先（托管平台注入） |
| `STORE_HOST` | `0.0.0.0` | 监听地址 |
| `STORE_DATA` | `./store_data` | 数据目录 **← 要持久化就挂这个目录** |
| `STORE_SEED_DIR` | `./samples` | 示例主题 zip 目录 |
| `STORE_TITLE` | `OC Theme Studio 主题商店` | 网页标题 |
| `STORE_MAX_MB` | `50` | 单个主题包大小上限 |
| `STORE_ADMIN_TOKEN` | 空 | 设置后 `DELETE /api/themes/{id}` 需带 `X-Admin-Token` 头 |

---

## 部署到托管平台

### Render（推荐，有一键配置）
本仓库已含 [render.yaml](render.yaml)。在 Render 选择 **New → Blueprint**，连接本仓库即可。
> 免费实例磁盘是临时的，重启会清空上传内容。要长期保存，在 `render.yaml` 中启用 `disk` 段并挂载到 `/var/data`。

### Railway / Heroku 类平台
已含 [Procfile](Procfile)，平台会自动执行 `uvicorn ... --port $PORT`。
在平台设置持久 Volume 并挂载到 `STORE_DATA` 指向的目录。

### Docker / VPS
```bash
docker compose up -d          # 含命名卷 ocstore-data，重启不丢数据
```
或 `docker build -t oc-theme-store . && docker run -p 8000:8000 -v $(pwd)/data:/data oc-theme-store`

### 任意 Python 托管
只要能跑 `uvicorn store_server:app --host 0.0.0.0 --port $PORT` 即可（含 WorkBuddy 应用发布、Fly.io、PythonAnywhere 等）。

---

## 在 App 里连接

打开 macOS 版 **OC Theme Studio → 🛒 主题商店**，在窗口顶部「服务器地址」栏填入本服务的公网地址（例如 `https://xxx.onrender.com`，**不带结尾斜杠**），列表会自动刷新。

- 未配置地址 / 连不上时，App 会自动回退到**内置离线示例主题**，不会白屏报错。
- 下载后点「应用主题」，App 会复用原有的 EFI 分区写入流程。
- 网页端上传的主题会立刻出现在 App 列表中，反之亦然。

---

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/` | 网页商店首页 |
| `GET` | `/api/health` | 健康检查 `{ok, service, themes}` |
| `GET` | `/api/themes` | 主题列表（数组） |
| `GET` | `/api/themes/{id}` | 主题详情 |
| `GET` | `/api/themes/{id}/download` | 下载 zip（下载次数 +1） |
| `POST` | `/api/themes` | 上传主题（multipart） |
| `DELETE` | `/api/themes/{id}` | 删除（可选，见 `STORE_ADMIN_TOKEN`） |
| `GET` | `/files/{id}/{path}` | 预览图等静态文件（含目录穿越防护） |

`POST /api/themes` 表单字段：`name*`、`author`、`description`、`version`、`tags`（逗号分隔）、`package*`（.zip）、`previews`（多张图片，可重复）。

返回的主题对象字段：
`id, name, author, description, version, tags[], thumbnail, download, previewCount, sizeText, updated, downloads`。
其中 `thumbnail` / `download` 是以 `/` 开头的相对路径，由客户端拼接服务器地址。

示例：
```bash
curl https://your-store.example/api/themes
curl -F "name=MyTheme" -F "author=me" -F "package=@theme.zip" \
     https://your-store.example/api/themes
```

---

## 主题包格式

标准 OpenCore 主题 zip：内含 `Background*.png` 与若干 `.icns` 图标（也可为
`EFI/OC/Resources/Image/...` 结构，App 会自动展平并重建 manifest）。
`samples/` 下 3 个示例包即为此格式，可直接作为上传样例。

---

## 安全说明

- 已做：`..` 目录穿越防护、id 白名单正则、zip 有效性校验、大小上限、文件名清洗、CORS 放开（供 App 跨域调用）。
- **未做**：用户账号体系、上传鉴权、内容审核、限流。公开部署前建议自行加一层网关鉴权，或设置 `STORE_ADMIN_TOKEN` 管理删除权限。
- 若用于公网长期运营，请把 `STORE_DATA` 放在持久磁盘并定期备份。
