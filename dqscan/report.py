"""Excel report writer for dqscan.

Builds a single .xlsx workbook from a RunResult: a run summary sheet, a
needs-calibration sheet, a dormant-rules sheet, and one detail sheet per
firing rule.
"""

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.worksheet import Worksheet

from dqscan.models import RunResult, RuleOutcome, ScanMeta

_BOLD = Font(bold=True)

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_INVALID_SHEET_CHARS = set('[]:*?/\\')


def write_report(run_result: RunResult, out_path: str) -> None:
    wb = Workbook()
    summary_ws = wb.active
    summary_ws.title = "Run summary"
    _write_summary_sheet(summary_ws, run_result)

    calibration_ws = wb.create_sheet("Needs calibration")
    _write_calibration_sheet(calibration_ws, run_result)

    dormant_ws = wb.create_sheet("Dormant rules")
    _write_dormant_sheet(dormant_ws, run_result)

    used_names = {"Run summary", "Needs calibration", "Dormant rules"}
    for outcome in run_result.outcomes:
        if outcome.run_status not in ("active", "calibration"):
            continue
        if not outcome.count or not outcome.detail_rows:
            # count > 0 but no detail rows means the table changed between
            # the count and detail queries (both are live, concurrent-write
            # data) — nothing to show, not a bug.
            continue
        sheet_name = _sanitize_sheet_name(outcome.rule.id, used_names)
        used_names.add(sheet_name)
        detail_ws = wb.create_sheet(sheet_name)
        _write_detail_sheet(detail_ws, outcome, run_result.meta)

    wb.save(out_path)


def _severity_sort_key(severity: str) -> int:
    return _SEVERITY_ORDER.get(severity, len(_SEVERITY_ORDER))


def _summary_row_sort_key(outcome: RuleOutcome) -> tuple:
    has_count = outcome.count is not None
    return (
        _severity_sort_key(outcome.rule.severity),
        0 if has_count else 1,
        -(outcome.count or 0),
    )


def _write_summary_sheet(ws: Worksheet, run_result: RunResult) -> None:
    meta: ScanMeta = run_result.meta
    labels = [
        ("Window start", meta.from_date),
        ("Window end", meta.to_date),
        ("Rows scanned", meta.rows_scanned),
        ("Distinct showtimes scanned", meta.distinct_showtimes),
        ("Distinct crawl days", meta.distinct_crawl_days),
        ("Run duration (s)", meta.duration_seconds),
        ("Database schema", meta.schema),
        ("Rule pack version", meta.pack_version),
    ]
    if meta.extent_note:
        labels.append(("Note", meta.extent_note))
    row = 1
    for label, value in labels:
        ws.cell(row=row, column=1, value=label).font = _BOLD
        ws.cell(row=row, column=2, value=value)
        row += 1

    row += 1
    header_row = row
    headers = [
        "Rule ID",
        "Name",
        "Class",
        "Severity",
        "Status",
        "Findings",
        "% of rows scanned",
        "Note",
    ]
    for col, header in enumerate(headers, start=1):
        ws.cell(row=header_row, column=col, value=header).font = _BOLD

    rows = [
        o
        for o in run_result.outcomes
        if o.run_status in ("active", "error")
    ]
    rows.sort(key=_summary_row_sort_key)

    row = header_row + 1
    for outcome in rows:
        rule = outcome.rule
        if outcome.run_status == "error":
            findings = "ERROR"
        else:
            findings = outcome.count
        percentage = (
            f"{outcome.percentage * 100:.2f}%"
            if outcome.percentage is not None
            else ""
        )
        if outcome.error:
            note = outcome.error
        elif outcome.needs_calibration:
            note = rule.note
        else:
            note = rule.note or ""
        ws.cell(row=row, column=1, value=rule.id)
        ws.cell(row=row, column=2, value=rule.name)
        ws.cell(row=row, column=3, value=rule.rule_class)
        ws.cell(row=row, column=4, value=rule.severity)
        ws.cell(row=row, column=5, value=outcome.run_status)
        ws.cell(row=row, column=6, value=findings)
        ws.cell(row=row, column=7, value=percentage)
        ws.cell(row=row, column=8, value=note)
        row += 1

    ws.freeze_panes = f"A{header_row + 1}"
    _autofit(ws)


def _write_calibration_sheet(ws: Worksheet, run_result: RunResult) -> None:
    headers = ["Rule ID", "Name", "Severity", "Findings", "% of rows scanned", "Note"]
    for col, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=header).font = _BOLD

    rows = [
        o
        for o in run_result.outcomes
        if o.run_status == "calibration" or o.needs_calibration
    ]
    rows.sort(key=lambda o: -(o.count or 0))

    row = 2
    for outcome in rows:
        rule = outcome.rule
        percentage = (
            f"{outcome.percentage * 100:.2f}%"
            if outcome.percentage is not None
            else ""
        )
        ws.cell(row=row, column=1, value=rule.id)
        ws.cell(row=row, column=2, value=rule.name)
        ws.cell(row=row, column=3, value=rule.severity)
        ws.cell(row=row, column=4, value=outcome.count)
        ws.cell(row=row, column=5, value=percentage)
        ws.cell(row=row, column=6, value=rule.note or "")
        row += 1

    ws.freeze_panes = "A2"
    _autofit(ws)


def _write_dormant_sheet(ws: Worksheet, run_result: RunResult) -> None:
    headers = ["Rule ID", "Name", "Class", "Severity", "Reason"]
    for col, header in enumerate(headers, start=1):
        ws.cell(row=1, column=col, value=header).font = _BOLD

    rows = [o for o in run_result.outcomes if o.run_status == "dormant"]

    row = 2
    for outcome in rows:
        rule = outcome.rule
        ws.cell(row=row, column=1, value=rule.id)
        ws.cell(row=row, column=2, value=rule.name)
        ws.cell(row=row, column=3, value=rule.rule_class)
        ws.cell(row=row, column=4, value=rule.severity)
        ws.cell(row=row, column=5, value=outcome.dormant_reason)
        row += 1

    ws.freeze_panes = "A2"
    _autofit(ws)


def _write_detail_sheet(ws: Worksheet, outcome: RuleOutcome, meta: ScanMeta) -> None:
    header_row = 1
    if outcome.truncated:
        note = (
            f"Showing {len(outcome.detail_rows)} of {outcome.count} — "
            f"listing capped at {meta.detail_row_cap} rows"
        )
        cell = ws.cell(row=1, column=1, value=note)
        cell.font = _BOLD
        header_row = 2

    columns = list(outcome.detail_rows[0].keys())
    for col, column_name in enumerate(columns, start=1):
        ws.cell(row=header_row, column=col, value=column_name).font = _BOLD

    row = header_row + 1
    for detail_row in outcome.detail_rows:
        for col, column_name in enumerate(columns, start=1):
            ws.cell(row=row, column=col, value=detail_row.get(column_name))
        row += 1

    ws.freeze_panes = f"A{header_row + 1}"
    _autofit(ws)


def _sanitize_sheet_name(rule_id: str, used_names: set[str]) -> str:
    name = "".join(c for c in rule_id if c not in _INVALID_SHEET_CHARS)
    name = name[:31] or "rule"

    if name not in used_names:
        return name

    suffix = 1
    while True:
        candidate_suffix = f"_{suffix}"
        candidate = name[: 31 - len(candidate_suffix)] + candidate_suffix
        if candidate not in used_names:
            return candidate
        suffix += 1


def _autofit(ws: Worksheet) -> None:
    for column_cells in ws.columns:
        max_len = max((len(str(cell.value)) for cell in column_cells if cell.value is not None), default=0)
        letter = column_cells[0].column_letter
        ws.column_dimensions[letter].width = min(max_len + 2, 60)
