"""Settings the plugin passes in through the environment.

Secrets go through the environment, not the command line, so they don't show up in `ps`.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vault: Path
    token: str
    llm_base_url: str = 'http://127.0.0.1:11434/v1'
    llm_model: str = 'gemma3:27b'
    llm_api_key: str = ''
    embed_model: str = 'nomic-embed-text'

    @classmethod
    def from_env(cls, vault: Path, env: dict[str, str] | None = None) -> 'Config':
        env = os.environ if env is None else env
        token = env.get('ZK_TOKEN', '')
        if not token:
            raise ValueError('ZK_TOKEN is not set')
        defaults = cls(vault=vault, token=token)
        return cls(
            vault=vault,
            token=token,
            llm_base_url=env.get('ZK_LLM_BASE_URL') or defaults.llm_base_url,
            llm_model=env.get('ZK_LLM_MODEL') or defaults.llm_model,
            llm_api_key=env.get('ZK_LLM_API_KEY', ''),
            embed_model=env.get('ZK_EMBED_MODEL') or defaults.embed_model,
        )

    def public(self) -> dict:
        """Settings safe to send back to the plugin. The key is never sent."""
        return {
            'vault': str(self.vault),
            'llm_base_url': self.llm_base_url,
            'llm_model': self.llm_model,
            'has_api_key': bool(self.llm_api_key),
            'embed_model': self.embed_model,
        }
