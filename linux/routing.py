"""Authenticated selection of Linux CFG files, without any hardware calls."""
import hashlib
from pathlib import Path

from .export_mapping import read_cfg


class RoutingFiles:
    def __init__(self, root):
        self.root = Path(root).resolve() if root else None

    def load(self, name, expected_sha=None):
        if self.root is None:
            raise ValueError('Linux 未配置路由目录')
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError('需要路由目录内的相对 CFG 路径')
        path = (self.root/name).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != '.cfg' or not path.is_file():
            raise ValueError('CFG 必须位于允许的 Linux 路由目录内')
        if path.stat().st_size > 2*1024*1024:
            raise ValueError('CFG 文件超过 2 MB')
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha is not None and digest != expected_sha:
            raise ValueError('CFG 已改变，请刷新列表并重新选择')
        rows = read_cfg(path)
        if path.read_bytes() != raw:
            raise ValueError('CFG 在读取时改变，请重新选择')
        return path, rows, digest

    def listing(self):
        if self.root is None or not self.root.is_dir():
            raise ValueError('Linux 路由目录不存在')
        items = []
        for path in self.root.rglob('*.cfg'):
            if len(items) >= 500:
                break
            try:
                relative = path.relative_to(self.root).as_posix()
                resolved, rows, digest = self.load(relative)
                items.append(dict(file=relative, path=str(resolved), channels=len(rows), sha256=digest))
            except (ValueError, OSError, UnicodeError):
                continue
        return dict(root=str(self.root), files=sorted(items, key=lambda r: r['file']))
