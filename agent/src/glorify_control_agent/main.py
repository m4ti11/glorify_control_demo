import fcntl
import logging
import os
import signal
from pathlib import Path
from threading import Event

from glorify_control_agent.config import Settings
from glorify_control_agent.control import ControlServer, execute_command, parser
from glorify_control_agent.logging_config import configure_logging
from glorify_control_agent.scheduler import Agent
from glorify_control_agent.storage.spool import Spool
from glorify_control_agent.transport.client import Client


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.command not in (None, "run"):
        if args.all and args.services:
            cli.error("--all cannot be combined with individual services")
        raise SystemExit(execute_command(args))
    run()


def run():
    os.umask(0o077)
    configure_logging()
    log = logging.getLogger(__name__)
    try:
        settings = Settings()
    except Exception:
        log.error("configuration_invalid check_environment_and_README")
        raise SystemExit(2) from None
    Path(settings.spool_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Prevent two processes sending the same queue simultaneously.
    with open(settings.spool_path + ".lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("another_agent_owns_spool")
            raise SystemExit(1) from None
        spool = Spool(
            settings.spool_path,
            settings.spool_max_bytes,
            settings.spool_max_items,
            settings.spool_retention_seconds,
        )
        client = Client(settings)
        agent = Agent(settings, spool, client)
        control = ControlServer(settings.spool_path + ".control.sock", agent)
        stopped = Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stopped.set())
        try:
            # Keep control responsive while the initial PostgreSQL probe is running.
            control.start()
            agent.start()
            log.info("agent_started")
            stopped.wait()
        finally:
            control.close()
            if agent.scheduler.running:
                agent.stop()
            client.close()
            spool.close()
            log.info("agent_stopped")


if __name__ == "__main__":
    main()
