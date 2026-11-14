# OC Theme Studio · 主题商店后端

给 [OC Theme Studio](https://github.com/huangjinkun2006/oc-theme-studio) macOS 原生 App 用的主题共享后端。
支持**多用户上传主题**（名称 / 作者 / 版本 / 分类 / 标签 / 简介 / 主题压缩包 / 预览图）与**其他用户按分类筛选、下载并一键应用**。

- 纯 Python + FastAPI，**无数据库**（主题以文件夹形式存储：`meta.json` + `package.zip` + `previews/`）
- 内置 5 个分类：**完整主题 / 图标区 / 背景区 / 字体区 / EFI 配置包**；早期没有分类字段的主题自动归为「完整主题」
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
| `STORE_MAX_MB` | `50` | 单个主题包大小上限（MB）。**调大时必须同步调 nginx 的 `client_max_body_size` 与超时**，否则大请求会先在 nginx 被 413 拒绝、到不了后端；参考 `deploy/nginx.conf`。网页端 `web/index.html` 的 `MAX_UPLOAD_MB` 也需保持一致 |
| `STORE_ICP_BEIAN` | `闽ICP备2024073090号-1` | 首页底部展示的备案号，设为空则不展示 |
| `STORE_ICP_ICON` | `1` | 备案号前的中性装饰小图标，设 `0` 关闭。工信部未下发官方 ICP 图标，此图标为自绘装饰，不冒充官方徽标 |
| `STORE_POLICE_BEIAN` | `闽公网安备35092202000207号` | 公安联网备案号，设为空则不展示 |
| `STORE_POLICE_ICON` | `/static/ghs.png` | 公安备案警徽，取自平台「已备案网站 → 公安机关备案号详情」下载的官方素材，本地托管于 `web/ghs.png` |
| `STORE_UPLOAD_TOKEN` | 空 | 可选上传口令；设置后 `POST /api/themes` 需带 `X-Upload-Token` 头 |
| `STORE_ADMIN_TOKEN` | 空 | 删除口令；**未设置时 `DELETE` 接口直接禁用**，设置后需带 `X-Admin-Token` 头 |

---

## 部署到托管平台

> ⚠️ **免费档持久性提醒**：Render / Railway 的**免费实例磁盘是临时的**——服务重启或休眠后，用户上传的主题会被清空（已播种的示例主题会在启动时自动重新生成，不会丢）。要长期保存用户上传内容，请用下面标 ✅ 持久的平台，或给 Render 实例挂持久磁盘（付费）。

### ✅ Fly.io（免费 + 真持久，长期运营首选）
免费档自带 3GB 持久磁盘，上传的主题永久保留。本仓库已含 [fly.toml](fly.toml)：
```bash
brew install flyctl && flyctl auth login
flyctl launch --no-deploy          # 选区域（如 hkg / sin）
flyctl volumes create ocstore_data --size 1 --region <上一步选的区域>
flyctl deploy
```
部署完会给出 `https://oc-theme-store.fly.dev` 这样的公网地址。

### Render（免费，但磁盘临时；不需要信用卡）
本仓库已含 [render.yaml](render.yaml)，最省事：
1. 打开 https://dashboard.render.com → **New → Blueprint**
2. 连接本 GitHub 仓库 `huangjinkun2006/oc-theme-store`
3. 确认 blueprint，点 **Apply** 即可，几分钟后得到 `https://xxx.onrender.com`
> 想让上传持久：编辑 `render.yaml` 取消 `disk` 段注释并挂载 `/var/data`（需付费实例）。

### Railway（免费试用额度，磁盘临时）
本仓库已含 [railway.json](railway.json)：
1. https://railway.app → **New Project → Deploy from GitHub repo**
2. 选择本仓库，Railway 会自动用 Dockerfile 构建并启动
> 免费额度用完后需付费；持久化需另挂 Volume 到 `STORE_DATA`。

### Docker / VPS（自己有服务器，最可控）
```bash
docker compose up -d          # 含命名卷 ocstore-data，重启不丢数据
```
或 `docker build -t oc-theme-store . && docker run -p 8000:8000 -v $(pwd)/data:/data oc-theme-store`

**七牛云云服务器（国内 + 自有域名，推荐给国内用户）**：见 [`deploy/七牛云部署.md`](deploy/七牛云部署.md) —— 一台 Ubuntu 云服务器 + 一条命令 `curl .../deploy/setup.sh | sudo bash` 即可起服务，再用你的域名做 A 记录 + nginx 反代 + certbot HTTPS（可选七牛云 CDN 加速）。

### 任意 Python 托管
只要能跑 `uvicorn store_server:app --host 0.0.0.0 --port $PORT` 即可（含 WorkBuddy 应用发布、PythonAnywhere 等）。

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
| `GET` | `/api/categories` | 分类列表 `[{id, label, count}]`，顺序固定 |
| `GET` | `/api/themes` | 主题列表（数组）；可用 `?category=theme\|icons\|background\|fonts\|efi` 或中文名称过滤，`all` / 空表示全部 |
| `GET` | `/api/themes/{id}` | 主题详情 |
| `GET` | `/api/themes/{id}/download` | 下载 zip（下载次数 +1） |
| `POST` | `/api/themes` | 上传主题（multipart；启用 `STORE_UPLOAD_TOKEN` 时需带 `X-Upload-Token`） |
| `DELETE` | `/api/themes/{id}` | 删除（默认禁用，配置 `STORE_ADMIN_TOKEN` 后才可用） |
| `GET` | `/files/{id}/{path}` | 预览图等静态文件（含目录穿越防护） |
| `GET` | `/static/{filename}` | 页脚备案图标等 `web/` 下静态素材（文件名 + 后缀白名单） |

`POST /api/themes` 表单字段：`name*`、`author`、`description`、`version`、`category`（`theme` / `icons` / `background` / `fonts` / `efi` 或中文名称，默认 `theme`）、`tags`（逗号分隔）、`package*`（.zip）、`previews`（多张图片，可重复）。

返回的主题对象字段：
`id, name, author, description, version, tags[], category, categoryLabel, thumbnail, download, previewCount, sizeText, updated, downloads`。
其中 `thumbnail` / `download` 是以 `/` 开头的相对路径，由客户端拼接服务器地址；`category` 存 slug，`categoryLabel` 是中文显示名。

示例：
```bash
curl https://your-store.example/api/categories
curl "https://your-store.example/api/themes?category=icons"
curl -F "name=MyTheme" -F "author=me" -F "category=theme" -F "package=@theme.zip" \
     https://your-store.example/api/themes
```

---

## 主题包格式

标准 OpenCore 主题 zip：内含 `Background*.png` 与若干 `.icns` 图标（也可为
`EFI/OC/Resources/Image/...` 结构，App 会自动展平并重建 manifest）。
`samples/` 下 3 个示例包即为此格式，可直接作为上传样例。

分类只影响商店里的展示与筛选，不改变 zip 校验规则。「图标区 / 背景区 / 字体区」
用于分享单类资源包时仍请打包为 `.zip`；纯字体包等不含完整主题结构的资源，
下载后可手动取用，App 的「一键应用」仍要求包内能识别出主题。

**EFI 配置包**（`efi`）建议只由 Mac App 从已挂载的 EFI 分区生成：包内含
`oc-efi-manifest.json`、`EFI/OC/Resources` 与只保留 `PickerMode` / `PickerVariant`
的最小 `config.plist`；App 已移除 `PlatformInfo` / `NVRAM` / `Kexts` / `Drivers`
等机器相关配置。网页手动上传的 EFI 包也必须包含该 manifest，否则 App 会拒绝安装。
请勿手动上传含真实序列号、MLB、ROM、UUID 的完整 `config.plist`。

---

## 安全说明

- 已做：`..` 目录穿越防护、id 白名单正则、zip 有效性校验、大小上限、文件名清洗、CORS 放开（供 App 跨域调用）、删除接口默认禁用、首页备案号展示与上传须知。
- **上传鉴权为可选**：默认开放以兼容现有 Mac App；设置 `STORE_UPLOAD_TOKEN` 后，网页端可在「上传口令」栏填写，**但 Mac App 端也需同步支持 `X-Upload-Token` 头并重新打包，否则 App 上传会被拒**。
- **未做**：用户账号体系、内容审核、限流。公开部署前建议加一层网关鉴权；`STORE_ADMIN_TOKEN` 仅用于开放删除接口。
- 若用于公网长期运营，请把 `STORE_DATA` 放在持久磁盘并定期备份。
