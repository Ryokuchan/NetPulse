# regional_hub.py
from fastapi import FastAPI, HTTPException
import sqlite3
import subprocess
import os
import signal
import json
import uuid
from typing import Dict, List
import threading
import time
from datetime import datetime

app = FastAPI(title="Regional Agent Hub")

# Конфигурация региона
REGION_ID = os.getenv("REGION_ID", "RU")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
MAIN_SERVER_URL = os.getenv("MAIN_SERVER_URL", "http://localhost:8000")

# База данных агентов
DB_PATH = "agents.db"


def init_db():
    """Инициализация базы данных агентов"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            region TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            process_id INTEGER,
            status TEXT DEFAULT 'stopped',
            created_at TEXT,
            last_heartbeat TEXT
        )
    ''')
    conn.commit()
    conn.close()


def get_db_connection():
    return sqlite3.connect(DB_PATH)


# API для управления агентами
@app.post("/api/agents/create")
def create_agents(count: int, agent_prefix: str = None):
    """Создать N агентов"""
    if not agent_prefix:
        agent_prefix = f"{REGION_ID}-agent"

    created_agents = []
    for i in range(count):
        agent_id = f"{agent_prefix}-{i + 1}"

        # Запускаем агента как отдельный процесс
        process = subprocess.Popen([
            "python", "agent_worker.py",
            "--id", agent_id,
            "--region", REGION_ID,
            "--redis-host", REDIS_HOST,
            "--main-server", MAIN_SERVER_URL
        ])

        # Сохраняем в базу
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO agents (id, region, agent_name, process_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (agent_id, REGION_ID, agent_id, process.pid, "running", datetime.now().isoformat()))
        conn.commit()
        conn.close()

        created_agents.append({
            "id": agent_id,
            "pid": process.pid,
            "status": "running"
        })

    return {"created_agents": created_agents}


@app.post("/api/agents/{agent_id}/stop")
def stop_agent(agent_id: str):
    """Остановить конкретного агента"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT process_id FROM agents WHERE id = ?", (agent_id,))
    result = cursor.fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Agent not found")

    pid = result[0]
    try:
        os.kill(pid, signal.SIGTERM)
        # Обновляем статус
        cursor.execute("UPDATE agents SET status = 'stopped' WHERE id = ?", (agent_id,))
        conn.commit()
        return {"status": "stopped", "agent_id": agent_id}
    except ProcessLookupError:
        cursor.execute("UPDATE agents SET status = 'dead' WHERE id = ?", (agent_id,))
        conn.commit()
        raise HTTPException(status_code=404, detail="Agent process not found")
    finally:
        conn.close()


@app.get("/api/agents")
def list_agents():
    """Список всех агентов в регионе"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agents WHERE region = ?", (REGION_ID,))
    agents = cursor.fetchall()
    conn.close()

    return [
        {
            "id": agent[0],
            "region": agent[1],
            "name": agent[2],
            "pid": agent[3],
            "status": agent[4],
            "created_at": agent[5],
            "last_heartbeat": agent[6]
        }
        for agent in agents
    ]


@app.post("/api/tasks/submit")
def submit_task(target: str, check_type: str, dns_type: str = None, port: int = None):
    """Принять задачу от пользователя и отправить в общую очередь"""
    # Здесь просто проксируем задачу в Redis очередь
    # В реальности может быть логика балансировки между агентами региона
    task_data = {
        "task_id": str(uuid.uuid4()),
        "target": target,
        "type": check_type,
        "dns_type": dns_type,
        "port": port,
        "region": REGION_ID,
        "created_at": datetime.now().isoformat()
    }

    # В реальности здесь будет логика распределения по агентам региона
    # Пока просто возвращаем данные задачи
    return {
        "task_id": task_data["task_id"],
        "status": "submitted",
        "region": REGION_ID,
        "message": "Task submitted to regional queue"
    }


# Запуск при старте
@app.on_event("startup")
def startup():
    init_db()
    print(f"🚀 Regional Hub started for region: {REGION_ID}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)