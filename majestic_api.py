import os
import time
import aiohttp

MAJESTIC_API_BASE = "https://api.majestic-files.net"
MAJESTIC_API_KEY = os.getenv("MAJESTIC_API_KEY")

# Кэш ответов по serverId — { serverId: (timestamp, data) }
_arena_cache = {}
CACHE_TTL = 30  # секунд — лимит 5 запросов/60с, кэш бережёт от перерасхода


async def get_arena_matches(server_id: str) -> dict | None:
    """Возвращает данные арены по серверу (с кэшем). None при ошибке."""
    now = time.time()
    cached = _arena_cache.get(server_id)
    if cached and (now - cached[0]) < CACHE_TTL:
        return cached[1]

    if not MAJESTIC_API_KEY:
        print("[ERROR] MAJESTIC_API_KEY не задан в переменных окружения")
        return None

    url = f"{MAJESTIC_API_BASE}/v1/ext/arena/{server_id}"
    headers = {"X-API-KEY": MAJESTIC_API_KEY}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    print(f"[ERROR] Majestic API arena: статус {resp.status}")
                    return None
                payload = await resp.json()
                if not payload.get("status"):
                    print(f"[ERROR] Majestic API arena: {payload}")
                    return None
                result = payload.get("result")
                _arena_cache[server_id] = (now, result)
                return result
    except Exception as e:
        print(f"[ERROR] Majestic API arena request: {e}")
        return None


def find_player_arena_stats(arena_data: dict, static_id: int) -> dict:
    """Считает статистику игрока по static ID из данных арены."""
    matches = arena_data.get("matches", []) if arena_data else []

    total_matches = 0
    total_kills = 0
    total_deaths = 0
    total_money = 0
    recent = []

    for match in matches:
        game_data = match.get("gameData")
        players_info = game_data.get("playersInfo", []) if game_data else []

        found_player = None
        for p in players_info:
            if p.get("staticId") == static_id:
                found_player = p
                break

        is_creator_or_leader = match.get("creator") == static_id or match.get("leader") == static_id

        if found_player or is_creator_or_leader:
            total_matches += 1
            if found_player:
                total_kills += found_player.get("kills", 0) or 0
                total_deaths += found_player.get("death", 0) or 0
                total_money += found_player.get("moneyWin", 0) or 0
            if len(recent) < 5:
                recent.append({
                    "id": match.get("id"),
                    "gamemode": match.get("gamemode"),
                    "status": match.get("status"),
                    "createDate": match.get("createDate"),
                    "kills": found_player.get("kills") if found_player else None,
                    "death": found_player.get("death") if found_player else None,
                    "moneyWin": found_player.get("moneyWin") if found_player else None,
                })

    return {
        "total_matches": total_matches,
        "total_kills": total_kills,
        "total_deaths": total_deaths,
        "total_money": total_money,
        "kd": round(total_kills / total_deaths, 2) if total_deaths > 0 else float(total_kills),
        "recent": recent,
    }
