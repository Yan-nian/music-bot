# Music Bot - 精简版音乐下载机器人

基于原 SaveXTube 项目重构，专注于音乐下载功能，支持 Web 配置管理。

## ✨ 功能特点

- 🎵 **网易云音乐** - 歌曲/专辑/歌单下载，支持无损音质
- 🍎 **Apple Music** - 支持 ALAC 无损下载
- 🌐 **Web 配置** - 可视化配置管理界面
- 🤖 **Telegram Bot** - 发送链接即可下载
- � **Telegram Session** - 内置 Session 生成器，支持大文件下载
- 📝 **元数据支持** - 完善的音频元数据管理（封面、歌词、专辑信息）
- �🐳 **Docker 部署** - 一键部署

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

打开浏览器访问 `http://localhost:5000`，配置：
- Telegram Bot Token
- 音乐平台设置
- 下载路径等

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
├── downloaders/            # 下载器模块
│   ├── __init__.py
│   ├── base.py            # 基础下载器类
│   ├── netease.py         # 网易云音乐下载器
│   ├── youtube_music.py   # YouTube Music 下载器
│   ├── apple_music.py     # Apple Music 下载器
│   └── metadata.py        # 音频元数据管理
│
└── web/                    # Web 配置服务
    ├── __init__.py
    ├── app.py             # Flask 应用
    ├── tg_setup.py        # Telegram Session 生成器
    └── templates/
        ├── index.html     # 配置界面
        └── setup.html     # Session 生成界面
```

## 🔧 使用方法

### Telegram Bot 命令

- `/start` - 显示帮助
- `/status` - 查看状态

### 发送链接下载

直接发送音乐链接即可自动下载：

```
# 网易云音乐
https://music.163.com/song?id=123456
https://music.163.com/#/album?id=123456
https://music.163.com/#/playlist?id=123456

# YouTube Music
https://music.youtube.com/watch?v=xxxxx
https://music.youtube.com/playlist?list=xxxxx

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

1. 登录网易云音乐网页版
2. 按 F12 打开开发者工具
3. 在 Console 中输入 `document.cookie`
4. 复制结果到配置中

### YouTube / YouTube Music

1. 安装浏览器扩展 "Get cookies.txt LOCALLY"
2. 登录 YouTube
3. 导出 cookies.txt
4. 放到 `cookies/youtube_cookies.txt`

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

# Telegram Session
POST /tg/send_code        # 发送验证码
POST /tg/confirm_code     # 确认验证码
POST /tg/save_session     # 保存 Session
```

## 📄 License

MIT License
