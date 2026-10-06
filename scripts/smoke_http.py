"""Exercise a disposable production container over HTTP, not Django's test client.

Never target a production database: this creates disposable accounts and files.
The caller supplies a loopback published Docker port. No secrets are printed.
"""

import http.client
import json
import secrets
import sys
from uuid import uuid4


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def smoke(port):
    def request(method, path, *, data=None, headers=None, secure=True):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        fields = {"Host": "api.example.test", "X-Request-ID": "production-smoke"}
        if secure:
            fields["X-Forwarded-Proto"] = "https"
        fields.update(headers or {})
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            fields["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=data, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(1024 * 1024)
        finally:
            connection.close()

    status, headers, body = request("GET", "/health/live/", secure=False, headers={"Authorization": "Token intentionally-invalid"})
    require(status == 200 and json.loads(body)["status"] == "ok", "Liveness must be independent of auth and HTTPS redirect.")
    require(headers.get("Cache-Control") == "no-store", "Liveness must not be cached.")
    status, _, body = request("GET", "/health/ready/", secure=False)
    require(status == 200 and json.loads(body)["status"] == "ready", "Readiness failed.")
    status, headers, _ = request("GET", "/api/v1/users/me/", secure=False)
    require(status == 301 and headers.get("Location", "").startswith("https://"), "API must redirect HTTP to HTTPS.")
    status, headers, _ = request("GET", "/api/v1/users/me/")
    require(status == 401 and headers.get("Cache-Control") == "no-store", "Anonymous API access must remain denied.")
    status, _, _ = request("GET", "/admin/login/")
    require(status == 200, "Admin HTML must render from the production image.")
    status, _, body = request("GET", "/static/admin/css/base.css")
    require(status == 200 and len(body) > 100, "Built static assets must be served by WhiteNoise.")

    username = "smoke-" + uuid4().hex
    email = username + "@example.test"
    password = secrets.token_urlsafe(32)
    status, _, _ = request("POST", "/api/v1/users/", data={"username": username, "email": email, "password": password})
    require(status == 201, "Production registration failed.")
    status, _, body = request("POST", "/api/v1/auth/token/", data={"username": email, "password": password})
    require(status == 200, "Production token login failed.")
    token = json.loads(body)["token"]
    auth = {"Authorization": "Token " + token}
    status, headers, body = request("GET", "/api/v1/users/me/", headers=auth)
    require(status == 200 and json.loads(body)["email"] == email, "Authenticated profile failed.")
    require(headers.get("X-Request-ID") == "production-smoke", "Request ID was lost.")

    boundary = uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"smoke.txt\"\r\n"
        "Content-Type: text/plain\r\n\r\nproduction smoke bytes\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    status, _, body = request("POST", "/api/v1/files/", data=body, headers={**auth, "Content-Type": "multipart/form-data; boundary=" + boundary})
    require(status == 201, "Private volume upload failed.")
    file_id = json.loads(body)["id"]
    path = f"/api/v1/files/{file_id}/"
    status, headers, body = request("GET", path + "download/", headers=auth)
    require(status == 200 and body == b"production smoke bytes", "Private download differs from upload.")
    require(headers.get("Cache-Control") == "no-store", "Private download was cacheable.")
    status, _, _ = request("GET", "/media/objects/" + file_id.replace("-", ""))
    require(status == 404, "Public media route must remain unavailable.")
    status, _, _ = request("DELETE", path, headers=auth)
    require(status == 204, "Logical deletion failed.")
    status, _, _ = request("GET", path + "download/", headers=auth)
    require(status == 404, "Deleted file remained accessible.")
    status, _, _ = request("POST", "/api/v1/auth/token/revoke/", headers=auth)
    require(status == 204, "Token revocation failed.")
    status, _, _ = request("GET", "/api/v1/users/me/", headers=auth)
    require(status == 401, "Revoked token remained usable.")
    print("Production HTTP smoke passed: auth, static assets, private files, revoke, probes.")


if __name__ == "__main__":
    try:
        smoke(int(sys.argv[1]))
    except Exception as error:
        # No response bodies/credentials/URLs in this failure path.
        print(f"Production HTTP smoke failed ({type(error).__name__}).", file=sys.stderr)
        sys.exit(1)
