# Gunicorn Configuration for Production
# Run with: gunicorn -c gunicorn.conf.py app:app

import multiprocessing
import os

# Server Binding. AppSail allocates the listening port at runtime; retaining the
# local fallback keeps the same command useful outside Catalyst.
appsail_port = os.getenv("X_ZOHO_CATALYST_LISTEN_PORT", "5000")
bind = os.getenv("GUNICORN_BIND", f"0.0.0.0:{appsail_port}")

# Worker Configuration
workers = int(os.getenv("GUNICORN_WORKERS", "1"))
worker_class = "sync"
timeout = 120  # Long timeout for deep searches
keepalive = 5

# Security
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190

# Logging
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log to stdout
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")

# Process Naming
proc_name = "deep-search-app"

# Development vs Production
reload = os.getenv("FLASK_ENV", "production") != "production"
