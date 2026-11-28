"""Keep exported spreadsheet text from being interpreted as a formula."""


def safe_cell(value):
    if isinstance(value, str) and (
        value.startswith(("\t", "\r")) or value.lstrip().startswith(("=", "+", "-", "@"))
    ):
        return "'" + value
    return value
