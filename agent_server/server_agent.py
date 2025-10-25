from fastapi import FastAPI, HTTPException
import secrets
import json
from typing import List, Dict
import logging
from datetime import datetime
import threading
import time
import requests

# FastAPI с отключенной документацией для скорости
app = FastAPI(
    title="Agent Server",
    docs_url=None,
    redoc_url=None
)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('AgentServer')

# Конфигурация сервера агентов
CONFIG = {
    "server_id": "agent-server-ru-01",
    "token": "ru-server-secret-token-12345",
    "country": "ru",
    "port": 8001,
    "main_server_url": "http://localhost:8000"
}

# Хранилище данных
agents_db = {}  # {agent_id: agent_data}
tasks_queue = []  # [task1, task2, ...]
task_results = {}  # {task_id: result}


class AgentManager:
    @staticmethod
    def create_agent(name: str, capabilities: List[str] = None):
        """Создать нового агента и запустить его"""
        if capabilities is None:
            capabilities = ["http", "ping", "tcp", "dns"]

        agent_id = f"agent-{CONFIG['country']}-{secrets.token_hex(4)}"
        agent_token = secrets.token_hex(16)

        agent_data = {
            "id": agent_id,
            "name": name,
            "country": CONFIG['country'],
            "token": agent_token,
            "capabilities": capabilities,
            "status": "online",
            "current_tasks": 0,
            "max_tasks": 5,
            "created_at": datetime.now().isoformat(),
            "last_heartbeat": datetime.now().isoformat()
        }

        agents_db[agent_id] = agent_data
        logger.info(f"🆕 New agent created: {agent_id}")

        # Запускаем рабочую функцию агента
        AgentManager.start_agent_worker(agent_data)

        return agent_data

    @staticmethod
    def start_agent_worker(agent_data: dict):
        """Запустить рабочую функцию агента в отдельном потоке"""

        def worker():
            agent_id = agent_data["id"]
            agent_token = agent_data["token"]

            logger.info(f"🤖 Agent worker started: {agent_id}")

            while agents_db.get(agent_id, {}).get("status") == "online":
                try:
                    # Запрашиваем задачу у сервера
                    response = requests.get(
                        f"http://localhost:{CONFIG['port']}/agent/pending-task",
                        params={
                            "agent_id": agent_id,
                            "agent_token": agent_token
                        },
                        timeout=10
                    )

                    if response.status_code == 200:
                        task_data = response.json()

                        if task_data.get("status") == "no_tasks":
                            # Нет задач - ждем
                            time.sleep(10)
                            continue

                        if task_data.get("status") == "busy":
                            # Агент перегружен - ждем
                            time.sleep(30)
                            continue

                        # Получили задачу - выполняем
                        logger.info(f"🎯 Agent {agent_id} executing task: {task_data['id']}")

                        # Имитация выполнения задачи
                        result = AgentManager.execute_task(task_data)

                        # Отправляем результат
                        requests.post(
                            f"http://localhost:{CONFIG['port']}/agent/submit-result",
                            params={
                                "task_id": task_data["id"],
                                "agent_id": agent_id,
                                "agent_token": agent_token
                            },
                            json=result
                        )

                        logger.info(f"✅ Agent {agent_id} completed task: {task_data['id']}")

                    # Обновляем heartbeat
                    agents_db[agent_id]["last_heartbeat"] = datetime.now().isoformat()

                    time.sleep(5)  # Пауза между проверками

                except Exception as e:
                    logger.error(f"❌ Agent {agent_id} error: {e}")
                    time.sleep(30)  # Пауза при ошибке

        # Запускаем в отдельном потоке
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    @staticmethod
    def execute_task(task_data: dict) -> dict:
        """Выполнить задачу мониторинга"""
        check_type = task_data["check_type"]
        target = task_data["target"]

        # Имитация выполнения различных проверок
        if check_type in ["http", "https"]:
            return {
                "status": "success",
                "status_code": 200,
                "response_time": 150,
                "checked_at": datetime.now().isoformat()
            }
        elif check_type == "ping":
            return {
                "status": "success",
                "reachable": True,
                "response_time": 50,
                "checked_at": datetime.now().isoformat()
            }
        elif check_type == "tcp":
            return {
                "status": "success",
                "port_open": True,
                "response_time": 100,
                "checked_at": datetime.now().isoformat()
            }
        else:
            return {
                "status": "success",
                "response_time": 80,
                "checked_at": datetime.now().isoformat()
            }

    @staticmethod
    def get_agent(agent_id: str):
        return agents_db.get(agent_id)

    @staticmethod
    def get_all_agents():
        return list(agents_db.values())

    @staticmethod
    def get_online_agents():
        return [agent for agent in agents_db.values() if agent["status"] == "online"]

    @staticmethod
    def verify_agent(agent_id: str, token: str):
        agent = agents_db.get(agent_id)
        return agent and agent["token"] == token

    @staticmethod
    def verify_server_token(token: str):
        return token == CONFIG['token']

    @staticmethod
    def stop_agent(agent_id: str):
        if agent_id in agents_db:
            agents_db[agent_id]["status"] = "stopped"
            logger.info(f"🛑 Agent stopped: {agent_id}")
            return True
        return False


class TaskManager:
    @staticmethod
    def add_task(task_data: dict):
        """Добавить задачу в очередь"""
        tasks_queue.append(task_data)
        logger.info(f"📥 Task added to queue: {task_data['id']}")

    @staticmethod
    def get_pending_task_for_agent(agent_capabilities: List[str]):
        """Найти подходящую задачу для агента"""
        for task in tasks_queue:
            if (task.get("status") == "pending" and
                    task.get("check_type") in agent_capabilities):
                return task
        return None

    @staticmethod
    def update_task_status(task_id: str, status: str, agent_id: str = None):
        """Обновить статус задачи"""
        for task in tasks_queue:
            if task.get("id") == task_id:
                task["status"] = status
                if agent_id:
                    task["assigned_agent"] = agent_id
                break


# ==================== API ENDPOINTS ====================

@app.post("/create-agent")
async def create_agent(name: str, server_token: str, capabilities: List[str] = None):
    """Основной сервер создает нового агента"""
    if not AgentManager.verify_server_token(server_token):
        raise HTTPException(status_code=401, detail="Invalid server token")

    agent_data = AgentManager.create_agent(name, capabilities)

    return {
        "status": "success",
        "message": f"Agent created and started in {CONFIG['country']}",
        "agent": {
            "id": agent_data["id"],
            "name": agent_data["name"],
            "country": agent_data["country"],
            "capabilities": agent_data["capabilities"],
            "status": agent_data["status"]
        },
        "agent_server": {
            "id": CONFIG["server_id"],
            "country": CONFIG["country"],
            "url": f"http://localhost:{CONFIG['port']}"
        }
    }


@app.post("/assign-task")
async def assign_task(task_data: dict, server_token: str):
    """Основной сервер назначает задачу этому серверу агентов"""
    if not AgentManager.verify_server_token(server_token):
        raise HTTPException(status_code=401, detail="Invalid server token")

    # Устанавливаем статус по умолчанию
    task_data["status"] = "pending"
    task_data["country"] = CONFIG["country"]

    # Добавляем задачу в очередь
    TaskManager.add_task(task_data)

    logger.info(f"📋 Task assigned: {task_data['id']} - {task_data['check_type']} for {task_data['target']}")

    return {
        "status": "success",
        "message": f"Task added to queue in {CONFIG['country']}",
        "task_id": task_data["id"],
        "queue_size": len(tasks_queue)
    }


@app.get("/agents")
async def list_agents(server_token: str):
    """Получить список всех агентов"""
    if not AgentManager.verify_server_token(server_token):
        raise HTTPException(status_code=401, detail="Invalid server token")

    online_agents = AgentManager.get_online_agents()

    return {
        "status": "success",
        "agents": AgentManager.get_all_agents(),
        "stats": {
            "total": len(agents_db),
            "online": len(online_agents),
            "offline": len(agents_db) - len(online_agents),
            "pending_tasks": len([t for t in tasks_queue if t.get("status") == "pending"]),
            "active_tasks": len([t for t in tasks_queue if t.get("status") == "assigned"])
        }
    }


@app.post("/stop-agent")
async def stop_agent(agent_id: str, server_token: str):
    """Остановить агента"""
    if not AgentManager.verify_server_token(server_token):
        raise HTTPException(status_code=401, detail="Invalid server token")

    success = AgentManager.stop_agent(agent_id)

    if success:
        return {"status": "success", "message": f"Agent {agent_id} stopped"}
    else:
        raise HTTPException(status_code=404, detail="Agent not found")


# ==================== API ДЛЯ АГЕНТОВ ====================

@app.get("/agent/pending-task")
async def get_pending_task(agent_id: str, agent_token: str):
    """Агент запрашивает задачу"""
    if not AgentManager.verify_agent(agent_id, agent_token):
        raise HTTPException(status_code=401, detail="Invalid agent credentials")

    agent = AgentManager.get_agent(agent_id)
    if not agent or agent["status"] != "online":
        return {"status": "offline"}

    if agent["current_tasks"] >= agent["max_tasks"]:
        return {"status": "busy"}

    # Ищем подходящую задачу для этого агента
    task = TaskManager.get_pending_task_for_agent(agent["capabilities"])
    if task:
        # Назначаем задачу агенту
        TaskManager.update_task_status(task["id"], "assigned", agent_id)
        agent["current_tasks"] += 1
        agent["last_heartbeat"] = datetime.now().isoformat()

        logger.info(f"🎯 Task {task['id']} assigned to agent {agent_id}")

        return task

    return {"status": "no_tasks"}


@app.post("/agent/submit-result")
async def submit_result(task_id: str, agent_id: str, agent_token: str, result: dict):
    """Агент отправляет результат задачи"""
    if not AgentManager.verify_agent(agent_id, agent_token):
        raise HTTPException(status_code=401, detail="Invalid agent credentials")

    # Обновляем статус задачи
    TaskManager.update_task_status(task_id, "completed")

    # Обновляем агента
    agent = AgentManager.get_agent(agent_id)
    if agent:
        agent["current_tasks"] = max(0, agent["current_tasks"] - 1)

    # Сохраняем результат
    task_results[task_id] = result

    logger.info(f"✅ Result submitted for task {task_id} by agent {agent_id}")

    return {"status": "success"}


@app.post("/agent/heartbeat")
async def agent_heartbeat(agent_id: str, agent_token: str):
    """Агент отправляет heartbeat"""
    if not AgentManager.verify_agent(agent_id, agent_token):
        raise HTTPException(status_code=401, detail="Invalid agent credentials")

    agent = AgentManager.get_agent(agent_id)
    if agent:
        agent["status"] = "online"
        agent["last_heartbeat"] = datetime.now().isoformat()

    return {"status": "success"}


@app.get("/task-result/{task_id}")
async def get_task_result(task_id: str, server_token: str):
    """Получить результат задачи"""
    if not AgentManager.verify_server_token(server_token):
        raise HTTPException(status_code=401, detail="Invalid server token")

    result = task_results.get(task_id)
    if result:
        return {"status": "success", "result": result}
    else:
        return {"status": "not_found"}


@app.get("/status")
async def server_status():
    """Статус сервера агентов"""
    online_agents = AgentManager.get_online_agents()
    pending_tasks = len([t for t in tasks_queue if t.get("status") == "pending"])

    return {
        "status": "running",
        "server_id": CONFIG["server_id"],
        "country": CONFIG["country"],
        "agents_count": len(agents_db),
        "online_agents": len(online_agents),
        "pending_tasks": pending_tasks,
        "main_server": CONFIG["main_server_url"]
    }


@app.get("/")
async def root():
    return {"message": f"Agent Server {CONFIG['server_id']} is running"}


# ==================== ЗАПУСК СЕРВЕРА ====================

if __name__ == "__main__":
    print(f"🚀 Starting Agent Server...")
    print(f"🔑 Server ID: {CONFIG['server_id']}")
    print(f"🌍 Country: {CONFIG['country']}")
    print(f"🔐 Token: {CONFIG['token']}")
    print(f"📡 Port: {CONFIG['port']}")
    print(f"📍 URL: http://localhost:{CONFIG['port']}")
    print("=" * 50)

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=CONFIG["port"],
        access_log=False
    )