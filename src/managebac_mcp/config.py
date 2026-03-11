"""Configuration loading and URL builders."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import AppError, ROUTE_MISCONFIGURED


class AuthConfig(BaseModel):
    login_url: str
    username_env: str = "MANAGEBAC_LOGIN"
    password_env: str = "MANAGEBAC_PASSWORD"


class RoutesConfig(BaseModel):
    classes_index: str
    class_page: str
    class_tasks: str
    task_page: str
    task_dropbox: str
    cas_index: str
    cas_experience: str
    cas_reflections: str


class TimeoutConfig(BaseModel):
    navigation: int = 30000
    action: int = 15000
    upload: int = 120000


class FeatureConfig(BaseModel):
    startup_sync: bool = True
    save_artifacts_on_success: bool = False


class ManageBacConfig(BaseModel):
    base_url: str
    auth: AuthConfig
    routes: RoutesConfig
    timeouts_ms: TimeoutConfig = Field(default_factory=TimeoutConfig)
    selectors: dict[str, list[str]] = Field(default_factory=dict)
    features: FeatureConfig = Field(default_factory=FeatureConfig)

    def build_url(self, route_template: str, **params: Any) -> str:
        try:
            route = route_template.format(**params)
        except KeyError as exc:
            raise AppError(ROUTE_MISCONFIGURED, f"Missing route parameter: {exc.args[0]}") from exc
        return urljoin(self.base_url, route)

    def route_url(self, route_name: str, **params: Any) -> str:
        template = getattr(self.routes, route_name, None)
        if template is None:
            raise AppError(ROUTE_MISCONFIGURED, f"Unknown route: {route_name}")
        return self.build_url(template, **params)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str | None = None
    managebac_config_path: Path = Path("config/managebac.yaml")
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_db: str = "managebac_mcp"
    mysql_user: str = "managebac"
    mysql_password: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}@"
            f"{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )


def load_managebac_config(path: Path) -> ManageBacConfig:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ManageBacConfig.model_validate(raw)


def resolve_credentials(config: ManageBacConfig) -> tuple[str, str]:
    username = os.getenv(config.auth.username_env)
    password = os.getenv(config.auth.password_env)
    if not username or not password:
        raise AppError(
            "AUTH_MISSING_CREDENTIALS",
            f"Environment variables {config.auth.username_env}/{config.auth.password_env} must be set",
        )
    return username, password
