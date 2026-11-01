"""Serializable source-analysis reports, including inconclusive outcomes."""

SOURCE_CHECKS = (
    ('unconstrained_output', 'Unconstrained Output'),
    ('unconstrained component input', 'Unconstrained Component Input'),
    ('data flow constraint discrepancy', 'Data Flow / Constraint Discrepancy'),
    ('unused component output', 'Unused Component Output'),
    ('unused signal', 'Unused Signal'),
)

_REASONS = {
    'include_not_found': 'A required include file is missing. Provide the complete project and preserve its folder structure.',
    'syntax_error': 'The source could not be parsed. Check the file and line in the diagnostic details.',
    'no_main': 'This file defines a library without a main component. Analyze an entry circuit that includes it.',
    'recursion_limit': 'The analyzer reached its recursion limit while processing this circuit.',
    'typecheck_error': 'The analyzer could not complete type checking. See the diagnostic details.',
    'graph_error': 'The analyzer could not construct the circuit dependency graph. See the diagnostic details.',
    'detection_error': 'The detector stopped before all checks completed. See the diagnostic details.',
    'missing_checks': 'The analyzer did not return all required checks. Unfinished checks have no conclusion.',
    'timeout': 'The analysis time limit was reached. No complete conclusion is available.',
    'cancelled': 'Analysis was cancelled. Unfinished checks have no conclusion.',
    'memory_limit': 'The analyzer could not allocate enough memory to complete this circuit.',
    'worker_error': 'The analysis process stopped without a complete result. See the diagnostic details.',
    'configuration_error': 'The source analysis configuration is invalid. See the diagnostic details.',
    'analysis_error': 'The source could not be fully analyzed. See the diagnostic details.',
}


def incomplete_source_report(reason, *, filename=None, elapsed_seconds=0,
                             diagnostics=None, stage=None):
    """An uncompleted analysis never implies zero findings or a passing check."""
    verdict = {'cancelled': 'cancelled', 'no_main': 'not_applicable'}.get(reason, 'unknown')
    status = verdict if verdict in ('cancelled', 'not_applicable') else 'incomplete'
    message = _REASONS.get(reason, _REASONS['analysis_error'])
    if isinstance(diagnostics, str):
        diagnostics = diagnostics.splitlines()
    return {
        'kind': 'circom', 'filename': filename, 'status': status,
        'verdict': verdict, 'reason': reason, 'stage': stage,
        'message': message, 'elapsed_seconds': round(elapsed_seconds, 2),
        'warning_count': None,
        'diagnostics': [str(line)[:2000] for line in (diagnostics or [])][-40:],
        'checks': [dict(id=key, name=name, status='skipped' if verdict != 'unknown' else 'unknown',
                        count=None, message=message, findings=[]) for key, name in SOURCE_CHECKS],
    }


def _source_finding(report, filename=None):
    location = report.location
    start = getattr(location, 'start', None)
    line = getattr(start, 'line', None)
    column = getattr(start, 'column', None)
    path = filename or getattr(location, 'path', '')
    display_location = f'{path}:{line}:{column}' if line is not None and column is not None else path
    return {'location': display_location, 'message': report.message}


def build_source_report(graphs, reports, elapsed_seconds=None, filename=None):
    """Keep completed findings, but never label a missing detector result pass."""
    if not graphs or not isinstance(reports, dict):
        return incomplete_source_report('missing_checks', filename=filename,
                                        elapsed_seconds=elapsed_seconds or 0, stage='detection')
    checks = []
    for check_id, name in SOURCE_CHECKS:
        findings, seen, complete = [], set(), True
        for graph in graphs.values():
            graph_reports = reports.get(graph.name)
            if not isinstance(graph_reports, dict) or not isinstance(graph_reports.get(check_id), list):
                complete = False
                continue
            for item in graph_reports[check_id]:
                finding = _source_finding(item, filename)
                key = (finding['location'], finding['message'])
                if key not in seen:
                    seen.add(key)
                    findings.append(finding)
        count = len(findings)
        checks.append({
            'id': check_id, 'name': name,
            'status': ('warning' if count else 'pass') if complete else 'unknown',
            'count': count if complete else None, 'findings': findings,
            'message': (f'Found {count} finding(s).' if count else 'No findings.') if complete
                       else 'This check did not complete for every component.',
        })
    complete = all(check['status'] != 'unknown' for check in checks)
    warning_count = sum(len(check['findings']) for check in checks)
    result = {
        'kind': 'circom', 'filename': filename, 'checks': checks,
        'status': 'completed' if complete else 'incomplete',
        'warning_count': warning_count if complete else None,
        'verdict': ('warning' if warning_count else 'safe') if complete else 'unknown',
    }
    if not complete:
        result.update(reason='missing_checks', stage='detection',
                      message=_REASONS['missing_checks'], found_warning_count=warning_count)
    if elapsed_seconds is not None:
        result['elapsed_seconds'] = round(elapsed_seconds, 2)
    return result


def summarize_source_analysis(report):
    if report.get('status') != 'completed':
        return report.get('message', _REASONS['analysis_error'])
    status = ('Analysis completed with findings to review.' if report['warning_count']
              else 'Analysis completed successfully.')
    return f"{status}\nTotal warnings: {report['warning_count']}"
