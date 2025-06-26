from __future__ import annotations

import asyncio
from typing import Any

import backoff
from abacusai import ApiClient

from config import load

client = ApiClient()


@backoff.on_exception(backoff.expo, Exception, max_tries=3)
async def ask_rocky(text: str) -> str:
    """Send a prompt to Abacus.AI deployment and return the reply text."""
    cfg = load()
    resp: Any = await asyncio.to_thread(
        client.get_chat_response,
        deployment_token=cfg.deploy_token,
        deployment_id=cfg.deploy_id,
        messages=[{"is_user": True, "text": text}],
        temperature=0.2,
    )
    if isinstance(resp, dict):
        if resp.get("messages"):
            return resp["messages"][-1]["text"]
        if resp.get("choices"):
            return resp["choices"][0]["text"]
    return str(resp)


if __name__ == "__main__":
    import sys

    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("You: ")
    print(asyncio.run(ask_rocky(prompt)))
