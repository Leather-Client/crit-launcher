"""Builds the java command line for an instance."""

import os

from ..accounts.ely import authlib_injector
from ..config.paths import ASSETS_DIR, DATA_DIR, LIBRARIES_DIR
from ..core.system import classpath_sep
from ..instances.store import instance_dir, instance_game_dir
from ..minecraft.rules import DEFAULT_MODERN_JVM_ARGS, resolve_argument_list, substitute


def build_launch_command(meta: dict, account: dict, memory_mb: int, java_exe: str, options: dict = None) -> list:
    options = options or {}
    name = meta["name"]
    game_dir = instance_game_dir(name)
    natives_dir = instance_dir(name) / "natives"
    game_dir.mkdir(parents=True, exist_ok=True)

    classpath_list = [str(DATA_DIR / rel) for rel in meta["classpath"]]

    replacements = {
        "auth_player_name": account["name"],
        "auth_uuid": account["uuid"].replace("-", "") if account.get("type") in ("microsoft", "ely") else account["uuid"],
        "auth_access_token": (account.get("mc_token") or "0") if account.get("type") in ("microsoft", "ely") else "0",
        "auth_session": "0",
        "auth_xuid": "0",
        "clientid": "0",
        "user_type": "msa" if account.get("type") in ("microsoft", "ely") else "legacy",
        "user_properties": "{}",
        "version_name": meta["version"],
        "version_type": "release",
        "game_directory": str(game_dir),
        "assets_root": str(ASSETS_DIR),
        "assets_index_name": meta.get("asset_index") or meta["version"],
        "natives_directory": str(natives_dir),
        "library_directory": str(LIBRARIES_DIR),
        "classpath_separator": classpath_sep(),
        "classpath": classpath_sep().join(classpath_list),
        "launcher_name": "CritLauncher",
        "launcher_version": "1.0",
    }

    features = {}
    args_spec = meta.get("arguments") or {}

    if args_spec.get("jvm"):
        jvm_args = resolve_argument_list(args_spec["jvm"], features, replacements)
    else:
        jvm_args = [substitute(a, replacements) for a in DEFAULT_MODERN_JVM_ARGS]

    if not any(a in ("-cp", "-classpath") for a in jvm_args):
        jvm_args = ["-cp", replacements["classpath"]] + jvm_args
    if not any(a.startswith("-Djava.library.path=") for a in jvm_args):
        jvm_args = [f"-Djava.library.path={natives_dir}"] + jvm_args

    if args_spec.get("game"):
        game_args = resolve_argument_list(args_spec["game"], features, replacements)
    elif meta.get("minecraft_arguments"):
        game_args = [substitute(tok, replacements)
                     for tok in meta["minecraft_arguments"].split(" ") if tok]
    else:
        game_args = [
            "--username", replacements["auth_player_name"],
            "--version", replacements["version_name"],
            "--gameDir", replacements["game_directory"],
            "--assetsDir", replacements["assets_root"],
            "--assetIndex", replacements["assets_index_name"],
            "--uuid", replacements["auth_uuid"],
            "--accessToken", replacements["auth_access_token"],
            "--userType", replacements["user_type"],
            "--versionType", replacements["version_type"],
        ]

    try:
        min_mb = int(options.get("min_memory_mb") or 0)
    except (TypeError, ValueError):
        min_mb = 0
    xms = min(min_mb, memory_mb) if min_mb >= 256 else min(memory_mb, 1024)
    command = [java_exe, f"-Xmx{memory_mb}M", f"-Xms{xms}M"]
    command.extend(jvm_args)
    extra_jvm = str(options.get("jvm_args") or "").strip()
    if extra_jvm:
        try:
            import shlex
            tokens = shlex.split(extra_jvm, posix=(os.name != "nt"))
        except ValueError:
            tokens = extra_jvm.split()
        command.extend(t.strip('"') for t in tokens if t.strip('"'))
    if account.get("type") == "ely":
        injector = authlib_injector()
        if injector:
            command.insert(1, f"-javaagent:{injector}=ely.by")

    command.append(meta["main_class"])
    command.extend(game_args)
    if options.get("fullscreen"):
        command.append("--fullscreen")
    else:
        try:
            gw, gh = int(options.get("game_width") or 0), int(options.get("game_height") or 0)
        except (TypeError, ValueError):
            gw = gh = 0
        if gw >= 320 and gh >= 240:
            command.extend(["--width", str(gw), "--height", str(gh)])
    return command
