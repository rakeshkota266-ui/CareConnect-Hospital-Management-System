bind = "127.0.0.1:8000"
workers = 2
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5
umask = 0o027
preload_app = True
accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True
forwarded_allow_ips = "127.0.0.1"
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}


def post_fork(server, worker):
    from app import app
    from extensions import db

    with app.app_context():
        db.engine.dispose(close=False)
