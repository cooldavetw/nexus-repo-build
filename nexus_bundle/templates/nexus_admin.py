"""Runs inside the bundled tools image; no host Python required."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class Nexus:
    def __init__(self):
        self.url = os.environ.get('NEXUS_URL', 'http://nexus:8081').rstrip('/')
        self.repository = os.environ['NEXUS_REPOSITORY']
        parsed = urllib.parse.urlsplit(self.url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or any(c.isspace() for c in self.url)):
            raise RuntimeError('Use an HTTP(S) Nexus base URL without embedded credentials, query or fragment')
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', self.repository):
            raise RuntimeError('Invalid repository name')
        self.username = os.environ.get('NEXUS_USERNAME', 'admin')
        self.password = os.environ.get('NEXUS_PASSWORD', '')

    def request(self, path, method='GET', data=None, content_type='application/json', auth=True):
        headers = {}
        if auth:
            token = base64.b64encode(f'{self.username}:{self.password}'.encode()).decode()
            headers['Authorization'] = f'Basic {token}'
        if data is not None:
            headers['Content-Type'] = content_type
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return json.loads(body) if body else None

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self.request('/service/rest/v1/status', auth=False)
                print('Nexus is ready', flush=True)
                return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(min(3, max(0, deadline - time.monotonic())))
        raise RuntimeError(f'Nexus was not ready within {timeout}s')

    def bootstrap(self):
        if not self.password:
            raise RuntimeError('NEXUS_PASSWORD is required')
        desired = self.password
        initial = os.environ.get('NEXUS_INITIAL_PASSWORD', '')
        if initial and initial != desired:
            # Try the desired password first: a interrupted setup may have already changed it.
            try:
                self.request('/service/rest/v1/security/users')
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise
                self.password = initial
                self.request('/service/rest/v1/security/users/admin/change-password',
                             'PUT', desired.encode(), 'text/plain')
                self.password = desired
        self.request('/service/rest/v1/security/anonymous', 'PUT', {
            'enabled': True, 'userId': 'anonymous', 'realmName': 'NexusAuthorizingRealm'})
        repositories = self.request('/service/rest/v1/repositories')
        existing = next((r for r in repositories if r['name'] == self.repository), None)
        if existing:
            if existing['format'] != 'pypi' or existing['type'] != 'hosted':
                raise RuntimeError('Existing repository is not PyPI hosted')
            print(f'Repository {self.repository} already exists')
        else:
            self.request('/service/rest/v1/repositories/pypi/hosted', 'POST', {
                'name': self.repository, 'online': True,
                'storage': {'blobStoreName': 'default', 'strictContentTypeValidation': True,
                            'writePolicy': 'ALLOW_ONCE'},
                'component': {'proprietaryComponents': False}})
            print(f'Created repository {self.repository}')

    def eula(self, accept=False):
        try:
            agreement = self.request('/service/rest/v1/system/eula')
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return True  # Older editions have no Community Edition EULA endpoint.
            raise
        if agreement.get('accepted'):
            print('Nexus EULA already accepted')
            return True
        print(agreement['disclaimer'], flush=True)
        if not accept:
            return False
        agreement['accepted'] = True
        self.request('/service/rest/v1/system/eula', 'POST', agreement)
        print('Nexus EULA accepted with explicit operator authorization')
        return True

    def assets(self):
        result = {}
        token = None
        while True:
            query = {'repository': self.repository}
            if token:
                query['continuationToken'] = token
            page = self.request('/service/rest/v1/assets?' + urllib.parse.urlencode(query))
            for asset in page['items']:
                filename = asset['path'].rsplit('/', 1)[-1]
                # Nexus also stores generated indexes and wheel metadata sidecars.
                if not filename.endswith('.whl'):
                    continue
                if filename in result:
                    raise RuntimeError(f'Ambiguous remote asset: {filename}')
                result[filename] = asset
            token = page.get('continuationToken')
            if not token:
                return result

    def upload(self):
        if not self.password:
            raise RuntimeError('NEXUS_PASSWORD is required')
        assets = self.assets()
        wheels = sorted(Path('/wheels').glob('*.whl'))
        if not wheels:
            raise RuntimeError('No wheels found')
        uploaded = skipped = 0
        for wheel in wheels:
            existing = assets.get(wheel.name)
            if existing:
                checksums = existing.get('checksum', {})
                algorithm = next((a for a in ('sha256', 'sha1') if a in checksums), None)
                if algorithm is None:
                    raise RuntimeError(f'No comparable checksum for {wheel.name}')
                digest = hashlib.new(algorithm)
                with wheel.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                if digest.hexdigest() != checksums[algorithm]:
                    raise RuntimeError(f'Remote content differs: {wheel.name}')
                print(f'Already present: {wheel.name}', flush=True)
                skipped += 1
                continue
            environment = os.environ.copy()
            environment.update(TWINE_USERNAME=self.username, TWINE_PASSWORD=self.password)
            subprocess.run([sys.executable, '-m', 'twine', 'upload', '--non-interactive',
                            '--disable-progress-bar', '--repository-url', f'{self.url}/repository/{self.repository}/',
                            str(wheel)], env=environment, check=True)
            uploaded += 1
        print(f'Uploaded: {uploaded}; already present: {skipped}; failed: 0')

    def verify_install(self):
        subprocess.run([sys.executable, '-m', 'venv', '/tmp/verify-env'], check=True)
        # Anonymous reads are enabled by bootstrap. Isolate pip from user config and env.
        environment = {k: v for k, v in os.environ.items() if not k.startswith('PIP_')}
        environment['PIP_CONFIG_FILE'] = os.devnull
        subprocess.run(['/tmp/verify-env/bin/python', '-m', 'pip', 'install',
                        '--disable-pip-version-check', '--no-cache-dir', '--only-binary=:all:',
                        '--index-url', f'{self.url}/repository/{self.repository}/simple/',
                        '--trusted-host', urllib.parse.urlsplit(self.url).hostname,
                        '-r', '/requirements.txt'], env=environment, check=True)
        subprocess.run(['/tmp/verify-env/bin/python', '-m', 'pip', 'check'], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['wait', 'bootstrap', 'upload', 'verify-install', 'eula-status', 'accept-eula'])
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    try:
        nexus = Nexus()
        if args.command == 'wait':
            nexus.wait(args.timeout)
        elif args.command in ('eula-status', 'accept-eula'):
            if not nexus.eula(accept=args.command == 'accept-eula'):
                sys.exit(2)
        else:
            getattr(nexus, args.command.replace('-', '_'))()
    except (RuntimeError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        # Never include request headers or credential-bearing command arguments.
        print(f'Operation failed: {error}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
