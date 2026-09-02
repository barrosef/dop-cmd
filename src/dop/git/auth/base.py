from __future__ import annotations

from abc import ABC, abstractmethod


class GitAuthProvider(ABC):
    @abstractmethod
    def git_env(self) -> dict[str, str]:
        """
        Retorna variaveis de ambiente para autenticacao git.
        Deve incluir copias de os.environ + vars adicionais.
        NUNCA logar o retorno deste metodo.
        """
        raise NotImplementedError

    @abstractmethod
    def git_command_prefix(self) -> list[str]:
        """
        Prefixo a ser inserido no comando git antes dos argumentos.
        Ex: ["git", "-c", "credential.helper="] para desabilitar credential store.
        """
        raise NotImplementedError
