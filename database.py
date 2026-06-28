import os
from pymongo import MongoClient, ReturnDocument

MONGO_URL = os.getenv("MONGO_URL")


def get_db():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return client["freshboyswag"]


# ───────────────────────────────────────────────
# Коллекции
# ───────────────────────────────────────────────

def get_collection():
    return get_db()["channels"]

def get_tickets_collection():
    return get_db()["tickets"]

def get_logs_collection():
    return get_db()["logs_settings"]

def get_reg_collection():
    return get_db()["reg_lists"]

def get_counters_collection():
    return get_db()["counters"]

def get_vacation_collection():
    return get_db()["vacations"]


# ───────────────────────────────────────────────
# Логи (включение/выключение)
# ───────────────────────────────────────────────

def is_log_enabled(log_type: str) -> bool:
    try:
        col = get_logs_collection()
        doc = col.find_one({"type": log_type})
        return doc.get("enabled", False) if doc else False
    except Exception:
        return False

def set_log_enabled(log_type: str, enabled: bool):
    try:
        col = get_logs_collection()
        col.update_one({"type": log_type}, {"$set": {"enabled": enabled}}, upsert=True)
    except Exception as e:
        print(f"[ERROR] MongoDB logs: {e}")


# ───────────────────────────────────────────────
# Счётчик МП
# ───────────────────────────────────────────────

async def get_next_mp_number() -> int:
    try:
        col = get_counters_collection()
        doc = col.find_one_and_update(
            {"_id": "mp_counter"},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER
        )
        return doc["value"]
    except Exception as e:
        print(f"[ERROR] get_next_mp_number: {e}")
        return 0


# ───────────────────────────────────────────────
# Регистрация МП
# ───────────────────────────────────────────────

async def get_reg_data(message_id: int):
    try:
        col = get_reg_collection()
        return col.find_one({"message_id": str(message_id)})
    except Exception as e:
        print(f"[ERROR] MongoDB reg get: {e}")
        return None

async def get_reg_data_by_thread(thread_id: int):
    try:
        col = get_reg_collection()
        return col.find_one({"thread_id": str(thread_id)})
    except Exception as e:
        print(f"[ERROR] get_reg_data_by_thread: {e}")
        return None

async def save_reg_data(message_id: int, update: dict):
    try:
        col = get_reg_collection()
        col.update_one({"message_id": str(message_id)}, {"$set": update})
    except Exception as e:
        print(f"[ERROR] MongoDB reg save: {e}")
