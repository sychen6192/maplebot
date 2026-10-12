"""怪物偵測器的工廠：依 vision.mob_detector 挑一種偵測器。

預設是描邊偵測（outline_mobs.py，零設定、換地圖直接能用）；另外三種是模板匹配
（template_mobs.py）、YOLO（yolo_mobs.py）、遠端推理（remote_mobs.py）。四種都實作
types.py 的 MobDetector 介面，決策層不知道用的是哪一種。

Mob、MobDetector、TemplateMobDetector 從這裡轉出去，既有的
`from maplebot.vision.mobs import Mob` 照樣能用。但 brain/ 要從 types.py 拿：
這個檔一 import 就會經由 template_mobs 把 cv2 拉進來。
"""
from .template_mobs import TemplateMobDetector
from .types import Mob, MobDetector

# 列進 __all__ 是為了轉出：Mob 在這個檔裡沒用到，不列 pyflakes 會當成多餘的 import
__all__ = ["Mob", "MobDetector", "TemplateMobDetector", "make_detector"]


def make_detector(vision_cfg, templates_dir: str, logger=None) -> MobDetector:
    if vision_cfg.mob_detector == "outline":
        from .outline_mobs import OutlineMobDetector

        return OutlineMobDetector(
            black_level=vision_cfg.outline_black_level,
            min_area=vision_cfg.outline_min_area,
            max_area=vision_cfg.outline_max_area,
            close_kernel=vision_cfg.outline_close_kernel,
            player_box=vision_cfg.outline_player_box,
            max_size=vision_cfg.outline_max_size,
            max_aspect=vision_cfg.outline_max_aspect,
            auto_scale=vision_cfg.outline_auto_scale,
        )
    if vision_cfg.mob_detector == "yolo":
        from .yolo_mobs import YoloMobDetector

        return YoloMobDetector(vision_cfg.yolo_model, vision_cfg.yolo_confidence,
                               device=vision_cfg.yolo_device,
                               imgsz=vision_cfg.yolo_imgsz or None)
    if vision_cfg.mob_detector == "remote":
        from .remote_mobs import RemoteMobDetector

        return RemoteMobDetector(
            vision_cfg.remote_endpoint,
            confidence=vision_cfg.yolo_confidence,
            timeout=vision_cfg.remote_timeout,
            jpeg_quality=vision_cfg.remote_jpeg_quality,
            max_width=vision_cfg.remote_max_width,
            logger=logger,
        )
    return TemplateMobDetector(templates_dir, vision_cfg.mob_match_threshold,
                               use_color=vision_cfg.mob_match_color)
