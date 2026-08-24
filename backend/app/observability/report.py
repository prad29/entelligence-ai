"""CSV/PDF usage report generation (spec §9).

collect_report() gathers exactly the same data the dashboard endpoints
already expose (queries.summary/timeseries/breakdown/dedupe/serpapi_credits,
serper_quota_status) into one payload, so a downloaded report can never
disagree with what the screen it was generated from was showing.

Both builders take that same payload — one implementation of "what goes in
the report", two renderers.
"""

from __future__ import annotations

import csv
import io
from typing import Optional

from sqlmodel import Session

from app.observability import queries
from app.observability.queries import UsageFilters
from app.observability.serper_quota import serper_quota_status


def collect_report(session: Session, filters: UsageFilters) -> dict:
    return {
        "range": {"start": filters.start.isoformat(), "end": filters.end.isoformat()},
        "summary": queries.summary(session, filters),
        "daily": queries.timeseries(session, filters, "day")["points"],
        "by_task_type": queries.breakdown(session, filters, "task_type"),
        "by_model": queries.breakdown(session, filters, "model_id"),
        "dedupe": queries.dedupe_stats(session, filters),
        "serpapi": queries.serpapi_credits(session, history_hours=24),
        "serper": serper_quota_status(session),
    }


def _money(value: Optional[float]) -> str:
    return "" if value is None else f"{value:.4f}"


def build_csv(report: dict) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["Usage report", report["range"]["start"], "to", report["range"]["end"]])
    writer.writerow([])

    totals = report["summary"]["totals"]
    derived = report["summary"]["derived"]
    writer.writerow(["Summary"])
    writer.writerow(["requests", "cost_usd", "input_tokens", "output_tokens",
                      "cache_hit_count", "failure_count", "avg_latency_ms"])
    writer.writerow([
        totals["request_count"], _money(totals["cost_usd"]), totals["input_tokens"],
        totals["output_tokens"], totals["cache_hit_count"], totals["failure_count"],
        _money(derived["avg_latency_ms"]),
    ])
    writer.writerow([])

    writer.writerow(["Daily"])
    writer.writerow(["day", "requests", "cost_usd", "input_tokens", "output_tokens"])
    for point in report["daily"]:
        writer.writerow([
            point["bucket"][:10], point["request_count"], _money(point["cost_usd"]),
            point["input_tokens"], point["output_tokens"],
        ])
    writer.writerow([])

    for label, key in (("By task type", "by_task_type"), ("By model", "by_model")):
        writer.writerow([label])
        writer.writerow(["key", "requests", "cost_usd"])
        for row in report[key]["rows"]:
            dim_value = row.get(report[key]["dimension"], "")
            writer.writerow([dim_value, row["request_count"], _money(row["cost_usd"])])
        writer.writerow([])

    writer.writerow(["Dedupe"])
    overall = report["dedupe"]["overall"]
    writer.writerow(["attempted", "cache_hits", "dedupe_rate", "estimated_savings_usd"])
    writer.writerow([
        overall["attempted"], overall["cache_hits"],
        "" if overall["dedupe_rate"] is None else f"{overall['dedupe_rate']:.4f}",
        _money(overall["estimated_savings_usd"]),
    ])
    writer.writerow([])

    writer.writerow(["SerpApi credits (slot, total_searches_left)"])
    for slot in report["serpapi"]["slots"]:
        writer.writerow([slot["slot"], slot["total_searches_left"]])
    writer.writerow([])

    writer.writerow(["Serper usage"])
    serper = report["serper"]
    writer.writerow(["used", "remaining", "quota_configured"])
    writer.writerow([serper["used"], serper["remaining"], serper["quota_configured"]])

    return buf.getvalue().encode("utf-8")


def build_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title="Usage report")
    story = []

    story.append(Paragraph("LLM/API Usage Report", styles["Title"]))
    story.append(
        Paragraph(
            f"{report['range']['start']} — {report['range']['end']}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 12))

    def table(rows, col_widths=None):
        t = Table(rows, colWidths=col_widths, hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            )
        )
        return t

    totals = report["summary"]["totals"]
    derived = report["summary"]["derived"]
    story.append(Paragraph("Summary", styles["Heading2"]))
    story.append(
        table(
            [
                ["Metric", "Value"],
                ["Total requests", str(totals["request_count"])],
                ["Total cost (USD)", _money(totals["cost_usd"])],
                ["Input tokens", str(totals["input_tokens"])],
                ["Output tokens", str(totals["output_tokens"])],
                ["Cache hits", str(totals["cache_hit_count"])],
                ["Failures", str(totals["failure_count"])],
                ["Avg latency (ms)", f"{derived['avg_latency_ms']:.1f}" if derived["avg_latency_ms"] is not None else ""],
            ],
            col_widths=[3.2 * inch, 3.8 * inch],
        )
    )
    story.append(Spacer(1, 12))

    story.append(Paragraph("Daily cost", styles["Heading2"]))
    daily_rows = [["Day", "Cost (USD)", "Requests", "Input tokens", "Output tokens"]]
    for point in report["daily"]:
        daily_rows.append([
            point["bucket"][:10], _money(point["cost_usd"]), str(point["request_count"]),
            str(point["input_tokens"]), str(point["output_tokens"]),
        ])
    if len(daily_rows) == 1:
        daily_rows.append(["(no data in range)", "", "", "", ""])
    story.append(table(daily_rows))
    story.append(Spacer(1, 12))

    for heading, key in (("Cost by task type", "by_task_type"), ("Cost by model", "by_model")):
        story.append(Paragraph(heading, styles["Heading2"]))
        rows = [["Key", "Cost (USD)", "Requests"]]
        for row in report[key]["rows"]:
            rows.append([str(row.get(report[key]["dimension"], "")), _money(row["cost_usd"]), str(row["request_count"])])
        if len(rows) == 1:
            rows.append(["(no data in range)", "", ""])
        story.append(table(rows))
        story.append(Spacer(1, 12))

    story.append(Paragraph("Search APIs", styles["Heading2"]))
    search_rows = [["Provider", "Slot", "Remaining/Used"]]
    for slot in report["serpapi"]["slots"]:
        search_rows.append(["serpapi", str(slot["slot"]), str(slot["total_searches_left"])])
    serper = report["serper"]
    search_rows.append([
        "serper", "all",
        "unknown" if serper["remaining"] is None else str(serper["remaining"]),
    ])
    story.append(table(search_rows))
    story.append(
        Paragraph(
            "Serper publishes no remaining-credit API; remaining is derived from "
            "the configured quota minus calls recorded ourselves.",
            styles["Italic"],
        )
    )
    story.append(Spacer(1, 12))

    dedupe = report["dedupe"]["overall"]
    story.append(Paragraph("Deduplication", styles["Heading2"]))
    story.append(
        table(
            [
                ["Metric", "Value"],
                ["Cache hits", str(dedupe["cache_hits"])],
                ["Attempted", str(dedupe["attempted"])],
                [
                    "Dedupe rate",
                    "" if dedupe["dedupe_rate"] is None else f"{dedupe['dedupe_rate']:.2%}",
                ],
                ["Estimated savings (USD)", _money(dedupe["estimated_savings_usd"])],
            ],
            col_widths=[3.2 * inch, 3.8 * inch],
        )
    )

    doc.build(story)
    return buf.getvalue()
