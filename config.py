from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    deploy_token: str
    deploy_id: str
    tg_token: str

def load() -> Config:
    import os
    return Config(
        deploy_token=os.getenv("ABACUS_DEPLOYMENT_TOKEN"),
        deploy_id=os.getenv("ABACUS_DEPLOYMENT_ID"),
        tg_token=os.getenv("TG_TOKEN"),
    )
