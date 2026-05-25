from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # IPX800 V4 — accessed via DDNS + port forwarding
    IPX800_HOST:    str   = "myhome.duckdns.org"
    IPX800_PORT:    int   = 8080
    IPX800_APIKEY:  str   = "apikey"
    IPX800_TIMEOUT: float = 5.0
    IPX800_RETRY:   int   = 3

    # Relay mapping (1-based for preset.htm URL)
    RELAY_LIGHT:        int = 1
    RELAY_CURTAIN_UP:   int = 2
    RELAY_CURTAIN_DOWN: int = 3

    # Logging
    LOG_LEVEL:        str = "INFO"
    LOG_FILE:         str = "logs/fastapi.log"
    LOG_MAX_BYTES:    int = 10_485_760
    LOG_BACKUP_COUNT: int = 7

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
