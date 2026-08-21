from __future__ import annotations

import os
import threading
from typing import Literal


_lock = threading.RLock()
_runtime_openai_api_key: str | None = None
_runtime_openai_api_key_source: Literal["application_memory", "user_environment"] | None = None


def _read_windows_user_openai_api_key() -> str | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _value_type = winreg.QueryValueEx(key, "OPENAI_API_KEY")
    except FileNotFoundError:
        return None
    normalized = str(value).strip()
    return normalized or None


def _write_windows_user_openai_api_key(api_key: str) -> None:
    if os.name != "nt":
        raise RuntimeError(
            "Persistent in-app API key storage is currently supported on Windows only. "
            "Set OPENAI_API_KEY in the backend environment instead."
        )
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, "OPENAI_API_KEY", 0, winreg.REG_SZ, api_key)


def _delete_windows_user_openai_api_key() -> None:
    if os.name != "nt":
        return
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, "OPENAI_API_KEY")
    except FileNotFoundError:
        pass


def set_runtime_openai_api_key(
    api_key: str,
    *,
    source: Literal["application_memory", "user_environment"] = "application_memory",
) -> None:
    value = str(api_key).strip()
    if not value:
        raise ValueError("OpenAI API key must not be blank")
    with _lock:
        global _runtime_openai_api_key, _runtime_openai_api_key_source
        _runtime_openai_api_key = value
        _runtime_openai_api_key_source = source


def set_persistent_openai_api_key(api_key: str) -> None:
    value = str(api_key).strip()
    if not value:
        raise ValueError("OpenAI API key must not be blank")
    _write_windows_user_openai_api_key(value)
    set_runtime_openai_api_key(value, source="user_environment")


def clear_runtime_openai_api_key() -> None:
    with _lock:
        global _runtime_openai_api_key, _runtime_openai_api_key_source
        _runtime_openai_api_key = None
        _runtime_openai_api_key_source = None


def clear_persistent_openai_api_key() -> None:
    persisted_value = _read_windows_user_openai_api_key()
    _delete_windows_user_openai_api_key()
    clear_runtime_openai_api_key()
    if persisted_value and os.getenv("OPENAI_API_KEY", "").strip() == persisted_value:
        os.environ.pop("OPENAI_API_KEY", None)


def get_openai_api_key() -> str | None:
    with _lock:
        if _runtime_openai_api_key:
            return _runtime_openai_api_key
    user_environment_value = _read_windows_user_openai_api_key()
    if user_environment_value:
        return user_environment_value
    value = os.getenv("OPENAI_API_KEY", "").strip()
    return value or None


def get_openai_api_key_status() -> dict[str, str | bool]:
    with _lock:
        if _runtime_openai_api_key:
            return {
                "configured": True,
                "source": _runtime_openai_api_key_source or "application_memory",
            }
    environment_value = os.getenv("OPENAI_API_KEY", "").strip()
    user_environment_value = _read_windows_user_openai_api_key()
    if user_environment_value:
        return {"configured": True, "source": "user_environment"}
    if environment_value:
        return {"configured": True, "source": "environment"}
    return {"configured": False, "source": "not_configured"}
