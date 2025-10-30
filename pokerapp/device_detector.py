"""Device detection and responsive layout utilities."""

from enum import Enum
from typing import Optional
from dataclasses import dataclass


class DeviceType(Enum):
    """Device categories based on screen width."""

    DESKTOP = "desktop"  # >768px
    TABLET = "tablet"  # 481-768px
    MOBILE = "mobile"  # <481px
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DeviceProfile:
    """Device characteristics for UI adaptation."""

    device_type: DeviceType
    max_line_length: int  # Characters per line
    emoji_size_multiplier: float  # Emoji scaling factor
    button_height: int  # Minimum touch target (px)
    stack_buttons: bool  # Vertical button layout
    show_full_labels: bool  # Full text vs icons


class DeviceDetector:
    """Detect device type from Telegram user context."""

    PROFILES = {
        DeviceType.MOBILE: DeviceProfile(
            device_type=DeviceType.MOBILE,
            max_line_length=30,
            emoji_size_multiplier=1.5,
            button_height=44,
            stack_buttons=True,
            show_full_labels=False,
        ),
        DeviceType.TABLET: DeviceProfile(
            device_type=DeviceType.TABLET,
            max_line_length=50,
            emoji_size_multiplier=1.2,
            button_height=40,
            stack_buttons=False,
            show_full_labels=True,
        ),
        DeviceType.DESKTOP: DeviceProfile(
            device_type=DeviceType.DESKTOP,
            max_line_length=70,
            emoji_size_multiplier=1.0,
            button_height=36,
            stack_buttons=False,
            show_full_labels=True,
        ),
    }

    @classmethod
    def detect_device(
        cls,
        user_id: Optional[int] = None,
        chat_type: Optional[str] = None,
    ) -> DeviceProfile:
        """Detect device type from available context."""

        if chat_type == "private":
            return cls.PROFILES[DeviceType.MOBILE]

        return cls.PROFILES[DeviceType.DESKTOP]

    @classmethod
    def get_profile(cls, device_type: DeviceType) -> DeviceProfile:
        """Get profile for specific device type."""

        return cls.PROFILES.get(device_type, cls.PROFILES[DeviceType.DESKTOP])
