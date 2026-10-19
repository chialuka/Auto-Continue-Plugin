"""Install/manage a per-user launchd worker, or configure direct timers without inference."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from direct_host import executable
from direct_scheduler import DEFAULT_INSTRUCTION, DirectStore, Worker, root_path

VERSION = '1.0.0'
LABEL = 'local.resume-after-reset.direct'
FILES = ('direct_service.py', 'direct_host.py', 'direct_scheduler.py', 'probe_direct_host.py')


def service_status(root=None):
    plist = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    if sys.platform != 'darwin':
        return {
            'installed': False,
            'loaded': False,
            'error': 'This installer currently supports macOS',
        }
    result = subprocess.run(
        ['launchctl', 'print', f'gui/{os.getuid()}/{LABEL}'],
        capture_output=True,
        text=True,
        timeout=10,
    )
    pid = re.search(r'\bpid = (\d+)', result.stdout)
    args = plistlib.loads(plist.read_bytes()).get('ProgramArguments', []) if plist.exists() else []
    data_root = args[args.index('--root') + 1] if '--root' in args else None
    return {
        'installed': plist.exists(),
        'loaded': result.returncode == 0,
        'pid': int(pid[1]) if pid else None,
        'plist': str(plist),
        'dataRoot': data_root,
    }


def install(root=None, codex=None, force=False):
    root = Path(root or root_path()).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'direct-install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = service_status(root)
        if state['loaded'] and state.get('dataRoot') != str(root):
            raise RuntimeError(
                'Existing service uses another data directory; inspect its timers first'
            )
        if state['loaded'] and state['pid'] and not force:
            return state
        return _install(root, codex)


def _install(root=None, codex=None):
    if sys.platform != 'darwin':
        raise RuntimeError('Automatic service installation currently supports macOS only')
    root = Path(root or root_path()).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    codex = codex or executable()
    source = Path(__file__).resolve().parent
    runtime_parent = root / 'runtime'
    runtime_parent.mkdir(exist_ok=True, mode=0o700)
    # Each install gets its own complete snapshot; never run out of a version cache.
    runtime = Path(tempfile.mkdtemp(prefix=VERSION + '-', dir=runtime_parent))
    for name in FILES:
        shutil.copy2(source / name, runtime / name)
    plist = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
    plist.parent.mkdir(parents=True, exist_ok=True)
    definition = {
        'Label': LABEL,
        'ProgramArguments': [
            str(Path(sys.executable).resolve()),
            str(runtime / 'direct_service.py'),
            '--root',
            str(root),
            '--codex',
            codex,
            'run',
        ],
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 10,
        'WorkingDirectory': str(root),
        'StandardOutPath': str(root / 'direct-service.log'),
        'StandardErrorPath': str(root / 'direct-service-error.log'),
        'EnvironmentVariables': {
            'PATH': os.pathsep.join(
                [
                    str(Path(codex).parent),
                    '/opt/homebrew/bin',
                    '/usr/local/bin',
                    '/usr/bin',
                    '/bin',
                    '/usr/sbin',
                    '/sbin',
                ]
            )
        },
    }
    if os.environ.get('CODEX_HOME'):
        definition['EnvironmentVariables']['CODEX_HOME'] = os.environ['CODEX_HOME']
    old = plistlib.loads(plist.read_bytes()) if plist.exists() else None
    if old:
        # Leave unrelated launch agents entirely alone.
        if old.get('Label') != LABEL:
            raise RuntimeError('Conflicting launch agent definition')
        result = subprocess.run(
            ['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, timeout=15
        )
        if result.returncode and service_status()['loaded']:
            raise RuntimeError('Could not stop the previous direct worker')
    temp = plist.with_suffix('.tmp')
    temp.write_bytes(plistlib.dumps(definition))
    temp.chmod(0o600)
    temp.replace(plist)
    result = subprocess.run(
        ['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise RuntimeError('launchd could not load the direct worker: ' + result.stderr.strip())
    for _ in range(20):
        state = service_status(root)
        if state['loaded'] and state['pid']:
            return state
        time.sleep(0.1)
    raise RuntimeError('Worker did not start; inspect direct-service-error.log')


def ensure_service(root=None):
    state = service_status(root)
    if state['loaded'] and state['dataRoot'] != str(Path(root or root_path()).resolve()):
        raise RuntimeError(
            'The running worker uses another data directory; inspect its timers before changing it'
        )
    if not state['loaded']:
        state = install(root)
    if not state['loaded']:
        raise RuntimeError('Background service is not loaded; no timer has been armed')
    return state


def run(root, codex):
    from direct_host import Host

    store = DirectStore(root)
    lock = (store.root / 'direct-worker.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    store.recover(time.time())
    worker = Worker(store, Host(codex))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(json.dumps({'event': 'started', 'version': VERSION, 'pid': os.getpid()}), flush=True)
    while not stopping:
        try:
            outcome = worker.tick()
            if outcome:
                print(json.dumps({'event': outcome, 'at': time.time()}), flush=True)
        except Exception as error:
            # Never log credentials, quota payloads, or saved instructions.
            print(json.dumps({'event': 'worker_error', 'type': type(error).__name__}), flush=True)
        time.sleep(2)
    lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(root_path()))
    parser.add_argument('--codex', default=None)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('install').add_argument('--force', action='store_true')
    sub.add_parser('run')
    sub.add_parser('uninstall')
    for name in ('enable', 'smoke', 'status', 'cancel'):
        command = sub.add_parser(name)
        command.add_argument('--thread', required=name != 'status')
        if name in ('enable', 'smoke'):
            command.add_argument('--instruction', default=DEFAULT_INSTRUCTION)
        if name == 'smoke':
            command.add_argument('--delay', type=int, default=30)
    args = parser.parse_args()
    os.environ['RESUME_AFTER_RESET_DATA'] = args.root
    if args.codex:
        os.environ['RESUME_CODEX_EXECUTABLE'] = args.codex
    if args.command == 'run':
        run(args.root, args.codex or executable())
        return
    if args.command == 'install':
        result = install(args.root, args.codex, force=args.force)
    elif args.command == 'uninstall':
        subprocess.run(
            ['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], capture_output=True, timeout=15
        )
        plist = Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist'
        if plist.exists():
            plist.unlink()
        result = {
            'service': 'removed',
            'savedTimers': 'retained but will not fire until service is installed',
        }
    else:
        store = DirectStore(args.root)
        if args.command == 'status':
            result = {
                'service': service_status(args.root),
                'job': store.status(args.thread) if args.thread else None,
            }
        elif args.command == 'cancel':
            result = store.cancel(args.thread)
        else:
            ensure_service(args.root)
            from direct_host import Host

            result = store.enable(
                args.thread,
                args.instruction,
                Host(args.codex or executable()),
                test_delay=args.delay if args.command == 'smoke' else None,
            )
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
