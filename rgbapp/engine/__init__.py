"""Headless lighting engine. No Qt imports anywhere in this package."""

from .backend import (
    Backend,
    BackendError,
    ConnectionState,
    DeviceInfo,
    FakeBackend,
    OpenRGBBackend,
    ZoneInfo,
)
from .compositor import Compositor, LayerSpec
from .core import Engine
from .devices import DeviceGroup, build_groups
from .host import EngineHost, GroupSettings, HostStatus
from .loop import LoopConfig, LoopStats, RenderLoop
from .openrgb_manager import ManagerState, OpenRGBManager

__all__ = [
    "Backend", "BackendError", "ConnectionState", "DeviceInfo", "FakeBackend",
    "OpenRGBBackend", "ZoneInfo", "Compositor", "LayerSpec", "Engine", "DeviceGroup",
    "build_groups", "LoopConfig", "LoopStats", "RenderLoop", "ManagerState", "OpenRGBManager",
    "EngineHost", "GroupSettings", "HostStatus",
]
