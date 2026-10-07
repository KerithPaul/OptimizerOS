"""Plugin-specific WordPress adapters. Fields never cross plugin boundaries."""

from app.connectors.wordpress.adapters.base import (
    AdapterFieldError,
    FieldWrite,
    SeoPlugin,
    wordpress_adapter,
)
from app.connectors.wordpress.adapters.core import CoreAdapter
from app.connectors.wordpress.adapters.rankmath import RankMathAdapter
from app.connectors.wordpress.adapters.yoast import YoastAdapter

__all__ = [
    "AdapterFieldError",
    "CoreAdapter",
    "FieldWrite",
    "RankMathAdapter",
    "SeoPlugin",
    "YoastAdapter",
    "wordpress_adapter",
]
