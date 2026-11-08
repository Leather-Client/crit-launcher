"""The API object handed to pywebview, assembled from every feature's mixin."""

import threading
import time

from ..accounts.api import AccountsApi
from ..addons.api import AddonsApi
from ..addons.api_critclient import CritClientApi
from ..addons.api_modpacks import ModpacksApi
from ..addons.api_mods import ModManagerApi
from ..addons.api_shaders import ShadersApi
from ..discord.api import DiscordPresenceApi
from ..horizon.api import HorizonApi
from ..importers.api import ImportApi
from ..instances.api import InstancesApi
from ..instances.api_content import InstanceContentApi
from ..instances.api_icons import InstanceIconsApi
from ..instances.api_screenshots import ScreenshotsApi
from ..instances.api_shortcuts import ShortcutsApi
from ..launch.api import LaunchApi
from ..launch.api_console import ConsoleApi
from ..launch.api_horizon import HorizonLaunchApi
from ..minecraft.api import VersionsApi
from ..news.api import NewsApi
from ..servers.api_content import ServerContentApi
from ..servers.api_lifecycle import ServerLifecycleApi
from ..servers.api_players import ServerPlayersApi
from ..servers.api_process import ServerProcessApi
from ..servers.api_settings import ServerSettingsApi
from ..skins.api import SkinsApi
from ..skins.api_catalog import SkinCatalogApi
from ..skins.api_local import LocalSkinsApi
from ..ui.api_assets import UiAssetsApi
from ..ui.api_window import WindowApi
from ..updates.api import UpdatesApi
from ..updates.api_changelog import ChangelogApi
from .api_settings import SettingsApi
from .api_state import AppStateApi


class API(
    AppStateApi,
    SettingsApi,
    VersionsApi,
    AccountsApi,
    SkinsApi,
    LocalSkinsApi,
    SkinCatalogApi,
    InstancesApi,
    InstanceIconsApi,
    InstanceContentApi,
    ScreenshotsApi,
    ShortcutsApi,
    ImportApi,
    AddonsApi,
    ModManagerApi,
    ModpacksApi,
    CritClientApi,
    ShadersApi,
    LaunchApi,
    ConsoleApi,
    HorizonLaunchApi,
    HorizonApi,
    ServerLifecycleApi,
    ServerProcessApi,
    ServerPlayersApi,
    ServerSettingsApi,
    ServerContentApi,
    NewsApi,
    UpdatesApi,
    ChangelogApi,
    WindowApi,
    UiAssetsApi,
    DiscordPresenceApi,
):
    """Everything the page can call. Each base class lives next to the feature it serves."""

    def __init__(self):
        self._window = None
        self._processes: dict = {}
        self._sessions: dict = {}
        self._stopping: set = set()
        self._installing: set = set()
        self._addon_busy = False
        self._importing: set = set()
        self._web_login = None
        self._proc_lock = threading.Lock()
        self._launcher_started = time.time()
        self._presence = None
        self._presence_starting = False
        self._presence_lock = threading.Lock()
        self._srv_init()
