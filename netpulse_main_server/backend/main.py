# main.py
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import redis
import json
import uuid
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import asyncio
import uvicorn

from config import config


# Модели данных
class CheckRequest:
    def __init__(self, target: str, check_type: str, dns_type: Optional[str] = None, port: Optional[int] = None):
        self.target = target
        self.check_type = check_type
        self.dns_type = dns_type
        self.port = port


class AgentRegister:
    def __init__(self, name: str, location: str, capabilities: List[str]):
        self.name = name
        self.location = location
        self.capabilities = capabilities


# In-memory хранилище
active_agents: Dict[str, dict] = {}
cleanup_task = None


async def cleanup_dead_agents():
    """Фоновая задача для очистки мертвых агентов"""
    while True:
        await asyncio.sleep(60)
        try:
            current_time = datetime.now()
            dead_agents = []

            for agent_id, agent in list(active_agents.items()):
                last_heartbeat = datetime.fromisoformat(agent["last_heartbeat"])
                if current_time - last_heartbeat > timedelta(seconds=config.HEARTBEAT_TIMEOUT):
                    dead_agents.append(agent_id)

            # Удаляем мертвых агентов
            for agent_id in dead_agents:
                agent = active_agents[agent_id]
                agent["status"] = "offline"
                redis_client.set(f"agent:{agent_id}", json.dumps(agent))
                redis_client.srem("active_agents", agent_id)
                del active_agents[agent_id]
                print(f"Agent {agent_id} marked as offline")

        except Exception as e:
            print(f"Cleanup error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Starting Main Server...")

    # Проверяем подключение к Redis
    try:
        redis_client.ping()
        print("✅ Connected to Redis")
    except redis.RedisError as e:
        print(f"❌ Redis connection failed: {e}")
        raise

    # Запускаем фоновые задачи
    global cleanup_task
    cleanup_task = asyncio.create_task(cleanup_dead_agents())

    yield

    # Shutdown
    print("🛑 Stopping Main Server...")
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


# Инициализация FastAPI с lifespan
app = FastAPI(
    title="Host Check Service",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBasic()

# Redis клиент
redis_client = redis.Redis(
    host=config.REDIS_HOST,
    port=config.REDIS_PORT,
    db=config.REDIS_DB,
    password=config.REDIS_PASSWORD,
    decode_responses=True,
    socket_connect_timeout=5,
    retry_on_timeout=True
)


def get_redis():
    return redis_client


# Аутентификация
def authenticate_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, config.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, config.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return credentials.username


# Генерация токена агента
def generate_agent_token(agent_name: str) -> str:
    salt = secrets.token_hex(16)
    return hashlib.sha256(f"{agent_name}{salt}{config.AGENT_TOKEN_SECRET}".encode()).hexdigest()


# Валидация типа проверки
def validate_check_type(check_type: str):
    if check_type not in config.ALLOWED_CHECK_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid check type. Allowed: {config.ALLOWED_CHECK_TYPES}")


# Валидация DNS типа
def validate_dns_type(dns_type: str):
    if dns_type and dns_type not in config.ALLOWED_DNS_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid DNS type. Allowed: {config.ALLOWED_DNS_TYPES}")


# API Endpoints
@app.post("/api/checks")
async def create_check(
        target: str,
        check_type: str,
        dns_type: Optional[str] = None,
        port: Optional[int] = None,
        redis: redis.Redis = Depends(get_redis)
):
    """Создать новую задачу проверки"""
    try:
        # Валидация
        validate_check_type(check_type)
        if dns_type:
            validate_dns_type(dns_type)

        task_id = str(uuid.uuid4())

        task_data = {
            "id": task_id,
            "target": target,
            "type": check_type,
            "dns_type": dns_type,
            "port": port,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "results": []
        }

        # Сохраняем в Redis
        redis.set(f"task:{task_id}", json.dumps(task_data), ex=config.TASK_TIMEOUT)
        redis.rpush("pending_tasks", json.dumps({
            "task_id": task_id,
            "target": target,
            "type": check_type,
            "dns_type": dns_type,
            "port": port
        }))

        return {"task_id": task_id, "status": "created"}

    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server error: {str(e)}")


@app.get("/api/checks/{task_id}")
async def get_check_results(task_id: str, redis: redis.Redis = Depends(get_redis)):
    """Получить результаты задачи"""
    try:
        task_data = redis.get(f"task:{task_id}")
        if not task_data:
            raise HTTPException(status_code=404, detail="Task not found")

        return json.loads(task_data)
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.post("/api/agents/register")
async def register_agent(
        name: str,
        location: str,
        capabilities: List[str],
        redis: redis.Redis = Depends(get_redis)
):
    """Зарегистрировать нового агента"""
    try:
        agent_id = str(uuid.uuid4())
        token = generate_agent_token(name)

        agent_info = {
            "id": agent_id,
            "name": name,
            "location": location,
            "capabilities": capabilities,
            "token": token,
            "status": "online",
            "last_heartbeat": datetime.now().isoformat(),
            "active_tasks": 0,
            "total_tasks": 0,
            "created_at": datetime.now().isoformat()
        }

        # Сохраняем в Redis
        redis.set(f"agent:{agent_id}", json.dumps(agent_info))
        redis.sadd("active_agents", agent_id)

        # In-memory копия
        active_agents[agent_id] = agent_info

        return {"agent_id": agent_id, "token": token}

    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.post("/api/agents/{agent_id}/heartbeat")
async def agent_heartbeat(
        agent_id: str,
        active_tasks: int = 0,
        redis: redis.Redis = Depends(get_redis)
):
    """Heartbeat от агента"""
    try:
        agent_data = redis.get(f"agent:{agent_id}")
        if not agent_data:
            raise HTTPException(status_code=404, detail="Agent not found")

        agent = json.loads(agent_data)
        agent["last_heartbeat"] = datetime.now().isoformat()
        agent["status"] = "online"
        agent["active_tasks"] = active_tasks

        redis.set(f"agent:{agent_id}", json.dumps(agent))
        active_agents[agent_id] = agent

        return {"status": "ok"}

    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.post("/api/results")
async def submit_result(
        task_id: str,
        agent_id: str,
        success: bool,
        result: dict,
        error: Optional[str] = None,
        redis: redis.Redis = Depends(get_redis)
):
    """Принять результат от агента"""
    try:
        task_data = redis.get(f"task:{task_id}")
        if not task_data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = json.loads(task_data)
        task["status"] = "completed" if success else "failed"
        task["results"].append({
            "agent_id": agent_id,
            "success": success,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })

        # Обновляем задачу
        redis.set(f"task:{task_id}", json.dumps(task), ex=config.TASK_TIMEOUT)

        # Обновляем статистику агента
        agent_data = redis.get(f"agent:{agent_id}")
        if agent_data:
            agent = json.loads(agent_data)
            agent["total_tasks"] = agent.get("total_tasks", 0) + 1
            redis.set(f"agent:{agent_id}", json.dumps(agent))
            active_agents[agent_id] = agent

        return {"status": "result_accepted"}

    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.get("/api/agents")
async def list_agents(
        admin: str = Depends(authenticate_admin),
        redis: redis.Redis = Depends(get_redis)
):
    """Список всех агентов (только для админа)"""
    try:
        agent_ids = redis.smembers("active_agents")
        agents = []

        for agent_id in agent_ids:
            agent_data = redis.get(f"agent:{agent_id}")
            if agent_data:
                agents.append(json.loads(agent_data))

        return agents
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.get("/api/tasks/pending")
async def get_pending_tasks(redis: redis.Redis = Depends(get_redis)):
    """Получить pending задачи (для агентов)"""
    try:
        task_data = redis.lpop("pending_tasks")
        if task_data:
            task = json.loads(task_data)
            return task
        return {}
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.get("/api/stats")
async def get_stats(redis: redis.Redis = Depends(get_redis)):
    """Статистика системы"""
    try:
        pending_tasks = redis.llen("pending_tasks")
        active_agents_count = redis.scard("active_agents")

        return {
            "pending_tasks": pending_tasks,
            "active_agents": active_agents_count,
            "timestamp": datetime.now().isoformat()
        }
    except redis.RedisError as e:
        raise HTTPException(status_code=500, detail=f"Redis error: {str(e)}")


@app.get("/")
async def root():
    """Корневой endpoint"""
    return {
        "message": "Host Check Service API",
        "version": "1.0.0",
        "endpoints": {
            "create_check": "POST /api/checks",
            "get_results": "GET /api/checks/{task_id}",
            "register_agent": "POST /api/agents/register",
            "stats": "GET /api/stats"
        }
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        reload=True
    )