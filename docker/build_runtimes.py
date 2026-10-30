"""Build three pinned, separate runtimes; runs only during Docker image creation."""
import subprocess
import shutil
import sys
from pathlib import Path


def run(*args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def checkout(name, repository, revision):
    path = Path('/opt/runtimes') / name
    path.mkdir(parents=True)
    run('git', 'init', path)
    run('git', '-C', path, 'remote', 'add', 'origin', repository)
    run('git', '-C', path, 'fetch', '--depth=1', 'origin', revision)
    run('git', '-C', path, 'checkout', '--detach', 'FETCH_HEAD')
    return path


spider = checkout('Spider_XHS', 'https://github.com/cv-cat/Spider_XHS.git',
                  '23dab803a0465e35e01cf87137353a900b47e93d')
run(sys.executable, '-m', 'venv', spider / '.venv')
run(spider / '.venv/bin/python', '-m', 'pip', 'install', '--no-cache-dir', '-r', '/build/requirements-lock.txt')
run('npm', 'ci', '--no-audit', '--no-fund', cwd=spider)
media = checkout('MediaCrawler', 'https://github.com/NanmiCoder/MediaCrawler.git',
                 '68d9ef1c1da22eab75940dfd7cec259907006538')
run('uv', 'sync', '--frozen', '--no-dev', '--python', sys.executable, '--project', media)
run(spider / '.venv/bin/python', '-c', 'from apis.xhs_pc_login_apis import XHSLoginApi; from apis.xhs_live import XHSLiveAPI', cwd=spider)
run(media / '.venv/bin/python', '-c', 'from media_platform.xhs.client import XiaoHongShuClient', cwd=media)
publisher = checkout('SocialAutoUpload', 'https://github.com/dreammis/social-auto-upload.git',
                     '3847fff01f5434a8a32829833163cd6723cabc59')
run(sys.executable, '-m', 'venv', publisher / '.venv')
run(publisher / '.venv/bin/python', '-m', 'pip', 'install', '--no-cache-dir', '-r', '/build/requirements-publish.lock')
shutil.copyfile(publisher / 'conf.example.py', publisher / 'conf.py')
run(publisher / '.venv/bin/python', '-c', 'import patchright.async_api')
