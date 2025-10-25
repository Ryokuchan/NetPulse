# agent_worker.py
import argparse
import requests
import redis
import time
import sys
import os


class AgentWorker:
    def __init__(self, agent_id: str, region: str, redis_host: str, main_server: str):
        self.agent_id = agent_id
        self.region = region
        self.redis = redis.Redis(host=redis_host, port=6379, db=0, decode_responses=True)
        self.main_server = main_server
        self.running = True

    def register(self):
        """Регистрация агента на главном сервере"""
        try:
            response = requests.post(
                f"{self.main_server}/api/agents/register",
                params={
                    "name": self.agent_id,
                    "location": self.region,
                    "capabilities": '["http", "https", "ping", "tcp", "dns", "traceroute"]'
                }
            )
            if response.status_code == 200:
                data = response.json()
                self.token = data["token"]
                print(f"✅ Agent {self.agent_id} registered")
                return True
            else:
                print(f"❌ Registration failed: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Registration error: {e}")
            return False

    def send_heartbeat(self):
        """Отправка heartbeat"""
        try:
            response = requests.post(
                f"{self.main_server}/api/agents/{self.agent_id}/heartbeat"
            )
            if response.status_code != 200:
                print(f"❌ Heartbeat failed")
        except Exception as e:
            print(f"❌ Heartbeat error: {e}")

    def work(self):
        """Основной цикл работы агента"""
        if not self.register():
            sys.exit(1)

        heartbeat_count = 0
        while self.running:
            try:
                # Каждые 30 секунд отправляем heartbeat
                if heartbeat_count % 30 == 0:
                    self.send_heartbeat()

                # Берем задачу из очереди
                task_data = self.redis.lpop("pending_tasks")
                if task_data:
                    task = json.loads(task_data)
                    print(f"🔄 Agent {self.agent_id} processing task: {task['task_id']}")
                    # Здесь должна быть логика выполнения задачи
                    # Пока просто имитируем работу
                    time.sleep(2)

                    # Возвращаем задачу если не обработали (в реальности отправляем результат)
                    self.redis.rpush("pending_tasks", json.dumps(task))

                time.sleep(1)
                heartbeat_count += 1

            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                print(f"❌ Worker error: {e}")
                time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Agent ID")
    parser.add_argument("--region", required=True, help="Region")
    parser.add_argument("--redis-host", default="localhost", help="Redis host")
    parser.add_argument("--main-server", required=True, help="Main server URL")

    args = parser.parse_args()

    worker = AgentWorker(args.id, args.region, args.redis_host, args.main_server)
    worker.work()