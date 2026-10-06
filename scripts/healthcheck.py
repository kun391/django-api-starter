"""Internal container readiness probe: no redirects, tokens or proxy settings."""

import http.client
import json
import os
import sys


def main():
    host = os.environ.get("ALLOWED_HOSTS", "localhost").split(",")[0].strip()
    client = http.client.HTTPConnection("127.0.0.1", 8000, timeout=3)
    try:
        client.request("GET", "/health/ready/", headers={"Host": host})
        response = client.getresponse()
        data = response.read(4096)
        return 0 if response.status == 200 and json.loads(data).get("status") == "ready" else 1
    except (OSError, ValueError, http.client.HTTPException):
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
