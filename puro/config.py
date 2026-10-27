"""Validated local settings for the offline workstation and future SOAP client.

Credentials live in local environment/.env, never in repository files or logs.
The app is intentionally review-only until the Purolator adapter is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """A configuration problem safe to display without disclosing secrets."""


@dataclass(frozen=True)
class AppConfig:
    environment: str = "development"
    data_dir: Path = Path("local_data")
    labels_dir: Path = Path("local_data/labels")
    sender_name: str = ""
    sender_street_no: str = ""
    sender_street: str = ""
    sender_city: str = ""
    sender_province: str = ""
    sender_postal_code: str = ""
    sender_country: str = "CA"
    sender_phone: str = ""
    account_number: str = field(default="", repr=False)
    api_key: str = field(default="", repr=False)
    api_password: str = field(default="", repr=False)

    @classmethod
    def from_environment(cls, *, dotenv: bool = True) -> "AppConfig":
        if dotenv:
            load_dotenv(override=False)
        env = os.getenv("PUROLATOR_ENV", "development").strip().lower()
        if env not in {"development", "production"}:
            raise ConfigurationError(
                "PUROLATOR_ENV must be 'development' or 'production'"
            )
        directory = Path(os.getenv("PURO_DATA_DIR", "local_data")).expanduser()
        labels = Path(os.getenv(
            "PURO_LABELS_DIR", str(directory / "labels")
        )).expanduser()
        return cls(
            environment=env,
            data_dir=directory,
            labels_dir=labels,
            sender_name=os.getenv("DEFAULT_SENDER_NAME", "").strip(),
            sender_street_no=os.getenv("DEFAULT_SENDER_STREET_NO", "").strip(),
            sender_street=os.getenv("DEFAULT_SENDER_STREET", "").strip(),
            sender_city=os.getenv("DEFAULT_SENDER_CITY", "").strip(),
            sender_province=os.getenv("DEFAULT_SENDER_PROVINCE", "").strip(),
            sender_postal_code=os.getenv("DEFAULT_SENDER_POSTAL_CODE", "").strip(),
            sender_country=os.getenv("DEFAULT_SENDER_COUNTRY", "CA").strip(),
            sender_phone=os.getenv("DEFAULT_SENDER_PHONE", "").strip(),
            account_number=os.getenv("PUROLATOR_ACCOUNT_NUMBER", "").strip(),
            api_key=os.getenv("PUROLATOR_API_KEY", "").strip(),
            api_password=os.getenv("PUROLATOR_API_PASSWORD", "").strip(),
        )

    def missing_shipping_settings(self) -> list[str]:
        """Report *setting names*, never credential values.

        A readiness check only: production shipment creation is NOT wired yet.
        """
        required = {
            "PUROLATOR_API_KEY": self.api_key,
            "PUROLATOR_API_PASSWORD": self.api_password,
            "PUROLATOR_ACCOUNT_NUMBER": self.account_number,
            "DEFAULT_SENDER_NAME": self.sender_name,
            "DEFAULT_SENDER_STREET": self.sender_street,
            "DEFAULT_SENDER_CITY": self.sender_city,
            "DEFAULT_SENDER_PROVINCE": self.sender_province,
            "DEFAULT_SENDER_POSTAL_CODE": self.sender_postal_code,
            "DEFAULT_SENDER_PHONE": self.sender_phone,
        }
        return [name for name, value in required.items() if not value]


def redact_text(message: str, config: AppConfig) -> str:
    """Mask known secrets when an unexpected error must be displayed.

    Prefer allowlisted structured events with NO raw request/customer data.
    """
    cleaned = str(message)
    for secret in (config.api_key, config.api_password, config.account_number):
        if secret:
            cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned
