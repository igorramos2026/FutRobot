"""
MELHORIA: antes, qualquer erro de processamento caía em `except Exception as e:
st.error(...)` e a causa real ficava mascarada — não dava pra saber se "zero
oportunidades" era porque os critérios não bateram ou porque o parsing quebrou.

Agora todo erro é logado com traceback completo neste log, além de aparecer
resumido (e com opção de detalhe técnico) na interface. Se algo parecer
errado, o primeiro lugar a checar é o arquivo robo_futebol.log.
"""

import logging

from config import LOG_PATH

logger = logging.getLogger("robo_futebol")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(_file_handler)
