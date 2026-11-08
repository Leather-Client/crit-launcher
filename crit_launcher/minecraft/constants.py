"""Mojang, Fabric, Forge and NeoForge endpoints plus install-wide constants."""

VERSION_MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_LOADER_LIST_URL = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_GAME_LIST_URL = "https://meta.fabricmc.net/v2/versions/game"
FABRIC_PROFILE_URL_TMPL = "https://meta.fabricmc.net/v2/versions/loader/{mc}/{loader}/profile/json"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge/"
FORGE_METADATA_URL = FORGE_MAVEN + "maven-metadata.xml"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
NEOFORGE_METADATA_URL = NEOFORGE_MAVEN + "maven-metadata.xml"
NEOFORGE_LEGACY_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/forge/"
NEOFORGE_LEGACY_METADATA_URL = NEOFORGE_LEGACY_MAVEN + "maven-metadata.xml"
NEOFORGE_LEGACY_MC = "1.20.1"
JAVA_RUNTIME_INDEX_URL = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                          "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
RESOURCES_BASE_URL = "https://resources.download.minecraft.net"
LIBRARIES_BASE_URL = "https://libraries.minecraft.net/"
INSTALL_SCHEMA = 2
DEFAULT_JAVA_INFO = {"component": "jre-legacy", "majorVersion": 8}
