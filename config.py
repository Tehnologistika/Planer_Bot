from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()


@dataclass
class Config:
    """Container for all runtime secrets."""

    deploy_token: str
    deploy_id: str
    tg_token: str
    deepseek_key: str


def load() -> Config:
    """Load configuration from environment variables."""
    return Config(
        deploy_token=os.getenv("ABACUS_DEPLOYMENT_TOKEN"),
        deploy_id=os.getenv("ABACUS_DEPLOYMENT_ID"),
        tg_token=os.getenv("TG_TOKEN"),
        deepseek_key=os.getenv("DEEPSEEK_KEY"),
    )

