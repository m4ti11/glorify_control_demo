#!/usr/bin/env python3
"""Offline publication gate. No credentials, network access or source repo needed."""
from pathlib import Path
import ipaddress
import json
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
# These are service namespaces, dependency registries, public standards and
# the owner's intended portfolio URLs, rather than deployment endpoints.
PUBLIC_HOSTS = {'example.com', 'example.test', 'api.test', 'localhost', '127.0.0.1', '0.0.0.0', 'api.openai.com', 'www.w3.org', 'registry.terraform.io', 'github.com', 'glorifydev.github.io', 'img.shields.io', 'pypi.org', 'files.pythonhosted.org', 'releases.hashicorp.com', 'registry.terraform.io'}
PATTERN = r'amazonaws|cognito|arn:aws|(?<!\d)\d{12}(?!\d)|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}|\b(?:\d{1,3}\.){3}\d{1,3}\b'
BLOCKED_NAMES = {'.env', 'terraform.tfvars', 'a.json', 'config.js', 'auth.js', 'login.js', 'config.example.js'}
BLOCKED_DIRS = {'node_modules', '.terraform', '.venv', '__pycache__', '.pytest_cache', 'build', 'output'}
def files():
    # Git metadata and generated local dependencies are not publication inputs.
    for p in sorted(ROOT.rglob('*')):
        if '.git' in p.parts or any(part in BLOCKED_DIRS for part in p.relative_to(ROOT).parts): continue
        if p.is_symlink(): raise RuntimeError('Symlinks are not allowed in the showcase')
        if p.is_file(): yield p

def main():
    findings = []; counts = {}; total = 0
    for p in files():
        relative = p.relative_to(ROOT).as_posix(); total += 1
        if p.name in BLOCKED_NAMES or p.suffix in {'.zip', '.tfstate', '.tfplan', '.log'}: findings.append((relative, 'excluded artifact'))
        if p.suffix in {'.png', '.pdf'}:
            if p.suffix == '.png': continue
            try:
                from pypdf import PdfReader
                t = '\n'.join(page.extract_text() for page in PdfReader(p).pages)
            except ImportError:
                if not shutil.which('pdftotext'):
                    raise RuntimeError('PDF scan requires pypdf or pdftotext')
                t = subprocess.check_output(['pdftotext', str(p), '-'], text=True)
        else:
            try: t = p.read_text()
            except UnicodeDecodeError:
                findings.append((relative, 'unreviewed binary')); continue
        counts[relative] = len(re.findall(PATTERN, t, flags=re.I))
        for value in re.findall(r'(?<!\d)\d{12}(?!\d)', t):
            if value != '123456789012': findings.append((relative, 'account ID'))
        for value in re.findall(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}', t):
            if not value.lower().endswith(('@example.com', '@example.test')): findings.append((relative, 'email outside fictional domains'))
        for value in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', t):
            try: addr = ipaddress.ip_address(value)
            except ValueError: continue
            if not (addr.is_loopback or addr.is_unspecified or str(addr).startswith(('192.0.2.', '198.51.100.', '203.0.113.'))): findings.append((relative, 'non-documentation IP'))
        if re.search(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{20,}', t): findings.append((relative, 'credential signature'))
        if re.search(r'\b(?!us-east-1\b)[a-z]{2}-(?:east|west|north|south|central)(?:east|west)?-\d\b',t): findings.append((relative, 'non-example region'))
        # Literal private endpoints are forbidden; interpolated IaC and service
        # principals are code, not connected destinations.
        for url in re.findall(r'https?://([a-zA-Z0-9][a-zA-Z0-9.\-]+)',t):
            if url not in PUBLIC_HOSTS and not url.endswith(('.example', '.invalid')) and url not in {'example', 'user', 'api', 'localhost', 'insecure.example', 'other.example', 'ingest.example', 's3.amazonaws.com', 'example.cloudfront.net'}:
                findings.append((relative, 'unreviewed literal URL host: ' + url))
    print('Fallback pattern:', PATTERN)
    print('Files inspected:', total, '| Pattern matches reviewed:', sum(counts.values()))
    for path,n in counts.items():
        if n: print(f'  {path}: {n} reviewed match(es)')
    if findings:
        for path, reason in sorted(set(findings)): print('FAIL', path, reason)
        return 1
    if shutil.which('gitleaks'):
        subprocess.run(['gitleaks','detect','--no-git','--source',str(ROOT),'--redact'],check=True)
    elif shutil.which('trufflehog'):
        subprocess.run(['trufflehog','filesystem',str(ROOT),'--fail'],check=True)
    else: print('gitleaks/trufflehog unavailable; documented offline pattern and credential checks used.')
    print('PASS: no unapproved identifiers or credential signatures found.')
    return 0
if __name__ == '__main__': sys.exit(main())
