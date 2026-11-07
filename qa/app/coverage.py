"""需求覆盖聚合。"""

SUCCESS_STATUSES = {"success", "passed"}


def _status_for(cases: list[dict]) -> str:
    if not cases:
        return "gap"
    failed = sum(
        1
        for c in cases
        if c.get("status") not in (None, *SUCCESS_STATUSES)
        and not c.get("failure_kind")
    )
    infra = sum(1 for c in cases if c.get("failure_kind"))
    not_run = sum(1 for c in cases if c.get("status") is None)
    if failed:
        return "failed"
    if infra:
        return "infra"
    if not_run == len(cases):
        return "not_run"
    if not_run:
        return "partial"
    return "covered"


def compute_requirement_coverage(rows: list[dict]) -> dict:
    """rows 每个元素是「一个用例的最近结果」（没有结果时 status 为 None）。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["requirement_id"], []).append(row)

    items = []
    for requirement_id, cases in sorted(grouped.items()):
        case_count = len(cases)
        executed = sum(1 for c in cases if c.get("status") is not None)
        passed = sum(1 for c in cases if c.get("status") in SUCCESS_STATUSES)
        failed = sum(
            1
            for c in cases
            if c.get("status") not in (None, *SUCCESS_STATUSES)
            and not c.get("failure_kind")
        )
        infra = sum(1 for c in cases if c.get("failure_kind"))
        not_run = case_count - executed
        feature_refs = sorted(
            {
                (str(c["feature_id"]), c["feature_name"])
                for c in cases
                if c.get("feature_id") and c.get("feature_name")
            },
            key=lambda item: (item[1], item[0]),
        )
        items.append(
            {
                "requirement_id": requirement_id,
                "features": [name for _id, name in feature_refs],
                "feature_ids": [fid for fid, _name in feature_refs],
                "systems": sorted(
                    {c["system_name"] for c in cases if c.get("system_name")}
                ),
                "case_count": case_count,
                "executed": executed,
                "passed": passed,
                "failed": failed,
                "infra": infra,
                "not_run": not_run,
                "coverage_rate": executed / case_count if case_count else 0,
                "pass_rate": passed / executed if executed else None,
                "status": _status_for(cases),
                "last_run_at": max(
                    (c["last_run_at"] for c in cases if c.get("last_run_at")),
                    default=None,
                ),
            }
        )

    summary = {"total": len(items)}
    for status in ("covered", "partial", "not_run", "infra", "failed", "gap"):
        summary[status] = sum(1 for item in items if item["status"] == status)
    return {"summary": summary, "requirements": items}
