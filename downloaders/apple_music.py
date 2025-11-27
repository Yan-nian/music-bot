#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple Music 音乐下载器
基于 gamdl 实现 Apple Music 音乐下载功能
"""

import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .base import BaseDownloader
from .metadata import MusicMetadataManager

logger = logging.getLogger(__name__)


class AppleMusicDownloader(BaseDownloader):
    """Apple Music 音乐下载器，基于 gamdl"""

    def __init__(self, config_manager=None):
        """
        初始化下载器

        Args:
            config_manager: 配置管理器实例
        """
        super().__init__(config_manager)
        self.name = "AppleMusic"

        # 加载配置
        self._load_config()

        # 检查 gamdl 是否可用
        self.gamdl_available = False
        self.gamdl_version = None
        self._check_gamdl_availability()

        # 元数据管理器
        self.metadata_manager = MusicMetadataManager()

    def _load_config(self):
        """加载配置"""
        if self.config_manager:
            self.cookies_path = self.config_manager.get("apple_music_cookies_path", "")
            self.output_dir = Path(
                self.config_manager.get("download_path", "./downloads/apple_music")
            )
            self.quality = self.config_manager.get("apple_music_quality", "aac-256")
            self.save_lyrics = self.config_manager.get("apple_music_save_lyrics", True)
            self.save_cover = self.config_manager.get("apple_music_save_cover", True)
            self.embed_lyrics = self.config_manager.get("apple_music_embed_lyrics", True)
        else:
            self.cookies_path = ""
            self.output_dir = Path("./downloads/apple_music")
            self.quality = "aac-256"
            self.save_lyrics = True
            self.save_cover = True
            self.embed_lyrics = True

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _check_gamdl_availability(self):
        """检查 gamdl 是否可用"""
        try:
            result = subprocess.run(
                ["gamdl", "--version"], capture_output=True, text=True, check=True
            )
            self.gamdl_available = True
            self.gamdl_version = result.stdout.strip()
            logger.info(f"✅ gamdl 可用，版本: {self.gamdl_version}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            self.gamdl_available = False
            logger.warning("⚠️ gamdl 未安装或不可用")
            logger.info("💡 请安装 gamdl: pip install gamdl")

    @staticmethod
    def parse_url(url: str) -> Optional[Dict[str, str]]:
        """
        解析 Apple Music URL

        支持的 URL 格式:
        - https://music.apple.com/cn/album/song-name/1234567890?i=1234567890 (单曲)
        - https://music.apple.com/cn/album/album-name/1234567890 (专辑)
        - https://music.apple.com/cn/playlist/playlist-name/pl.1234567890 (播放列表)
        - https://music.apple.com/cn/artist/artist-name/1234567890 (艺术家)

        Returns:
            解析结果字典，包含 type, id, country 等
        """
        if not url:
            return None

        try:
            parsed = urlparse(url)

            # 验证域名
            if "music.apple.com" not in parsed.netloc:
                return None

            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) < 2:
                return None

            # 解析国家/地区代码
            country = path_parts[0] if len(path_parts[0]) == 2 else "us"

            # 解析类型和 ID
            content_type = path_parts[1] if len(path_parts) > 1 else None
            content_id = None

            if content_type == "album":
                # 专辑或单曲
                if len(path_parts) >= 4:
                    content_id = path_parts[3]

                # 检查是否是单曲（带有 ?i= 参数）
                if parsed.query:
                    params = dict(p.split("=") for p in parsed.query.split("&") if "=" in p)
                    if "i" in params:
                        return {
                            "type": "song",
                            "id": params["i"],
                            "album_id": content_id,
                            "country": country,
                            "url": url,
                        }

                return {
                    "type": "album",
                    "id": content_id,
                    "country": country,
                    "url": url,
                }

            elif content_type == "playlist":
                if len(path_parts) >= 4:
                    content_id = path_parts[3]
                return {
                    "type": "playlist",
                    "id": content_id,
                    "country": country,
                    "url": url,
                }

            elif content_type == "artist":
                if len(path_parts) >= 4:
                    content_id = path_parts[3]
                return {
                    "type": "artist",
                    "id": content_id,
                    "country": country,
                    "url": url,
                }

        except Exception as e:
            logger.error(f"解析 Apple Music URL 失败: {e}")

        return None

    def is_supported_url(self, url: str) -> bool:
        """检查 URL 是否支持"""
        return self.parse_url(url) is not None

    async def download(
        self, url: str, progress_callback=None, **kwargs
    ) -> Dict[str, Any]:
        """
        下载音乐

        Args:
            url: Apple Music URL
            progress_callback: 进度回调函数
            **kwargs: 其他参数

        Returns:
            下载结果字典
        """
        if not self.gamdl_available:
            return {
                "success": False,
                "error": "gamdl 未安装，请先安装: pip install gamdl",
                "platform": "AppleMusic",
            }

        # 解析 URL
        url_info = self.parse_url(url)
        if not url_info:
            return {
                "success": False,
                "error": "无效的 Apple Music URL",
                "platform": "AppleMusic",
            }

        content_type = url_info["type"]

        try:
            if content_type == "song":
                return await self._download_song(url, url_info, progress_callback)
            elif content_type == "album":
                return await self._download_album(url, url_info, progress_callback)
            elif content_type == "playlist":
                return await self._download_playlist(url, url_info, progress_callback)
            else:
                return {
                    "success": False,
                    "error": f"不支持的内容类型: {content_type}",
                    "platform": "AppleMusic",
                }
        except Exception as e:
            logger.error(f"Apple Music 下载失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "platform": "AppleMusic",
            }

    async def _download_song(
        self, url: str, url_info: Dict, progress_callback=None
    ) -> Dict[str, Any]:
        """下载单曲"""
        logger.info(f"🎵 开始下载 Apple Music 单曲: {url}")

        if progress_callback:
            await self._safe_callback(
                progress_callback, {"phase": "starting", "message": "正在准备下载..."}
            )

        # 构建 gamdl 命令
        cmd = self._build_gamdl_command(url)

        try:
            # 执行下载
            result = await self._run_gamdl(cmd, progress_callback)

            if result["success"]:
                # 查找下载的文件
                files = self._find_downloaded_files()
                if files:
                    result["files"] = files
                    result["file_count"] = len(files)

                    # 应用元数据（如果需要）
                    for file_path in files:
                        await self._post_process_file(file_path)

            return result

        except Exception as e:
            logger.error(f"下载单曲失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "platform": "AppleMusic",
            }

    async def _download_album(
        self, url: str, url_info: Dict, progress_callback=None
    ) -> Dict[str, Any]:
        """下载专辑"""
        logger.info(f"💿 开始下载 Apple Music 专辑: {url}")

        if progress_callback:
            await self._safe_callback(
                progress_callback, {"phase": "starting", "message": "正在准备下载专辑..."}
            )

        # 构建 gamdl 命令
        cmd = self._build_gamdl_command(url)

        try:
            result = await self._run_gamdl(cmd, progress_callback)

            if result["success"]:
                files = self._find_downloaded_files()
                if files:
                    result["files"] = files
                    result["file_count"] = len(files)

                    for file_path in files:
                        await self._post_process_file(file_path)

            return result

        except Exception as e:
            logger.error(f"下载专辑失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "platform": "AppleMusic",
            }

    async def _download_playlist(
        self, url: str, url_info: Dict, progress_callback=None
    ) -> Dict[str, Any]:
        """下载播放列表"""
        logger.info(f"📋 开始下载 Apple Music 播放列表: {url}")

        if progress_callback:
            await self._safe_callback(
                progress_callback,
                {"phase": "starting", "message": "正在准备下载播放列表..."},
            )

        # 构建 gamdl 命令
        cmd = self._build_gamdl_command(url)

        try:
            result = await self._run_gamdl(cmd, progress_callback)

            if result["success"]:
                files = self._find_downloaded_files()
                if files:
                    result["files"] = files
                    result["file_count"] = len(files)

                    for file_path in files:
                        await self._post_process_file(file_path)

            return result

        except Exception as e:
            logger.error(f"下载播放列表失败: {e}")
            return {
                "success": False,
                "error": str(e),
                "platform": "AppleMusic",
            }

    def _build_gamdl_command(self, url: str) -> List[str]:
        """
        构建 gamdl 命令

        Args:
            url: Apple Music URL

        Returns:
            命令列表
        """
        cmd = ["gamdl", url]

        # 输出目录
        cmd.extend(["--output-path", str(self.output_dir)])

        # 音质设置
        if self.quality:
            cmd.extend(["--codec-song", self.quality])

        # 是否保存歌词
        if self.save_lyrics:
            cmd.append("--save-lyrics")

        # 是否嵌入歌词
        if self.embed_lyrics:
            cmd.append("--embed-lyrics")

        # 是否保存封面
        if self.save_cover:
            cmd.append("--save-cover")

        # cookies 文件
        if self.cookies_path and os.path.exists(self.cookies_path):
            cmd.extend(["--cookies-path", self.cookies_path])

        # 其他选项
        cmd.append("--no-config-file")

        logger.debug(f"gamdl 命令: {' '.join(cmd)}")
        return cmd

    async def _run_gamdl(
        self, cmd: List[str], progress_callback=None
    ) -> Dict[str, Any]:
        """
        运行 gamdl 命令

        Args:
            cmd: 命令列表
            progress_callback: 进度回调

        Returns:
            执行结果
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.output_dir),
            )

            stdout_lines = []
            stderr_lines = []

            # 实时读取输出
            async def read_stream(stream, lines_list, is_stdout=True):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if line_str:
                        lines_list.append(line_str)
                        logger.debug(
                            f"[gamdl {'stdout' if is_stdout else 'stderr'}] {line_str}"
                        )

                        # 解析进度信息
                        if progress_callback:
                            progress_info = self._parse_gamdl_progress(line_str)
                            if progress_info:
                                await self._safe_callback(progress_callback, progress_info)

            # 并行读取 stdout 和 stderr
            await asyncio.gather(
                read_stream(process.stdout, stdout_lines, True),
                read_stream(process.stderr, stderr_lines, False),
            )

            # 等待进程完成
            await process.wait()

            if process.returncode == 0:
                logger.info("✅ gamdl 下载完成")
                return {
                    "success": True,
                    "platform": "AppleMusic",
                    "message": "下载成功",
                }
            else:
                error_msg = "\n".join(stderr_lines) if stderr_lines else "未知错误"
                logger.error(f"❌ gamdl 下载失败: {error_msg}")
                return {
                    "success": False,
                    "platform": "AppleMusic",
                    "error": error_msg,
                }

        except asyncio.TimeoutError:
            logger.error("❌ gamdl 执行超时")
            return {
                "success": False,
                "platform": "AppleMusic",
                "error": "下载超时",
            }
        except Exception as e:
            logger.error(f"❌ gamdl 执行异常: {e}")
            return {
                "success": False,
                "platform": "AppleMusic",
                "error": str(e),
            }

    def _parse_gamdl_progress(self, line: str) -> Optional[Dict]:
        """
        解析 gamdl 输出的进度信息

        Args:
            line: 输出行

        Returns:
            进度信息字典
        """
        # 下载进度：Downloading: 50% |████████          | 5.0MB/10.0MB
        download_match = re.search(
            r"Downloading.*?(\d+)%.*?(\d+\.?\d*)\s*([KMGT]?B)\s*/\s*(\d+\.?\d*)\s*([KMGT]?B)",
            line,
            re.IGNORECASE,
        )
        if download_match:
            return {
                "phase": "downloading",
                "percentage": int(download_match.group(1)),
                "downloaded": f"{download_match.group(2)}{download_match.group(3)}",
                "total": f"{download_match.group(4)}{download_match.group(5)}",
            }

        # 处理中：Processing...
        if "processing" in line.lower():
            return {"phase": "processing", "message": "正在处理..."}

        # 获取信息：Getting...
        if "getting" in line.lower() or "fetching" in line.lower():
            return {"phase": "fetching", "message": "正在获取信息..."}

        # 歌曲信息
        song_match = re.search(r"Downloading:\s*(.+?)\s*-\s*(.+)", line)
        if song_match:
            return {
                "phase": "downloading",
                "artist": song_match.group(1).strip(),
                "title": song_match.group(2).strip(),
            }

        return None

    def _find_downloaded_files(self) -> List[Path]:
        """
        查找下载的文件

        Returns:
            文件路径列表
        """
        audio_extensions = {".m4a", ".mp3", ".flac", ".aac", ".alac"}
        files = []

        for file_path in self.output_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in audio_extensions:
                files.append(file_path)

        return sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)

    async def _post_process_file(self, file_path: Path):
        """
        后处理下载的文件

        Args:
            file_path: 文件路径
        """
        try:
            # 可以在这里添加额外的元数据处理
            logger.debug(f"后处理文件: {file_path}")
        except Exception as e:
            logger.warning(f"后处理文件失败 {file_path}: {e}")

    async def _safe_callback(self, callback, data):
        """安全调用回调函数"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.warning(f"回调函数执行失败: {e}")

    async def get_song_info(self, url: str) -> Optional[Dict[str, Any]]:
        """
        获取歌曲信息

        Args:
            url: Apple Music URL

        Returns:
            歌曲信息字典
        """
        url_info = self.parse_url(url)
        if not url_info:
            return None

        # 暂时返回基本信息
        return {
            "type": url_info["type"],
            "id": url_info["id"],
            "country": url_info["country"],
            "platform": "AppleMusic",
        }

    async def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        搜索音乐（暂不支持）

        Args:
            query: 搜索关键词
            limit: 结果数量限制

        Returns:
            搜索结果列表
        """
        logger.warning("Apple Music 搜索功能暂不支持")
        return []
