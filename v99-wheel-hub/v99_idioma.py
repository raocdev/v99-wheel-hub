"""Idioma das mensagens do serviço e da bandeja (a página tem o próprio dicionário).

O texto em português é a chave; `t()` devolve a tradução do idioma atual e
preenche os {campos}.
"""

IDIOMAS = ("pt", "en")
atual = "pt"

EN = {
    # status
    "procurando volante...": "looking for the wheel...",
    "procurando PXN-V99...": "looking for PXN-V99...",
    "volante não encontrado — está ligado? o app do celular está fechado?":
        "wheel not found — is it on? is the phone app closed?",
    "conectando...": "connecting...",
    "conectado ({end})": "connected ({end})",
    "conexão perdida: {erro}": "connection lost: {erro}",
    "volante não respondeu à leitura": "the wheel did not answer the read",
    "desconhecido": "unknown",
    # gravações
    "parâmetro desconhecido: {nome}": "unknown parameter: {nome}",
    "{nome} fora de {lo}–{hi}": "{nome} outside {lo}–{hi}",
    "volante não conectado": "wheel not connected",
    "o volante não confirmou (4 tentativas)": "the wheel did not confirm (4 attempts)",
    "o volante não confirmou": "the wheel did not confirm",
    "botão inválido": "invalid button",
    "pedal inválido": "invalid pedal",
    "pontos inválidos": "invalid points",
    "a curva precisa de 4 pontos dentro da faixa": "the curve needs 4 points within range",
    "os pontos não podem se cruzar": "the points cannot cross",
    "o V99 só troca entre PC DInput e PC XInput (PS4/Xbox são automáticos no console)":
        "the V99 only switches between PC DInput and PC XInput (PS4/Xbox are automatic on the console)",
    "modo {modo}": "mode {modo}",
    "modo trocado, mas a releitura falhou": "mode switched, but re-reading failed",
    "perfil restaurado ao padrão": "profile restored to default",
    "reset feito, mas a releitura falhou": "reset done, but re-reading failed",
    # perfis
    "{nome}: {msg}": "{nome}: {msg}",
    "botão {origem}: {msg}": "button {origem}: {msg}",
    "curva {pedal}: {msg}": "{pedal} curve: {msg}",
    "perfil aplicado": "profile applied",
    "angulo": "angle", "ffb": "ffb", "reverso": "reverse", "mola": "spring",
    "amortecimento": "damper", "atrito": "friction", "inercia": "inertia",
    "acelerador": "throttle", "freio": "brake", "embreagem": "clutch",
    "{jogo} aberto: perfil '{nome}' aplicado": "{jogo} opened: profile '{nome}' applied",
    "Jogo fechado: perfil '{nome}' aplicado": "Game closed: profile '{nome}' applied",
    "Perfil automático falhou: {msg}": "Automatic profile failed: {msg}",
    "perfil relido": "profile re-read",
    "o volante não respondeu": "the wheel did not respond",
    "nome vazio ou volante não lido": "empty name or wheel not read yet",
    "perfil '{nome}' salvo": "profile '{nome}' saved",
    "nenhum perfil para exportar": "no profiles to export",
    "exportação cancelada": "export canceled",
    "use o programa V99 Wheel Hub para exportar": "use the V99 Wheel Hub app to export",
    "{n} perfil(is) exportado(s)": "{n} profile(s) exported",
    "importação cancelada": "import canceled",
    "use o programa V99 Wheel Hub para importar": "use the V99 Wheel Hub app to import",
    "arquivo inválido": "invalid file",
    "o arquivo não tem perfis válidos": "the file has no valid profiles",
    "{n} perfil(is) importado(s)": "{n} profile(s) imported",
    "informe o nome do executável (ex.: acc.exe)": "enter the executable name (e.g. acc.exe)",
    "disponível só no programa V99 Wheel Hub": "only available in the V99 Wheel Hub app",
    "idioma inválido": "invalid language",
    "perfil não encontrado": "profile not found",
    "apagado": "deleted",
    "pedido inválido: {erro}": "invalid request: {erro}",
    # bandeja e janela (v99_hub)
    "Abrir": "Open",
    "Aplicar perfil": "Apply profile",
    "(nenhum perfil salvo)": "(no saved profiles)",
    "Sair": "Exit",
    "Continua rodando aqui na bandeja. Clique com o botão direito para sair.":
        "Still running here in the tray. Right-click to exit.",
    "Perfil '{nome}' aplicado": "Profile '{nome}' applied",
    "Falhou: {msg}": "Failed: {msg}",
    "Perfis (*.json)": "Profiles (*.json)",
    "perfis V99 Wheel Hub.json": "V99 Wheel Hub profiles.json",
}


def em_ingles(texto: str, **campos) -> str:
    """Para o log, que é sempre em inglês."""
    s = EN.get(texto, texto)
    return s.format(**campos) if campos else s


def t(texto: str, **campos) -> str:
    s = EN.get(texto, texto) if atual == "en" else texto
    return s.format(**campos) if campos else s
