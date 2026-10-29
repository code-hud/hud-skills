#!/usr/bin/env python3
"""Fill the report page template with a report JSON document.

Checks the envelope (an object with the supported `schemaVersion`) and that the impact graph's ids
are unique and its references resolve, then puts the JSON into the template's report-data script
tag. Nothing else in the template changes.

Usage:
  python3 fill_report.py --input report.json|- --output report.html [--template PATH]

Python standard library only.
"""

import argparse
import math
import os
import sys

from impact_graph import js_number_text, parse_json, to_json

SUPPORTED_SCHEMA_VERSION = 1
REPORT_DATA_ELEMENT_ID = 'report-data'
REPORT_DATA_PLACEHOLDER = f'<script type="application/json" id="{REPORT_DATA_ELEMENT_ID}"></script>'

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TEMPLATE = os.path.join(HERE, '..', 'assets', 'report-template.html')


class InvalidReportInputError(Exception):
    def __init__(self, reason):
        super().__init__(f'Invalid report input: {reason}')


class ReportDataPlaceholderMissingError(Exception):
    def __init__(self):
        super().__init__(f'The HTML does not contain {REPORT_DATA_PLACEHOLDER}')


class MalformedGraphError(Exception):
    pass


# --- JavaScript values ---------------------------------------------------------------
# The graph comes from untrusted JSON: ids and references are compared and printed the way
# JavaScript would, so a numeric id never collides with a string one.

UNDEFINED = object()


def prop(value, name, path):
    if value is None or value is UNDEFINED:
        raise MalformedGraphError(f'Invalid report input: {path} is missing')
    return value.get(name, UNDEFINED) if isinstance(value, dict) else UNDEFINED


def items(value, path):
    if not isinstance(value, list):
        raise MalformedGraphError(f'Invalid report input: {path} must be an array')
    return value


def same_value(v):
    """A key under JavaScript's SameValueZero."""
    if v is UNDEFINED:
        return ('undefined',)
    if v is None:
        return ('null',)
    if isinstance(v, bool):
        return ('boolean', v)
    if isinstance(v, (int, float)):
        try:
            x = float(v)
        except OverflowError:
            x = math.inf if v > 0 else -math.inf
        return ('number', 'NaN' if x != x else x)
    if isinstance(v, str):
        return ('string', v)
    return ('object', id(v))


def js_text(v):
    """`${v}`."""
    if v is UNDEFINED:
        return 'undefined'
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        try:
            x = float(v)
        except OverflowError:
            return 'Infinity' if v > 0 else '-Infinity'
        if x != x:
            return 'NaN'
        if math.isinf(x):
            return 'Infinity' if x > 0 else '-Infinity'
        return js_number_text(x)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ','.join('' if e is None or e is UNDEFINED else js_text(e) for e in v)
    return '[object Object]'


# --- Validation ----------------------------------------------------------------------

def find_graph_reference_error(graph):
    """First broken reference in the graph, as a human-readable reason, or None when every id is
    unique and every `from` / `to` / `caller` points at an entity of the right kind."""
    endpoint_id = prop(prop(graph, 'endpoint', 'prExample.graph'), 'id', 'prExample.graph.endpoint')
    functions = items(prop(graph, 'functions', 'prExample.graph'), 'prExample.graph.functions')
    dependencies = items(prop(graph, 'dependencies', 'prExample.graph'), 'prExample.graph.dependencies')
    minor_calls = items(prop(graph, 'minorCalls', 'prExample.graph'), 'prExample.graph.minorCalls')
    function_ids = [prop(fn, 'id', 'prExample.graph.functions[]') for fn in functions]
    dependency_ids = [prop(d, 'id', 'prExample.graph.dependencies[]') for d in dependencies]
    minor_ids = [prop(m, 'id', 'prExample.graph.minorCalls[]') for m in minor_calls]

    seen = set()
    for i in [endpoint_id, *function_ids, *dependency_ids, *minor_ids]:
        if same_value(i) in seen:
            return f'duplicate graph id "{js_text(i)}"'
        seen.add(same_value(i))

    function_keys = {same_value(i) for i in function_ids}
    dependency_keys = {same_value(i) for i in dependency_ids}

    def is_caller(i):
        return (same_value(i) == same_value(endpoint_id) and same_value(i) != ('number', 'NaN')) \
            or same_value(i) in function_keys

    calls = items(prop(graph, 'calls', 'prExample.graph'), 'prExample.graph.calls')
    for index, call in enumerate(calls):
        src, dst = prop(call, 'from', f'calls[{index}]'), prop(call, 'to', f'calls[{index}]')
        if not is_caller(src):
            return f'calls[{index}].from "{js_text(src)}" is not the endpoint or a function'
        if same_value(dst) not in function_keys:
            return f'calls[{index}].to "{js_text(dst)}" is not a function'
    dependency_calls = items(prop(graph, 'dependencyCalls', 'prExample.graph'), 'prExample.graph.dependencyCalls')
    for index, call in enumerate(dependency_calls):
        src, dst = prop(call, 'from', f'dependencyCalls[{index}]'), prop(call, 'to', f'dependencyCalls[{index}]')
        if not is_caller(src):
            return f'dependencyCalls[{index}].from "{js_text(src)}" is not the endpoint or a function'
        if same_value(dst) not in dependency_keys:
            return f'dependencyCalls[{index}].to "{js_text(dst)}" is not a dependency'

    minor_callers = {}
    for m, i in zip(minor_calls, minor_ids):
        minor_callers[same_value(i)] = prop(m, 'caller', 'prExample.graph.minorCalls[]')
    for index, (m, i) in enumerate(zip(minor_calls, minor_ids)):
        caller = prop(m, 'caller', f'minorCalls[{index}]')
        if not is_caller(caller) and same_value(caller) not in minor_callers:
            return f'minorCalls[{index}].caller "{js_text(caller)}" is not the endpoint, a function or a minor call'
        chain = {same_value(i)}
        cur = caller
        while same_value(cur) in minor_callers:
            if same_value(cur) in chain:
                return f'minorCalls[{index}].caller "{js_text(caller)}" is part of a loop of minor calls'
            chain.add(same_value(cur))
            cur = minor_callers[same_value(cur)]
    return None


def fill_report_data(html, report):
    if REPORT_DATA_PLACEHOLDER not in html:
        raise ReportDataPlaceholderMissingError()
    # `<` as \u003c, so no string value can close the script tag or open an HTML comment in it.
    # U+FFFD as \ufffd: the artifact publisher refuses a page holding the character itself.
    data = to_json(report).replace('<', '\\u003c').replace('\ufffd', '\\ufffd')
    return html.replace(REPORT_DATA_PLACEHOLDER,
                        f'<script type="application/json" id="{REPORT_DATA_ELEMENT_ID}">{data}</script>', 1)


def fill_report(template_html, input_json):
    """The template filled with the report JSON text, after checking its envelope and graph."""
    try:
        report = parse_json(input_json)
    except ValueError as error:
        raise InvalidReportInputError(f'not valid JSON ({error})')
    if not isinstance(report, dict):
        raise InvalidReportInputError('expected a JSON object')

    version = report.get('schemaVersion', UNDEFINED)
    if not (isinstance(version, (int, float)) and not isinstance(version, bool) and version == SUPPORTED_SCHEMA_VERSION):
        shown = 'undefined' if version is UNDEFINED else to_json(version)
        raise InvalidReportInputError(f'schemaVersion must be {SUPPORTED_SCHEMA_VERSION}, got {shown}')

    pr_example = report.get('prExample')
    graph = pr_example.get('graph') if isinstance(pr_example, dict) else None
    if not isinstance(graph, dict):
        raise InvalidReportInputError('prExample.graph is missing')
    graph_error = find_graph_reference_error(graph)
    if graph_error:
        raise InvalidReportInputError(graph_error)
    return fill_report_data(template_html, report)


def read_text(path):
    if path == '-':
        return sys.stdin.buffer.read().decode('utf-8', errors='replace')
    with open(path, encoding='utf-8', errors='replace', newline='') as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)
    ap.add_argument('--input', help='report JSON file, or "-" to read it from stdin')
    ap.add_argument('--output', help='where to write the filled report')
    ap.add_argument('--template', default=DEFAULT_TEMPLATE, help='the report page template')
    args = ap.parse_args(argv)
    if not args.input or not args.output:
        ap.print_usage(sys.stderr)
        sys.exit('--input and --output are required')
    try:
        html = fill_report(read_text(args.template), read_text(args.input))
    except (OSError, InvalidReportInputError, ReportDataPlaceholderMissingError, MalformedGraphError) as error:
        sys.exit(str(error))
    with open(args.output, 'w', encoding='utf-8', newline='') as fh:
        fh.write(html)
    print(f'Wrote {os.path.abspath(args.output)}')


if __name__ == '__main__':
    main()
