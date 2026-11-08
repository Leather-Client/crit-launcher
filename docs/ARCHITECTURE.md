# Architecture

Crit Launcher is a Python backend (`crit_launcher/`) driving an HTML page (`index.html`) through
pywebview. Everything the page can call lives on one `API` object; the code behind it is organised
**one feature per package**.

## Where things live

```
crit.py                  entry point (what PyInstaller builds); `python crit.py` or `python -m crit_launcher`
crit_launcher/
  app/                   main(), the API facade, window/state/settings endpoints
  core/                  shared infrastructure: http, files, logging, OS helpers, json store, ed25519
  config/                paths.py (every folder/file location) and settings.py
  accounts/ skins/ instances/ importers/ addons/ servers/
  minecraft/ horizon/ launch/ updates/ news/ discord/ ui/     one package per feature
index.html               Windows UI          index.linux.html   Linux UI (picked automatically on Linux)
assets/                  UI artwork (the path is also a raw.githubusercontent URL, do not move it)
packaging/               windows/critsetup.iss, linux/ (AppImage build)
tests/                   unit tests + API-surface checks
```

## Rules the layout follows

1. **Dependencies point down.** `core` and `config` import no feature. Domain modules
   (`accounts/microsoft.py`, `servers/properties.py`, ...) never import an `api*.py` file.
   `app/facade.py` is the only place that sees every feature.
2. **`api*.py` files are the JS bridge.** Each defines one mixin class holding the methods the page
   (or a worker thread) calls. They are thin: validate input, call domain modules, shape the result.
   `app/facade.py` combines the mixins into `API`, which is handed to pywebview.
3. **Domain modules are plain functions/classes** with no `self` of the API, so they can be tested and
   debugged on their own.
4. **Importing has no side effects.** Data folders, the log file and Linux process fixes are set up by
   `bootstrap.init()`, which `app.main.main()` calls first. Tests call it themselves.
5. **One version string:** `crit_launcher/version.py` (packaging scripts read it).

## Debugging

* Log file: `<data dir>/launcher.log` (data dir: `launcher_data/` beside the exe on Windows,
  `~/.local/share/crit-launcher` on Linux, or `$CRIT_LAUNCHER_DATA`).
* A traceback names the feature file directly, e.g. `servers/api_process.py`, `addons/curseforge.py`.
* To find the code behind a page call, search the table below for the method name.
* Run from source: `python crit.py` (needs `pywebview`; Linux also needs PyQt6 + PyQt6-WebEngine).
* Tests: `python -m unittest discover -s tests` (no GUI needed).

## Adding a feature

Create a package, put logic in plain modules, put the page-facing methods in `api.py` as a mixin,
add the mixin to the bases in `app/facade.py`, and add its methods to `tests/api_surface.txt`.

## Module map

| Module | Purpose |
| --- | --- |
| `crit_launcher/accounts/api.py` | JS-bridge API: accounts. |
| `crit_launcher/accounts/ely.py` | Ely.by accounts, skins and authlib-injector. |
| `crit_launcher/accounts/errors.py` | Account errors. |
| `crit_launcher/accounts/microsoft.py` | Microsoft / Xbox / Minecraft sign-in. |
| `crit_launcher/accounts/offline.py` | Offline account UUIDs. |
| `crit_launcher/accounts/session.py` | Refreshing an account's tokens before launch. |
| `crit_launcher/accounts/store.py` | The accounts file and account lookup. |
| `crit_launcher/accounts/tokens.py` | JWT expiry and the public view of an account. |
| `crit_launcher/addons/api.py` | JS-bridge API: add-on search and install. |
| `crit_launcher/addons/api_critclient.py` | JS-bridge API: Crit Client. |
| `crit_launcher/addons/api_modpacks.py` | JS-bridge API: modpacks. |
| `crit_launcher/addons/api_mods.py` | JS-bridge API: mod identification and version switching. |
| `crit_launcher/addons/api_shaders.py` | JS-bridge API: Iris and shaders. |
| `crit_launcher/addons/constants.py` | Add-on type names. |
| `crit_launcher/addons/critclient.py` | Crit Client mod set endpoints. |
| `crit_launcher/addons/curseforge.py` | CurseForge proxy client. |
| `crit_launcher/addons/fingerprint.py` | File hashes and CurseForge fingerprints for identifying mods. |
| `crit_launcher/addons/modrinth.py` | Modrinth API helpers. |
| `crit_launcher/app/api_settings.py` | JS-bridge API: settings. |
| `crit_launcher/app/api_state.py` | JS-bridge API: state snapshots and window events. |
| `crit_launcher/app/facade.py` | The API object handed to pywebview, assembled from every feature's mixin. |
| `crit_launcher/app/main.py` | Program entry: builds the window and wires events. |
| `crit_launcher/config/paths.py` | Where the app lives and where its data lives; every path in the launcher comes from here. |
| `crit_launcher/config/settings.py` | Launcher settings: allowed values, defaults and the settings file store. |
| `crit_launcher/core/ed25519.py` | Pure-Python ed25519 signature verification (used for signed Horizon and launcher releases). |
| `crit_launcher/core/fsutil.py` | File helpers: hashing, validation, path containment, atomic writes, directory copying. |
| `crit_launcher/core/http.py` | HTTP helpers shared by every feature (JSON/text/bytes fetches, downloads, authenticated calls). |
| `crit_launcher/core/images.py` | PNG helpers shared by skins and instance icons. |
| `crit_launcher/core/jsonstore.py` | Small thread-safe JSON file store used for settings, accounts and indexes. |
| `crit_launcher/core/logs.py` | Log file setup and the log_exception helper. |
| `crit_launcher/core/memory.py` | System memory detection and the RAM slider limits. |
| `crit_launcher/core/progress.py` | Progress tracking for installs and downloads. |
| `crit_launcher/core/system.py` | Operating-system helpers: OS/arch names, child-process environment, opening folders. |
| `crit_launcher/discord/api.py` | JS-bridge API: Discord Rich Presence. |
| `crit_launcher/horizon/api.py` | JS-bridge API: Horizon version info. |
| `crit_launcher/horizon/constants.py` | Horizon repository, signing keys and version pins. |
| `crit_launcher/horizon/game.py` | Preparing the Minecraft files a Horizon build needs. |
| `crit_launcher/horizon/layout.py` | Horizon on-disk layout and asset naming. |
| `crit_launcher/horizon/releases.py` | Finding, verifying and unpacking signed Horizon builds. |
| `crit_launcher/horizon/setup.py` | Full Horizon instance setup. |
| `crit_launcher/horizon/sounds.py` | Copying Horizon's sound assets. |
| `crit_launcher/importers/api.py` | JS-bridge API: importing instances. |
| `crit_launcher/importers/common.py` | Shared helpers for reading other launchers' data. |
| `crit_launcher/importers/fastclient.py` | FastClient instance scanner. |
| `crit_launcher/importers/modrinth_app.py` | Modrinth App instance scanner. |
| `crit_launcher/importers/prism.py` | Prism / MultiMC instance scanner. |
| `crit_launcher/importers/registry.py` | Scanner registry and import source resolution. |
| `crit_launcher/importers/sklauncher.py` | SKLauncher instance scanner. |
| `crit_launcher/instances/api.py` | JS-bridge API: instance management. |
| `crit_launcher/instances/api_content.py` | JS-bridge API: installed content. |
| `crit_launcher/instances/api_icons.py` | JS-bridge API: instance icons. |
| `crit_launcher/instances/api_screenshots.py` | JS-bridge API: screenshots. |
| `crit_launcher/instances/api_shortcuts.py` | JS-bridge API: desktop shortcuts. |
| `crit_launcher/instances/content.py` | Reading mods, resource packs and their icons from disk. |
| `crit_launcher/instances/icons.py` | Built-in and custom instance icons. |
| `crit_launcher/instances/playtime.py` | Playtime bookkeeping. |
| `crit_launcher/instances/store.py` | Instance folders and instance.json metadata. |
| `crit_launcher/launch/api.py` | JS-bridge API: start and stop the game. |
| `crit_launcher/launch/api_console.py` | JS-bridge API: instance console log. |
| `crit_launcher/launch/api_horizon.py` | JS-bridge API: Horizon launching. |
| `crit_launcher/launch/command.py` | Builds the java command line for an instance. |
| `crit_launcher/launch/crash.py` | Reading crash output and explaining it. |
| `crit_launcher/minecraft/api.py` | JS-bridge API: version lists. |
| `crit_launcher/minecraft/constants.py` | Mojang, Fabric, Forge and NeoForge endpoints plus install-wide constants. |
| `crit_launcher/minecraft/installer.py` | Installing a Minecraft version with its mod loader. |
| `crit_launcher/minecraft/java.py` | Finding, probing and installing Java runtimes. |
| `crit_launcher/minecraft/libraries.py` | Libraries and assets: resolving, downloading and extracting natives. |
| `crit_launcher/minecraft/loaders.py` | Mod loader names and NeoForge version helpers. |
| `crit_launcher/minecraft/meta_cache.py` | Cached version manifests and loader metadata. |
| `crit_launcher/minecraft/rules.py` | Mojang rule evaluation and launch argument templating. |
| `crit_launcher/news/api.py` | JS-bridge API: Minecraft news and public servers. |
| `crit_launcher/servers/api_content.py` | JS-bridge API: server plugins and mods. |
| `crit_launcher/servers/api_lifecycle.py` | JS-bridge API: server create/install/delete. |
| `crit_launcher/servers/api_players.py` | JS-bridge API: server players. |
| `crit_launcher/servers/api_process.py` | JS-bridge API: server start/stop/console. |
| `crit_launcher/servers/api_settings.py` | JS-bridge API: server settings. |
| `crit_launcher/servers/constants.py` | Server software, gamerules and world type tables. |
| `crit_launcher/servers/meta.py` | Server folders and crit_server.json. |
| `crit_launcher/servers/paper.py` | PaperMC download API. |
| `crit_launcher/servers/plugins.py` | Reading plugin jars. |
| `crit_launcher/servers/properties.py` | server.properties reading, writing and generation. |
| `crit_launcher/servers/runtime.py` | Server process runtime state and sampling. |
| `crit_launcher/skins/api.py` | JS-bridge API: skins and capes. |
| `crit_launcher/skins/api_catalog.py` | JS-bridge API: skin catalogs. |
| `crit_launcher/skins/api_local.py` | JS-bridge API: offline-account skins. |
| `crit_launcher/skins/capes.py` | Cape catalogue. |
| `crit_launcher/skins/catalog.py` | The Critical skin catalog (GitHub backed). |
| `crit_launcher/skins/custom_skin_loader.py` | CustomSkinLoader config for offline/Ely skins. |
| `crit_launcher/skins/laby.py` | LabyMod skin catalog client. |
| `crit_launcher/skins/library.py` | The saved-skins library. |
| `crit_launcher/skins/sources.py` | URL validation and fetching for catalog skins. |
| `crit_launcher/skins/textures.py` | Skin endpoints, texture fetching and upload helpers. |
| `crit_launcher/ui/api_assets.py` | JS-bridge API: artwork. |
| `crit_launcher/ui/api_window.py` | JS-bridge API: native window chrome. |
| `crit_launcher/ui/assets.py` | Locating, seeding and downloading UI artwork. |
| `crit_launcher/ui/mc_textures.py` | Minecraft GUI textures and panorama for the Minecraft theme. |
| `crit_launcher/ui/page.py` | Preparing the HTML page and the home artwork. |
| `crit_launcher/ui/winframe.py` | Windows title bar / frame constants and DWM helpers. |
| `crit_launcher/updates/api.py` | JS-bridge API: self-update. |
| `crit_launcher/updates/api_changelog.py` | JS-bridge API: changelog. |
| `crit_launcher/updates/changelog.py` | Release notes and version comparison. |
| `crit_launcher/updates/signature.py` | Signature check for launcher updates. |

## JS-callable methods

| JS-callable method | Defined in |
| --- | --- |
| `accept_eula` | `crit_launcher/servers/api_lifecycle.py` |
| `add_account` | `crit_launcher/accounts/api.py` |
| `add_custom_instance_icon` | `crit_launcher/instances/api_icons.py` |
| `add_ely_account` | `crit_launcher/accounts/api.py` |
| `apply_saved_titlebar_theme` | `crit_launcher/ui/api_window.py` |
| `cancel_minecraft_web_login` | `crit_launcher/accounts/api.py` |
| `catalog_all` | `crit_launcher/skins/api_catalog.py` |
| `catalog_fetch_skin` | `crit_launcher/skins/api_catalog.py` |
| `catalog_laby` | `crit_launcher/skins/api_catalog.py` |
| `check_for_updates` | `crit_launcher/updates/api.py` |
| `create_desktop_shortcut` | `crit_launcher/instances/api_shortcuts.py` |
| `create_instance` | `crit_launcher/instances/api.py` |
| `create_server` | `crit_launcher/servers/api_lifecycle.py` |
| `delete_custom_instance_icon` | `crit_launcher/instances/api_icons.py` |
| `delete_instance` | `crit_launcher/instances/api.py` |
| `delete_instance_content` | `crit_launcher/instances/api_content.py` |
| `delete_saved_skin` | `crit_launcher/skins/api.py` |
| `delete_server` | `crit_launcher/servers/api_lifecycle.py` |
| `delete_server_content` | `crit_launcher/servers/api_content.py` |
| `ely_skin_profile` | `crit_launcher/skins/api.py` |
| `emit` | `crit_launcher/app/api_state.py` |
| `enable_custom_titlebar` | `crit_launcher/ui/api_window.py` |
| `ensure_assets` | `crit_launcher/ui/api_assets.py` |
| `ensure_mc_panorama` | `crit_launcher/ui/api_assets.py` |
| `ensure_mc_textures` | `crit_launcher/ui/api_assets.py` |
| `get_addon` | `crit_launcher/addons/api.py` |
| `get_addon_categories` | `crit_launcher/addons/api.py` |
| `get_addon_dependencies` | `crit_launcher/addons/api.py` |
| `get_addon_targets` | `crit_launcher/addons/api.py` |
| `get_changelog` | `crit_launcher/updates/api_changelog.py` |
| `get_content_icon` | `crit_launcher/instances/api_content.py` |
| `get_critclient_info` | `crit_launcher/addons/api_critclient.py` |
| `get_disk_usage` | `crit_launcher/instances/api.py` |
| `get_fabric_game_versions` | `crit_launcher/minecraft/api.py` |
| `get_fabric_loaders` | `crit_launcher/minecraft/api.py` |
| `get_forge_mc_versions` | `crit_launcher/minecraft/api.py` |
| `get_forge_versions` | `crit_launcher/minecraft/api.py` |
| `get_horizon_mc_versions` | `crit_launcher/horizon/api.py` |
| `get_import_targets` | `crit_launcher/importers/api.py` |
| `get_instance_console` | `crit_launcher/launch/api_console.py` |
| `get_instance_content` | `crit_launcher/instances/api_content.py` |
| `get_instance_screenshots` | `crit_launcher/instances/api_screenshots.py` |
| `get_live_skins` | `crit_launcher/skins/api.py` |
| `get_mc_news` | `crit_launcher/news/api.py` |
| `get_mod_versions` | `crit_launcher/addons/api_mods.py` |
| `get_modpack_versions` | `crit_launcher/addons/api_modpacks.py` |
| `get_neoforge_mc_versions` | `crit_launcher/minecraft/api.py` |
| `get_neoforge_versions` | `crit_launcher/minecraft/api.py` |
| `get_player_info` | `crit_launcher/servers/api_players.py` |
| `get_public_servers` | `crit_launcher/news/api.py` |
| `get_runtime` | `crit_launcher/app/api_state.py` |
| `get_screenshot_image` | `crit_launcher/instances/api_screenshots.py` |
| `get_server_content` | `crit_launcher/servers/api_content.py` |
| `get_server_log` | `crit_launcher/servers/api_process.py` |
| `get_server_players` | `crit_launcher/servers/api_players.py` |
| `get_server_settings` | `crit_launcher/servers/api_settings.py` |
| `get_server_versions` | `crit_launcher/servers/api_lifecycle.py` |
| `get_servers` | `crit_launcher/servers/api_lifecycle.py` |
| `get_skin_profile` | `crit_launcher/skins/api.py` |
| `get_state` | `crit_launcher/app/api_state.py` |
| `get_versions` | `crit_launcher/minecraft/api.py` |
| `get_window_state` | `crit_launcher/ui/api_window.py` |
| `identify_mods` | `crit_launcher/addons/api_mods.py` |
| `import_launcher_instance` | `crit_launcher/importers/api.py` |
| `install_addon` | `crit_launcher/addons/api.py` |
| `install_iris` | `crit_launcher/addons/api_shaders.py` |
| `install_modpack` | `crit_launcher/addons/api_modpacks.py` |
| `install_server_mod` | `crit_launcher/servers/api_content.py` |
| `install_update` | `crit_launcher/updates/api.py` |
| `launch` | `crit_launcher/launch/api.py` |
| `list_saved_skins` | `crit_launcher/skins/api.py` |
| `local_cape_image` | `crit_launcher/skins/api_local.py` |
| `local_capes` | `crit_launcher/skins/api_local.py` |
| `local_ely_preview` | `crit_launcher/skins/api_local.py` |
| `local_install_csl` | `crit_launcher/skins/api_local.py` |
| `local_reset` | `crit_launcher/skins/api_local.py` |
| `local_set_cape` | `crit_launcher/skins/api_local.py` |
| `local_set_skin` | `crit_launcher/skins/api_local.py` |
| `local_skin_plan` | `crit_launcher/skins/api_local.py` |
| `local_skin_state` | `crit_launcher/skins/api_local.py` |
| `local_skin_targets` | `crit_launcher/skins/api_local.py` |
| `local_use_ely` | `crit_launcher/skins/api_local.py` |
| `lookup_player_skin` | `crit_launcher/skins/api.py` |
| `mute_updates` | `crit_launcher/updates/api.py` |
| `open_instance_folder` | `crit_launcher/instances/api.py` |
| `open_server_folder` | `crit_launcher/servers/api_lifecycle.py` |
| `open_url` | `crit_launcher/app/api_state.py` |
| `pick_import_folder` | `crit_launcher/importers/api.py` |
| `player_action` | `crit_launcher/servers/api_players.py` |
| `push_state` | `crit_launcher/app/api_state.py` |
| `quit_app` | `crit_launcher/app/api_state.py` |
| `remove_account` | `crit_launcher/accounts/api.py` |
| `rename_instance` | `crit_launcher/instances/api.py` |
| `reset_skin` | `crit_launcher/skins/api.py` |
| `restart_server` | `crit_launcher/servers/api_process.py` |
| `save_settings` | `crit_launcher/app/api_settings.py` |
| `save_skin` | `crit_launcher/skins/api.py` |
| `scan_custom_import_dir` | `crit_launcher/importers/api.py` |
| `scan_import_sources` | `crit_launcher/importers/api.py` |
| `search_addons` | `crit_launcher/addons/api.py` |
| `search_server_mods` | `crit_launcher/servers/api_content.py` |
| `send_server_command` | `crit_launcher/servers/api_process.py` |
| `set_active_instance` | `crit_launcher/instances/api.py` |
| `set_cape` | `crit_launcher/skins/api.py` |
| `set_instance_icon` | `crit_launcher/instances/api_icons.py` |
| `set_skin` | `crit_launcher/skins/api.py` |
| `set_titlebar_theme` | `crit_launcher/ui/api_window.py` |
| `set_window` | `crit_launcher/app/api_state.py` |
| `start_minecraft_web_login` | `crit_launcher/accounts/api.py` |
| `start_server` | `crit_launcher/servers/api_process.py` |
| `stop` | `crit_launcher/launch/api.py` |
| `stop_all_servers` | `crit_launcher/servers/api_process.py` |
| `stop_server` | `crit_launcher/servers/api_process.py` |
| `switch_account` | `crit_launcher/accounts/api.py` |
| `switch_mod_version` | `crit_launcher/addons/api_mods.py` |
| `toggle_server_content` | `crit_launcher/servers/api_content.py` |
| `update_server_settings` | `crit_launcher/servers/api_settings.py` |
| `window_control` | `crit_launcher/ui/api_window.py` |
| `window_drag` | `crit_launcher/ui/api_window.py` |
