"""Rule pack loader, gate and SQL compiler for dqscan.

Turns rules.yaml into Rule objects, decides which of those are actually
runnable against the live schema (gate_rules), and turns a runnable Rule
into the count/detail SQL text the engine hands to db.run_query. No I/O
beyond reading the YAML file; no execution.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Optional

import yaml

from dqscan.models import CompiledRule, Rule, RuleOutcome

logger = logging.getLogger("dqscan.compiler")

_IDENTITY_COLUMNS = ("id", "mm_id", "date_sh", "running_date", "theater_id", "theater_name", "title")

_ALLOWLIST_PLACEHOLDER = re.compile(r"'?\{\{allowlist_regex\}\}'?")
_CA_PROVINCES_PLACEHOLDER = re.compile(r"'?\{\{ca_provinces\}\}'?")

_META_KEYS = ("version", "table", "row_id", "grain", "window_column", "allowlist_regex", "ca_provinces")


class _RulePackLoader(yaml.SafeLoader):
    """YAML 1.1's bare on/off/yes/no bool resolver turns the province code
    ON (Ontario) into Python True. Rules.yaml has no actual booleans, so
    drop that resolver entirely rather than lose province codes silently."""


_RulePackLoader.yaml_implicit_resolvers = {
    first_char: [pair for pair in resolvers if pair[0] != "tag:yaml.org,2002:bool"]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_rules(path: str) -> tuple[dict, list[Rule]]:
    with open(path, "r") as f:
        raw = yaml.load(f, Loader=_RulePackLoader)

    meta = {key: raw[key] for key in _META_KEYS}

    rule_fields = {f.name for f in dataclasses.fields(Rule)}
    rules = []
    for entry in raw["rules"]:
        entry = dict(entry)
        entry["rule_class"] = entry.pop("class")
        kwargs = {k: v for k, v in entry.items() if k in rule_fields}
        rules.append(Rule(**kwargs))
    return meta, rules


def _format_blocked_by(blocked_by) -> str:
    if isinstance(blocked_by, list):
        return f"blocked by: {', '.join(str(b) for b in blocked_by)}"
    if blocked_by:
        return f"blocked by: {blocked_by}"
    return ""


def gate_rules(rules: list[Rule], existing_columns: set[str]) -> tuple[list[Rule], list[RuleOutcome]]:
    compilable: list[Rule] = []
    dormant_outcomes: list[RuleOutcome] = []

    for rule in rules:
        missing = next((name for name in rule.requires if name not in existing_columns), None)
        if missing is not None:
            dormant_outcomes.append(
                RuleOutcome(rule=rule, run_status="dormant", dormant_reason=f"missing column: {missing}")
            )
            continue

        if rule.rule_class == "domain" and not rule.allowed:
            logger.warning("Rule %s is a domain rule with an empty allowed list", rule.id)
            dormant_outcomes.append(
                RuleOutcome(rule=rule, run_status="dormant", dormant_reason="empty allowed list")
            )
            continue

        if rule.status == "dormant":
            reason = _format_blocked_by(rule.blocked_by) or "declared dormant in rule pack"
            dormant_outcomes.append(RuleOutcome(rule=rule, run_status="dormant", dormant_reason=reason))
            continue

        compilable.append(rule)

    return compilable, dormant_outcomes


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _substitute_placeholders(
    text: str,
    *,
    allowlist_regex: str,
    ca_provinces: list[str],
    table: Optional[str] = None,
    window: Optional[str] = None,
) -> str:
    if "{{allowlist_regex}}" in text:
        literal = _sql_string_literal(allowlist_regex)
        text = _ALLOWLIST_PLACEHOLDER.sub(lambda _match: literal, text)
    if "{{ca_provinces}}" in text:
        # FIND_IN_SET wants one comma-joined string argument, not a parenthesized IN-list.
        literal = _sql_string_literal(",".join(ca_provinces))
        text = _CA_PROVINCES_PLACEHOLDER.sub(lambda _match: literal, text)
    if table is not None and "{{table}}" in text:
        text = text.replace("{{table}}", table)
    if window is not None and "{{window}}" in text:
        text = text.replace("{{window}}", window)
    return text


def _window_clause(window_column: str) -> str:
    return f"{window_column} >= :from_date AND {window_column} <= :to_date"


def _detail_select_columns(detail_columns: list[str], *, prefix: str = "") -> str:
    identity = [f"{prefix}{col}" for col in _IDENTITY_COLUMNS]
    extra = [col for col in detail_columns if col not in _IDENTITY_COLUMNS]
    return ", ".join(identity + extra)


def _condition_for_rule(
    rule: Rule, *, allowlist_regex: str, ca_provinces: list[str], param_prefix: str
) -> tuple[str, dict]:
    """The single boolean predicate a presence/format/cross_column/domain
    rule fires on — shared by individual compilation and batching so the
    two paths can never disagree about what a rule actually checks."""
    if rule.rule_class == "domain":
        param_names = [f"{param_prefix}_{i}" for i in range(len(rule.allowed))]
        in_clause = ", ".join(f":{name}" for name in param_names)
        params = dict(zip(param_names, rule.allowed))
        condition = (
            f"{rule.column} IS NOT NULL AND {rule.column} <> '' "
            f"AND {rule.column} NOT IN ({in_clause})"
        )
        return condition, params

    expr = _substitute_placeholders(rule.expr, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces)
    return expr, {}


def _compile_expr_or_domain_rule(
    rule: Rule, *, table: str, window: str, allowlist_regex: str, ca_provinces: list[str], cap: int
) -> CompiledRule:
    condition, params = _condition_for_rule(
        rule, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces, param_prefix="allowed"
    )
    count_sql = f"SELECT COUNT(*) FROM {table} WHERE {window} AND ({condition})"
    cols = _detail_select_columns(rule.detail_columns)
    # cap + 1, not cap: lets the engine tell "exactly cap findings" apart
    # from "truncated at cap" without a second query.
    detail_sql = f"SELECT {cols} FROM {table} WHERE {window} AND ({condition}) LIMIT {cap + 1}"
    return CompiledRule(
        rule=rule, count_sql=count_sql, count_params=dict(params), detail_sql=detail_sql, detail_params=dict(params)
    )


def compile_batch(
    rules: list[Rule], *, window_column: str, table: str, allowlist_regex: str, ca_provinces: list[str]
) -> tuple[str, dict, list[str]]:
    """One SUM(CASE WHEN ...) per rule in a single pass over the window,
    for the presence/format/cross_column/domain rules — the ones that
    would otherwise each scan the table separately for an identical
    WHERE <window> clause. Returns (sql, params, rule_ids) where rule_ids[i]
    names the column alias c_i holds the count for.

    Only ever called with rule_class in _BATCHABLE_CLASSES; cross_row and
    custom keep their own GROUP BY / window-function queries, compiled by
    compile_rule as before.
    """
    window = _window_clause(window_column)
    select_parts = []
    params: dict = {}
    rule_ids = []
    for i, rule in enumerate(rules):
        condition, rule_params = _condition_for_rule(
            rule, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces, param_prefix=f"r{i}_allowed"
        )
        select_parts.append(f"SUM(CASE WHEN ({condition}) THEN 1 ELSE 0 END) AS c_{i}")
        params.update(rule_params)
        rule_ids.append(rule.id)

    sql = f"SELECT {', '.join(select_parts)} FROM {table} WHERE {window}"
    assert sql.strip().upper().startswith(("SELECT", "WITH")), "batch query failed the read-only shape check"
    return sql, params, rule_ids


def _compile_cross_row_rule(
    rule: Rule, *, table: str, window: str, allowlist_regex: str, ca_provinces: list[str], cap: int
) -> CompiledRule:
    group_by = ", ".join(rule.group_by)
    having = _substitute_placeholders(rule.having, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces)

    filter_clause = ""
    if rule.filter:
        filter_expr = _substitute_placeholders(rule.filter, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces)
        filter_clause = f" AND {filter_expr}"

    subquery = (
        f"SELECT {group_by} FROM {table} "
        f"WHERE {window}{filter_clause} "
        f"GROUP BY {group_by} HAVING {having}"
    )
    count_sql = f"SELECT COUNT(*) FROM ( {subquery} ) g"

    cols = _detail_select_columns(rule.detail_columns, prefix="t.")
    detail_sql = (
        f"SELECT {cols} "
        f"FROM {table} t "
        f"JOIN ( {subquery} ) g USING ({group_by}) "
        f"WHERE {window}{filter_clause} "
        f"ORDER BY {group_by} "
        f"LIMIT {cap + 1}"
    )
    return CompiledRule(rule=rule, count_sql=count_sql, count_params={}, detail_sql=detail_sql, detail_params={})


def _compile_custom_rule(
    rule: Rule, *, table: str, window: str, allowlist_regex: str, ca_provinces: list[str], cap: int
) -> CompiledRule:
    substituted = _substitute_placeholders(
        rule.sql, allowlist_regex=allowlist_regex, ca_provinces=ca_provinces, table=table, window=window
    )
    count_sql = f"SELECT COUNT(*) FROM ( {substituted} ) c"
    detail_sql = f"SELECT * FROM ( {substituted} ) d LIMIT {cap + 1}"
    return CompiledRule(rule=rule, count_sql=count_sql, count_params={}, detail_sql=detail_sql, detail_params={})


def compile_rule(
    rule: Rule,
    *,
    window_column: str,
    table: str,
    allowlist_regex: str,
    ca_provinces: list[str],
    detail_row_cap: int,
) -> CompiledRule:
    window = _window_clause(window_column)

    match rule.rule_class:
        case "presence" | "format" | "cross_column" | "domain":
            compiled = _compile_expr_or_domain_rule(
                rule, table=table, window=window, allowlist_regex=allowlist_regex,
                ca_provinces=ca_provinces, cap=detail_row_cap,
            )
        case "cross_row":
            compiled = _compile_cross_row_rule(
                rule, table=table, window=window, allowlist_regex=allowlist_regex,
                ca_provinces=ca_provinces, cap=detail_row_cap,
            )
        case "custom" | "cross_table":
            # cross_table is compiled identically to custom: both are a raw
            # `sql:` SELECT (here, one that joins another schema's master
            # table) wrapped in the same COUNT(*)/LIMIT shape. The declared
            # class is kept distinct in rules.yaml purely for report
            # readability (the "Class" column), not because the SQL shape
            # differs.
            compiled = _compile_custom_rule(
                rule, table=table, window=window, allowlist_regex=allowlist_regex,
                ca_provinces=ca_provinces, cap=detail_row_cap,
            )
        case _:
            raise ValueError(f"Unknown rule class: {rule.rule_class!r}")

    for sql in (compiled.count_sql, compiled.detail_sql):
        assert sql is not None and sql.strip().upper().startswith(("SELECT", "WITH")), (
            f"compiled SQL for rule {rule.id} does not start with SELECT/WITH: {sql!r:.80}"
        )

    return compiled
