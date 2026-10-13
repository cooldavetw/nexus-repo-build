import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

TEMPLATES = Path(__file__).with_name('templates')
DEFAULT = {
    'output': 'dist/nexus-bundle.tar.gz',
    'nexus_image': 'sonatype/nexus3:3.96.3-alpine',
    'container_platform': 'linux/amd64',
    'python_image': 'python:3.12.12-slim-bookworm',
    'repository': 'pypi-offline',
    'requirements': 'requirements.txt',
}


def run(arguments, **kwargs):
    print('+ ' + ' '.join(map(str, arguments)), flush=True)
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def digest(stream):
    sha = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        sha.update(chunk)
    return sha.hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return digest(stream)


def load_config(path):
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError('Configuration must be a JSON object')
    unknown = set(config) - set(DEFAULT)
    if unknown:
        raise ValueError(f'Unknown configuration keys: {sorted(unknown)}')
    config = DEFAULT | config
    if any(not isinstance(value, str) or not value for value in config.values()):
        raise ValueError('Configuration values must be nonempty strings')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', config['repository']):
        raise ValueError('Invalid repository name')
    for key in ('nexus_image', 'python_image'):
        image = config[key]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]*', image):
            raise ValueError(f'Invalid {key}')
        if '@sha256:' not in image and (':' not in image.rsplit('/', 1)[-1] or image.endswith(':latest')):
            raise ValueError(f'{key} must use a fixed tag or digest')
    if config['container_platform'] not in ('linux/amd64', 'linux/arm64'):
        raise ValueError('Supported container platforms: linux/amd64, linux/arm64')
    return config


def validate_requirements(path):
    lines = path.read_text().splitlines()
    requirements = []
    for line in lines:
        line = line.split(' #', 1)[0].strip()
        if not line or line.startswith('#'):
            continue
        # Exact pins keep resolution repeatable and avoid network references at deployment.
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9][A-Za-z0-9_.+!-]*', line):
            raise ValueError(f'Use one exact name==version pin per line; unsupported entry: {line}')
        requirements.append(line)
    if not requirements:
        raise ValueError('Requirements must contain at least one pinned package')
    return requirements


def initialize(directory):
    directory.mkdir(parents=True, exist_ok=True)
    files = {
        'bundle.json': json.dumps(DEFAULT, indent=2) + '\n',
        'requirements.txt.example': '# Copy to requirements.txt and edit the exact pins.\nrequests==2.32.5\n',
    }
    for name in files:
        if (directory / name).exists():
            raise ValueError(f'Refusing to overwrite {directory / name}')
    for name, content in files.items():
        (directory / name).write_text(content)
    print(f'Created configuration in {directory}. Copy requirements.txt.example to requirements.txt.')


def inventory(root):
    return {p.relative_to(root).as_posix(): file_digest(p)
            for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ('manifest.json', 'SHA256SUMS', '.env')}


def build(config_path, *, wheels_only=False, include_uploader=False, output_override=None):
    config_path = config_path.resolve()
    config = load_config(config_path)
    requirements = (config_path.parent / config['requirements']).resolve()
    pins = validate_requirements(requirements)
    output_name = output_override or ('dist/nexus-wheels-update.tar.gz' if wheels_only else config['output'])
    output = (config_path.parent / output_name).resolve()
    if output.exists():
        raise ValueError(f'Refusing to overwrite {output}')
    run(['docker', 'info'], stdout=subprocess.DEVNULL)
    if not wheels_only:
        run(['docker', 'compose', 'version'])
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the output for atomic publication with rename.
    with tempfile.TemporaryDirectory(prefix='.nexus-bundle-', dir=output.parent) as temporary:
        stage = Path(temporary)
        bundle_name = 'nexus-wheels-update' if wheels_only else 'nexus-bundle'
        root = stage / bundle_name
        for folder in ('images', 'wheels', 'scripts'):
            (root / folder).mkdir(parents=True, exist_ok=True)
        source_images = [config['python_image']]
        if not wheels_only:
            source_images.append(config['nexus_image'])
        for image in source_images:
            run(['docker', 'pull', '--platform', config['container_platform'], image])
        uploader = 'nexus-bundle-uploader:' + hashlib.sha256(
            (config['python_image'] + config['container_platform'] +
             (TEMPLATES / 'uploader.Dockerfile').read_text()).encode()).hexdigest()[:16]
        run(['docker', 'build', '--platform', config['container_platform'],
             '--build-arg', 'BASE_IMAGE=' + config['python_image'], '-t', uploader,
             '-f', TEMPLATES / 'uploader.Dockerfile', TEMPLATES])
        (root / 'requirements.txt').write_text('\n'.join(pins) + '\n')
        if not wheels_only:
            shutil.copy(TEMPLATES / 'compose.yaml', root / 'compose.yaml')
        shutil.copy(requirements, root / 'requirements.input.txt')
        (root / 'requirements.txt.example').write_text('# Exact package pins\nrequests==2.32.5\n')
        # Download in the target interpreter and CPU environment, including dependencies.
        run(['docker', 'run', '--rm', '--platform', config['container_platform'],
             '--user', f'{os.getuid()}:{os.getgid()}',
             '-e', 'HOME=/tmp', '-v', f'{root}:/bundle', '--entrypoint', 'python', uploader,
             '-m', 'pip', 'download', '--disable-pip-version-check', '--no-cache-dir',
             '--only-binary=:all:', '-r', '/bundle/requirements.txt', '-d', '/bundle/wheels'])
        wheels = sorted((root / 'wheels').glob('*.whl'))
        if not wheels:
            raise ValueError('Package collection produced no wheels')
        # Verify offline that the bundled uploader can parse every wheel's metadata.
        run(['docker', 'run', '--rm', '--network', 'none',
             '--platform', config['container_platform'], '-v', f'{root}:/bundle:ro',
             '--entrypoint', 'python', uploader, '-c',
             "from pathlib import Path; from twine.package import PackageFile; "
             "[PackageFile.from_filename(str(p), None) for p in Path('/bundle/wheels').glob('*.whl')]"])
        # Lock every resolved dependency by reading the wheels' authoritative metadata.
        import email
        import zipfile
        resolved = {}
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
                if len(metadata) != 1:
                    raise ValueError(f'Invalid wheel metadata: {wheel.name}')
                message = email.message_from_bytes(archive.read(metadata[0]))
                name, version = message['Name'], message['Version']
                if not name or not version:
                    raise ValueError(f'Missing wheel name/version: {wheel.name}')
                if name in resolved and resolved[name] != version:
                    raise ValueError(f'Conflicting versions for {name}')
                resolved[name] = version
        (root / 'requirements.txt').write_text(''.join(f'{n}=={v}\n' for n, v in sorted(resolved.items())))
        deployment_images = [uploader]
        if wheels_only:
            if include_uploader:
                run(['docker', 'image', 'save', '--output', root / 'images/uploader.tar', uploader])
            else:
                (root / 'images').rmdir()
            shutil.copy(TEMPLATES / 'update-upload.sh', root / 'upload-wheels.sh')
            (root / 'upload-wheels.sh').chmod(0o755)
            (root / 'uploader-image.txt').write_text(uploader + '\n')
            (root / 'container-platform.txt').write_text(config['container_platform'] + '\n')
            (root / 'repository.txt').write_text(config['repository'] + '\n')
            shutil.copy(TEMPLATES / 'wheels-README.md', root / 'README.md')
            script_names = ['verify-bundle.sh']
        else:
            # Save a local tag so digest-pinned images work after docker load.
            inspected = run(['docker', 'image', 'inspect', config['nexus_image']],
                            capture_output=True, text=True)
            nexus_id = json.loads(inspected.stdout)[0]['Id']
            nexus_local = 'nexus-bundle-nexus:' + nexus_id.split(':')[-1][:24]
            run(['docker', 'tag', config['nexus_image'], nexus_local])
            for name, image in (('nexus', nexus_local), ('uploader', uploader)):
                run(['docker', 'image', 'save', '--output', root / 'images' / f'{name}.tar', image])
            env = (f'NEXUS_IMAGE={nexus_local}\nUPLOADER_IMAGE={uploader}\n'
                   f'CONTAINER_PLATFORM={config["container_platform"]}\n'
                   f'NEXUS_REPOSITORY={config["repository"]}\nNEXUS_PORT=8081\nNEXUS_BIND=127.0.0.1\n')
            (root / '.env').write_text(env)
            (root / '.env.example').write_text(env)
            shutil.copy(TEMPLATES / 'bundle-README.md', root / 'README.md')
            deployment_images.append(config['nexus_image'])
            script_names = ['start.sh', 'bootstrap-nexus.sh', 'upload-wheels.sh',
                            'verify-install.sh', 'verify-bundle.sh']
        for name in script_names:
            shutil.copy(TEMPLATES / name, root / name)
            (root / name).chmod(0o755)
        shutil.copy(TEMPLATES / 'nexus_admin.py', root / 'scripts/nexus_admin.py')
        image_info = {}
        for image in deployment_images:
            result = run(['docker', 'image', 'inspect', image], capture_output=True, text=True)
            info = json.loads(result.stdout)[0]
            image_info[image] = {key: info.get(key) for key in ('Id', 'RepoDigests', 'Architecture', 'Os')}
        files = inventory(root)
        manifest = {'schema': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'kind': 'wheels-update' if wheels_only else 'deployment',
                    'uploader_included': not wheels_only or include_uploader,
                    'config': config, 'images': image_info, 'resolved_packages': resolved, 'files': files}
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        files['manifest.json'] = file_digest(root / 'manifest.json')
        (root / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(files.items())))
        archive_path = stage / 'bundle.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            archive.add(root, arcname=bundle_name)
        verify(archive_path)
        archive_path.rename(output)
    print(f'Created {output}')


def verify(path):
    # Do not extract untrusted archive paths. Hash file streams directly.
    with tarfile.open(path, 'r:*') as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError('Archive contains duplicate paths')
        roots = {Path(name).parts[0] for name in names if Path(name).parts}
        if len(roots) != 1 or not roots <= {'nexus-bundle', 'nexus-wheels-update'}:
            raise ValueError('Archive must contain one supported bundle directory')
        bundle_name = roots.pop()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != bundle_name or '..' in parts or not (member.isfile() or member.isdir()):
                raise ValueError(f'Unsafe archive member: {member.name}')
        sums = archive.extractfile(f'{bundle_name}/SHA256SUMS')
        if sums is None:
            raise ValueError('Missing SHA256SUMS')
        expected = {}
        for line in sums.read().decode().splitlines():
            sha, name = line.split('  ', 1)
            if not re.fullmatch(r'[a-f0-9]{64}', sha) or name in expected:
                raise ValueError('Invalid checksum inventory')
            expected[name] = sha
        actual = {m.name.removeprefix(bundle_name + '/'): m for m in members
                  if m.isfile() and m.name not in (f'{bundle_name}/SHA256SUMS', f'{bundle_name}/.env')}
        if set(actual) != set(expected):
            raise ValueError('Archive files do not match checksum inventory')
        for name, member in actual.items():
            with archive.extractfile(member) as stream:
                if digest(stream) != expected[name]:
                    raise ValueError(f'Checksum mismatch: {name}')
    print(f'Verified {path}')


def main():
    parser = argparse.ArgumentParser(description='Build offline Nexus deployment bundles')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Generate configuration and sample requirements')
    init.add_argument('directory', type=Path, nargs='?', default=Path('.'))
    create = sub.add_parser('build', help='Pull images, collect wheels, and create an archive')
    create.add_argument('--config', type=Path, default=Path('bundle.json'))
    update = sub.add_parser('build-wheels', help='Create a wheel update for an existing Nexus server')
    update.add_argument('--config', type=Path, default=Path('bundle.json'))
    update.add_argument('--output', help='Output path relative to the config directory')
    update.add_argument('--include-uploader', action='store_true',
                        help='Include the tools image for standalone offline uploads')
    check = sub.add_parser('verify', help='Validate archive contents without extracting')
    check.add_argument('archive', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(args.directory)
        elif args.command == 'build':
            build(args.config)
        elif args.command == 'build-wheels':
            build(args.config, wheels_only=True, include_uploader=args.include_uploader,
                  output_override=args.output)
        else:
            verify(args.archive)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
