"""Hard process lifetime independent of the host's docker-compose client."""
import signal
from .cli import main


def run():
    signal.alarm(420)
    try:return main()
    finally:signal.alarm(0)


if __name__=='__main__':raise SystemExit(run())
