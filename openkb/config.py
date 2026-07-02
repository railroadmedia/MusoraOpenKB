from __future__ import annotations

import contextlib
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from openkb.locks import atomic_write_text, flock, funlock

logger = logging.getLogger(__name__)

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-5.4",
    "language": "en",
    "pageindex_threshold": 20,
}

# Default entity-type vocabulary. Overridable per-KB via the optional
# ``entity_types:`` config key (see ``resolve_entity_types``).
DEFAULT_ENTITY_TYPES: tuple[str, ...] = (
    "person", "organization", "place", "product", "work", "event", "other",
)

GLOBAL_CONFIG_DIR = Path.home() / ".config" / "openkb"
GLOBAL_CONFIG_PATH = GLOBAL_CONFIG_DIR / "global.yaml"
GLOBAL_CONFIG_LOCK_PATH = GLOBAL_CONFIG_DIR / "global.lock"


@contextlib.contextmanager
def _with_global_config_lock() -> Iterator[None]:
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with GLOBAL_CONFIG_LOCK_PATH.open("a+", encoding="utf-8") as fh:
        flock(fh, exclusive=True)
        try:
            yield
        finally:
            funlock(fh)


def _atomic_yaml_dump(path: Path, config: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        yaml.safe_dump(config, allow_unicode=True, sort_keys=True),
    )


def _load_global_config_unlocked() -> dict[str, Any]:
    if GLOBAL_CONFIG_PATH.exists():
        with GLOBAL_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def resolve_entity_types(config: dict) -> list[str]:
    """Resolve the effective entity-type list from a loaded config dict.

    If ``config["entity_types"]`` is a non-empty list, each string item is
    cleaned (lowercased, trimmed, restricted to ``[a-z0-9 _-]`` so a stray
    brace/punctuation can't leak into a prompt template or frontmatter value);
    non-string items (YAML nulls, numbers) are skipped. The cleaned list is
    de-duped (order preserving) and ``"other"`` is always appended when missing
    (it is the coercion fallback). Otherwise — key absent, not a list, empty,
    or fully malformed — :data:`DEFAULT_ENTITY_TYPES` is returned, so behavior
    is byte-identical to the default. A warning is logged only when
    ``entity_types`` was present-but-malformed.
    """
    raw = config.get("entity_types")
    if raw is None:
        return list(DEFAULT_ENTITY_TYPES)
    if not isinstance(raw, list):
        logger.warning(
            "config: 'entity_types' must be a list of strings, got %s — "
            "falling back to the default entity types.",
            type(raw).__name__,
        )
        return list(DEFAULT_ENTITY_TYPES)
    cleaned: list[str] = []
    for x in raw:
        if not isinstance(x, str):
            continue  # skip YAML nulls/numbers (str(None) would become "none")
        s = re.sub(r"[^a-z0-9 _-]+", "", x.strip().lower()).strip()
        if s and s not in cleaned:
            cleaned.append(s)
    if not cleaned:
        logger.warning(
            "config: 'entity_types' was present but yielded no usable values — "
            "falling back to the default entity types.",
        )
        return list(DEFAULT_ENTITY_TYPES)
    if "other" not in cleaned:
        cleaned.append("other")
    return cleaned


@dataclass(frozen=True)
class HierarchyConfig:
    """Effective sizing/behavior settings for bottom-up ``distill`` (Pass 2).

    Depth is *dynamic* — it falls out of ``target_fanout`` and the leaf count —
    so ``max_depth`` is only a safety backstop. The two invariants (always one
    root file, always >= 2 layers) are enforced in the engine, not here.
    """
    target_fanout: int = 8
    min_fanout: int = 4
    max_fanout: int = 12
    max_depth: int = 6
    node_summary_tokens: int = 600      # soft target for each pathway summary
    node_summary_hard_cap: int = 1000   # hard cap
    sideways_links: bool = True
    sideways_links_max: int = 5
    sideways_method: str = "llm"        # "llm" | "embedding"


DEFAULT_HIERARCHY = HierarchyConfig()
_VALID_SIDEWAYS_METHODS = frozenset({"llm", "embedding"})


def _pos_int(raw: dict, key: str, default: int) -> int:
    """Read a positive int from a mapping, warning + defaulting on bad values."""
    if key not in raw:
        return default
    val = raw[key]
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        logger.warning(
            "config: 'hierarchy.%s' must be a positive integer, got %r — "
            "using default %d.", key, val, default,
        )
        return default
    return val


def resolve_hierarchy(config: dict) -> HierarchyConfig:
    """Resolve the optional ``hierarchy:`` config block into a HierarchyConfig.

    Absent or non-mapping → all defaults (byte-identical behavior). Each key is
    validated independently and falls back to its default with a warning. The
    fan-out band is repaired to satisfy ``min_fanout <= target_fanout <=
    max_fanout``; if the supplied values invert that ordering the whole band
    falls back to defaults (a partial repair would silently change intent).
    """
    raw = config.get("hierarchy")
    if raw is None:
        return DEFAULT_HIERARCHY
    if not isinstance(raw, dict):
        logger.warning(
            "config: 'hierarchy' must be a mapping, got %s — using defaults.",
            type(raw).__name__,
        )
        return DEFAULT_HIERARCHY

    d = DEFAULT_HIERARCHY
    target = _pos_int(raw, "target_fanout", d.target_fanout)
    lo = _pos_int(raw, "min_fanout", d.min_fanout)
    hi = _pos_int(raw, "max_fanout", d.max_fanout)
    if not (lo <= target <= hi):
        logger.warning(
            "config: hierarchy fan-out band must satisfy min <= target <= max "
            "(got min=%s target=%s max=%s) — falling back to defaults %s/%s/%s.",
            lo, target, hi, d.min_fanout, d.target_fanout, d.max_fanout,
        )
        lo, target, hi = d.min_fanout, d.target_fanout, d.max_fanout

    summary_tokens = _pos_int(raw, "node_summary_tokens", d.node_summary_tokens)
    hard_cap = _pos_int(raw, "node_summary_hard_cap", d.node_summary_hard_cap)
    if hard_cap < summary_tokens:
        logger.warning(
            "config: 'hierarchy.node_summary_hard_cap' (%d) < node_summary_tokens "
            "(%d) — raising the cap to the target.", hard_cap, summary_tokens,
        )
        hard_cap = summary_tokens

    sideways = raw.get("sideways_links", d.sideways_links)
    if not isinstance(sideways, bool):
        logger.warning(
            "config: 'hierarchy.sideways_links' must be a boolean, got %r — "
            "using default %s.", sideways, d.sideways_links,
        )
        sideways = d.sideways_links

    method = raw.get("sideways_method", d.sideways_method)
    if not isinstance(method, str) or method.strip().lower() not in _VALID_SIDEWAYS_METHODS:
        logger.warning(
            "config: 'hierarchy.sideways_method' must be one of %s, got %r — "
            "using default %r.", sorted(_VALID_SIDEWAYS_METHODS), method, d.sideways_method,
        )
        method = d.sideways_method
    else:
        method = method.strip().lower()

    return HierarchyConfig(
        target_fanout=target,
        min_fanout=lo,
        max_fanout=hi,
        max_depth=_pos_int(raw, "max_depth", d.max_depth),
        node_summary_tokens=summary_tokens,
        node_summary_hard_cap=hard_cap,
        sideways_links=sideways,
        sideways_links_max=_pos_int(raw, "sideways_links_max", d.sideways_links_max),
        sideways_method=method,
    )


def resolve_extra_headers(config: dict) -> dict[str, str]:
    """Resolve the optional ``extra_headers:`` config key into a str→str dict.

    Some LiteLLM providers need extra HTTP headers on every request (e.g.
    GitHub Copilot's ``Editor-Version`` IDE-auth headers). Users opt in via
    an ``extra_headers:`` mapping in config.yaml; the result is forwarded to
    LiteLLM's ``extra_headers`` parameter on all LLM calls.

    Values are stringified (YAML may parse version-like values as numbers).
    Entries with a non-string/empty key or a non-scalar value are skipped.
    A non-mapping ``extra_headers`` is ignored entirely. Warnings are logged
    only when the key was present but malformed.
    """
    raw = config.get("extra_headers")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "config: 'extra_headers' must be a mapping of header name to "
            "value, got %s — ignoring it.",
            type(raw).__name__,
        )
        return {}
    headers: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            logger.warning(
                "config: skipping 'extra_headers' entry with non-string "
                "or empty key: %r", key,
            )
            continue
        if value is None or not isinstance(value, (str, int, float, bool)):
            logger.warning(
                "config: skipping 'extra_headers' entry %r with "
                "non-scalar value: %r", key, value,
            )
            continue
        headers[key.strip()] = str(value)
    return headers


def resolve_timeout(config: dict) -> float | None:
    """Resolve the optional ``timeout:`` key to a finite positive number of seconds.

    Returns ``None`` (use LiteLLM's default) when absent or invalid; rejects
    bools and ``nan``/``inf``, warning when present but unusable.
    """
    raw = config.get("timeout")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        logger.warning(
            "config: 'timeout' must be a positive number of seconds, got %s — "
            "ignoring it.",
            type(raw).__name__,
        )
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "config: 'timeout' must be a positive number of seconds, got %r — "
            "ignoring it.",
            raw,
        )
        return None
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            "config: 'timeout' must be a finite positive number of seconds, got "
            "%s — ignoring it.",
            value,
        )
        return None
    return value


def resolve_litellm_settings(config: dict) -> dict[str, Any]:
    """Resolve the optional ``litellm:`` mapping of LiteLLM module settings.

    Values are forwarded verbatim (the user owns them); only the container shape
    is enforced — returns ``{}`` if absent or not a mapping, and drops non-string
    keys. ``cli._apply_litellm_settings`` applies them.
    """
    raw = config.get("litellm")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning(
            "config: 'litellm' must be a mapping of LiteLLM settings, got %s — "
            "ignoring it.",
            type(raw).__name__,
        )
        return {}
    settings: dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            logger.warning(
                "config: skipping 'litellm' entry with non-string key %r.", key
            )
            continue
        settings[key] = value
    return settings


# Process-wide extra headers for LLM requests, resolved from the active KB's
# config by the CLI entry points (cli._setup_llm_key). LLM call sites read it
# via get_extra_headers() so the value doesn't have to be threaded through
# every compile/agent call chain — mirroring how the API key is applied
# globally via litellm.api_key / provider env vars.
_runtime_extra_headers: dict[str, str] = {}


def set_extra_headers(headers: dict[str, str]) -> None:
    """Set the process-wide extra headers for LLM requests."""
    global _runtime_extra_headers
    _runtime_extra_headers = dict(headers)


def get_extra_headers() -> dict[str, str]:
    """Return a copy of the process-wide extra headers for LLM requests."""
    return dict(_runtime_extra_headers)


# Process-wide LLM request timeout (seconds), set from config by the CLI and
# read at the call sites via get_timeout(). None = use LiteLLM's default.
_runtime_timeout: float | None = None


def set_timeout(timeout: float | None) -> None:
    """Set the process-wide LLM request timeout in seconds; ``None`` clears it."""
    global _runtime_timeout
    _runtime_timeout = timeout


def get_timeout() -> float | None:
    """Return the process-wide LLM request timeout in seconds, or ``None``."""
    return _runtime_timeout


def get_timeout_extra_args() -> dict[str, float] | None:
    """Timeout as Agents-SDK ``ModelSettings.extra_args`` (it has no ``timeout``
    field), or ``None``. The LiteLLM provider forwards it to the completion call.
    """
    return {"timeout": _runtime_timeout} if _runtime_timeout is not None else None


def load_config(config_path: Path) -> dict[str, Any]:
    """Load YAML config from config_path, merged with DEFAULT_CONFIG.

    If the file does not exist, returns a copy of the defaults.
    """
    config = dict(DEFAULT_CONFIG)
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        config.update(data)
    return config


def save_config(config_path: Path, config: dict) -> None:
    """Persist config dict to YAML, creating parent directories as needed."""
    _atomic_yaml_dump(config_path, config)


def load_global_config() -> dict[str, Any]:
    """Load the global config from ~/.config/openkb/global.yaml."""
    return _load_global_config_unlocked()


def save_global_config(config: dict[str, Any]) -> None:
    """Save the global config to ~/.config/openkb/global.yaml."""
    with _with_global_config_lock():
        _atomic_yaml_dump(GLOBAL_CONFIG_PATH, config)


def register_kb(kb_path: Path) -> None:
    """Register a KB path in the global config's known_kbs list."""
    with _with_global_config_lock():
        gc = _load_global_config_unlocked()
        known = gc.get("known_kbs", [])
        resolved = str(kb_path.resolve())
        if resolved not in known:
            known.append(resolved)
            gc["known_kbs"] = known
        gc["default_kb"] = resolved
        _atomic_yaml_dump(GLOBAL_CONFIG_PATH, gc)
