# Gunicorn Configuration for Production
# Run with: gunicorn -c gunicorn.conf.py app:app

import multiprocessing
import os

# Server Binding
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:5000")

# Worker Configuration
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
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
reload = os.getenv("FLASK_ENV") != "production"
