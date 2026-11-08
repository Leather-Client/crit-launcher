"""Server software, gamerules and world type tables."""

import re


SERVER_META_FILE = "crit_server.json"
SERVER_SOFTWARE = ("vanilla", "paper", "fabric")
PAPER_API = "https://fill.papermc.io/v3/projects/paper"
PAPER_USER_AGENT = "CritLauncher/1.6 (https://github.com/snigglaberry/crit-launcher)"
FABRIC_INSTALLER_LIST_URL = "https://meta.fabricmc.net/v2/versions/installer"
EULA_URL = "https://aka.ms/MinecraftEULA"
PLUGIN_LOADERS = ["paper", "spigot", "bukkit"]
PLAYER_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,16}$")
GAMERULES = [
    ("keepInventory", "Keep inventory", "bool", False, ["keep_inventory"]),
    ("mobGriefing", "Mob griefing", "bool", True, ["mob_griefing"]),
    ("doDaylightCycle", "Daylight cycle", "bool", True, ["advance_time"]),
    ("doWeatherCycle", "Weather cycle", "bool", True, ["advance_weather"]),
    ("doMobSpawning", "Mob spawning", "bool", True, ["spawn_mobs"]),
    ("doFireTick", "Fire spreads", "bool", True, []),
    ("naturalRegeneration", "Natural regeneration", "bool", True, ["natural_health_regeneration"]),
    ("showDeathMessages", "Death messages", "bool", True, ["show_death_messages"]),
    ("announceAdvancements", "Announce advancements", "bool", True, ["show_advancement_messages"]),
    ("commandBlockOutput", "Command block output", "bool", True, ["command_block_output"]),
    ("doImmediateRespawn", "Immediate respawn", "bool", False, ["immediate_respawn"]),
    ("fallDamage", "Fall damage", "bool", True, ["fall_damage"]),
    ("fireDamage", "Fire damage", "bool", True, ["fire_damage"]),
    ("drowningDamage", "Drowning damage", "bool", True, ["drowning_damage"]),
    ("doInsomnia", "Phantoms spawn", "bool", True, ["spawn_phantoms"]),
    ("doPatrolSpawning", "Pillager patrols", "bool", True, ["spawn_patrols"]),
    ("doTraderSpawning", "Wandering traders", "bool", True, ["spawn_wandering_traders"]),
    ("disableRaids", "Disable raids", "bool", False, ["raids"]),
    ("playersSleepingPercentage", "Sleeping percentage", "int", 100, ["players_sleeping_percentage"]),
    ("randomTickSpeed", "Random tick speed", "int", 3, ["random_tick_speed"]),
    ("spawnRadius", "Spawn radius", "int", 10, ["respawn_radius"]),
]
_GAMERULE_IDS = {g[0]: g for g in GAMERULES}
WORLD_TYPES = ["minecraft:normal", "minecraft:flat", "minecraft:large_biomes", "minecraft:amplified"]
LEGACY_WORLD_TYPES = {"minecraft:normal": "default", "minecraft:flat": "flat",
                      "minecraft:large_biomes": "largeBiomes", "minecraft:amplified": "amplified"}
