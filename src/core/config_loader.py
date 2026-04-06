from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


JsonDict = dict[str, Any]


DEFAULT_USER_CONFIG: JsonDict = {
    "refresh_interval_ms": 10000,
    "timezone_offset_hours": 0,
    "time_window_hours": 3,
    "entries_to_fetch": 90,
    "target_low": 70,
    "target_high": 180,
    "widget_width": 400,
    "widget_height": 280,
    "glucose_font_size": 18,
    "time_font_size": 12,
    "age_font_size": 10,
    "data_point_size": 6,
    "adaptive_dot_size": False,
    "show_delta": True,
    "show_float_glucose": True,
    "show_treatments": True,
    "treatments_to_fetch": 50,
    "default_insulin_type": "Humalog Lispro",
    "iob_dia_hours": 5.0,
    "iob_peak_minutes": 75,
    "iob_onset_minutes": 15,
    "gradient_interpolation": True,
    "header_pills": [],
    "appearance": {},
}


@dataclass(frozen=True)
class ConfigBundle:
    """Normalized config payload returned by the loader."""

    nightscout_url: str
    api_secret_hashed: str
    api_secret_raw: str
    settings: JsonDict

    def as_tuple(self) -> tuple[str, str, str, JsonDict]:
        return (
            self.nightscout_url,
            self.api_secret_hashed,
            self.api_secret_raw,
            self.settings,
        )


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> JsonDict:
    """Merge override into base and return a new dict."""
    result: JsonDict = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_initial_config(
    nightscout_url: str,
    api_secret: str,
    overrides: Mapping[str, Any] | None = None,
) -> JsonDict:
    """Build first-run config from a single canonical defaults source."""
    normalized_url = nightscout_url.strip().rstrip("/")
    base: JsonDict = {
        "nightscout_url": normalized_url,
        "api_secret": api_secret,
        **DEFAULT_USER_CONFIG,
    }
    if overrides:
        return _deep_merge(base, overrides)
    return base


def load_config_bundle(config_file: str) -> ConfigBundle:
    if not os.path.exists(config_file):
        raise FileNotFoundError(f"config.json not found at: {config_file}")

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"config.json is not valid JSON: {e}")

    url = config.get("nightscout_url", "").strip().rstrip("/")
    secret = config.get("api_secret", "").strip()

    if not url:
        raise ValueError("nightscout_url is missing or empty in config.json")
    if not secret:
        raise ValueError("api_secret is missing or empty in config.json")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"nightscout_url must start with http:// or https://, got: {url!r}")

    target_low = config.get("target_low", DEFAULT_USER_CONFIG["target_low"])
    target_high = config.get("target_high", DEFAULT_USER_CONFIG["target_high"])
    if not isinstance(target_low, (int, float)) or not isinstance(target_high, (int, float)):
        raise ValueError("target_low and target_high must be numbers")
    if target_low >= target_high:
        raise ValueError(f"target_low ({target_low}) must be less than target_high ({target_high})")

    hashed_secret = hashlib.sha1(secret.encode()).hexdigest()

    default_appearance: JsonDict = {
        "marker_outline_width": 1.5,
        "marker_outline_color": "#000000",
        "graph_line_width": 2,
        "graph_line_style": "solid",
        "graph_line_smooth": True,
        "show_y_label": True,
        "target_zone_opacity": 20,
        "grid_opacity": 0.3,
        "background_color": "#1a1a1a",
        "graph_background_opacity": 100,
        "transparency_enabled": True,
        "label_pill_opacity": 40,
        "colors": {
            "ui": {
                "main_glucose_text": "#ffffff",
                "time_label": "#cccccc",
                "age_label": "#999999",
                "close_button": "#ff4444",
                "close_button_hover": "#ff6666",
                "close_button_background": "rgba(0, 0, 0, 150)",
                "close_button_hover_background": "rgba(255, 68, 68, 200)",
                "widget_background": "#2a2a2a",
                "header_background": "#0d0d0d",
            },
            "graph": {
                "axis_lines": "#888888",
                "axis_text": "#cccccc",
                "axis_labels": "#cccccc",
                "current_time_line": "#888888",
                "main_line": "#a0a0a0",
                "background": "#1a1a1a",
            },
            "glucose_ranges": {
                "low": "#ff4444",
                "in_range": "#00d4aa",
                "high": "#ff8800",
            },
            "target_zones": {
                "low_line": "#ff4444",
                "high_line": "#ff8800",
                "target_fill": "#00d4aa",
            },
        },
    }

    appearance = _deep_merge(default_appearance, config.get("appearance", {}))

    settings: JsonDict = {
        "refresh_interval": max(
            5000,
            int(config.get("refresh_interval_ms", DEFAULT_USER_CONFIG["refresh_interval_ms"])),
        ),
        "timezone_offset": float(
            config.get("timezone_offset_hours", DEFAULT_USER_CONFIG["timezone_offset_hours"])
        ),
        "time_window_hours": max(
            0.25,
            float(config.get("time_window_hours", DEFAULT_USER_CONFIG["time_window_hours"])),
        ),
        "entries_to_fetch": max(
            10,
            min(500, int(config.get("entries_to_fetch", DEFAULT_USER_CONFIG["entries_to_fetch"]))),
        ),
        "target_low": target_low,
        "target_high": target_high,
        "widget_width": max(150, int(config.get("widget_width", DEFAULT_USER_CONFIG["widget_width"]))),
        "widget_height": max(
            100, int(config.get("widget_height", DEFAULT_USER_CONFIG["widget_height"]))
        ),
        "glucose_font_size": max(
            8, int(config.get("glucose_font_size", DEFAULT_USER_CONFIG["glucose_font_size"]))
        ),
        "time_font_size": max(
            6, int(config.get("time_font_size", DEFAULT_USER_CONFIG["time_font_size"]))
        ),
        "age_font_size": max(
            6, int(config.get("age_font_size", DEFAULT_USER_CONFIG["age_font_size"]))
        ),
        "data_point_size": max(
            2, int(config.get("data_point_size", DEFAULT_USER_CONFIG["data_point_size"]))
        ),
        "adaptive_dot_size": bool(
            config.get("adaptive_dot_size", DEFAULT_USER_CONFIG["adaptive_dot_size"])
        ),
        "show_delta": bool(config.get("show_delta", DEFAULT_USER_CONFIG["show_delta"])),
        "show_treatments": bool(
            config.get("show_treatments", DEFAULT_USER_CONFIG["show_treatments"])
        ),
        "treatments_to_fetch": max(
            1,
            min(
                500,
                int(config.get("treatments_to_fetch", DEFAULT_USER_CONFIG["treatments_to_fetch"])),
            ),
        ),
        "default_insulin_type": str(
            config.get("default_insulin_type", DEFAULT_USER_CONFIG["default_insulin_type"])
        ),
        "iob_dia_hours": max(
            2.0,
            min(
                12.0,
                float(config.get("iob_dia_hours", DEFAULT_USER_CONFIG["iob_dia_hours"])),
            ),
        ),
        "iob_peak_minutes": max(
            30,
            min(
                180,
                int(config.get("iob_peak_minutes", DEFAULT_USER_CONFIG["iob_peak_minutes"])),
            ),
        ),
        "iob_onset_minutes": max(
            0,
            min(
                60,
                int(config.get("iob_onset_minutes", DEFAULT_USER_CONFIG["iob_onset_minutes"])),
            ),
        ),
        "gradient_interpolation": bool(
            config.get("gradient_interpolation", DEFAULT_USER_CONFIG["gradient_interpolation"])
        ),
        "show_float_glucose": bool(
            config.get("show_float_glucose", DEFAULT_USER_CONFIG["show_float_glucose"])
        ),
        "header_pills": config.get("header_pills", DEFAULT_USER_CONFIG["header_pills"]),
        "appearance": appearance,
    }

    return ConfigBundle(
        nightscout_url=url,
        api_secret_hashed=hashed_secret,
        api_secret_raw=secret,
        settings=settings,
    )


def load_config(config_file: str) -> tuple[str, str, str, JsonDict]:
    """Backward-compatible tuple API used by existing callers."""
    return load_config_bundle(config_file).as_tuple()
