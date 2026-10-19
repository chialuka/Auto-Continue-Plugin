"""Direct, inference-free usage reads and queued input through the installed CLI."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
from probe_direct_host import Client


def thread_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('An exact chat UUID is required')
    return value


def executable():
    candidate = (os.environ.get('RESUME_CODEX_EXECUTABLE') or shutil.which('codex') or
                 '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    path = Path(candidate).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('Install Codex or set RESUME_CODEX_EXECUTABLE to its executable')
    return str(path)


class Host:
    def __init__(self, codex=None):
        self.codex = codex or executable()

    def usage(self):
        client = Client([self.codex, 'app-server', '--listen', 'stdio://'])
        try:
            client.initialize()
            return client.call('account/rateLimits/read', {})
        finally:
            client.close()

    def queue(self, thread, instruction):
        thread_id(thread)
        result = subprocess.run([self.codex, 'queue', '--thread', thread, '--message', instruction],
                                text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Queue command failed; inspect delivery before rearming')
        match = re.search(r'Queued message ([0-9a-f-]{36}) for thread ([0-9a-f-]{36})', result.stdout)
        if not match or match[2] != thread:
            raise RuntimeError('Queue receipt unavailable; delivery is uncertain')
        return str(uuid.UUID(match[1]))
