# config.py
import os


class Config:
    # Redis
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    REDIS_DB = int(os.getenv("REDIS_DB", 0))
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

    # Server
    SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
    SERVER_PORT = int(os.getenv("SERVER_PORT", 8000))

    # Security
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
    AGENT_TOKEN_SECRET = os.getenv("AGENT_TOKEN_SECRET", "default-secret-change-me")

    # Tasks
    MAX_TASKS_PER_AGENT = int(os.getenv("MAX_TASKS_PER_AGENT", 10))
    TASK_TIMEOUT = int(os.getenv("TASK_TIMEOUT", 300))  # 5 minutes

    # Agent heartbeat
    HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", 300))  # 5 minutes

    # Allowed check types
    ALLOWED_CHECK_TYPES = ["http", "https", "ping", "tcp", "dns", "traceroute"]
    ALLOWED_DNS_TYPES = ["A", "AAAA", "MX", "NS", "TXT", "CNAME"]


config = Config()