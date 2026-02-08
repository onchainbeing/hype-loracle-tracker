"""Configuration loading from YAML and environment variables."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv


@dataclass
class WalletConfig:
    name: str
    address: str
    enabled: bool = True


@dataclass
class SmtpConfig:
    host: str
    port: int
    use_tls: bool
    use_ssl: bool = False
    username: str = ""
    password: str = ""
    enabled: bool = True


@dataclass
class ResendConfig:
    api_key: str = ""
    from_email: str = "Hyperliquid Tracker <onboarding@resend.dev>"


@dataclass
class PushPlusConfig:
    token: str = ""
    enabled: bool = True


@dataclass
class NotificationConfig:
    recipients: list[str]
    daily_summary_time: str
    timezone: str
    batch_window_seconds: int = 300  # 5 minutes default


@dataclass
class DatabaseConfig:
    path: str


@dataclass
class LoggingConfig:
    level: str
    file: str


@dataclass
class HyperliquidConfig:
    rest_url: str
    ws_url: str
    reconnect_delay: int
    max_reconnect_delay: int


@dataclass
class GeminiConfig:
    api_key: str = ""
    model: str = "gemini-2.0-flash"
    enabled: bool = True


@dataclass
class ObsidianConfig:
    vault_path: str = ""
    notes_folder: str = "Trading"
    enabled: bool = True


@dataclass
class Config:
    wallets: list[WalletConfig]
    smtp: SmtpConfig
    resend: ResendConfig
    pushplus: PushPlusConfig
    notifications: NotificationConfig
    database: DatabaseConfig
    logging: LoggingConfig
    hyperliquid: HyperliquidConfig
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    obsidian: ObsidianConfig = field(default_factory=ObsidianConfig)
    project_root: Path = field(default_factory=Path)

    def get_enabled_wallets(self) -> list[WalletConfig]:
        """Return only enabled wallets."""
        return [w for w in self.wallets if w.enabled]

    def get_db_path(self) -> Path:
        """Return absolute path to database."""
        return self.project_root / self.database.path

    def get_log_path(self) -> Path:
        """Return absolute path to log file."""
        return self.project_root / self.logging.file


def load_config(config_path: Optional[str] = None, env_path: Optional[str] = None) -> Config:
    """Load configuration from YAML file and environment variables."""
    # Determine project root
    if config_path:
        project_root = Path(config_path).parent.parent
        config_file = Path(config_path)
    else:
        project_root = Path(__file__).parent.parent.parent.parent
        config_file = project_root / "config" / "config.yaml"

    # Load environment variables
    if env_path:
        load_dotenv(env_path)
    else:
        env_file = project_root / ".env"
        if env_file.exists():
            load_dotenv(env_file)

    # Load YAML config
    with open(config_file) as f:
        data = yaml.safe_load(f)

    # Parse wallets
    wallets = [
        WalletConfig(
            name=w["name"],
            address=w["address"].lower(),
            enabled=w.get("enabled", True),
        )
        for w in data.get("wallets", [])
    ]

    # Parse SMTP config with env vars
    smtp_data = data.get("smtp", {})
    smtp = SmtpConfig(
        host=smtp_data.get("host", "smtp.gmail.com"),
        port=smtp_data.get("port", 587),
        use_tls=smtp_data.get("use_tls", True),
        use_ssl=smtp_data.get("use_ssl", False),
        username=os.getenv("SMTP_USERNAME", ""),
        password=os.getenv("SMTP_PASSWORD", ""),
        enabled=smtp_data.get("enabled", True),
    )

    # Parse Resend config
    resend_data = data.get("resend", {})
    resend = ResendConfig(
        api_key=os.getenv("RESEND_API_KEY", ""),
        from_email=resend_data.get("from_email", "Hyperliquid Tracker <onboarding@resend.dev>"),
    )

    # Parse PushPlus config
    pushplus_data = data.get("pushplus", {})
    pushplus = PushPlusConfig(
        token=os.getenv("PUSHPLUS_TOKEN", ""),
        enabled=pushplus_data.get("enabled", True),
    )

    # Parse notification config
    notif_data = data.get("notifications", {})
    notifications = NotificationConfig(
        recipients=notif_data.get("recipients", []),
        daily_summary_time=notif_data.get("daily_summary_time", "09:00"),
        timezone=notif_data.get("timezone", "Asia/Shanghai"),
        batch_window_seconds=notif_data.get("batch_window_seconds", 300),
    )

    # Parse database config
    db_data = data.get("database", {})
    database = DatabaseConfig(
        path=db_data.get("path", "data/tracker.db"),
    )

    # Parse logging config
    log_data = data.get("logging", {})
    logging_config = LoggingConfig(
        level=log_data.get("level", "INFO"),
        file=log_data.get("file", "logs/tracker.log"),
    )

    # Parse Hyperliquid config
    hl_data = data.get("hyperliquid", {})
    hyperliquid = HyperliquidConfig(
        rest_url=hl_data.get("rest_url", "https://api.hyperliquid.xyz"),
        ws_url=hl_data.get("ws_url", "wss://api.hyperliquid.xyz/ws"),
        reconnect_delay=hl_data.get("reconnect_delay", 5),
        max_reconnect_delay=hl_data.get("max_reconnect_delay", 300),
    )

    # Parse Gemini config
    gemini_data = data.get("gemini", {})
    gemini = GeminiConfig(
        api_key=os.getenv("GEMINI_API_KEY", ""),
        model=gemini_data.get("model", "gemini-2.0-flash"),
        enabled=gemini_data.get("enabled", True),
    )

    # Parse Obsidian config
    obsidian_data = data.get("obsidian", {})
    obsidian = ObsidianConfig(
        vault_path=obsidian_data.get("vault_path", ""),
        notes_folder=obsidian_data.get("notes_folder", "Trading"),
        enabled=obsidian_data.get("enabled", True),
    )

    return Config(
        wallets=wallets,
        smtp=smtp,
        resend=resend,
        pushplus=pushplus,
        notifications=notifications,
        database=database,
        logging=logging_config,
        hyperliquid=hyperliquid,
        gemini=gemini,
        obsidian=obsidian,
        project_root=project_root,
    )
