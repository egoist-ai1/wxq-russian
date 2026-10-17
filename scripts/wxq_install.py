"""Locate the WXQ installation that the official launcher currently uses.

The only source is the launcher's `game-install-path.json`. No other file in the
launcher profile is opened (it also holds account tokens).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

INSTALL_PATH_FILE = ('os-launcher', 'Launcher', 'game-install-path.json')


class InstallError(ValueError):
    pass


def default_install_file() -> Path:
    appdata = os.environ.get('APPDATA')
    if not appdata:
        raise InstallError('APPDATA is not set; pass the launcher install-path json explicitly')
    return Path(appdata).joinpath(*INSTALL_PATH_FILE)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def require_install_structure(root: Path, *, full: bool = False) -> Path:
    """Check that `root` looks like a WXQ installation. `full` also needs the QtsVFS data folders."""
    root = Path(root)
    if not root.is_dir():
        raise InstallError(f'WXQ install folder does not exist: {root}')
    if not (root / 'WXQ.exe').is_file():
        raise InstallError(f'WXQ.exe not found in {root}')
    if not (root / 'WXQ_Data').is_dir():
        raise InstallError(f'WXQ_Data not found in {root}')
    if full:
        if not (root / 'WXQ_Data/Package').is_dir():
            raise InstallError(f'WXQ_Data\\Package not found in {root}')
        if not (root / 'QtsDataFolder').is_dir():
            raise InstallError(f'QtsDataFolder not found in {root}')
    return root.resolve()


def detect_active_install(install_file: Path | str | None = None) -> Path:
    """Return the single install path recorded by the launcher, or raise InstallError."""
    path = Path(install_file) if install_file is not None else default_install_file()
    if not path.is_file():
        raise InstallError(f'Launcher install-path file not found: {path}')
    try:
        data = json.loads(path.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise InstallError(f'Cannot read launcher install-path file: {path}') from error
    distinct: dict[str, str] = {}
    for text in _strings(data):
        text = text.strip()
        if text:
            distinct.setdefault(os.path.normcase(os.path.normpath(text)), text)
    if not distinct:
        raise InstallError(f'No install path recorded in {path}')
    if len(distinct) > 1:
        raise InstallError('Launcher records several different install paths, refusing to guess: ' + '; '.join(sorted(distinct.values())))
    return require_install_structure(Path(next(iter(distinct.values()))))
