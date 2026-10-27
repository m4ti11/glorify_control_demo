"""Local control channel. Only the agent process opens and mutates its spool."""

import argparse
import json
import os
import socket
import socketserver
import subprocess
import time
from pathlib import Path
from threading import Thread

SERVICES = ("metrics", "events", "pg-statements", "delivery")
ROUTE_SERVICES = {"metrics": "metrics", "events": "events", "pg-statements": "pg-statements"}
MAX_REQUEST = 4096


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                raise ValueError("Invalid control request")
            request = json.loads(raw)
            result = self.server.agent.control(request["action"], request["services"])
            response = {"ok": True, **result}
        except (ValueError, KeyError, TypeError):
            response = {"ok": False, "error": "Invalid command or services"}
        except Exception:
            response = {"ok": False, "error": "Could not apply command"}
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except OSError:
            pass


class ControlServer:
    def __init__(self, path, agent):
        self.path = Path(path)
        # The caller holds the exclusive process lock, so any old socket is stale.
        self.path.unlink(missing_ok=True)
        self.server = socketserver.UnixStreamServer(str(self.path), ControlHandler)
        os.chmod(self.path, 0o600)
        self.server.agent = agent
        self.thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join()
        self.server.server_close()
        self.path.unlink(missing_ok=True)


def send_command(path, action, services):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(path)
        client.sendall(json.dumps({"action": action, "services": services}).encode() + b"\n")
        with client.makefile("rb") as response:
            raw = response.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
            raise ValueError("Invalid control response")
        result = json.loads(raw)
        if not result.get("ok"):
            raise ValueError(result.get("error", "Command failed"))
        return result


def parser():
    result = argparse.ArgumentParser(description="Glorify Control agent and local task control")
    commands = result.add_subparsers(dest="command")
    commands.add_parser("run", help="Run the agent in the foreground (used by systemd)")
    for action in ("start", "pause", "resume", "status"):
        command = commands.add_parser(action)
        command.add_argument("--all", action="store_true", help="All services (default)")
        for service in SERVICES:
            flags = ["--" + service]
            if service == "pg-statements":
                flags.append("--daily")
            command.add_argument(*flags, dest="services", action="append_const", const=service)
        spool_path = os.environ.get("GLORIFY_SPOOL_PATH", "/var/lib/glorify-control-agent/spool.sqlite3")
        command.add_argument("--socket-path", default=spool_path + ".control.sock")
    return result


def execute_command(args):
    services = args.services or list(SERVICES)
    action = "resume" if args.command == "start" else args.command
    try:
        try:
            result = send_command(args.socket_path, action, services)
        except (FileNotFoundError, ConnectionRefusedError):
            if args.command != "start":
                raise ValueError("The agent is not running; use start --all") from None
            # No shell and no loading/sourcing of the credentials file by the CLI.
            subprocess.run(
                ["systemctl", "start", "glorify-control-agent"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            deadline = time.monotonic() + 10
            while True:
                try:
                    result = send_command(args.socket_path, action, services)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        raise ValueError("The service did not open its socket; check journalctl") from None
                    time.sleep(0.1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except PermissionError:
        print("Permission denied; run the command with sudo")
    except (subprocess.SubprocessError, FileNotFoundError):
        print("Could not start the systemd service; check installation and permissions")
    except (OSError, ValueError) as error:
        print(str(error))
    return 1
