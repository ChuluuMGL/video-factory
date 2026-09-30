"""Cloud-only real PTY driver. Product secrets must never echo into output."""
import errno
import os
import pty
import select
import subprocess
import time


def drive(command, steps, *, secrets=(), timeout=900, on_prompt=None, expected_code=0):
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    output = b''; pending = b''; index = 0; deadline = time.monotonic()+timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], .2)
            if ready:
                try: chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO: raise
                    chunk = b''
                if not chunk:
                    process.wait(timeout=10)
                    break
                output += chunk; pending += chunk
                if len(output) > 1024*1024: raise AssertionError('PTY_OUTPUT_LIMIT')
                while index < len(steps) and steps[index][0].encode() in pending:
                    marker, answer = steps[index]
                    pending = pending.split(marker.encode(), 1)[1]
                    if on_prompt: on_prompt(index, output.decode(errors='replace'))
                    if answer is None: process.terminate()
                    else: os.write(master, (answer+'\n').encode())
                    index += 1
            if process.poll() is not None and not ready: break
        if process.poll() is None:
            process.terminate(); process.wait(timeout=30)
            raise AssertionError('PTY_DEADLINE')
        transcript = output.decode(errors='replace')
        # Synthetic-only diagnostics, with all hidden input removed if a test
        # fails before asserting the actual absence of those bytes.
        redacted = transcript
        for value in secrets: redacted = redacted.replace(value, '<redacted>')
        assert process.returncode == expected_code and index == len(steps), ('PTY_FAILED', process.returncode, index, redacted[-5000:])
        assert all(value not in transcript for value in secrets), 'HIDDEN_INPUT_ECHOED'
        return transcript
    finally:
        if process.poll() is None: process.terminate(); process.wait(timeout=30)
        os.close(master)
