"""Filesystem plugin discovery.

Adding a provider means adding ``plugins/<name>/provider.py`` that exposes
``PROVIDERS: list[type[OSINTProvider]]``. Nothing in the core changes.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from app.core.config import get_settings
from app.core.enums import HealthState
from app.providers.base import OSINTProvider
from app.providers.types import ProviderCapabilities, ProviderHealth

logger = logging.getLogger(__name__)

#: Parent package plugin modules are registered under, so they can be imported,
#: monkeypatched and introspected by dotted name like any other module.
PLUGIN_NAMESPACE = "graphintel_plugins"


@dataclass
class RegisteredProvider:
    provider: OSINTProvider
    capabilities: ProviderCapabilities
    path: Path
    enabled: bool = True
    config: dict[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.provider.name


@dataclass
class LoadError:
    plugin: str
    message: str


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, RegisteredProvider] = {}
        self.errors: list[LoadError] = []

    # -- discovery -------------------------------------------------------------

    def discover(self, path: Path | None = None) -> ProviderRegistry:
        root = Path(path or get_settings().plugins_path)
        self._providers.clear()
        self.errors.clear()
        if not root.is_dir():
            logger.warning("plugins path %s does not exist", root)
            return self
        for module_file in self._candidates(root):
            # Namespaced by its path so plugins/social/mastodon and plugins/mastodon can
            # coexist without one shadowing the other in sys.modules.
            plugin_name = ".".join(module_file.parent.relative_to(root).parts)
            try:
                self._load_plugin(plugin_name, module_file)
            except Exception as exc:  # a broken plugin must not break startup
                self.errors.append(LoadError(plugin_name, f"{exc}\n{traceback.format_exc()}"))
                logger.exception("failed to load plugin %s", plugin_name)
        return self

    @staticmethod
    def _candidates(root: Path) -> list[Path]:
        """Every ``provider.py`` up to two levels deep.

        Two levels is what lets providers be grouped by domain -- plugins/social/mastodon,
        plugins/external/maigret -- without the registry, graph engine or frontend
        knowing anything about the grouping.
        """
        found: list[Path] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            if (entry / "provider.py").is_file():
                found.append(entry / "provider.py")
                continue
            for nested in sorted(entry.iterdir()):
                if (
                    nested.is_dir()
                    and not nested.name.startswith((".", "_"))
                    and (nested / "provider.py").is_file()
                ):
                    found.append(nested / "provider.py")
        return found

    @staticmethod
    def _ensure_namespace() -> None:
        """Register the parent package so plugin modules are importable by dotted name."""
        if PLUGIN_NAMESPACE in sys.modules:
            return
        package = ModuleType(PLUGIN_NAMESPACE)
        package.__path__ = []  # namespace package: submodules are loaded by file path
        sys.modules[PLUGIN_NAMESPACE] = package

    def _load_plugin(self, plugin_name: str, module_file: Path) -> None:
        self._ensure_namespace()
        module_name = f"{PLUGIN_NAMESPACE}.{plugin_name.replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, module_file)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot build import spec for {module_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        setattr(sys.modules[PLUGIN_NAMESPACE], plugin_name.replace('.', '_'), module)
        spec.loader.exec_module(module)
        classes = getattr(module, "PROVIDERS", None)
        if not classes:
            raise ImportError(f"{module_file} does not define PROVIDERS")
        for cls in classes:
            if not (isinstance(cls, type) and issubclass(cls, OSINTProvider)):
                raise TypeError(f"{cls!r} in {plugin_name} is not an OSINTProvider subclass")
            self.register(cls(), path=module_file.parent)

    def register(self, provider: OSINTProvider, path: Path | None = None) -> RegisteredProvider:
        if provider.name in self._providers:
            raise ValueError(f"duplicate provider name: {provider.name}")
        entry = RegisteredProvider(
            provider=provider,
            capabilities=provider.capabilities(),
            path=path or Path("<memory>"),
        )
        self._providers[provider.name] = entry
        return entry

    # -- access ----------------------------------------------------------------

    def get(self, name: str) -> RegisteredProvider | None:
        return self._providers.get(name)

    def all(self) -> list[RegisteredProvider]:
        return list(self._providers.values())

    def enabled(self) -> list[RegisteredProvider]:
        return [p for p in self._providers.values() if p.enabled]

    def names(self) -> list[str]:
        return list(self._providers)

    def set_enabled(self, name: str, enabled: bool) -> None:
        entry = self._providers.get(name)
        if entry is not None:
            entry.enabled = enabled

    def set_config(self, name: str, config: dict[str, object]) -> None:
        entry = self._providers.get(name)
        if entry is not None:
            entry.config = config

    def capable_of(self, target_type: str) -> list[RegisteredProvider]:
        return [p for p in self.enabled() if target_type in p.capabilities.accepts]

    async def health(self) -> list[ProviderHealth]:
        results: list[ProviderHealth] = []
        for entry in self._providers.values():
            if not entry.enabled:
                results.append(
                    ProviderHealth(name=entry.name, state=HealthState.DISABLED, detail="disabled")
                )
                continue
            try:
                results.append(await entry.provider.health_check())
            except Exception as exc:
                results.append(ProviderHealth.unavailable(entry.name, str(exc)))
        for err in self.errors:
            results.append(ProviderHealth.unavailable(err.plugin, err.message.splitlines()[0]))
        return results

    def __len__(self) -> int:
        return len(self._providers)


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry().discover()
    return _registry


def set_registry(registry: ProviderRegistry | None) -> None:
    global _registry
    _registry = registry
