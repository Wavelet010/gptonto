"""Parser Profile manager — YAML loading with caching."""

from __future__ import annotations

import logging
import yaml
from pathlib import Path
from typing import Any

from lightrag.edp.models import ParserProfile

logger = logging.getLogger(__name__)

# Built-in profiles directory (shipped with the package).
_BUILTIN_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"


class ProfileManager:
    """Load, cache, and list Parser Profile YAML files.

    Search order: *custom_dirs* (in order) → built-in ``profiles/``.
    """

    def __init__(self, custom_dirs: list[Path] | None = None) -> None:
        self._search_dirs: list[Path] = []
        if custom_dirs:
            for d in custom_dirs:
                p = Path(d).resolve()
                if p.is_dir():
                    self._search_dirs.append(p)
                else:
                    logger.warning("EDP profile dir does not exist, skipped: %s", p)
        self._search_dirs.append(_BUILTIN_PROFILES_DIR)
        self._cache: dict[str, ParserProfile] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_profiles(self) -> list[str]:
        """Return available profile names (without file extension)."""
        names: set[str] = set()
        for d in self._search_dirs:
            if not d.is_dir():
                continue
            for yf in d.glob("*.yml"):
                names.add(yf.stem)
            for yf in d.glob("*.yaml"):
                names.add(yf.stem)
        return sorted(names)

    def get_profile(self, name: str) -> ParserProfile:
        """Load a profile by *name* (without extension).

        Raises FileNotFoundError when the profile cannot be resolved.
        """
        if name in self._cache:
            return self._cache[name]

        path = self._resolve_path(name)
        if path is None:
            available = ", ".join(self.list_profiles()) or "(none)"
            raise FileNotFoundError(
                f"Parser profile '{name}' not found. Available: {available}"
            )
        profile = self._load_yaml(path)
        self._cache[name] = profile
        logger.info("Loaded EDP profile '%s' from %s", name, path)
        return profile

    def reload_profiles(self) -> None:
        """Clear the profile cache."""
        self._cache.clear()
        logger.info("EDP profile cache cleared")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_path(self, name: str) -> Path | None:
        """Find the first matching ``<name>.yml`` or ``<name>.yaml`` file."""
        for d in self._search_dirs:
            for ext in (".yml", ".yaml"):
                candidate = d / f"{name}{ext}"
                if candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _load_yaml(path: Path) -> ParserProfile:
        """Load and validate a YAML profile file."""
        with open(path, "r", encoding="utf-8") as fh:
            raw: dict[str, Any] = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise ValueError(f"Profile YAML must be a dict, got {type(raw)} in {path}")
        return ParserProfile.model_validate(raw)
