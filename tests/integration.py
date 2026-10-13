"""Real deployment smoke test. Requires Docker and an already-built archive.

Run: python3 tests/integration.py dist/nexus-bundle.tar.gz
Creates an isolated internal network with no external connectivity. Deletes only
its own test containers and volume in a finally block.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus_bundle.cli import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--wheels-update', type=Path, action='append', default=[],
                        help='Also upload and verify this wheels-only archive against the running test Nexus')
    parser.add_argument('--accept-nexus-eula', action='store_true',
                        help='Authorize acceptance of Sonatype CE EULA for the temporary test server')
    args = parser.parse_args()
    if not args.accept_nexus_eula:
        parser.error('Review https://links.sonatype.com/products/nxrm/ce-eula and supply '
                     '--accept-nexus-eula to authorize the test server agreement')
    archive_path = args.archive.resolve()
    verify(archive_path)
    with tempfile.TemporaryDirectory(prefix='nexus-integration-') as temporary:
        directory = Path(temporary)
        with tarfile.open(archive_path) as archive:
            # verify() rejects traversal, links, and special files before extraction.
            archive.extractall(directory)
        root = directory / 'nexus-bundle'
        override = directory / 'offline.yaml'
        override.write_text('networks:\n  default:\n    internal: true\n')
        environment = os.environ.copy()
        environment.update(
            COMPOSE_PROJECT_NAME='nexus-test-' + secrets.token_hex(4),
            COMPOSE_FILE=f'{root / "compose.yaml"}:{override}',
            NEXUS_PORT='0',
            NEXUS_PASSWORD=secrets.token_urlsafe(32),
        )
        try:
            for script in ('start.sh', 'bootstrap-nexus.sh', 'bootstrap-nexus.sh',
                           'upload-wheels.sh', 'upload-wheels.sh', 'verify-install.sh'):
                print(f'Integration: {script}', flush=True)
                arguments = ['bash', script]
                if script == 'bootstrap-nexus.sh':
                    arguments.append('--accept-eula')
                subprocess.run(arguments, cwd=root, env=environment, check=True)
            for index, update_path in enumerate(args.wheels_update):
                update_path = update_path.resolve()
                verify(update_path)
                update_directory = directory / f'update-{index}'
                update_directory.mkdir()
                with tarfile.open(update_path) as archive:
                    archive.extractall(update_directory)
                update_root = update_directory / 'nexus-wheels-update'
                network = environment['COMPOSE_PROJECT_NAME'] + '_default'
                for repeat in range(2):
                    subprocess.run(['bash', 'upload-wheels.sh', '--url', 'http://nexus:8081',
                                    '--network', network], cwd=update_root, env=environment, check=True)
                uploader = (update_root / 'uploader-image.txt').read_text().strip()
                subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '--network', network,
                                '-e', 'NEXUS_URL=http://nexus:8081', '-e', 'NEXUS_REPOSITORY=pypi-offline',
                                '-v', f'{update_root / "scripts"}:/scripts:ro',
                                '-v', f'{update_root / "requirements.txt"}:/requirements.txt:ro',
                                '--entrypoint', 'python', uploader,
                                '/scripts/nexus_admin.py', 'verify-install'], check=True)
            # Original packages remain installable after the updates.
            if args.wheels_update:
                subprocess.run(['bash', 'verify-install.sh'], cwd=root, env=environment, check=True)
        except subprocess.CalledProcessError:
            subprocess.run(['docker', 'compose', 'logs', '--tail', '100', 'nexus'],
                           cwd=root, env=environment, check=False)
            raise
        finally:
            subprocess.run(['docker', 'compose', 'down', '--volumes', '--remove-orphans'],
                           cwd=root, env=environment, check=True)
    print('Offline integration passed: bootstrap, duplicate upload, clean install.')


if __name__ == '__main__':
    main()
