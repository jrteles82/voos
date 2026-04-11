import signal
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
processes = []


def shutdown(*_args):
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    py = str(BASE_DIR / '.venv' / 'bin' / 'python')
    children = [
        [py, str(BASE_DIR / 'bot.py')],
        [py, str(BASE_DIR / 'bot_scheduler.py')],
        [py, str(BASE_DIR / 'job_worker.py')],
    ]

    for cmd in children:
        processes.append(subprocess.Popen(cmd))

    while True:
        for proc in processes:
            code = proc.poll()
            if code is not None:
                shutdown()
        signal.pause()


if __name__ == '__main__':
    main()
