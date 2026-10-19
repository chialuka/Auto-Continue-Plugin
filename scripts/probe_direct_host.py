"""Read-only feasibility probe. Never starts a turn, resumes a chat, or creates a timer."""
import argparse
import json
import os
import selectors
import shutil
import subprocess
import time


class Client:
    ALLOWED = {'initialize', 'account/rateLimits/read', 'thread/read'}

    def __init__(self, command):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b''
        self.sequence = 0

    def call(self, method, params, timeout=15):
        if method not in self.ALLOWED:
            raise ValueError('Probe permits only read-only methods')
        self.sequence += 1
        request = {'id': self.sequence, 'method': method, 'params': params}
        self.process.stdin.write(json.dumps(request).encode() + b'\n')
        self.process.stdin.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                response = json.loads(line)
                if response.get('id') == self.sequence:
                    if 'error' in response:
                        raise RuntimeError(str(response['error']))
                    return response['result']
            if self.selector.select(min(.25, max(0, end-time.monotonic()))):
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('Connection closed before response')
                self.buffer += chunk
        raise TimeoutError(method)

    def initialize(self):
        self.call('initialize', {'clientInfo': {'name': 'resume_after_reset_probe', 'version': '0.1.0'}})
        self.process.stdin.write(b'{"method":"initialized"}\n')
        self.process.stdin.flush()

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


def investigate(codex, thread):
    report = {'modelTurnsStarted': 0, 'timersCreated': 0,
              'liveDesktopAttach': False, 'safeAutomaticDeliveryVerified': False}
    proxy = Client([codex, 'app-server', 'proxy'])
    try:
        proxy.initialize()
        # A successful handshake alone does not establish ownership of the desktop chat.
        report['controlSocketHandshake'] = True
    except (RuntimeError, TimeoutError, OSError) as error:
        report['controlSocketHandshake'] = False
        report['controlSocketError'] = str(error)
    finally:
        proxy.close()
    client = Client([codex, 'app-server', '--listen', 'stdio://'])
    try:
        client.initialize()
        usage = client.call('account/rateLimits/read', {})
        limits = usage.get('rateLimitsByLimitId') or {}
        report['freshUsageRead'] = True
        report['ordinaryUsageAllowed'] = usage.get('ordinaryUsageAllowed')
        report['windows'] = {bucket: {key: value.get(key) for key in ('primary', 'secondary')}
                             for bucket, value in limits.items()}
        if thread:
            response = client.call('thread/read', {'threadId': thread, 'includeTurns': False})
            saved = response['thread']
            report['savedChatFound'] = saved['id'] == thread
            report['separateProcessChatStatus'] = saved.get('status')
        report['conclusion'] = ('Fresh usage can be read without inference. Saved chat visibility '
                                'does not establish live desktop attachment or safe concurrent delivery. '
                                'Do not enable an autonomous writer from this probe result.')
    except (RuntimeError, TimeoutError, OSError) as error:
        report['readProbeError'] = str(error)
    finally:
        client.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex'))
    parser.add_argument('--thread', help='Exact chat ID supplied by its user; no unrelated chats are searched')
    args = parser.parse_args()
    if not args.codex:
        parser.error('A Codex app-server executable is required')
    print(json.dumps(investigate(args.codex, args.thread), indent=2))
