# Music Bot - 精简版音乐下载机器人

基于原 SaveXTube 项目重构，专注于音乐下载功能，支持 Web 配置管理。
当前内置两个下载器：网易云音乐、Apple Music。

## ✨ 功能特点

- 🎵 **网易云音乐** - 歌曲/专辑/歌单下载，支持无损音质
- 🔄 **登录态自动续期** - 后台定时检测 Cookie，能续就自动续，失效才提醒；也支持扫码登录
- 🍎 **Apple Music** - 支持 ALAC 无损下载
- 🌐 **Web 管理后台** - 仪表盘 / 歌单订阅 / 下载记录 / 日志 / 设置 / 账号，全部可视化
- 🤖 **Telegram Bot** - 发送链接即可下载，支持并发处理多条消息
- 💬 **消息交互** - 单条消息原地刷新的进度卡（进度条/速度/剩余/排队位次）、可用的取消按钮、完成卡片带音质与落盘路径、失败原因翻译成人话
- 🔑 **Telegram Session** - 内置 Session 生成器，支持大文件下载
- 📝 **元数据支持** - 完善的音频元数据管理（封面、歌词、专辑信息）
- 📦 **下载队列** - 并发上限、失败重试（指数退避）、断点续传、任务取消
- 🐳 **Docker 部署** - 一键部署

## 🚀 快速开始

### Docker Compose 部署（推荐）

#### 1. 克隆项目

```bash
git clone https://github.com/Yan-nian/music-bot.git
cd music-bot
```

#### 2. 创建 docker-compose.yml

```yaml
version: '3.8'

services:
  music-bot:
    image: yannian/music-bot:latest  # 或使用 build: . 本地构建
    container_name: music-bot
    restart: unless-stopped
    ports:
      - "5000:5000"  # Web 配置界面
    volumes:
      - ./db:/app/db                    # 配置数据库
      - ./cookies:/app/cookies          # Cookies 文件
      - ./logs:/app/logs                # 日志文件
      - /path/to/downloads:/downloads   # 下载目录 (修改为你的实际路径)
    environment:
      - TZ=Asia/Shanghai
      # 以下环境变量可选，也可以通过 Web 界面配置
      # - TELEGRAM_BOT_TOKEN=your_bot_token
      # - PROXY_HOST=http://192.168.1.1:7890
```

#### 3. 启动服务

```bash
# 后台启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

#### 4. 访问 Web 配置界面

打开浏览器访问 `http://localhost:5000`。

**首次启动会随机生成管理后台密码**，在容器日志里可以看到：

```bash
docker logs music-bot | grep -A4 "首次启动"
```

```
  用户名: admin
  密  码: xxxxxxxx-xxxxxxx
```

也可以用环境变量指定固定密码：`ADMIN_PASSWORD=你的密码`。
登录后请到「账号与登录」页改掉。

登录后可配置：Telegram Bot Token、音乐平台设置、下载路径等。

#### 5. 使用本地构建 (可选)

如果你想本地构建镜像而不是使用预构建镜像：

```yaml
services:
  music-bot:
    build: .  # 替换 image 为 build
    # ... 其余配置相同
```

然后运行：
```bash
docker-compose up -d --build
```

### 本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 运行（同时启动 Web 和 Bot）
python main.py

# 仅启动 Web 配置服务
python main.py --web-only

# 仅启动 Telegram Bot
python main.py --bot-only

# 指定 Web 端口
python main.py --web-port 8080

# 指定数据库路径
python main.py --db-path /path/to/config.db
```

## 📝 配置说明

访问 `http://localhost:5000` 进入 Web 配置界面，可配置：

### Telegram 配置
- Bot Token - 从 @BotFather 获取
- API ID / API Hash - 从 my.telegram.org 获取（Session 生成需要）
- Session String - 访问 `/setup` 页面生成
- 允许的用户 ID - 限制谁可以使用 Bot

### 音乐平台配置

#### 网易云音乐
- 音质选择：标准/较高/极高/无损
- 下载歌词、封面
- 自定义目录和文件名格式
- Cookies 配置（需要会员下载高品质）
- 登录态自动维护：定时检测 Cookie、能续期就自动续、失效时 Telegram 提醒

> **推荐用扫码登录**：打开「账号与登录」→「扫码登录」→ 用网易云 App 扫一扫，
> Cookie 会自动写入，不用手动复制。登录态过期前，后台线程（`cookie_keeper.py`）
> 会按 `netease_cookie_check_interval`（默认 1800 秒）自动调续期接口；
> 只有真正失效时才会发一次 Telegram 提醒（6 小时内不重复），不会刷屏。

#### Apple Music
- 音质选择：AAC-256/无损ALAC/杜比全景声
- 地区设置
- Cookies 配置

### 通用设置
- 下载保存路径
- 代理设置
- 日志配置

## 📁 目录结构

```
music_bot/
├── main.py                 # 主程序入口
├── config_manager.py       # 配置管理器（SQLite）
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 构建文件
├── docker-compose.yml      # Docker Compose 配置
├── README.md               # 本文档
│
├── config/                 # 配置/历史/日志/歌单的 Mixin，以及曲库扫盘统计
├── download_queue.py       # 下载队列（并发/重试/取消）
├── cookie_keeper.py        # 网易云登录态守护线程（定时检测 + 自动续期 + 失效提醒）
├── db/                     # 本地运行时的默认数据库目录（Docker 下是 /app/db）
│
├── downloaders/            # 下载器模块
│   ├── __init__.py         # 进程内下载器注册表（全局复用同一实例）
│   ├── base.py             # 基础下载器类
│   ├── netease.py          # 网易云音乐下载器
│   ├── errors.py           # DownloadCancelled（让「取消」能穿透到下载线程）
│   ├── netease_auth.py     # 网易云 Cookie / 扫码登录 / 续期
│   ├── apple_music.py      # Apple Music 下载器
│   └── metadata.py         # 音频元数据管理
│
└── web/                    # Web 管理后台
    ├── __init__.py
    ├── app.py              # Flask 应用 + API
    ├── jobs.py             # 后台任务（同步歌单 / 刷新元数据 / 批量重试）
    ├── db_logger.py         # 把应用日志写进数据库（供日志页展示）
    ├── tg_setup.py         # Telegram Session 生成器
    ├── tg_ui.py            # TG 消息渲染层（HTML 转义 / 安全发送 / 进度卡片）
    ├── static/
    │   ├── css/app.css     # 设计系统（不依赖任何 CSS 框架）
    │   └── js/app.js       # 图标 / 请求封装 / 弹窗 / 格式化工具
    └── templates/          # base.html + 各页面模板
```

> 数据库位置：Docker 内固定 `/app/db/music_bot.db`（由 `MUSIC_BOT_DB_PATH` 指定，compose 里挂载 `./db`）。
> 本地直接跑源码时默认落在代码目录下的 `db/music_bot.db`；也可以用 `--db-path` 或环境变量覆盖。
> 老版本把库放在 `/app/db`（Windows 上会变成 `C://app//db`）的，升级后会自动继续沿用，不会丢配置。

## 🔧 使用方法

### Web 管理后台

登录后有 6 个页面：

| 页面 | 作用 |
|------|------|
| 仪表盘 | 累计下载 / 今日新增 / 曲库容量 / 订阅数，实时下载队列（可取消），登录态概览，常用操作入口 |
| 歌单订阅 | 订阅/暂停/删除歌单，看每张歌单的下载进度和失败曲目，一键重试 |
| 下载记录 | 按平台/类型/关键词筛选历史，悬停可看精确时间 |
| 运行日志 | 按级别/类别/关键词过滤，支持「只看异常」、自动刷新、导出、清空 |
| 设置 | 7 组配置（Telegram / 网易云 / Apple Music / 下载与队列 / 网络 / qBittorrent / 系统），改完统一保存，支持导入导出 |
| 账号与登录 | 网易云扫码登录、登录态检测与续期、自动维护开关、后台账号改密 |

细节说明：
- 长任务（同步歌单、刷新元数据、批量重试）都在后台跑，页面显示进度，不会转圈卡住
- 设置页会把当前 tab 记在 URL（`/settings#download`），保存后不会跳回第一页
- 弹窗支持 `Esc` 关闭、点遮罩关闭、`Enter` 提交
- **曲库容量是扫盘算出来的**（扫配置文件里的下载目录），不依赖下载记录——所以手动删过的文件、Web 触发的下载都能算准。数据不对时点那张卡片会强制重扫

### Telegram Bot 命令

命令菜单会自动同步到聊天框左下角的菜单按钮，不用背命令。

| 命令 | 作用 |
|------|------|
| `/start` `/help` | 使用说明 + 快捷按钮（状态 / 队列 / 历史 / 账号 / 后台） |
| `/status` | 状态总览：下载器、登录态、队列、曲库容量、今日与累计，可点刷新 |
| `/queue` | 进行中的任务列表，看得到「前面还有几个」 |
| `/history [页码]` | 下载历史，每页 8 条，按钮翻页 |
| `/cookie [cookie]` | 查看登录态（优先引导去后台扫码）；带参数则更新 Cookie |
| `/cancel` | 取消自己所有排队中/下载中的任务 |
| `/id` | 查看自己的 Telegram ID，配白名单用 |

### 消息交互

- **进度卡片**：一条消息原地刷新，含文字进度条、当前第几首、大小/速度/剩余时间，右下角带 `⏹ 取消下载`，点了会真的中断（不是只改个文字）
- **排队可见**：还没轮到时会显示「前面还有 N 个任务」，不会让人以为卡死
- **完成卡片**：单曲显示大小/音质/码率/时长/落盘目录；专辑和歌单显示成功失败数、本次大小、前 5 首，失败曲目会翻译成人话（比如「Cookie 失效」「需要会员」）并给建议
- **不再整条消息报错**：所有消息走 HTML 渲染并转义，歌单名带 `[` `_` `*` `&` 也不会再发不出去
- **私聊发错东西有提示**：发一段不是链接的文字会提醒怎么发链接，30 秒内不重复刷

### 发送链接下载

直接发送音乐链接即可自动下载：

```
# 网易云音乐
https://music.163.com/song?id=123456
https://music.163.com/#/album?id=123456
https://music.163.com/#/playlist?id=123456

# Apple Music
https://music.apple.com/cn/album/xxxxx
https://music.apple.com/cn/song/xxxxx?i=123456
```

## 🔐 Telegram Session 生成

1. 访问 `http://localhost:5000/setup`
2. 输入 API ID 和 API Hash（从 my.telegram.org 获取）
3. 输入手机号码（带国际区号）
4. 收到验证码后输入
5. Session String 会自动保存到配置

**为什么需要 Session？**
- Telegram Bot API 限制发送文件最大 50MB
- 使用 Telethon Session 可以发送高达 2GB 的文件
- 对于下载无损音质的专辑非常有用

## 🍪 Cookies 获取

### 网易云音乐

**方式一（推荐）：扫码登录**

Web 后台 →「账号与登录」→ 点「生成二维码」→ 用网易云音乐 App 扫码确认，
Cookie 自动落库，之后由后台线程自动续期。

**方式二：手动复制**

1. 登录网易云音乐网页版
2. 按 F12 打开开发者工具
3. 在 Console 中输入 `document.cookie`
4. 粘贴到「账号与登录」→「手动填写 Cookie」，或 Web 设置里的 Cookie 字段

> 关键 Cookie 是 `MUSIC_U`（会员身份）和 `__csrf`。

### Apple Music

1. 安装浏览器扩展 "Get cookies.txt LOCALLY"
2. 登录 music.apple.com
3. 导出 cookies.txt
4. 放到 `cookies/apple_music_cookies.txt`

## 🎵 音频元数据

Music Bot 完整支持音频元数据管理：

### 支持的格式
- **MP3** - ID3v2.4 标签
- **FLAC** - Vorbis Comments
- **M4A/AAC** - MP4 标签

### 支持的元数据字段
- 标题、艺术家、专辑
- 专辑艺术家、作曲家
- 曲目号、碟片号、年份
- 流派、封面图片
- 同步/非同步歌词
- 音乐发行时间

## 🛠️ API 接口

Web 服务提供以下 API：

```
GET  /api/config          # 获取所有配置
POST /api/config          # 更新配置
GET  /api/config/<key>    # 获取单个配置
PUT  /api/config/<key>    # 设置单个配置
POST /api/config/reset    # 重置为默认值
GET  /api/config/export   # 导出配置
POST /api/config/import   # 导入配置
GET  /api/history         # 获取下载历史
GET  /api/status          # 获取服务状态
GET  /api/dashboard       # 首页聚合数据（统计 + 队列 + 最近下载 + 登录态）
POST /api/library/rescan  # 重扫曲库目录，返回真实音频文件数与总大小
GET  /api/logs            # 应用日志（支持 level / category / search / 多级别）

# 网易云登录态
GET  /api/netease/status       # 登录态 + 会员信息 + 检测/续期时间
POST /api/netease/check        # 立即检测（可顺便续期）
POST /api/netease/refresh      # 手动续期
POST /api/netease/cookies      # 手动写入 Cookie
POST /api/netease/logout       # 清除 Cookie
POST /api/netease/qr           # 生成扫码登录二维码
GET  /api/netease/qr/<unikey>  # 轮询扫码状态（801/802/803）

# 后台任务（长任务不再阻塞请求）
GET  /api/jobs                        # 任务列表
GET  /api/jobs/<id>                   # 任务进度
POST /api/jobs/<id>/cancel            # 取消任务
POST /api/jobs/playlist-sync          # 同步单个歌单
POST /api/jobs/playlist-sync-all      # 同步全部歌单
POST /api/jobs/retry-failed           # 重试失败曲目
POST /api/jobs/refresh-metadata       # 刷新已下载文件元数据

# Telegram Session
POST /tg/send_code        # 发送验证码
POST /tg/confirm_code     # 确认验证码
POST /tg/save_session     # 保存 Session
```

## 📄 License

MIT License
