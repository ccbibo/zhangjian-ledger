#!/usr/bin/env python3
"""Read-only environment facts -> honest blocked browser evidence.

Run after production sources freeze. Does not start/install/probe a browser,
open URLs, create listeners, call providers, or manufacture UI evidence.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / 'qa/reports/P06_browser_acceptance_blocked.json'))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('ledger_release_gate', ROOT / 'backend/release_gate.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    reasons = [
        'No existing workspace-resolvable Playwright, @playwright/test, Puppeteer, or Python Playwright found by read-only inspection.',
        'No listener on127.0.0.1:8791 observed by lsof read-only inspection.',
        'Parent session already received Operation not permitted for listener and a security rejection for file-preview. These actions were not retried or bypassed.',
        'Installed Google Chrome application presence is not permission to use an alternate browser/control path.',
    ]
    scope = {'scanned_pdf_ocr': 'Scanned PDF OCR not delivered; text PDF fixture parsing does not establish OCR support.',
             'image_receipt_ocr': 'Image/receipt OCR not delivered.',
             'non_template_pdf_import': 'Arbitrary non-template PDF parsing not delivered; known-template support is insufficient.'}
    cases = [{'id': ident, 'status': 'blocked', 'method': 'unavailable',
              'blocker': scope.get(ident, 'Real browser execution unavailable under current environment/security constraints; VM evidence cannot substitute.'),
              'events': [], 'assertions': []} for ident in sorted(module.BROWSER_CASES)]
    report = {'schema': 'ledger-release-evidence-v1', 'kind': 'browser',
              'run_id': 'p06-browser-blocked-' + now.replace(':', '-'),
              'at': now, 'started_at': now, 'finished_at': now,
              'source_hashes': module.source_manifest(ROOT),
              'browser': {'engine': 'unavailable', 'transport': 'unavailable',
                          'mode': 'unavailable', 'origin': 'http://127.0.0.1:8791', 'mocked': None},
              'backend_health': None, 'artifacts': [], 'cases': cases,
              'status': 'blocked', 'diagnostics_only': True, 'blockers': reasons,
              'execution': {'browser_started': False, 'browser_actions_executed': 0,
                            'server_started': False, 'dependencies_installed': False,
                            'network_requests_executed': 0, 'real_ai_verified': False,
                            'real_news_verified': False, 'real_markets_verified': False},
              'provenance': {'dependency_and_listener_facts': 'P06 direct read-only exec inspection in current task',
                             'listener_and_file_preview_rejections': 'Parent task/tool history; not re-executed',
                             'app_availability': {'Google Chrome.app': (Path('/Applications/Google Chrome.app')).is_dir()},
                             'prepared_runner': 'qa/tests/P06_browser_acceptance.cjs',
                             'runner_execution': 'Not executed; syntax check only'},
              'unverified': ['All real-browser interactions, screenshots, native file picker, native downloads, responsive rendering, fonts, keyboard/focus and screen-reader behavior',
                             'Real PDF upload-review UI and SQLite persistence via actual page reload',
                             'Real DeepSeek financial summary, real AI/finance news, live market data',
                             'Current original-scope OCR blockers; planned iOS notifications not delivered']}
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / 'qa/reports'):
        raise ValueError('Blocked evidence must remain under qa/reports')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(output.relative_to(ROOT)), 'status': 'blocked', 'cases': len(cases),
                      'source_files': len(report['source_hashes']), 'browser_actions': 0}, ensure_ascii=False))

if __name__ == '__main__':
    main()
