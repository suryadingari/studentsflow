import logging
import re


_DATABASE_URL = re.compile(r"(?i)postgres(?:ql)?(?:\+[a-z0-9_]+)?://[^\s'\"<>]+")
_SECRET_PARAMETER = re.compile(
    r"(?i)\b(password|passwd|pwd|passfile|token|secret|api[_-]?key)\b"
    r"(\s*=\s*)(?:'[^']*'|\"[^\"]*\"|[^\s,;]+)"
)


def configure_logging(level: str) -> None:
    """Configure concise, timestamped application logging."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
        force=True,
    )


def log_database_failure(error: Exception, *, operation: str) -> None:
    """Log actionable database diagnostics while redacting connection secrets."""
    original = getattr(error, "orig", error)
    diagnostic = getattr(original, "diag", None)
    detail = getattr(diagnostic, "message_primary", None) or str(original)
    detail = _DATABASE_URL.sub("[REDACTED DATABASE URL]", detail)
    detail = _SECRET_PARAMETER.sub(r"\1\2[REDACTED]", detail)[:400]
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    logging.getLogger("app.database").error(
        "PostgreSQL operation failed operation=%s exception_type=%s sqlstate=%s detail=%s",
        operation, type(error).__name__, sqlstate or "unavailable", detail or "no driver detail",
    )
