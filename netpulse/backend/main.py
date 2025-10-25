from fastapi import FastAPI, HTTPException, Query
import uuid
import redis
import json
import asyncio
from datetime import datetime
from typing import List, Optional
import secrets
import uvicorn
import requests
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('MainServer')

app = FastAPI(title="Monitoring Server")

# Redis клиент
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

# ==================== КОНФИГУРАЦИЯ СЕРВЕРОВ АГЕНТОВ ====================
AGENT_SERVERS = {
    "ru": {
        "url": "http://localhost:8001",  # URL сервера агентов для США
        "token": "ru-server-secret-token-12345"  # Токен сервера агентов для США
    }
}


# ==================== КОНЕЦ КОНФИГУРАЦИИ ====================

class TaskManager:
    @staticmethod
    def create_task(target: str, check_type: str, country: str) -> str:
        task_id = str(uuid.uuid4())
        task_data = {
            "id": task_id,
            "target": target,
            "check_type": check_type,
            "country": country,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "assigned_agent": None,
            "result": None,
            "agent_server": country  # Какой сервер агентов обрабатывает
        }

        redis_client.set(f"task:{task_id}", json.dumps(task_data))
        redis_client.lpush(f"queue:{country}", task_id)

        return task_id

    @staticmethod
    def get_task(task_id: str) -> Optional[dict]:
        task_json = redis_client.get(f"task:{task_id}")
        return json.loads(task_json) if task_json else None

    @staticmethod
    def update_task(task_id: str, updates: dict):
        task = TaskManager.get_task(task_id)
        if task:
            task.update(updates)
            redis_client.set(f"task:{task_id}", json.dumps(task))

    @staticmethod
    def get_all_tasks() -> List[dict]:
        """Получить все задачи (для админки)"""
        tasks = []
        for key in redis_client.scan_iter("task:*"):
            task_id = key.replace("task:", "")
            task = TaskManager.get_task(task_id)
            if task:
                tasks.append(task)
        return tasks


class AgentManager:
    @staticmethod
    def generate_token() -> str:
        """Сгенерировать токен агента"""
        return secrets.token_hex(32)

    @staticmethod
    def register_agent(agent_id: str, name: str, country: str, server: str, token: str, capabilities: List[str]):
        agent_data = {
            "id": agent_id,
            "name": name,
            "country": country,
            "server": server,
            "token": token,
            "capabilities": capabilities,
            "status": "offline",
            "current_tasks": 0,
            "max_tasks": 10,
            "last_heartbeat": None,
            "created_at": datetime.now().isoformat(),
            "ip_address": None
        }
        redis_client.set(f"agent:{agent_id}", json.dumps(agent_data))
        return agent_data

    @staticmethod
    def get_agent(agent_id: str) -> Optional[dict]:
        agent_json = redis_client.get(f"agent:{agent_id}")
        return json.loads(agent_json) if agent_json else None

    @staticmethod
    def get_all_agents() -> List[dict]:
        """Получить всех агентов"""
        agents = []
        for key in redis_client.scan_iter("agent:*"):
            agent_id = key.replace("agent:", "")
            agent = AgentManager.get_agent(agent_id)
            if agent:
                agents.append(agent)
        return agents

    @staticmethod
    def verify_agent(agent_id: str, token: str) -> bool:
        agent = AgentManager.get_agent(agent_id)
        return agent and agent["token"] == token

    @staticmethod
    def update_agent(agent_id: str, updates: dict):
        agent = AgentManager.get_agent(agent_id)
        if agent:
            agent.update(updates)
            redis_client.set(f"agent:{agent_id}", json.dumps(agent))

    @staticmethod
    def delete_agent(agent_id: str):
        """Удалить агента"""
        redis_client.delete(f"agent:{agent_id}")


# ==================== API ENDPOINTS ====================

@app.post("/tasks")
async def create_task(target: str, country: str = "auto"):
    """Создать задачу со ВСЕМИ проверками"""

    # Если auto - выбираем случайную страну
    if country == "auto":
        country = secrets.choice(list(AGENT_SERVERS.keys()))

    if country not in AGENT_SERVERS:
        raise HTTPException(status_code=400, detail=f"No agent server for country: {country}")

    # Все типы проверок которые будут выполнены
    all_check_types = [
        "http", "https", "ping", "tcp", "traceroute",
        "dns_a", "dns_aaaa", "dns_mx", "dns_ns", "dns_txt"
    ]

    task_id = str(uuid.uuid4())
    sub_tasks = []

    # Создаем подзадачи для КАЖДОГО типа проверки
    for check_type in all_check_types:
        sub_task_id = f"{task_id}-{check_type}"
        task_data = {
            "id": sub_task_id,
            "parent_task_id": task_id,
            "target": target,
            "check_type": check_type,
            "country": country,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "assigned_agent": None,
            "result": None,
            "agent_server": country
        }

        # Сохраняем в Redis
        redis_client.set(f"task:{sub_task_id}", json.dumps(task_data))
        redis_client.lpush(f"queue:{country}", sub_task_id)

        # Отправляем задачу на сервер агентов
        try:
            server_config = AGENT_SERVERS[country]
            response = requests.post(
                f"{server_config['url']}/assign-task",
                params={
                    "server_token": server_config['token']
                },
                json=task_data
            )

            if response.status_code != 200:
                logger.error(f"Failed to send task to agent server: {response.text}")

        except Exception as e:
            logger.error(f"Error sending task to agent server: {e}")

        sub_tasks.append(sub_task_id)

    return {
        "status": "success",
        "task_id": task_id,
        "target": target,
        "country": country,
        "check_types": all_check_types,
        "sub_tasks": sub_tasks,
        "agent_server": AGENT_SERVERS[country]["url"],
        "message": f"Задача создана со всеми проверками для {target}"
    }


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Получить статус ВСЕХ проверок задачи"""

    # Ищем все подзадачи родительской задачи
    sub_tasks = []

    # Ищем все ключи task:*
    for key in redis_client.scan_iter("task:*"):
        # Получаем task_id из ключа (убираем "task:")
        stored_task_id = key.replace("task:", "")

        # Проверяем что это подзадача нашего родителя
        if stored_task_id.startswith(f"{task_id}-"):
            task = TaskManager.get_task(stored_task_id)
            if task:
                sub_tasks.append(task)

    if not sub_tasks:
        # Если не нашли подзадач, возможно это старая задача без подзадач
        main_task = TaskManager.get_task(task_id)
        if main_task:
            return {
                "parent_task_id": task_id,
                "target": main_task["target"],
                "country": main_task["country"],
                "status_summary": {main_task["status"]: 1},
                "completed": main_task["status"] == "completed",
                "checks": [main_task]
            }
        else:
            raise HTTPException(status_code=404, detail="Task not found")

    # Статистика по статусам
    status_counts = {}
    for task in sub_tasks:
        status = task["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "parent_task_id": task_id,
        "target": sub_tasks[0]["target"] if sub_tasks else None,
        "country": sub_tasks[0]["country"] if sub_tasks else None,
        "status_summary": status_counts,
        "completed": status_counts.get("completed", 0) == len(sub_tasks),
        "total_checks": len(sub_tasks),
        "checks": sub_tasks
    }


@app.post("/admin/agents")
async def create_agent(
        name: str,
        country: str,
        capabilities: List[str] = None
):
    """Создать агента через сервер агентов"""

    if country not in AGENT_SERVERS:
        raise HTTPException(status_code=400, detail=f"No agent server for country: {country}")

    server_config = AGENT_SERVERS[country]

    try:
        # Отправляем запрос к серверу агентов
        logger.info(f"Creating agent on {server_config['url']}")
        response = requests.post(
            f"{server_config['url']}/create-agent",
            params={
                "name": name,
                "server_token": server_config['token'],
                "capabilities": capabilities
            },
            timeout=10
        )

        logger.info(f"Agent server response: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            logger.info(f"Agent creation result: {result}")

            agent_data = result["agent"]

            # УПРОЩЕННАЯ РЕГИСТРАЦИЯ - используем фиксированный server_id
            AgentManager.register_agent(
                agent_data["id"],
                agent_data["name"],
                country,
                server_config["server_id"],  # Используем server_id из конфига
                "hidden-token",
                agent_data["capabilities"]
            )

            return {
                "status": "success",
                "agent": agent_data,
                "message": f"Agent created and started on {country} server"
            }
        else:
            error_text = response.text
            logger.error(f"Agent server error {response.status_code}: {error_text}")
            raise HTTPException(status_code=500, detail=f"Agent server error: {error_text}")

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Cannot connect to agent server: {e}")
        raise HTTPException(status_code=500, detail=f"Cannot connect to agent server for {country}")
    except Exception as e:
        logger.error(f"Agent server connection failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Agent server connection failed: {str(e)}")


@app.get("/admin/agents")
async def admin_agents():
    """Получить список всех агентов для админки"""
    agents = AgentManager.get_all_agents()

    # Статистика по агентам
    stats = {
        "total": len(agents),
        "online": len([a for a in agents if a["status"] == "online"]),
        "offline": len([a for a in agents if a["status"] == "offline"]),
        "total_tasks": sum(a["current_tasks"] for a in agents)
    }

    return {
        "status": "success",
        "stats": stats,
        "agents": agents
    }


@app.get("/admin/agent-servers")
async def get_agent_servers():
    """Получить статус всех серверов агентов"""
    servers_status = {}

    for country, config in AGENT_SERVERS.items():
        try:
            response = requests.get(f"{config['url']}/status", timeout=5)
            if response.status_code == 200:
                servers_status[country] = response.json()
            else:
                servers_status[country] = {"status": "error", "message": response.text}
        except Exception as e:
            servers_status[country] = {"status": "offline", "message": str(e)}

    return {
        "status": "success",
        "agent_servers": servers_status
    }


@app.delete("/admin/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Удалить агента"""
    agent = AgentManager.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Получаем страну агента
    country = agent.get("country", "us")

    # Останавливаем агента на сервере агентов (если сервер доступен)
    if country in AGENT_SERVERS:
        server_config = AGENT_SERVERS[country]
        try:
            response = requests.post(
                f"{server_config['url']}/stop-agent",
                params={
                    "agent_id": agent_id,
                    "server_token": server_config['token']
                },
                timeout=5  # Добавляем таймаут
            )
            logger.info(f"Agent stop request sent: {response.status_code}")
        except Exception as e:
            logger.warning(f"Could not stop agent on agent server: {e}")
            # Продолжаем удаление даже если не удалось остановить на сервере агентов

    # Удаляем агента из основного сервера
    AgentManager.delete_agent(agent_id)

    return {
        "status": "success",
        "message": f"Agent {agent_id} deleted",
        "deleted_agent": agent
    }

@app.get("/admin/queues")
async def admin_queues():
    """Статус очередей для админки"""
    queues = {}
    total_pending = 0

    for key in redis_client.scan_iter("queue:*"):
        country = key.replace("queue:", "")
        count = redis_client.llen(key)
        queues[country] = count
        total_pending += count

    # Статистика по задачам
    all_tasks = TaskManager.get_all_tasks()
    tasks_stats = {
        "pending": total_pending,
        "assigned": len([t for t in all_tasks if t["status"] == "assigned"]),
        "completed": len([t for t in all_tasks if t["status"] == "completed"])
    }

    return {
        "status": "success",
        "queues": queues,
        "tasks_stats": tasks_stats
    }


@app.get("/")
async def root():
    return {
        "status": "Monitoring Server",
        "redis": redis_client.ping(),
        "agent_servers": list(AGENT_SERVERS.keys()),
        "endpoints": {
            "create_task": "POST /tasks?target=example.com&country=us",
            "get_task": "GET /tasks/{task_id}",
            "create_agent": "POST /admin/agents?name=US-Agent&country=us",
            "admin_agents": "GET /admin/agents",
            "agent_servers": "GET /admin/agent-servers"
        }
    }


if __name__ == "__main__":
    print("🚀 Starting Monitoring Server...")
    print("📊 API docs: http://localhost:8000/docs")
    print("👑 Admin endpoints available at /admin/*")
    print("🌍 Configured agent servers:", list(AGENT_SERVERS.keys()))
    print("=" * 50)

    # Настройка логирования
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('MainServer')

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)