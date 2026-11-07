"""手填编号的统一拆分。"""

import re


def split_ids(raw: str | None) -> list[str]:
    """「1234, BUG-56；789」→ ['1234', 'BUG-56', '789']。"""
    if not raw:
        return []
    return [p for p in re.split(r"[,，;；\s]+", raw.strip()) if p]
