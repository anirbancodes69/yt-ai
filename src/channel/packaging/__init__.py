"""Title, description, tags, chapters, affiliate links and thumbnails per format."""

from channel.packaging.builder import PackagingError, affiliate_url, build_package
from channel.packaging.checks import PackageCheckReport, check_package
from channel.packaging.models import Package, PackagingConfig, load_packaging
from channel.packaging.pipeline import (
    PackageBlocked,
    package_video,
    preview_all,
    preview_package,
    require_package,
)

__all__ = [
    "Package",
    "PackageBlocked",
    "PackageCheckReport",
    "PackagingConfig",
    "PackagingError",
    "affiliate_url",
    "build_package",
    "check_package",
    "load_packaging",
    "package_video",
    "preview_all",
    "preview_package",
    "require_package",
]
