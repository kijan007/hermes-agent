"""Turn-result exit policy shared by CLI views and authority-owned workers.

Worker identity is supplied by the caller, not inferred from a daemon's environment.
"""

# Provider walls do not spend a Kanban task's retry budget.
TRANSIENT_PROVIDER_REASONS = frozenset({
    "rate_limit", "upstream_rate_limit", "billing", "overloaded", "server_error", "timeout",
})
# These failures need an operator change; the dispatcher parks the task immediately.
TERMINAL_PROVIDER_REASONS = frozenset({
    "auth", "auth_permanent", "model_not_found", "ssl_cert_verification", "upstream_blocked",
})


def turn_exit_code(
    result, *, kanban_worker: bool, credentials_rate_limited: bool = False,
    credentials_terminal: bool = False,
    transient_reasons=TRANSIENT_PROVIDER_REASONS, terminal_reasons=TERMINAL_PROVIDER_REASONS,
) -> int:
    """Preserve completion, interruption and retryable/terminal provider outcomes."""
    if not isinstance(result, dict):
        if credentials_rate_limited and kanban_worker:
            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
            return KANBAN_RATE_LIMIT_EXIT_CODE
        if credentials_terminal and kanban_worker:
            from hermes_cli.kanban_db import KANBAN_TERMINAL_PROVIDER_EXIT_CODE
            return KANBAN_TERMINAL_PROVIDER_EXIT_CODE
        return 1
    if result.get("interrupted"):
        return 130
    if kanban_worker:
        # A tool-guardrail halt (identical-call-streak / cycle) is a controlled stop, not a
        # task failure: the worker deliberately skipped a terminal board tool. It must exit
        # with the sentinel whether or not the turn is framed failed — the halt message is a
        # normal (non-failed) response, so without this check it would book 0 and read as a
        # protocol violation (re-dispatch). The sentinel lets the dispatcher park the card
        # ``blocked`` (retry_status stays ``blocked``, no re-dispatch, no ``crashed``).
        # ``guardrail`` metadata is stamped by ``turn_finalizer`` on the halt decision.
        if result.get("guardrail") or result.get("failure_reason") == "guardrail_halt":
            from hermes_cli.kanban_db import KANBAN_GUARDRAIL_HALT_EXIT_CODE
            return KANBAN_GUARDRAIL_HALT_EXIT_CODE
    if not (result.get("failed") or result.get("partial") or result.get("completed") is False):
        return 0
    if kanban_worker:
        reason = result.get("failure_reason")
        if reason in transient_reasons:
            from hermes_cli.kanban_db import KANBAN_RATE_LIMIT_EXIT_CODE
            return KANBAN_RATE_LIMIT_EXIT_CODE
        if reason in terminal_reasons:
            from hermes_cli.kanban_db import KANBAN_TERMINAL_PROVIDER_EXIT_CODE
            return KANBAN_TERMINAL_PROVIDER_EXIT_CODE
    return 1


def credential_failure_flags(exc) -> dict:
    """``turn_exit_code`` credential kwargs for an exception raised before any turn result exists.

    Same predicates as the one-shot CLI's ``_ensure_runtime_credentials``: a quota/rate-limit
    ``AuthError`` is transient, only an explicit re-authentication requirement is terminal. The
    cause chain is walked because agent construction wraps the resolver's error."""
    from hermes_cli.auth import AuthError, is_rate_limited_auth_error
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, AuthError):
            return {"credentials_rate_limited": is_rate_limited_auth_error(exc),
                    "credentials_terminal": bool(exc.relogin_required)}
        exc = exc.__cause__ or exc.__context__
    return {}
