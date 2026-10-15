from dataclasses import dataclass
from enum import Enum


class ConnectionMode(str, Enum):
    WIFI = "Wi-Fi"
    USB = "USB"


class SessionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True)
class CameraControls:
    autofocus: bool = True
    focus: float = 0.0
    exposure: float = 0.0
    iso: int = 100
    white_balance_kelvin: int = 5000
    lens: str = "main"
    zoom: float = 1.0
    fps: int = 30


@dataclass(frozen=True)
class DeviceSession:
    status: SessionStatus = SessionStatus.DISCONNECTED
    mode: ConnectionMode = ConnectionMode.WIFI
    device_name: str | None = None
    error: str | None = None
