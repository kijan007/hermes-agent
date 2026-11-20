from __future__ import annotations

_CUSTOM_ID = "custom"
_CUSTOM_PREFIX = "custom:"


def _coerce_timeout(raw: object) -> float | None:
    try:
        timeout = float(raw)
    except (TypeError, ValueError):
        return None
    return timeout if timeout > 0 else None


def _normalize_custom_name(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "-")


def _is_custom_runtime_id(provider_id: str) -> bool:
    """True for the runtime ids a custom route reports: ``custom`` or ``custom:<name>``.

    Workers run with provider id ``custom`` even when the profile configures the route
    as ``custom_providers[].name = or``; timeout lookups against ``providers.custom``
    therefore found nothing and returned None (task t_7bb26ba4, run 61896 hang).
    """
    return provider_id == _CUSTOM_ID or provider_id.startswith(_CUSTOM_PREFIX)


def _custom_entries(config: dict) -> list[dict]:
    entries = config.get("custom_providers")
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []


def _entry_name(entry: dict) -> str:
    return _normalize_custom_name(entry.get("name") or entry.get("provider_key"))


def _entry_matches_model(entry: dict, model: str) -> bool:
    catalog = entry.get("models")
    names = {_normalize_custom_name(m) for m in catalog} if isinstance(catalog, (dict, list, tuple)) else set()
    if model in names:
        return True
    entry_model = _normalize_custom_name(entry.get("model") or entry.get("default_model"))
    return bool(entry_model) and entry_model == model


def _resolve_custom_provider_names(provider_id: str, model: str | None, config: dict) -> list[str]:
    """Map the runtime id ``custom`` / ``custom:<name>`` onto configured provider names.

    ``custom:<name>`` carries the name and resolves directly. A bare ``custom`` is only
    resolved when it is unambiguous: exactly one configured entry, or exactly one entry
    serving the session model. Ambiguous bare ids stay unresolved (no behavior change).
    """
    wanted = _normalize_custom_name(provider_id.partition(":")[2])
    if wanted:
        return [wanted]
    entries = _custom_entries(config)
    names = {_entry_name(entry) for entry in entries} - {""}
    if len(names) == 1:
        return [next(iter(names))]
    model_norm = _normalize_custom_name(model)
    if model_norm:
        matches = {_entry_name(entry) for entry in entries if _entry_matches_model(entry, model_norm)} - {""}
        if len(matches) == 1:
            return [next(iter(matches))]
    return []


def _lookup_provider(providers: dict, provider_id: str, model: str | None,
                     model_key: str, provider_key: str) -> float | None:
    provider_config = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    if not isinstance(provider_config, dict):
        return None
    model_config = _get_model_config(provider_config, model)
    if model_config is not None:
        timeout = _coerce_timeout(model_config.get(model_key))
        if timeout is not None:
            return timeout
    return _coerce_timeout(provider_config.get(provider_key))


def _custom_entry_timeout(config: dict, names: list[str], provider_key: str) -> float | None:
    """``request_timeout_seconds`` / ``stale_timeout_seconds`` written on a legacy
    ``custom_providers`` entry itself (the key is accepted by the config normalizer)."""
    for entry in _custom_entries(config):
        if _entry_name(entry) in names:
            timeout = _coerce_timeout(entry.get(provider_key))
            if timeout is not None:
                return timeout
    return None


def _configured_timeout(provider_id: str, model: str | None, model_key: str, provider_key: str) -> float | None:
    """Per-model ``providers.<id>.models.<model>.<model_key>`` wins over ``providers.<id>.<provider_key>``.

    A runtime id of ``custom`` / ``custom:<name>`` is first resolved to the configured
    provider name(s) and looked up under ``providers.<name>``, then against the
    ``custom_providers`` entry itself, so the configured value applies to custom routes.
    """
    if not provider_id:
        return None
    try:
        from hermes_cli.config import load_config_readonly
        config = load_config_readonly()
    except Exception:
        return None
    if not isinstance(config, dict):
        return None
    providers = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}
    if _is_custom_runtime_id(provider_id):
        names = _resolve_custom_provider_names(provider_id, model, config)
        for name in names:
            timeout = _lookup_provider(providers, name, model, model_key, provider_key)
            if timeout is not None:
                return timeout
        return _custom_entry_timeout(config, names, provider_key)
    return _lookup_provider(providers, provider_id, model, model_key, provider_key)


def get_provider_request_timeout(provider_id: str, model: str | None = None) -> float | None:
    """Return a configured provider request timeout in seconds, if any."""
    return _configured_timeout(provider_id, model, "timeout_seconds", "request_timeout_seconds")


def get_provider_stale_timeout(provider_id: str, model: str | None = None) -> float | None:
    """Return a configured non-stream stale timeout in seconds, if any."""
    return _configured_timeout(provider_id, model, "stale_timeout_seconds", "stale_timeout_seconds")


def _get_model_config(provider_config: dict[str, object], model: str | None) -> dict[str, object] | None:
    if not model:
        return None
    models = provider_config.get("models", {})
    model_config = models.get(model, {}) if isinstance(models, dict) else {}
    return model_config if isinstance(model_config, dict) else None
