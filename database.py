import os
from pymongo import MongoClient, ReturnDocument

MONGO_URL = os.getenv("MONGO_URL")

_client = None  # переиспользуем одно подключение вместо нового на каждый вызов


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _client["freshboyswag"]


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

def get_settings_collection():
    return get_db()["bot_settings"]

def get_category_pool_collection():
    return get_db()["category_pools"]


# ───────────────────────────────────────────────
# Кастомные тексты панелей (редактируются из админ-панели)
# ───────────────────────────────────────────────

def get_panel_override(panel_key: str):
    """Возвращает {"title": ..., "description": ...} если панель редактировали, иначе None."""
    try:
        col = get_settings_collection()
        doc = col.find_one({"_id": f"panel_override_{panel_key}"})
        if doc:
            return {"title": doc.get("title"), "description": doc.get("description")}
        return None
    except Exception as e:
        print(f"[ERROR] get_panel_override: {e}")
        return None

def set_panel_override(panel_key: str, title: str, description: str):
    try:
        col = get_settings_collection()
        col.update_one(
            {"_id": f"panel_override_{panel_key}"},
            {"$set": {"title": title, "description": description}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] set_panel_override: {e}")


# ───────────────────────────────────────────────
# Набор в семью (открыт/закрыт) + админ-панель
# ───────────────────────────────────────────────

def is_recruitment_open() -> bool:
    try:
        col = get_settings_collection()
        doc = col.find_one({"_id": "recruitment"})
        # по умолчанию набор считается открытым, если настройка ещё не создавалась
        return doc.get("open", True) if doc else True
    except Exception as e:
        print(f"[ERROR] is_recruitment_open: {e}")
        return True

def set_recruitment_open(enabled: bool):
    try:
        col = get_settings_collection()
        col.update_one({"_id": "recruitment"}, {"$set": {"open": enabled}}, upsert=True)
    except Exception as e:
        print(f"[ERROR] set_recruitment_open: {e}")

def get_admin_panel_message_id():
    try:
        col = get_settings_collection()
        doc = col.find_one({"_id": "admin_panel"})
        return doc.get("message_id") if doc else None
    except Exception as e:
        print(f"[ERROR] get_admin_panel_message_id: {e}")
        return None

def set_admin_panel_message_id(message_id: int):
    try:
        col = get_settings_collection()
        col.update_one({"_id": "admin_panel"}, {"$set": {"message_id": str(message_id)}}, upsert=True)
    except Exception as e:
        print(f"[ERROR] set_admin_panel_message_id: {e}")


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


# ───────────────────────────────────────────────
# Пул категорий для личных каналов (с заделом на лимит в 50 каналов)
# ───────────────────────────────────────────────

def get_category_pool(tier_key: str, default_category_id=None) -> list:
    """
    Возвращает список ID категорий (строки) для тира/"no_tier".
    Если пула ещё нет в базе — сидирует его дефолтной категорией из config.py
    (TIER_ROLES / NO_TIER_CATEGORY_ID) и сохраняет, чтобы дальше расширять именно его.
    """
    try:
        col = get_category_pool_collection()
        doc = col.find_one({"_id": tier_key})
        if doc and doc.get("categories"):
            return doc["categories"]
        categories = [str(default_category_id)] if default_category_id else []
        if categories:
            col.update_one({"_id": tier_key}, {"$set": {"categories": categories}}, upsert=True)
        return categories
    except Exception as e:
        print(f"[ERROR] get_category_pool: {e}")
        return [str(default_category_id)] if default_category_id else []

def add_category_to_pool(tier_key: str, category_id: str):
    try:
        col = get_category_pool_collection()
        col.update_one(
            {"_id": tier_key},
            {"$addToSet": {"categories": str(category_id)}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] add_category_to_pool: {e}")

def set_category_pool(tier_key: str, categories: list):
    """Полностью заменяет список категорий тира (используется формой редактирования в админ-панели)."""
    try:
        col = get_category_pool_collection()
        col.update_one(
            {"_id": tier_key},
            {"$set": {"categories": [str(c) for c in categories]}},
            upsert=True
        )
    except Exception as e:
        print(f"[ERROR] set_category_pool: {e}")
