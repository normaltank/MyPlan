"""Retired historical entry point; use the verified browser harvesting modules."""

_MESSAGE = (
    "This script is retired. Use server.browser_batch_tool with a browser-verified "
    "plan; use server.faculty_extractor for offline rendered-HTML parsing and "
    "server.excel_export for audited records. Historical implementation is in Git."
)

if __name__ == "__main__":
    raise SystemExit(_MESSAGE)
raise RuntimeError(_MESSAGE)
