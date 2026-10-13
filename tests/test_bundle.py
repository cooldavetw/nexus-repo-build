import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import subprocess
import zipfile
import unittest
from unittest.mock import patch
import urllib.error

from nexus_bundle.cli import DEFAULT, build, initialize, load_config, validate_requirements, verify

spec = importlib.util.spec_from_file_location('nexus_admin', Path(__file__).parents[1] / 'nexus_bundle/templates/nexus_admin.py')
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


class BundleTests(unittest.TestCase):
    def test_init_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            config = load_config(root / 'bundle.json')
            self.assertEqual(config, DEFAULT)
            with self.assertRaises(ValueError):
                initialize(root)

    def test_requirements_reject_remote_and_unpinned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requirements.txt'
            for content in ('requests', '-r other.txt', 'foo @ https://example.com/x.whl', 'foo>=1', ''):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    validate_requirements(path)
            path.write_text('# comment\nrequests[socks]==2.32.5 # comment\n')
            self.assertEqual(validate_requirements(path), ['requests[socks]==2.32.5'])

    def test_config_rejects_latest_and_shell_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.json'
            for config in ({'nexus_image': 'sonatype/nexus3:latest'}, {'repository': 'foo/bar'},
                           {'python_image': 'python:3.12\nSECRET=1'}, {'container_platform': 'windows/amd64'}):
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    load_config(path)

    def archive(self, path, content=b'payload', expected=None, unsafe=False):
        with tarfile.open(path, 'w:gz') as archive:
            sha = hashlib.sha256(content if expected is None else expected).hexdigest()
            for name, body in [('nexus-bundle/file', content),
                               ('nexus-bundle/SHA256SUMS', f'{sha}  file\n'.encode())]:
                member = tarfile.TarInfo(name)
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            if unsafe:
                member = tarfile.TarInfo('nexus-bundle/link')
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
                archive.addfile(member)

    def test_verify_rejects_corruption_and_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar.gz'
            self.archive(path)
            verify(path)
            self.archive(path, expected=b'other')
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                verify(path)
            self.archive(path, unsafe=True)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                verify(path)

    def test_verify_allows_mutable_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.tar'
            payload = b'original defaults'
            checksum = hashlib.sha256(payload).hexdigest()
            with tarfile.open(path, 'w') as archive:
                for name, content in [('.env', b'NEXUS_PORT=18081\n'),
                                      ('.env.example', payload),
                                      ('SHA256SUMS', f'{checksum}  .env.example\n'.encode())]:
                    member = tarfile.TarInfo('nexus-bundle/' + name)
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
            verify(path)

    def test_deployment_and_wheels_build_modes(self):
        for wheels_only, include_uploader in ((True, False), (True, True), (False, False)):
            with self.subTest(wheels_only=wheels_only, include_uploader=include_uploader), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                initialize(root)
                (root / 'requirements.txt').write_text('example==1.0\n')
                commands = []

                def fake_run(arguments, **kwargs):
                    arguments = list(map(str, arguments))
                    commands.append(arguments)
                    if 'download' in arguments:
                        mount = arguments[arguments.index('-v') + 1].removesuffix(':/bundle')
                        wheel = Path(mount) / 'wheels/example-1.0-py3-none-any.whl'
                        with zipfile.ZipFile(wheel, 'w') as archive:
                            archive.writestr('example-1.0.dist-info/METADATA',
                                             'Metadata-Version: 2.4\nName: example\nVersion: 1.0\n')
                    if arguments[1:3] == ['image', 'save']:
                        Path(arguments[arguments.index('--output') + 1]).write_bytes(b'image archive')
                    return subprocess.CompletedProcess(arguments, 0, stdout=json.dumps([
                        {'Id': 'sha256:123', 'Architecture': 'amd64', 'Os': 'linux'}]))

                with patch('nexus_bundle.cli.run', side_effect=fake_run):
                    build(root / 'bundle.json', wheels_only=wheels_only, include_uploader=include_uploader)
                bundle_name = 'nexus-wheels-update' if wheels_only else 'nexus-bundle'
                output = root / 'dist' / (bundle_name + '.tar.gz')
                verify(output)
                with tarfile.open(output) as archive:
                    names = archive.getnames()
                    manifest = json.load(archive.extractfile(bundle_name + '/manifest.json'))
                self.assertEqual(manifest['kind'], 'wheels-update' if wheels_only else 'deployment')
                self.assertEqual(manifest['uploader_included'], not wheels_only or include_uploader)
                self.assertIn(bundle_name + '/upload-wheels.sh', names)
                self.assertEqual(bundle_name + '/compose.yaml' in names, not wheels_only)
                self.assertEqual(bundle_name + '/bootstrap-nexus.sh' in names, not wheels_only)
                self.assertEqual(bundle_name + '/images/uploader.tar' in names,
                                 not wheels_only or include_uploader)
                self.assertEqual(any('sonatype/nexus3' in ' '.join(command) for command in commands),
                                 not wheels_only)
                self.assertEqual(any('compose' in command for command in commands), not wheels_only)

    def test_failed_build_does_not_publish_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / 'requirements.txt').write_text('requests==2.32.5\n')
            with patch('nexus_bundle.cli.run', side_effect=RuntimeError('Docker failed')):
                with self.assertRaises(RuntimeError):
                    build(root / 'bundle.json')
            self.assertFalse((root / DEFAULT['output']).exists())


class NexusTests(unittest.TestCase):
    def setUp(self):
        with patch.dict('os.environ', NEXUS_REPOSITORY='pypi-offline', NEXUS_PASSWORD='new'):
            self.nexus = admin.Nexus()

    def test_url_rejects_embedded_credentials_and_supports_context_path(self):
        for url in ('http://admin:secret@nexus:8081', 'file:///etc/passwd',
                    'http://nexus?token=secret', 'http://nexus/#fragment'):
            with patch.dict('os.environ', NEXUS_URL=url, NEXUS_REPOSITORY='pypi-offline'):
                with self.assertRaises(RuntimeError):
                    admin.Nexus()
        with patch.dict('os.environ', NEXUS_URL='https://nexus.example/nexus/', NEXUS_REPOSITORY='pypi-offline'):
            self.assertEqual(admin.Nexus().url, 'https://nexus.example/nexus')

    def test_bootstrap_existing_wrong_repository_fails(self):
        with patch.object(self.nexus, 'request', side_effect=[None, [
                {'name': 'pypi-offline', 'format': 'raw', 'type': 'hosted'}]]):
            with self.assertRaisesRegex(RuntimeError, 'not PyPI'):
                self.nexus.bootstrap()

    def test_initial_password_change(self):
        unauthorized = urllib.error.HTTPError('http://nexus', 401, 'Unauthorized', {}, None)
        with patch.dict('os.environ', NEXUS_INITIAL_PASSWORD='old'), patch.object(
                self.nexus, 'request', side_effect=[unauthorized, None, None, [], None]) as request:
            self.nexus.bootstrap()
            self.assertEqual(request.call_args_list[1].args[0],
                             '/service/rest/v1/security/users/admin/change-password')
            self.assertEqual(self.nexus.password, 'new')

    def test_eula_requires_explicit_authorization(self):
        agreement = {'accepted': False, 'disclaimer': 'Server terms'}
        with patch.object(self.nexus, 'request', return_value=agreement) as request:
            self.assertFalse(self.nexus.eula())
            self.assertEqual(request.call_count, 1)
        with patch.object(self.nexus, 'request', side_effect=[agreement.copy(), None]) as request:
            self.assertTrue(self.nexus.eula(accept=True))
            self.assertEqual(request.call_args_list[1].args,
                             ('/service/rest/v1/system/eula', 'POST',
                              {'accepted': True, 'disclaimer': 'Server terms'}))

    def test_assets_pagination(self):
        with patch.object(self.nexus, 'request', side_effect=[
                {'items': [{'path': 'x/a.whl'}], 'continuationToken': 'next'},
                {'items': [{'path': 'x/b.whl'}], 'continuationToken': None}]) as request:
            self.assertEqual(set(self.nexus.assets()), {'a.whl', 'b.whl'})
            self.assertIn('continuationToken=next', request.call_args_list[1].args[0])

    def test_assets_ignore_generated_index_files_but_reject_duplicate_wheels(self):
        with patch.object(self.nexus, 'request', return_value={
                'items': [{'path': 'simple/a/index.json'}, {'path': 'simple/b/index.json'},
                          {'path': 'packages/a/a.whl.metadata'}, {'path': 'packages/a/a.whl'}],
                'continuationToken': None}):
            self.assertEqual(set(self.nexus.assets()), {'a.whl'})
        with patch.object(self.nexus, 'request', return_value={
                'items': [{'path': 'one/a.whl'}, {'path': 'two/a.whl'}],
                'continuationToken': None}):
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous remote asset'):
                self.nexus.assets()

    def test_matching_upload_skips_and_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            wheel = Path(directory) / 'a.whl'
            wheel.write_bytes(b'wheel')
            checksum = hashlib.sha256(b'wheel').hexdigest()
            with patch.object(admin.Path, 'glob', return_value=[wheel]), patch.object(
                    self.nexus, 'assets', return_value={'a.whl': {'checksum': {'sha256': checksum}}}), patch.object(
                    admin.subprocess, 'run') as run:
                self.nexus.upload()
                run.assert_not_called()
            with patch.object(admin.Path, 'glob', return_value=[wheel]), patch.object(
                    self.nexus, 'assets', return_value={'a.whl': {'checksum': {'sha256': 'wrong'}}}):
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    self.nexus.upload()


if __name__ == '__main__':
    unittest.main()
