import os
import copy
from pymongo import MongoClient

import config

MONGO_URL = os.getenv("MONGO_URL")

_client = None  # переиспользуем одно подключение вместо нового на каждый вызов


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    return _client["freshboyswag"]


def get_tickets_collection():
    return get_db()["tickets"]


def get_settings_collection():
    return get_db()["bot_settings"]


# ═══════════════════════════════════════════════
# Единый конфиг тикет-системы (settings._id == "ticket_settings")
# Поля формы, пинг-роли, роли доступа, каналы, текст/картинка панели —
# всё живёт здесь и полностью редактируется из Discord.
# ═══════════════════════════════════════════════

_DEFAULTS = {
    "recruitment_open": True,
    "panel_title": config.DEFAULT_PANEL_TITLE,
    "panel_description": config.DEFAULT_PANEL_DESCRIPTION,
    "panel_image_url": config.DEFAULT_PANEL_IMAGE,
    "fields": config.DEFAULT_FIELDS,
    "ping_role_ids": config.DEFAULT_PING_ROLE_IDS,
    "access_role_ids": config.DEFAULT_ACCESS_ROLE_IDS,
    "channel_tickets": config.DEFAULT_CHANNEL_TICKETS,
    "channel_category": config.DEFAULT_CHANNEL_CATEGORY,
    "channel_results": config.DEFAULT_CHANNEL_RESULTS,
    "channel_accepted_logs": config.DEFAULT_CHANNEL_ACCEPTED_LOGS,
    "channel_rejected_logs": config.DEFAULT_CHANNEL_REJECTED_LOGS,
    "channel_call": config.DEFAULT_CHANNEL_CALL,
}


def get_ticket_settings() -> dict:
    """Возвращает текущие настройки тикет-системы.
    Если в базе их ещё нет — засевает их значениями по умолчанию из config.py."""
    try:
        col = get_settings_collection()
        doc = col.find_one({"_id": "ticket_settings"})
        if doc is None:
            seed = copy.deepcopy(_DEFAULTS)
            col.update_one({"_id": "ticket_settings"}, {"$set": seed}, upsert=True)
            seed["_id"] = "ticket_settings"
            return seed
        # добавляем недостающие ключи (например, если DEFAULTS расширили после деплоя)
        changed = False
        for key, value in _DEFAULTS.items():
            if key not in doc:
                doc[key] = copy.deepcopy(value)
                changed = True
        if changed:
            col.update_one({"_id": "ticket_settings"}, {"$set": doc}, upsert=True)
        return doc
    except Exception as e:
        print(f"[ERROR] get_ticket_settings: {e}")
        fallback = copy.deepcopy(_DEFAULTS)
        fallback["_id"] = "ticket_settings"
        return fallback


def update_ticket_settings(**fields):
    try:
        col = get_settings_collection()
        col.update_one({"_id": "ticket_settings"}, {"$set": fields}, upsert=True)
    except Exception as e:
        print(f"[ERROR] update_ticket_settings: {e}")


def is_recruitment_open() -> bool:
    return bool(get_ticket_settings().get("recruitment_open", True))


def set_recruitment_open(enabled: bool):
    update_ticket_settings(recruitment_open=enabled)
