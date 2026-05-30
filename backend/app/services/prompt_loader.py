"""YAML Prompt 模板加载器"""

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


@lru_cache(maxsize=16)
def load_prompt(name: str) -> dict:
    """加载并缓存 YAML prompt 模板

    Args:
        name: 模板名称（不含 .yaml 后缀），如 "extract_text"
    Returns:
        dict with keys: system, tool
    """
    path = PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt 模板不存在: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    logger.debug("已加载 prompt 模板: %s", name)
    return data
