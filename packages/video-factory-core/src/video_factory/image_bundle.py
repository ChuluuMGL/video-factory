"""Version-bound, trusted-manifest Docker delivery; no registry access on import."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import tarfile

from .runtime_store import RuntimeFault, canonical, exclusive_write, private_directory, private_file

ROLES = ('runtime', 'postgres', 'n8n', 'gateway')
MAX_IMAGE_BYTES = 6 * 1024**3


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate_manifest(value, wheels, locks):
    fields = {'schema', 'platform', 'locks', 'wheels', 'images', 'archive'}
    if isinstance(value, dict) and value.get('schema') == 2:
        fields.add('oci_images')
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['schema']) is not int or value['schema'] not in (1, 2) or value['platform'] != 'linux/amd64'
            or value['wheels'] != wheels or value['locks'] != locks
            or not isinstance(value['images'], dict) or set(value['images']) != set(ROLES)
            or any(not isinstance(v, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in value['images'].values())):
        raise RuntimeFault('IMAGE_BUNDLE_RELEASE_OR_PLATFORM_MISMATCH')
    if value['schema'] == 2 and (not isinstance(value['oci_images'], dict) or set(value['oci_images']) != set(ROLES)
            or any(not isinstance(v, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', v) for v in value['oci_images'].values())):
        raise RuntimeFault('IMAGE_BUNDLE_PORTABLE_IDS_INVALID')
    archive = value['archive']
    if (not isinstance(archive, dict) or set(archive) != {'file', 'sha256', 'size'} or archive['file'] != 'images.tar'
            or not isinstance(archive['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', archive['sha256'])
            or type(archive['size']) is not int or not 0 < archive['size'] <= MAX_IMAGE_BYTES):
        raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INVALID')


def inspect_bundle(directory, expected_sha, wheels, locks):
    # The digest must arrive via the trusted release channel, not be accepted
    # automatically from an adjacent, self-reported checksum file.
    if not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha):
        raise RuntimeFault('TRUSTED_IMAGE_MANIFEST_SHA256_REQUIRED')
    directory = private_directory(directory)
    manifest = directory/'manifest.json'
    private_file(manifest)
    if manifest.stat().st_size > 65536 or file_hash(manifest) != expected_sha:
        raise RuntimeFault('IMAGE_BUNDLE_MANIFEST_CHANGED')
    value = json.loads(manifest.read_text())
    validate_manifest(value, wheels, locks)
    archive = directory/'images.tar'
    private_file(archive)
    if archive.stat().st_size != value['archive']['size'] or file_hash(archive) != value['archive']['sha256']:
        raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_CHANGED')
    return value


def inspect_image(reference):
    from .stack import run
    value = json.loads(run(['docker', 'image', 'inspect', reference]))[0]
    if value.get('Os') != 'linux' or value.get('Architecture') != 'amd64':
        raise RuntimeFault('IMAGE_BUNDLE_PLATFORM_MISMATCH')
    image_id = value.get('Id', '')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise RuntimeFault('IMAGE_BUNDLE_IMAGE_ID_INVALID')
    return image_id


def verify_loaded(manifest):
    resolved = {}
    for role in ROLES:
        candidates = [manifest['images'][role]]
        if manifest['schema'] == 2:
            candidates.append(manifest['oci_images'][role])
        for image_id in candidates:
            try:
                loaded = inspect_image(image_id)
            except RuntimeFault as error:
                if str(error) == 'STACK_COMMAND_FAILED':
                    continue
                raise
            if loaded not in candidates:
                raise RuntimeFault('IMAGE_BUNDLE_LOADED_ID_MISMATCH')
            resolved[role] = loaded
            break
        else:
            raise RuntimeFault('IMAGE_BUNDLE_REQUIRED_IMAGE_MISSING')
    return resolved


def verify_archive_index(archive, ids):
    """Both Docker and OCI readers must discover every required config digest."""
    expected = set(ids.values())
    with tarfile.open(archive, 'r:') as source:
        def read(name):
            member = source.getmember(name)
            if not member.isfile() or member.size > 1024 * 1024:
                raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
            return source.extractfile(member).read()
        docker = json.loads(read('manifest.json'))
        configs = {'sha256:' + Path(item['Config']).name.removesuffix('.json') for item in docker}
        if configs != expected:
            raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_IMAGES_MISSING')
        # Older Docker archives need no OCI index. If present, containerd uses
        # it instead of manifest.json, so a complete legacy list is not enough.
        if 'index.json' not in source.getnames():
            return {}
        found = {}
        def visit(value, depth=0, descriptor=None):
            if depth > 4:
                raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
            if 'config' in value:
                config_id = value['config']['digest']
                if descriptor is None or config_id in found and found[config_id] != descriptor:
                    raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
                found[config_id] = descriptor
                return
            entries = value.get('manifests', [])
            if not 0 < len(entries) <= 32:
                raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
            for entry in entries:
                checksum = entry['digest']
                if not re.fullmatch(r'sha256:[0-9a-f]{64}', checksum):
                    raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
                raw = read('blobs/sha256/' + checksum[7:])
                if hashlib.sha256(raw).hexdigest() != checksum[7:]:
                    raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
                visit(json.loads(raw), depth + 1, checksum)
        visit(json.loads(read('index.json')))
        if set(found) != expected:
            raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_IMAGES_MISSING')
        return found


def import_bundle(directory, expected_sha, wheels, locks):
    from .stack import admin_host, local_engine, run
    admin_host(); local_engine()
    value = inspect_bundle(directory, expected_sha, wheels, locks)
    # No shell and no Python extraction of the Docker archive. Input has been
    # hashed in a private owner-only directory before invoking the engine.
    run(['docker', 'image', 'load', '--input', str(Path(directory)/'images.tar')], timeout=900)
    verify_loaded(value)
    return value


def export_bundle(directory, wheelhouse, source_root=None):
    from .stack import admin_host, local_engine, images, run, ASSETS
    from .setup_deploy import release_manifest
    admin_host(); local_engine()
    directory = private_directory(directory)
    if any(directory.iterdir()):
        raise RuntimeFault('IMAGE_BUNDLE_EMPTY_DIRECTORY_REQUIRED')
    wheels = release_manifest(wheelhouse)
    locks = images()
    source_images = None
    replace_dependencies = False
    if source_root is not None:
        from .stack import Stack
        source = Stack(source_root)
        old = source.config.get('image_bundle')
        if old is None: raise RuntimeFault('IMAGE_BUNDLE_SOURCE_OFFLINE_REQUIRED')
        validate_manifest(old, source.config['wheels'], locks)
        product = lambda values: {name: digest for name, digest in values.items()
                                  if name.startswith('video_factory_core-')}
        if product(wheels) == product(source.config['wheels']):
            raise RuntimeFault('IMAGE_BUNDLE_UPGRADE_WHEELS_CHANGED')
        other = lambda values: {name: digest for name, digest in values.items()
                                if not name.startswith('video_factory_core-')}
        replace_dependencies = other(wheels) != other(source.config['wheels'])
        source_images = verify_loaded(old)
    else:
        for role in ('python', 'ffmpeg', 'postgres', 'n8n', 'gateway'):
            run(['docker', 'pull', '--platform', 'linux/amd64', locks[role]], timeout=600)
    with tempfile.TemporaryDirectory(prefix='.image-build-', dir=directory) as temporary:
        context = Path(temporary)
        if source_images is None:
            shutil.copyfile(ASSETS/'Dockerfile', context/'Dockerfile')
        else:
            install = ('--no-index --find-links=/wheels --only-binary=:all: '
                       '--upgrade --force-reinstall ' if replace_dependencies else
                       '--no-index --no-deps --force-reinstall ')
            (context/'Dockerfile').write_text(
                'FROM '+source_images['runtime']+'\n'
                'USER root\n'
                'COPY wheels /wheels\n'
                'RUN python -m pip install '+install+
                '/wheels/video_factory_core-*.whl && rm -rf /wheels\n'
                'USER 10001:10001\n')
        (context/'wheels').mkdir()
        for name in wheels if source_images is None or replace_dependencies else (
                name for name in wheels if name.startswith('video_factory_core-')):
            shutil.copyfile(wheelhouse/name, context/'wheels'/name)
        expected = wheels if source_images is None or replace_dependencies else {
            name: digest for name, digest in wheels.items() if name.startswith('video_factory_core-')}
        if release_manifest(context/'wheels') != expected:
            raise RuntimeFault('IMAGE_BUNDLE_WHEELS_CHANGED')
        iidfile = directory/'runtime.iid'
        build = ['docker', 'build', '--platform', 'linux/amd64', '--network=none', '--pull=false',
                 '--iidfile', str(iidfile)]
        if source_images is None:
            build += ['--build-arg', 'PYTHON_IMAGE='+locks['python'],
                      '--build-arg', 'FFMPEG_IMAGE='+locks['ffmpeg']]
        run([*build, str(context)], timeout=900)
        runtime_id = iidfile.read_text().strip()
        ids = {'runtime': inspect_image(runtime_id)}
        ids.update({role: inspect_image(locks[role] if source_images is None else source_images[role])
                    for role in ROLES if role != 'runtime'})
        iidfile.unlink()
    archive = directory/'images.tar'
    # Saving bare IDs can produce a complete Docker manifest.json but an OCI
    # index containing only one image. Explicit content-derived tags retain all
    # four roots for both classic and containerd image-store importers.
    tags = {}
    for role, image_id in ids.items():
        tag = 'vf-offline/' + role + ':' + image_id[7:]
        run(['docker', 'image', 'tag', image_id, tag])
        tags[role] = tag
    run(['docker', 'image', 'save', '--output', str(archive), *tags.values()], timeout=900)
    archive.chmod(0o600)
    # ImageInspect.Id is a config digest on classic, an OCI digest on
    # containerd. Read canonical config identities from the saved archive.
    with tarfile.open(archive, 'r:') as source:
        member = source.getmember('manifest.json')
        if not member.isfile() or member.size > 1024 * 1024:
            raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_INDEX_INVALID')
        saved = json.load(source.extractfile(member))
    ids = {}
    for role, tag in tags.items():
        matches = [item for item in saved if tag in (item.get('RepoTags') or [])]
        if len(matches) != 1:
            raise RuntimeFault('IMAGE_BUNDLE_ARCHIVE_IMAGES_MISSING')
        ids[role] = 'sha256:' + Path(matches[0]['Config']).name.removesuffix('.json')
    portable = verify_archive_index(archive, ids)
    if not portable:
        raise RuntimeFault('IMAGE_BUNDLE_PORTABLE_INDEX_REQUIRED')
    value = {'schema': 2, 'platform': 'linux/amd64', 'locks': locks, 'wheels': wheels, 'images': ids,
             'oci_images': {role: portable[image_id] for role, image_id in ids.items()},
             'archive': {'file': archive.name, 'size': archive.stat().st_size, 'sha256': file_hash(archive)}}
    validate_manifest(value, wheels, locks)
    exclusive_write(directory/'manifest.json', (canonical(value)+'\n').encode())
    return {'status': 'image_bundle_exported', 'manifest_sha256': file_hash(directory/'manifest.json'),
            'archive_sha256': value['archive']['sha256'], 'archive_bytes': value['archive']['size'], 'paid_requests': 0}


def add_arguments(parser):
    parser.add_argument('--image-bundle', type=Path, help='private directory containing images.tar and manifest.json')
    parser.add_argument('--image-manifest-sha256', help='trusted manifest SHA256 from the release channel')


def requested(args, wheels, locks):
    directory = getattr(args, 'image_bundle', None)
    checksum = getattr(args, 'image_manifest_sha256', None)
    if directory is None and checksum is None:
        return None
    if directory is None:
        raise RuntimeFault('IMAGE_BUNDLE_DIRECTORY_REQUIRED')
    return inspect_bundle(directory, checksum, wheels, locks)
