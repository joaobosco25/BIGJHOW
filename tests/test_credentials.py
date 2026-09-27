from bigjhow.services.credenciais import (
    normalizar_usuario,
    validar_senha_forte,
    validar_usuario_novo,
)


def test_username_policy_and_normalization():
    assert normalizar_usuario("  Caixa.Norte  ") == "caixa.norte"
    assert validar_usuario_novo("caixa.norte") == []
    assert validar_usuario_novo("ab")
    assert validar_usuario_novo("caixa@norte")


def test_password_policy_rejects_predictable_personal_and_oversized_values():
    assert validar_senha_forte(
        "Segura!456XYZ", usuario="caixa.norte", nome="Carlos Souza"
    ) == []
    assert validar_senha_forte("curta")
    assert validar_senha_forte(
        "Caixa.Norte!456X", usuario="caixa.norte", nome="Carlos Souza"
    )
    assert validar_senha_forte(
        "CarlosSeguro!456", usuario="caixa.norte", nome="Carlos Souza"
    )
    assert validar_senha_forte("A1!" + "x" * 130)
