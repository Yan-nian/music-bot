#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
音乐元数据处理模块
为下载的音乐文件添加ID3标签和封面
与原项目 music_metadata.py 保持一致
"""
import sys
import logging
import io
from pathlib import Path
from typing import Dict, Optional, Union
import requests

# 配置日志
logger = logging.getLogger(__name__)


class MusicMetadataManager:
    """音乐元数据管理器"""
    
    def __init__(self):
        self.session = requests.Session()
        # 设置User-Agent避免被网站屏蔽
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        
        # 检查可用的音频标签库
        self.available_libraries = self._check_available_libraries()
        logger.info(f"🔧 可用的音频标签库: {', '.join(self.available_libraries) if self.available_libraries else '无'}")
    
    def _check_available_libraries(self) -> list:
        """检查可用的音频标签处理库"""
        available = []
        
        # 检查 mutagen (推荐)
        try:
            import mutagen  # noqa: F401  仅用于探测是否安装
            available.append('mutagen')
            logger.info("✅ 检测到 mutagen 库")
        except ImportError:
            logger.warning("⚠️ 未安装 mutagen 库")
        
        # 检查 eyed3 (MP3专用)
        try:
            import eyed3  # noqa: F401  仅用于探测是否安装
            available.append('eyed3')
            logger.info("✅ 检测到 eyed3 库")
        except ImportError:
            logger.warning("⚠️ 未安装 eyed3 库")
        
        # 检查 tinytag (只读)
        try:
            import tinytag  # noqa: F401  仅用于探测是否安装
            available.append('tinytag')
            logger.info("✅ 检测到 tinytag 库（只读）")
        except ImportError:
            logger.warning("⚠️ 未安装 tinytag 库")
        
        return available
    
    def add_metadata_to_file(
        self, 
        file_path: Union[str, Path], 
        metadata: Dict[str, str],
        cover_url: Optional[str] = None,
        cover_data: Optional[bytes] = None
    ) -> bool:
        """
        为音乐文件添加元数据
        
        Args:
            file_path: 音乐文件路径
            metadata: 元数据字典 {title, artist, album, album_artist, date, track_number, genre, disc_number, releasetime}
            cover_url: 专辑封面URL
            cover_data: 专辑封面数据（二进制）
            
        Returns:
            bool: 是否成功添加元数据
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            logger.error(f"❌ 文件不存在: {file_path}")
            return False
        
        # 记录详细的元数据信息（用于排查 Plex 刮削问题）
        self._log_metadata_details(file_path, metadata, cover_url)
        
        # 如果提供了 cover_url 但没有 cover_data，则下载封面
        if cover_url and not cover_data:
            cover_data = self._download_cover_image(cover_url)
        
        # 根据文件扩展名和可用库选择处理方法
        file_ext = file_path.suffix.lower()
        
        if 'mutagen' in self.available_libraries:
            return self._add_metadata_with_mutagen(file_path, metadata, cover_data)
        elif 'eyed3' in self.available_libraries and file_ext == '.mp3':
            return self._add_metadata_with_eyed3(file_path, metadata, cover_data)
        else:
            logger.warning(f"⚠️ 无可用的音频标签库处理 {file_ext} 文件")
            logger.info("💡 建议安装: pip install mutagen")
            return False
    
    def _log_metadata_details(
        self,
        file_path: Path,
        metadata: Dict[str, str],
        cover_url: Optional[str] = None
    ) -> None:
        """记录详细的元数据信息，用于排查问题"""
        # 检查关键字段是否缺失
        missing_fields = []
        empty_fields = []
        
        critical_fields = ['title', 'artist', 'album', 'album_artist']
        for field in critical_fields:
            value = metadata.get(field)
            if value is None:
                missing_fields.append(field)
            elif value == '' or (isinstance(value, str) and value.strip() == ''):
                empty_fields.append(field)
        
        # 记录元数据摘要（INFO级别，方便在日志中快速定位）
        title = metadata.get('title', '未知')
        artist = metadata.get('artist', '未知')
        album = metadata.get('album', '未知')
        album_artist = metadata.get('album_artist', '未知')
        track = metadata.get('track_number', '-')
        total = metadata.get('total_tracks', '-')
        date = metadata.get('date', '-')
        
        logger.info(f"📋 元数据摘要: {file_path.name}")
        logger.info(f"   标题={title} | 艺术家={artist} | 专辑={album}")
        logger.info(f"   专辑艺术家={album_artist} | 曲目={track}/{total} | 年份={date}")
        logger.info(f"   封面={'有' if cover_url else '无'}")
        
        # 警告缺失或空的关键字段
        if missing_fields:
            logger.warning(f"⚠️ 关键字段缺失: {', '.join(missing_fields)} - 可能影响Plex刮削")
        if empty_fields:
            logger.warning(f"⚠️ 关键字段为空: {', '.join(empty_fields)} - 可能影响Plex刮削")
        
        # 特别检查 album_artist（Plex 最依赖这个字段）
        if not metadata.get('album_artist') or metadata.get('album_artist', '').strip() == '':
            if metadata.get('artist'):
                logger.info(f"💡 album_artist为空，将使用artist作为fallback: {metadata.get('artist')}")
            else:
                logger.warning("⚠️ artist和album_artist都为空，Plex可能无法正确刮削此歌曲")
    
    def _add_metadata_with_mutagen(
        self, 
        file_path: Path, 
        metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> bool:
        """使用mutagen库添加元数据"""
        try:
            from mutagen import File
            
            logger.info(f"🎵 使用mutagen为文件添加元数据: {file_path.name}")
            
            # 尝试加载音频文件
            audio_file = File(str(file_path))
            
            if audio_file is None:
                logger.error(f"❌ mutagen无法识别音频文件: {file_path}")
                return False
            
            file_ext = file_path.suffix.lower()
            
            if file_ext == '.mp3':
                return self._add_metadata_mp3_mutagen(file_path, metadata, cover_data)
            elif file_ext == '.flac':
                return self._add_metadata_flac_mutagen(file_path, metadata, cover_data)
            elif file_ext in ['.m4a', '.mp4', '.aac']:
                return self._add_metadata_m4a_mutagen(file_path, metadata, cover_data)
            else:
                logger.warning(f"⚠️ mutagen暂不支持处理 {file_ext} 文件的元数据")
                return False
                
        except ImportError:
            logger.error("❌ mutagen库导入失败")
            return False
        except Exception as e:
            logger.error(f"❌ mutagen处理文件时出错: {e}")
            return False
    
    def _add_metadata_mp3_mutagen(
        self, 
        file_path: Path, 
        metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> bool:
        """使用mutagen为MP3文件添加ID3标签"""
        try:
            from mutagen.id3 import ID3NoHeaderError, ID3, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TCON, APIC, TPOS, TYER
            
            # 尝试加载现有的ID3标签
            try:
                tags = ID3(str(file_path))
            except ID3NoHeaderError:
                # 如果没有ID3标签，创建新的
                tags = ID3()
            
            # 添加基本元数据
            if metadata.get('title'):
                tags['TIT2'] = TIT2(encoding=3, text=metadata['title'])
                logger.debug(f"  添加标题: {metadata['title']}")
            
            if metadata.get('artist'):
                tags['TPE1'] = TPE1(encoding=3, text=metadata['artist'])
                logger.debug(f"  添加艺术家: {metadata['artist']}")
            
            if metadata.get('album'):
                tags['TALB'] = TALB(encoding=3, text=metadata['album'])
                logger.debug(f"  添加专辑: {metadata['album']}")
            
            if metadata.get('album_artist'):
                tags['TPE2'] = TPE2(encoding=3, text=metadata['album_artist'])
                logger.debug(f"  添加专辑艺术家: {metadata['album_artist']}")
            
            # 处理时间字段：支持同时写入年份和完整日期
            if metadata.get('date'):
                # 写入年份
                try:
                    tags['TYER'] = TYER(encoding=3, text=metadata['date'])
                    logger.debug(f"  添加年份(TYER): {metadata['date']}")
                except:
                    # 如果TYER不可用，使用TDRC写入年份
                    tags['TDRC'] = TDRC(encoding=3, text=metadata['date'])
                    logger.debug(f"  添加年份(TDRC): {metadata['date']}")
            
            if metadata.get('releasetime'):
                # 写入完整发布时间 (录音时间)
                tags['TDRC'] = TDRC(encoding=3, text=metadata['releasetime'])
                logger.debug(f"  添加录音时间(TDRC): {metadata['releasetime']}")
            
            if metadata.get('track_number'):
                track_str = str(metadata['track_number'])
                total_tracks = metadata.get('total_tracks', '')
                if total_tracks:
                    track_str = f"{track_str}/{total_tracks}"
                tags['TRCK'] = TRCK(encoding=3, text=track_str)
                logger.debug(f"  添加曲目编号: {track_str}")
            
            if metadata.get('genre'):
                tags['TCON'] = TCON(encoding=3, text=metadata['genre'])
                logger.debug(f"  添加流派: {metadata['genre']}")
            
            # 添加光盘编号
            if metadata.get('disc_number'):
                try:
                    disc_number = str(metadata['disc_number'])
                    tpos_value = f"{disc_number}/1"
                    tags['TPOS'] = TPOS(encoding=3, text=tpos_value)
                    logger.debug(f"  添加光盘编号: {tpos_value}")
                except Exception as e:
                    logger.warning(f"⚠️ 添加光盘编号失败: {e}")
            
            # 添加封面
            if cover_data:
                tags['APIC'] = APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,  # 封面图片
                    desc='Cover',
                    data=cover_data
                )
                logger.debug("  添加专辑封面")
            
            # 保存标签
            tags.save(str(file_path))
            
            # 验证写入结果
            self._verify_metadata_written(file_path, 'mp3', metadata, cover_data)
            
            logger.info(f"✅ 成功为MP3文件添加元数据: {file_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 为MP3文件添加元数据失败: {e}")
            import traceback
            logger.error(f"   详细错误: {traceback.format_exc()}")
            return False
    
    def _add_metadata_flac_mutagen(
        self, 
        file_path: Path, 
        metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> bool:
        """使用mutagen为FLAC文件添加Vorbis Comment标签"""
        try:
            from mutagen.flac import FLAC, Picture
            
            audio_file = FLAC(str(file_path))
            
            # 添加基本元数据
            if metadata.get('title'):
                audio_file['TITLE'] = metadata['title']
                logger.debug(f"  添加标题: {metadata['title']}")
            
            if metadata.get('artist'):
                audio_file['ARTIST'] = metadata['artist']
                logger.debug(f"  添加艺术家: {metadata['artist']}")
            
            if metadata.get('album'):
                audio_file['ALBUM'] = metadata['album']
                logger.debug(f"  添加专辑: {metadata['album']}")
            
            if metadata.get('album_artist'):
                audio_file['ALBUMARTIST'] = metadata['album_artist']
                logger.debug(f"  添加专辑艺术家: {metadata['album_artist']}")
            
            # 处理时间字段：支持同时写入年份和完整日期
            if metadata.get('date'):
                audio_file['DATE'] = metadata['date']
                logger.debug(f"  添加年份(DATE): {metadata['date']}")
            
            if metadata.get('releasetime'):
                audio_file['RELEASETIME'] = metadata['releasetime']
                audio_file['RELEASEDATE'] = metadata['releasetime']
                logger.debug(f"  添加录音时间(RELEASETIME): {metadata['releasetime']}")
            
            if metadata.get('track_number'):
                audio_file['TRACKNUMBER'] = str(metadata['track_number'])
                logger.info(f"  添加曲目编号: {metadata['track_number']}")
            
            if metadata.get('total_tracks'):
                audio_file['TOTALTRACKS'] = str(metadata['total_tracks'])
                audio_file['TRACKTOTAL'] = str(metadata['total_tracks'])
                logger.info(f"  添加总曲目数: {metadata['total_tracks']}")
            
            if metadata.get('genre'):
                audio_file['GENRE'] = metadata['genre']
                logger.debug(f"  添加流派: {metadata['genre']}")
            
            # 添加光盘编号
            if metadata.get('disc_number'):
                disc_number = str(metadata['disc_number'])
                audio_file['DISCNUMBER'] = disc_number
                audio_file['DISCTOTAL'] = '1'
                audio_file['TOTALDISCS'] = '1'
                audio_file['DISC'] = disc_number
                audio_file['PART'] = disc_number
                audio_file['PARTOFSET'] = f"{disc_number}/1"
                audio_file['PART_OF_SET'] = f"{disc_number}/1"
                logger.debug(f"  添加光盘编号: {disc_number}")
            
            # 添加封面
            if cover_data:
                picture = Picture()
                picture.type = 3  # 封面图片
                picture.mime = 'image/jpeg'
                picture.desc = 'Cover'
                picture.data = cover_data
                audio_file.clear_pictures()
                audio_file.add_picture(picture)
                logger.debug("  添加专辑封面")
            
            # 保存标签
            audio_file.save()
            
            # 验证写入结果
            self._verify_metadata_written(file_path, 'flac', metadata, cover_data)
            
            logger.info(f"✅ 成功为FLAC文件添加元数据: {file_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 为FLAC文件添加元数据失败: {e}")
            import traceback
            logger.error(f"   详细错误: {traceback.format_exc()}")
            return False
    
    def _add_metadata_m4a_mutagen(
        self, 
        file_path: Path, 
        metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> bool:
        """使用mutagen为M4A/AAC文件添加元数据"""
        try:
            from mutagen.mp4 import MP4, MP4Cover
            
            audio_file = MP4(str(file_path))
            
            # MP4/M4A 标签映射
            if metadata.get('title'):
                audio_file['\xa9nam'] = metadata['title']
                logger.debug(f"  添加标题: {metadata['title']}")
            
            if metadata.get('artist'):
                audio_file['\xa9ART'] = metadata['artist']
                logger.debug(f"  添加艺术家: {metadata['artist']}")
            
            if metadata.get('album'):
                audio_file['\xa9alb'] = metadata['album']
                logger.debug(f"  添加专辑: {metadata['album']}")
            
            if metadata.get('album_artist'):
                audio_file['aART'] = metadata['album_artist']
                logger.debug(f"  添加专辑艺术家: {metadata['album_artist']}")
            
            if metadata.get('date'):
                audio_file['\xa9day'] = metadata['date']
                logger.debug(f"  添加年份: {metadata['date']}")
            
            if metadata.get('track_number'):
                try:
                    track_num = int(metadata['track_number'])
                    total_tracks = int(metadata.get('total_tracks', 0)) if metadata.get('total_tracks') else 0
                    audio_file['trkn'] = [(track_num, total_tracks)]
                    logger.debug(f"  添加曲目编号: {track_num}/{total_tracks}")
                except (ValueError, TypeError):
                    pass
            
            if metadata.get('genre'):
                audio_file['\xa9gen'] = metadata['genre']
                logger.debug(f"  添加流派: {metadata['genre']}")
            
            # 添加光盘编号
            if metadata.get('disc_number'):
                try:
                    disc_num = int(metadata['disc_number'])
                    audio_file['disk'] = [(disc_num, 1)]
                    logger.debug(f"  添加光盘编号: {disc_num}")
                except (ValueError, TypeError):
                    pass
            
            # 添加封面
            if cover_data:
                audio_file['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
                logger.debug("  添加专辑封面")
            
            # 保存标签
            audio_file.save()
            
            # 验证写入结果
            self._verify_metadata_written(file_path, 'm4a', metadata, cover_data)
            
            logger.info(f"✅ 成功为M4A文件添加元数据: {file_path.name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 为M4A文件添加元数据失败: {e}")
            import traceback
            logger.error(f"   详细错误: {traceback.format_exc()}")
            return False
    
    def _add_metadata_with_eyed3(
        self, 
        file_path: Path, 
        metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> bool:
        """使用eyed3库为MP3文件添加元数据（备用方案）"""
        try:
            import eyed3  # noqa: F401  仅用于探测是否安装
            
            logger.info(f"🎵 使用eyed3为MP3文件添加元数据: {file_path.name}")
            
            # 加载音频文件
            audiofile = eyed3.load(str(file_path))
            
            if audiofile is None:
                audiofile = eyed3.load(str(file_path))
                if audiofile is None:
                    logger.error(f"❌ eyed3无法加载文件: {file_path}")
                    return False
            
            if audiofile.tag is None:
                audiofile.initTag()
            
            # 添加基本元数据
            if metadata.get('title'):
                audiofile.tag.title = metadata['title']
                logger.debug(f"  添加标题: {metadata['title']}")
            
            if metadata.get('artist'):
                audiofile.tag.artist = metadata['artist']
                logger.debug(f"  添加艺术家: {metadata['artist']}")
            
            if metadata.get('album'):
                audiofile.tag.album = metadata['album']
                logger.debug(f"  添加专辑: {metadata['album']}")
            
            if metadata.get('album_artist'):
                audiofile.tag.album_artist = metadata['album_artist']
                logger.debug(f"  添加专辑艺术家: {metadata['album_artist']}")
            
            if metadata.get('date'):
                try:
                    year = int(metadata['date'][:4])
                    audiofile.tag.recording_date = year
                    logger.debug(f"  添加年份: {year}")
                except (ValueError, TypeError):
                    logger.warning(f"⚠️ 无法解析年份: {metadata['date']}")
            
            if metadata.get('track_number'):
                try:
                    track_num = int(metadata['track_number'])
                    audiofile.tag.track_num = track_num
                    logger.debug(f"  添加曲目编号: {track_num}")
                except (ValueError, TypeError):
                    logger.warning(f"⚠️ 无法解析曲目编号: {metadata['track_number']}")
            
            if metadata.get('genre'):
                audiofile.tag.genre = metadata['genre']
                logger.debug(f"  添加流派: {metadata['genre']}")
            
            # 添加封面
            if cover_data:
                audiofile.tag.images.set(3, cover_data, "image/jpeg", "Cover")
                logger.debug("  添加专辑封面")
            
            # 保存标签
            audiofile.tag.save()
            logger.info(f"✅ 成功为MP3文件添加元数据: {file_path.name}")
            return True
            
        except ImportError:
            logger.error("❌ eyed3库导入失败")
            return False
        except Exception as e:
            logger.error(f"❌ eyed3处理文件时出错: {e}")
            return False
    
    def _download_cover_image(self, cover_url: str) -> Optional[bytes]:
        """下载专辑封面图片"""
        try:
            if not cover_url:
                return None
            
            logger.debug(f"🖼️ 下载专辑封面: {cover_url}")
            
            response = self.session.get(cover_url, timeout=30)
            response.raise_for_status()
            
            # 检查内容类型
            content_type = response.headers.get('content-type', '').lower()
            if not any(img_type in content_type for img_type in ['image/jpeg', 'image/jpg', 'image/png', 'image/']):
                logger.warning(f"⚠️ 不支持的图片格式: {content_type}")
                # 仍然尝试使用图片数据
            
            image_data = response.content
            original_size = len(image_data)
            
            # 检查图片大小，如果过大则尝试压缩
            max_size = 3 * 1024 * 1024  # 3MB 作为嵌入封面的合理限制
            if original_size > max_size:
                logger.info(f"🖼️ 专辑封面较大 ({original_size / 1024 / 1024:.1f}MB)，尝试压缩...")
                image_data = self._compress_image(image_data, max_size)
                
                if image_data is None:
                    logger.warning("⚠️ 专辑封面压缩失败，跳过添加")
                    return None
                    
                logger.info(f"✅ 封面压缩成功: {original_size / 1024:.0f}KB -> {len(image_data) / 1024:.0f}KB")
            
            logger.debug(f"✅ 成功获取专辑封面: {len(image_data)} 字节")
            return image_data
            
        except Exception as e:
            logger.warning(f"⚠️ 下载专辑封面失败: {e}")
            return None
    
    def _compress_image(self, image_data: bytes, max_size: int) -> Optional[bytes]:
        """压缩图片到指定大小以下
        
        Args:
            image_data: 原始图片数据
            max_size: 目标最大大小（字节）
            
        Returns:
            压缩后的图片数据，失败返回 None
        """
        try:
            from PIL import Image
            
            # 加载图片
            img = Image.open(io.BytesIO(image_data))
            
            # 转换为 RGB 模式（如果是 RGBA 或其他模式）
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # 获取原始尺寸
            original_width, original_height = img.size
            
            # 尝试不同的质量和尺寸进行压缩
            for quality in [85, 75, 60, 50, 40]:
                for scale in [1.0, 0.8, 0.6, 0.5, 0.4]:
                    # 缩放图片
                    if scale < 1.0:
                        new_width = int(original_width * scale)
                        new_height = int(original_height * scale)
                        # 限制最小尺寸为 500x500
                        new_width = max(new_width, 500)
                        new_height = max(new_height, 500)
                        resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    else:
                        resized_img = img
                    
                    # 压缩为 JPEG
                    output = io.BytesIO()
                    resized_img.save(output, format='JPEG', quality=quality, optimize=True)
                    compressed_data = output.getvalue()
                    
                    if len(compressed_data) <= max_size:
                        logger.debug(f"🖼️ 压缩参数: quality={quality}, scale={scale}, size={len(compressed_data)/1024:.0f}KB")
                        return compressed_data
            
            # 如果所有尝试都失败，返回最后一次压缩的结果（可能仍然过大）
            logger.warning(f"⚠️ 封面压缩后仍较大: {len(compressed_data)/1024:.0f}KB")
            return compressed_data if len(compressed_data) < len(image_data) else None
            
        except ImportError:
            logger.warning("⚠️ Pillow 库未安装，无法压缩图片")
            return None
        except Exception as e:
            logger.warning(f"⚠️ 图片压缩失败: {e}")
            return None
    
    def _verify_metadata_written(
        self,
        file_path: Path,
        file_type: str,
        expected_metadata: Dict[str, str],
        cover_data: Optional[bytes] = None
    ) -> None:
        """验证元数据是否正确写入文件
        
        Args:
            file_path: 文件路径
            file_type: 文件类型 (mp3/flac/m4a)
            expected_metadata: 期望写入的元数据
            cover_data: 封面数据
        """
        try:
            from mutagen import File
            
            audio_file = File(str(file_path))
            if audio_file is None:
                logger.warning(f"⚠️ 验证失败：无法读取文件 {file_path.name}")
                return
            
            # 读取写入后的元数据
            actual_metadata = {}
            has_cover = False
            
            if file_type == 'mp3':
                tags = audio_file.tags
                if tags:
                    actual_metadata['title'] = str(tags.get('TIT2', [''])[0]) if tags.get('TIT2') else ''
                    actual_metadata['artist'] = str(tags.get('TPE1', [''])[0]) if tags.get('TPE1') else ''
                    actual_metadata['album'] = str(tags.get('TALB', [''])[0]) if tags.get('TALB') else ''
                    actual_metadata['album_artist'] = str(tags.get('TPE2', [''])[0]) if tags.get('TPE2') else ''
                    has_cover = 'APIC:Cover' in tags or any(k.startswith('APIC') for k in tags.keys())
                    
            elif file_type == 'flac':
                actual_metadata['title'] = audio_file.get('TITLE', [''])[0] if audio_file.get('TITLE') else ''
                actual_metadata['artist'] = audio_file.get('ARTIST', [''])[0] if audio_file.get('ARTIST') else ''
                actual_metadata['album'] = audio_file.get('ALBUM', [''])[0] if audio_file.get('ALBUM') else ''
                actual_metadata['album_artist'] = audio_file.get('ALBUMARTIST', [''])[0] if audio_file.get('ALBUMARTIST') else ''
                has_cover = len(audio_file.pictures) > 0
                
            elif file_type == 'm4a':
                actual_metadata['title'] = audio_file.get('\xa9nam', [''])[0] if audio_file.get('\xa9nam') else ''
                actual_metadata['artist'] = audio_file.get('\xa9ART', [''])[0] if audio_file.get('\xa9ART') else ''
                actual_metadata['album'] = audio_file.get('\xa9alb', [''])[0] if audio_file.get('\xa9alb') else ''
                actual_metadata['album_artist'] = audio_file.get('aART', [''])[0] if audio_file.get('aART') else ''
                has_cover = 'covr' in audio_file
            
            # 验证关键字段
            verification_passed = True
            issues = []
            
            for field in ['title', 'artist', 'album', 'album_artist']:
                expected = expected_metadata.get(field, '')
                actual = actual_metadata.get(field, '')
                
                if expected and not actual:
                    issues.append(f"{field}:期望'{expected}',实际为空")
                    verification_passed = False
                elif expected and actual and expected != actual:
                    issues.append(f"{field}:期望'{expected}',实际'{actual}'")
                    verification_passed = False
            
            # 验证封面
            if cover_data and not has_cover:
                issues.append("封面:期望有,实际无")
                verification_passed = False
            
            if verification_passed:
                logger.info(f"✅ 元数据验证通过: {file_path.name}")
                logger.info(f"   写入结果: title={actual_metadata.get('title', '无')}, artist={actual_metadata.get('artist', '无')}, album={actual_metadata.get('album', '无')}, album_artist={actual_metadata.get('album_artist', '无')}, 封面={'有' if has_cover else '无'}")
            else:
                logger.warning(f"⚠️ 元数据验证失败: {file_path.name}")
                for issue in issues:
                    logger.warning(f"   - {issue}")
                    
        except Exception as e:
            logger.warning(f"⚠️ 元数据验证出错: {e}")
    
    def get_file_metadata(self, file_path: Union[str, Path]) -> Optional[Dict[str, str]]:
        """读取音乐文件的现有元数据"""
        file_path = Path(file_path)
        
        if not file_path.exists():
            logger.error(f"❌ 文件不存在: {file_path}")
            return None
        
        try:
            if 'mutagen' in self.available_libraries:
                return self._get_metadata_with_mutagen(file_path)
            elif 'tinytag' in self.available_libraries:
                return self._get_metadata_with_tinytag(file_path)
            else:
                logger.warning("⚠️ 无可用的音频标签读取库")
                return None
                
        except Exception as e:
            logger.error(f"❌ 读取文件元数据失败: {e}")
            return None
    
    def _get_metadata_with_mutagen(self, file_path: Path) -> Optional[Dict[str, str]]:
        """使用mutagen读取元数据"""
        try:
            from mutagen import File
            
            audio_file = File(str(file_path))
            if audio_file is None:
                return None
            
            metadata = {}
            
            # 根据文件类型读取不同的标签
            if hasattr(audio_file, 'tags') and audio_file.tags:
                tags = audio_file.tags
                
                # 尝试读取常见的标签字段
                for key, mutagen_keys in [
                    ('title', ['TIT2', 'TITLE', '\xa9nam']),
                    ('artist', ['TPE1', 'ARTIST', '\xa9ART']),
                    ('album', ['TALB', 'ALBUM', '\xa9alb']),
                    ('album_artist', ['TPE2', 'ALBUMARTIST', 'aART']),
                    ('date', ['TDRC', 'DATE', '\xa9day']),
                    ('track_number', ['TRCK', 'TRACKNUMBER', 'trkn']),
                    ('genre', ['TCON', 'GENRE', '\xa9gen'])
                ]:
                    for mutagen_key in mutagen_keys:
                        if mutagen_key in tags:
                            value = tags[mutagen_key]
                            if hasattr(value, 'text'):
                                metadata[key] = str(value.text[0]) if value.text else ''
                            elif isinstance(value, list):
                                metadata[key] = str(value[0]) if value else ''
                            else:
                                metadata[key] = str(value)
                            break
            
            return metadata if metadata else None
            
        except Exception as e:
            logger.error(f"❌ mutagen读取元数据失败: {e}")
            return None
    
    def _get_metadata_with_tinytag(self, file_path: Path) -> Optional[Dict[str, str]]:
        """使用tinytag读取元数据"""
        try:
            from tinytag import TinyTag
            
            tag = TinyTag.get(str(file_path))
            
            metadata = {
                'title': tag.title or '',
                'artist': tag.artist or '',
                'album': tag.album or '',
                'album_artist': tag.albumartist or '',
                'date': str(tag.year) if tag.year else '',
                'track_number': str(tag.track) if tag.track else '',
                'genre': tag.genre or ''
            }
            
            return metadata if any(metadata.values()) else None
            
        except Exception as e:
            logger.error(f"❌ tinytag读取元数据失败: {e}")
            return None
    
    def embed_lyrics(self, file_path: Union[str, Path], lyrics: str) -> bool:
        """
        将歌词嵌入到音频文件中
        
        Args:
            file_path: 音频文件路径
            lyrics: 歌词内容
            
        Returns:
            是否成功
        """
        file_path = Path(file_path)
        file_ext = file_path.suffix.lower()
        
        if 'mutagen' not in self.available_libraries:
            logger.warning("⚠️ 嵌入歌词需要 mutagen 库")
            return False
        
        try:
            if file_ext == '.mp3':
                from mutagen.id3 import ID3, USLT
                try:
                    tags = ID3(str(file_path))
                except:
                    tags = ID3()
                
                tags['USLT'] = USLT(encoding=3, lang='zho', desc='', text=lyrics)
                tags.save(str(file_path))
                logger.info("✅ 成功嵌入歌词到 MP3 文件")
                return True
                
            elif file_ext == '.flac':
                from mutagen.flac import FLAC
                audio = FLAC(str(file_path))
                audio['LYRICS'] = lyrics
                audio.save()
                logger.info("✅ 成功嵌入歌词到 FLAC 文件")
                return True
                
            elif file_ext in ['.m4a', '.mp4']:
                from mutagen.mp4 import MP4
                audio = MP4(str(file_path))
                audio['\xa9lyr'] = lyrics
                audio.save()
                logger.info("✅ 成功嵌入歌词到 M4A 文件")
                return True
            
            else:
                logger.warning(f"⚠️ 不支持嵌入歌词到 {file_ext} 文件")
                return False
                
        except Exception as e:
            logger.error(f"❌ 嵌入歌词失败: {e}")
            return False


def install_required_libraries():
    """安装音频元数据处理所需的库"""
    import subprocess
    
    print("🔧 安装音频元数据处理库...")
    
    libraries = ['mutagen', 'eyed3', 'tinytag']
    
    for lib in libraries:
        try:
            print(f"📦 安装 {lib}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', lib])
            print(f"✅ {lib} 安装成功")
        except subprocess.CalledProcessError as e:
            print(f"❌ {lib} 安装失败: {e}")
        except Exception as e:
            print(f"❌ 安装 {lib} 时出错: {e}")


if __name__ == "__main__":
    # 测试模块
    logging.basicConfig(level=logging.DEBUG)
    
    manager = MusicMetadataManager()
    
    # 示例元数据
    test_metadata = {
        'title': '测试歌曲',
        'artist': '测试艺术家',
        'album': '测试专辑',
        'album_artist': '测试专辑艺术家',
        'date': '2024',
        'track_number': '1',
        'genre': '流行'
    }
    
    print("🎵 音乐元数据管理器测试")
    print(f"可用库: {manager.available_libraries}")
    
    if not manager.available_libraries:
        print("⚠️ 没有可用的音频标签库")
        print("💡 运行以下命令安装:")
        print("pip install mutagen eyed3 tinytag")
    else:
        print("✅ 元数据管理器初始化成功")
