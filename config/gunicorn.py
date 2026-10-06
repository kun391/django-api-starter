"""Small bounded WSGI runtime; never runs migrations or collectstatic."""

import os


def positive_int(name, default, maximum):
    value = int(os.environ.get(name, default))
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} is outside the supported range.")
    return value


bind = "0.0.0.0:8000"
workers = positive_int("WEB_CONCURRENCY", "2", 32)
threads = positive_int("WEB_THREADS", "2", 16)
timeout = positive_int("WEB_TIMEOUT", "60", 300)
graceful_timeout = positive_int("WEB_GRACEFUL_TIMEOUT", "30", 120)
keepalive = 5
worker_tmp_dir = "/tmp"
max_requests = 1000
max_requests_jitter = 100
preload_app = False
# Django alone interprets the explicitly opted-in protocol header. Avoid an
# independent Gunicorn trust default (including loopback) bypassing that opt-in.
forwarded_allow_ips = ""
secure_scheme_headers = {}
errorlog = "-"
accesslog = "-"
# Deliberately omit URL/query strings, request bodies, cookies and Authorization.
access_log_format = '%(m)s %(s)s %(L)s'
capture_output = True
