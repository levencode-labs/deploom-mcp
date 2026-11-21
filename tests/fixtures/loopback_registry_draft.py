"""Run the real Draft with fixture DNS and a test CA; never contact public hosts."""
from pathlib import Path
import runpy
import socket
import sys

import requests.adapters

cert, port, blocked, generator, *args = sys.argv[1:]
requests.adapters.DEFAULT_CA_BUNDLE_PATH = cert
resolve = socket.getaddrinfo


def fixture_dns(host, original_port, *args, **kwargs):
    if host not in {"registry.npmjs.org", "mirror.test.invalid"} or int(original_port) != 443:
        with open(blocked, "a", encoding="utf-8") as record:
            record.write(f"{host}:{original_port}\n")
        raise socket.gaierror("Fixture has no external DNS")
    return resolve("127.0.0.1", int(port), *args, **kwargs)


socket.getaddrinfo = fixture_dns
sys.path.insert(0, str(Path(generator).parent))
sys.argv = [generator, *args]
runpy.run_path(generator, run_name="__main__")
