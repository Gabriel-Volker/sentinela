"""
ibm/iam.py — Autenticação IBM Cloud IAM com controle de expiração.

Nunca loga ou expõe tokens/chaves nas respostas ou mensagens de erro.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_IAM_URL = "https://iam.cloud.ibm.com/identity/token"
_MARGEM_RENOVACAO_SEG = 120  # renovar 2 min antes de expirar


class TokenIAM:
    """Gerencia um token IAM com renovação automática."""

    def __init__(self, api_key: str) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("WXO_API_KEY não configurada.")
        self._api_key = api_key
        self._token: str | None = None
        self._expira_em: float = 0.0

    def obter(self) -> str:
        """Retorna um token válido, renovando se necessário."""
        if self._token is None or time.time() >= self._expira_em:
            self._renovar()
        return self._token  # type: ignore[return-value]

    def _renovar(self) -> None:
        dados = urllib.parse.urlencode(
            {
                "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                "apikey": self._api_key,
            }
        ).encode()
        req = urllib.request.Request(
            _IAM_URL,
            data=dados,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except Exception as exc:
            raise RuntimeError(
                "Falha ao obter token IAM. Verifique WXO_API_KEY e conectividade."
            ) from exc

        self._token = body.get("access_token")
        expira_em = body.get("expiration", 0)
        if expira_em:
            self._expira_em = float(expira_em) - _MARGEM_RENOVACAO_SEG
        else:
            # fallback: 55 min
            self._expira_em = time.time() + 55 * 60
        logger.debug("Token IAM renovado.")


# Instância global (inicializada na primeira chamada)
_gerenciador: TokenIAM | None = None


def _obter_gerenciador() -> TokenIAM:
    global _gerenciador
    if _gerenciador is None:
        api_key = os.environ.get("WXO_API_KEY", "")
        _gerenciador = TokenIAM(api_key)
    return _gerenciador


def token_atual() -> str:
    return _obter_gerenciador().obter()
