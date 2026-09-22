"""Identity enumerations."""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    PASSWORD_RESET_REQUIRED = "PASSWORD_RESET_REQUIRED"
    SUSPENDED = "SUSPENDED"
    DEACTIVATED = "DEACTIVATED"


class DevicePlatform(StrEnum):
    ANDROID = "ANDROID"
    IOS = "IOS"
    WEB = "WEB"


class LoginIdentifier(StrEnum):
    """Which field a login attempt was resolved by.

    Recorded on audit entries so "who logged in from the site app" is
    answerable without inference.
    """

    EMAIL = "EMAIL"
    PHONE = "PHONE"


class SessionRevocationReason(StrEnum):
    LOGOUT = "LOGOUT"
    ROTATED = "ROTATED"
    REUSE_DETECTED = "REUSE_DETECTED"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    ADMIN_REVOKED = "ADMIN_REVOKED"
    DEVICE_REVOKED = "DEVICE_REVOKED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    EXPIRED = "EXPIRED"
