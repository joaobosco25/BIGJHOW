from __future__ import annotations

import re


_USUARIO_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
_SENHAS_BLOQUEADAS = {
    "administrador",
    "bigjhow123!",
    "operador123!",
    "proprietario123!",
    "senha123456!",
}


def normalizar_usuario(usuario: str) -> str:
    return (usuario or "").strip().lower()


def validar_usuario_novo(usuario: str) -> list[str]:
    usuario = normalizar_usuario(usuario)
    if not _USUARIO_RE.fullmatch(usuario):
        return [
            "Use de 3 a 32 caracteres: letras minúsculas, números, ponto, hífen ou sublinhado."
        ]
    return []


def validar_senha_forte(senha: str, *, usuario: str = "", nome: str = "") -> list[str]:
    erros: list[str] = []
    if len(senha) < 12:
        erros.append("A senha precisa ter pelo menos 12 caracteres.")
    if len(senha) > 128:
        erros.append("A senha pode ter no máximo 128 caracteres.")
    if not re.search(r"[a-z]", senha):
        erros.append("Inclua uma letra minúscula.")
    if not re.search(r"[A-Z]", senha):
        erros.append("Inclua uma letra maiúscula.")
    if not re.search(r"\d", senha):
        erros.append("Inclua um número.")
    if not re.search(r"[^A-Za-z0-9]", senha):
        erros.append("Inclua um caractere especial.")

    senha_normalizada = senha.casefold()
    usuario_normalizado = normalizar_usuario(usuario)
    if usuario_normalizado and usuario_normalizado in senha_normalizada:
        erros.append("A senha não pode conter o nome de usuário.")

    partes_nome = [parte.casefold() for parte in nome.split() if len(parte) >= 4]
    if any(parte in senha_normalizada for parte in partes_nome):
        erros.append("A senha não pode conter partes relevantes do nome da pessoa.")
    if senha_normalizada in _SENHAS_BLOQUEADAS:
        erros.append("Escolha uma senha menos previsível.")
    return erros
