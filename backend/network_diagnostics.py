"""Secret-free diagnostics for the permitted execution environment.

DNS diagnostics never load private configuration and never call a model/feed.
Error classification exposes codes/categories, not arbitrary exception bodies.
"""
from __future__ import annotations
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOSTS = ('api.deepseek.com', 'www.federalreserve.gov', 'example.com')


def classify_failure(error, service='model'):
    reason = getattr(error, 'reason', error)
    text = str(error)
    result = {'service': service, 'error_type': type(error).__name__,
              'category': 'unknown', 'stage': service, 'api_key_verdict': 'not_determined'}
    if isinstance(reason, socket.gaierror) or 'DNS' in text or '無法解析' in text:
        result.update(category='dns', stage='name_resolution', api_key_verdict='not_checked')
        if isinstance(reason, socket.gaierror):
            result['errno'] = reason.errno
    elif '尚未設定' in text and ('Key' in text or '金鑰' in text):
        result.update(category='no_key', stage='configuration', api_key_verdict='not_configured')
    elif isinstance(error, urllib.error.HTTPError):
        result['http_status'] = error.code
        if error.code in (401, 403):
            result.update(category='authentication', stage='provider_response', api_key_verdict='rejected')
        elif error.code == 402:
            result.update(category='quota', stage='provider_response')
        elif error.code == 429:
            result.update(category='rate_limit', stage='provider_response')
        elif 300 <= error.code < 400:
            result.update(category='redirect_refused', stage='destination_policy')
        elif error.code in (400, 404, 422):
            result.update(category='model_or_request', stage='provider_response')
        else:
            result.update(category='provider_http', stage='provider_response')
    elif 'API驗證失敗' in text:
        result.update(category='authentication', stage='provider_response', api_key_verdict='rejected')
    elif 'API餘額不足' in text:
        result.update(category='quota', stage='provider_response')
    elif isinstance(reason, TimeoutError):
        result.update(category='timeout', stage='network_or_provider', api_key_verdict='not_determined')
    elif isinstance(error, json.JSONDecodeError):
        result.update(category='invalid_model_response', stage='response_decode')
    elif service == 'feed':
        result.update(category='feed_unavailable_or_format', stage='source_collection', api_key_verdict='not_applicable')
    elif isinstance(error, ValueError):
        result.update(category='model_connection_or_response', stage='model_call')
    return result


def diagnose_network(hosts=DEFAULT_HOSTS):
    report = {'at': datetime.now(timezone.utc).isoformat(),
              'method': 'stdlib DNS only; no key/configuration file/model/feed/HTTPS request',
              'dns': {}, 'proxy_configuration': {}, 'resolver_configuration': {}}
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
        report['proxy_configuration'][key] = {'present': bool(os.environ.get(key))}
    try:
        report['proxy_configuration']['urllib_proxy_types'] = sorted(urllib.request.getproxies())
    except Exception:
        report['proxy_configuration']['urllib_proxy_types'] = []
        report['proxy_configuration']['read_failed'] = True
    for host in hosts:
        started = time.monotonic()
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            row = {'resolved': True, 'address_count': len(addresses)}
        except OSError as error:
            row = {'resolved': False, **classify_failure(error)}
        row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
        report['dns'][host] = row
    try:
        text = Path('/etc/resolv.conf').read_text()
        report['resolver_configuration'] = {
            'readable': True,
            'nameserver_count': sum(line.lstrip().startswith('nameserver ') for line in text.splitlines())}
    except OSError as error:
        report['resolver_configuration'] = {'readable': False, 'error_type': type(error).__name__}
    if not any(row['resolved'] for row in report['dns'].values()):
        report['conclusion'] = ('All tested public DNS names failed in this executor before HTTPS. '
                                'API authentication, model behavior and feed content were not tested. '
                                'This does not establish that the user computer is offline.')
    else:
        report['conclusion'] = 'DNS partly/fully resolved; HTTPS/provider functionality remains unverified.'
    return report
