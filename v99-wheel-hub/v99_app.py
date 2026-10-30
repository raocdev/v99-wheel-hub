"""V99 Wheel Hub — app de configuração para PC do volante V99 (Bluetooth).

Conecta ao volante por BLE, liga o modo de teste (54 01) para a leitura ao vivo
e serve a interface em http://127.0.0.1:8765. Ao sair desliga o modo de teste.

Gravações suportadas (ver PROTOCOLO_V99.md):
  ângulo     53 04 M 01 lo hi          (90–900, passo 10)
  FFB        53 03 M ffb reverso       (0–100, passo 10; reverso 0/1)
  forças     53 09 01 mola amort atrito inércia (0–100, passo 10)
  mapeamento 53 02 M origem destino
  modo       53 01 modo                (só entre 1 PC DInput e 2 PC XInput)
  reset      53 08 M
  curvas     52 M parte(3–5) 4×(x u16, y u16)
Cada gravação só é dada como feita quando o volante devolve o eco (D3 ou D2).
"""

import asyncio
import json
import logging
import os
import pathlib
import struct
import sys
import webbrowser
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

from bleak import BleakClient, BleakScanner

import v99_idioma as idioma
from v99_idioma import em_ingles, t
from v99_ler import CHAR_ESCRITA, CHAR_NOTIFICA, KEYMAP_SEQUENCE, NOME

PORTA = int(os.environ.get("V99_PORTA", 8765))  # outra porta só para testar ao lado do .exe
# Recursos (página, imagens) vêm de dentro do .exe quando empacotado; os dados do
# usuário ficam ao lado do .exe (portátil) ou do script.
RECURSOS = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).parent))
DADOS = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) else pathlib.Path(__file__).parent
PAGINA = RECURSOS / "app_v99.html"
ASSETS = RECURSOS / "assets"
ARQ_PERFIS = DADOS / "perfis_v99.json"
ARQ_CONFIG = DADOS / "config_v99.json"
ARQ_LOG = DADOS / "v99_log.txt"
VERSAO = "1.0"

# Log sempre em inglês, ao lado do .exe (até 1 MB + 1 arquivo antigo).
log = logging.getLogger("v99")


def configura_log():
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    try:
        arq = RotatingFileHandler(ARQ_LOG, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        arq.setFormatter(formato)
        log.addHandler(arq)
    except OSError:
        pass  # pasta sem permissão de escrita: segue sem log em arquivo
    if sys.stdout:  # o .exe sem console não tem stdout
        tela = logging.StreamHandler(sys.stdout)
        tela.setFormatter(formato)
        log.addHandler(tela)
TIPOS = {".png": "image/png", ".jpg": "image/jpeg", ".ico": "image/x-icon", ".svg": "image/svg+xml"}

# Ganchos preenchidos pela janela (v99_hub.py): mostrar, notificar, salvar_arquivo,
# abrir_arquivo, autostart_ler, autostart_gravar. Sem janela, ficam vazios.
ganchos: dict = {}

MODOS = {1: "PC DInput", 2: "PC XInput", 5: "PS4", 8: "Xbox One", 10: "Android", 11: "Switch Pro"}
MODOS_TROCAVEIS = (1, 2)  # PS4/Xbox são escolhidos pelo próprio volante no console
FAIXAS = {
    "angulo": (90, 900), "ffb": (0, 100), "reverso": (0, 1),
    "mola": (0, 100), "amortecimento": (0, 100), "atrito": (0, 100), "inercia": (0, 100),
}
FORCAS = ("mola", "amortecimento", "atrito", "inercia")
PEDAIS = {"acelerador": 3, "freio": 4, "embreagem": 5}  # parte do perfil de cada curva
CURVA_X_MAX, CURVA_Y_MAX = 4095, 65535
CODIGO_PADRAO = 46  # "restaurar": o botão volta a ser ele mesmo
ACESSORIOS = {6: "pedal", 7: "alavanca", 14: "freio_mao"}  # subcomando do aviso 53/D3 → campo do estado

# Botões do quadro ao vivo D4 03: bit i do byte → código do botão
SEQ_BYTE2 = [0, 1, 2, 3, 4, 5, 6, 7]
SEQ_BYTE3 = [8, 9, 10, 11, 12, 13, 14, 15]
SEQ_BYTE4 = [16, 17]
SEQ_BYTE12 = [25, 26, 23, 24]

clientes: set[asyncio.Queue] = set()
# Guardado sem traduzir, para mudar de idioma sem esperar o próximo status.
ultimo_status: tuple = ("procurando volante...", {}, False)
perfil: dict = {}
conexao: dict = {}          # "cliente": BleakClient conectado
esperas: list = []          # [(teste(bytes) -> bool, Future)]
trava_escrita = asyncio.Lock()
# Leitura ao vivo (modo de teste, ~33 quadros/s). O Hub a desliga quando a janela vai
# para a bandeja ou é minimizada, para não gastar nada em segundo plano.
teste: dict = {"ligado": True}


def publica(msg: dict):
    texto = json.dumps(msg)
    for q in list(clientes):
        if q.qsize() < 50:
            q.put_nowait(texto)


def msg_status() -> dict:
    texto, campos, ok = ultimo_status
    return {"tipo": "status", "texto": t(texto, **campos), "ok": ok}


def status(texto: str, ok: bool = False, **campos):
    global ultimo_status
    if (texto, campos, ok) != ultimo_status:
        log.info("Status: %s", em_ingles(texto, **campos))
    ultimo_status = (texto, campos, ok)
    publica(msg_status())


def msg_perfil() -> dict:
    return {"tipo": "perfil", **perfil, "modo_nome": MODOS.get(perfil.get("modo")) or t("desconhecido"),
            "pode_trocar_modo": perfil.get("modo") in MODOS_TROCAVEIS}


def publica_perfil():
    if perfil:
        publica(msg_perfil())


def bits(valor: int, seq: list[int]) -> list[int]:
    return [b for i, b in enumerate(seq) if (valor >> i) & 1]


def decodifica_teste(b: bytes) -> dict:
    return {
        "tipo": "dados", "raw": list(b), "angulo8": b[6],
        "acelerador": b[8], "freio": b[9], "embreagem": b[10],
        "cambio_status": b[11], "marcha": b[13],
        "botoes": bits(b[2], SEQ_BYTE2) + bits(b[3], SEQ_BYTE3)
        + bits(b[4], SEQ_BYTE4) + bits(b[12], SEQ_BYTE12),
    }


# ---------- quadros recebidos ----------

def interpreta(b: bytes):
    """Atualiza o perfil com respostas e ecos (D0, D1, D3)."""
    if b[0] == 0xD0:
        perfil.update({"modo": b[2], "firmware": b[3] | (b[4] << 8), "pedal": b[5],
                       "alavanca": b[6], "modo_angulo": b[7], "freio_mao": b[8]})
    elif b[0] == 0xD1:
        parte = b[2]
        km = perfil.setdefault("keymap", {})
        if parte in (0, 1):
            base = 17 * parte
            for i in range(17):
                if KEYMAP_SEQUENCE[base + i] != 47:
                    km[str(KEYMAP_SEQUENCE[base + i])] = b[3 + i]
        elif parte == 2:
            for i in range(2):
                km[str(KEYMAP_SEQUENCE[34 + i])] = b[3 + i]
            perfil.update({"angulo": struct.unpack_from("<h", b, 5)[0], "ffb": b[7], "mola": b[8],
                           "amortecimento": b[9], "atrito": b[10], "inercia": b[11],
                           "reverso": 1 if b[12] else 0})
        elif parte in (3, 4, 5):
            nome = {3: "acelerador", 4: "freio", 5: "embreagem"}[parte]
            perfil.setdefault("curvas", {})[nome] = [list(struct.unpack_from("<HH", b, 3 + 4 * i))
                                                     for i in range(4)]
    elif b[0] in (0xD3, 0x53) and len(b) >= 5:
        sub = b[1]
        if sub == 1:
            perfil["modo"] = b[2]
        elif sub == 2:
            if str(b[3]) not in perfil.get("keymap", {}):
                return
            perfil["keymap"][str(b[3])] = b[4]
        elif sub == 3:
            perfil["ffb"], perfil["reverso"] = b[3], 1 if b[4] else 0
        elif sub == 4:
            # Também chega sozinho quando o botão de ângulo do volante é usado
            # (modo de ângulo 0). Quadros corrompidos são ignorados.
            angulo = struct.unpack_from("<h", b, 4)[0]
            if b[3] not in (0, 1) or not 90 <= angulo <= 1080:
                return
            perfil["angulo"], perfil["modo_angulo"] = angulo, b[3]
        elif sub == 9:
            for i, k in enumerate(FORCAS):
                perfil[k] = b[3 + i]
        elif sub in ACESSORIOS:
            # O volante avisa sozinho quando um acessório é ligado ou desligado.
            perfil[ACESSORIOS[sub]] = b[3]
        if sub in (1, 2, 3, 4, 9, *ACESSORIOS):
            publica_perfil()


def ao_receber(b: bytes):
    log.info("RX %s", b.hex(" ").upper())
    interpreta(b)
    for item in list(esperas):
        teste, fut = item
        if not fut.done() and teste(b):
            fut.set_result(b)
            esperas.remove(item)


async def envia(cmd: bytes, teste, tentativas=4, espera=0.8) -> bytes | None:
    """Envia e espera a resposta que passa em `teste`, reenviando se não vier.

    Só usar com comandos idempotentes (valor absoluto ou leitura): o volante às
    vezes não responde a um comando que chega logo depois de outro.
    """
    c = conexao.get("cliente")
    if c is None or not c.is_connected:
        return None
    for tentativa in range(tentativas):
        fut = asyncio.get_running_loop().create_future()
        item = (teste, fut)
        esperas.append(item)
        log.info("TX %s%s", cmd.hex(" ").upper(), f" (retry {tentativa})" if tentativa else "")
        await c.write_gatt_char(CHAR_ESCRITA, cmd, response=True)
        try:
            return await asyncio.wait_for(fut, espera)
        except asyncio.TimeoutError:
            if item in esperas:
                esperas.remove(item)
            await asyncio.sleep(0.3 * (tentativa + 1))
    return None


async def modo_teste(ligar: bool):
    """Liga (54 01) ou desliga (54 02) a leitura ao vivo, se o volante estiver conectado."""
    if teste["ligado"] == ligar:
        return
    teste["ligado"] = ligar
    c = conexao.get("cliente")
    if c is None or not c.is_connected:
        return
    try:
        async with trava_escrita:
            await c.write_gatt_char(CHAR_ESCRITA, bytes([0x54, 0x01 if ligar else 0x02]), response=True)
        log.info("Live data (test mode) %s", "on" if ligar else "off")
    except Exception as e:
        log.warning("Could not switch test mode: %s", e)


def eco(sub: int):
    return lambda b: b[0] in (0xD3, 0x53) and b[1] == sub


async def le_perfil(tentativas=4) -> bool:
    est = await envia(bytes([0x50, 0x01]), lambda b: b[0] == 0xD0, tentativas)
    if est is None:
        return False
    for parte in range(6):
        r = await envia(bytes([0x51, perfil["modo"], parte]),
                        lambda b, p=parte: b[0] == 0xD1 and b[2] == p, tentativas)
        if r is None:
            return False
    publica_perfil()
    return True


# ---------- gravações ----------

def pronto() -> str | None:
    c = conexao.get("cliente")
    if c is None or not c.is_connected or "angulo" not in perfil:
        return t("volante não conectado")
    return None


async def grava(nome: str, valor: int) -> tuple[bool, str]:
    if nome not in FAIXAS:
        return False, t("parâmetro desconhecido: {nome}", nome=nome)
    lo, hi = FAIXAS[nome]
    if not lo <= valor <= hi:
        return False, t("{nome} fora de {lo}–{hi}", nome=t(nome), lo=lo, hi=hi)
    if (erro := pronto()):
        return False, erro
    m = perfil["modo"]
    # A confirmação precisa devolver os mesmos valores (o eco às vezes chega
    # misturado com um quadro ao vivo).
    if nome == "angulo":
        lo_hi = bytes([valor & 0xFF, (valor >> 8) & 0xFF])
        cmd = bytes([0x53, 0x04, m, 0x01]) + lo_hi
        teste = lambda b: eco(4)(b) and b[4:6] == lo_hi
    elif nome in ("ffb", "reverso"):
        ffb, rev = (valor, perfil["reverso"]) if nome == "ffb" else (perfil["ffb"], valor)
        cmd = bytes([0x53, 0x03, m, ffb, rev])
        teste = lambda b: eco(3)(b) and b[3] == ffb and (1 if b[4] else 0) == rev
    else:
        novas = bytes(({k: perfil[k] for k in FORCAS} | {nome: valor})[k] for k in FORCAS)
        cmd = bytes([0x53, 0x09, 0x01]) + novas
        teste = lambda b: eco(9)(b) and b[3:7] == novas
    async with trava_escrita:
        ok = await envia(cmd, teste) is not None
        await asyncio.sleep(0.1)  # o volante precisa de ~100 ms entre comandos
    return (True, "ok") if ok else (False, t("o volante não confirmou (4 tentativas)"))


async def mapeia(origem: int, destino: int) -> tuple[bool, str]:
    if (erro := pronto()):
        return False, erro
    if str(origem) not in perfil.get("keymap", {}) or not 0 <= destino <= 255:
        return False, t("botão inválido")
    if destino == CODIGO_PADRAO:
        destino = origem
    cmd = bytes([0x53, 0x02, perfil["modo"], origem, destino])
    async with trava_escrita:
        r = await envia(cmd, lambda b: eco(2)(b) and b[3] == origem and b[4] == destino)
        await asyncio.sleep(0.1)
    return (True, "ok") if r is not None else (False, t("o volante não confirmou"))


async def grava_curva(pedal: str, pontos: list) -> tuple[bool, str]:
    if (erro := pronto()):
        return False, erro
    if pedal not in PEDAIS:
        return False, t("pedal inválido")
    try:
        pts = [(int(x), int(y)) for x, y in pontos]
    except (TypeError, ValueError):
        return False, t("pontos inválidos")
    if len(pts) != 4 or any(not (0 <= x <= CURVA_X_MAX and 0 <= y <= CURVA_Y_MAX) for x, y in pts):
        return False, t("a curva precisa de 4 pontos dentro da faixa")
    if any(pts[i][0] > pts[i + 1][0] or pts[i][1] > pts[i + 1][1] for i in range(3)):
        return False, t("os pontos não podem se cruzar")
    parte = PEDAIS[pedal]
    cmd = bytearray(20)
    cmd[0], cmd[1], cmd[2] = 0x52, perfil["modo"], parte
    for i, (x, y) in enumerate(pts):
        struct.pack_into("<HH", cmd, 3 + 4 * i, x, y)
    async with trava_escrita:
        r = await envia(bytes(cmd), lambda b: b[0] == 0xD2 and b[2] == parte)
        await asyncio.sleep(0.1)
    if r is None:
        return False, t("o volante não confirmou")
    perfil.setdefault("curvas", {})[pedal] = [list(p) for p in pts]
    publica_perfil()
    return True, "ok"


async def troca_modo(modo: int) -> tuple[bool, str]:
    if (erro := pronto()):
        return False, erro
    if perfil["modo"] not in MODOS_TROCAVEIS or modo not in MODOS_TROCAVEIS:
        return False, t("o V99 só troca entre PC DInput e PC XInput (PS4/Xbox são automáticos no console)")
    async with trava_escrita:
        r = await envia(bytes([0x53, 0x01, modo]), lambda b: eco(1)(b) and b[2] == modo)
        if r is None:
            return False, t("o volante não confirmou")
        await asyncio.sleep(1.0)  # cada modo tem o seu perfil: relê tudo
        ok = await le_perfil()
    return (True, t("modo {modo}", modo=MODOS[modo])) if ok else (True, t("modo trocado, mas a releitura falhou"))


async def reseta() -> tuple[bool, str]:
    if (erro := pronto()):
        return False, erro
    async with trava_escrita:
        r = await envia(bytes([0x53, 0x08, perfil["modo"]]), eco(8))
        if r is None:
            return False, t("o volante não confirmou")
        await asyncio.sleep(1.0)
        ok = await le_perfil()
    return (True, t("perfil restaurado ao padrão")) if ok else (True, t("reset feito, mas a releitura falhou"))


# ---------- perfis salvos ----------

def carrega_perfis() -> dict:
    try:
        return json.loads(ARQ_PERFIS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def salva_perfis(p: dict):
    ARQ_PERFIS.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def foto_do_perfil() -> dict:
    """Perfil completo: ajustes + botões alterados + curvas gravadas."""
    foto = {k: perfil[k] for k in ("angulo", "ffb", "reverso", *FORCAS)}
    foto["botoes"] = {o: d for o, d in perfil.get("keymap", {}).items() if int(o) != d}
    foto["curvas"] = {p: c for p, c in perfil.get("curvas", {}).items() if any(x or y for x, y in c)}
    return foto


async def aplica_perfil(valores: dict) -> tuple[bool, str]:
    for nome in ("angulo", "ffb", "reverso", *FORCAS):
        if nome in valores and valores[nome] != perfil.get(nome):
            ok, msg = await grava(nome, int(valores[nome]))
            if not ok:
                return False, t("{nome}: {msg}", nome=t(nome), msg=msg)
    # Perfis antigos (sem "botoes"/"curvas") não mexem nessas partes.
    if "botoes" in valores:
        desejado = {str(k): int(v) for k, v in valores["botoes"].items()}
        for origem, atual in list(perfil.get("keymap", {}).items()):
            alvo = desejado.get(origem, int(origem))
            if alvo != atual:
                ok, msg = await mapeia(int(origem), alvo)
                if not ok:
                    return False, t("botão {origem}: {msg}", origem=origem, msg=msg)
    for pedal, pontos in (valores.get("curvas") or {}).items():
        if pedal in PEDAIS and pontos != perfil.get("curvas", {}).get(pedal):
            ok, msg = await grava_curva(pedal, pontos)
            if not ok:
                return False, t("curva {pedal}: {msg}", pedal=t(pedal), msg=msg)
    return True, t("perfil aplicado")


def perfil_valido(p) -> bool:
    return isinstance(p, dict) and all(isinstance(p.get(k), int) for k in ("angulo", "ffb", *FORCAS))


# ---------- configuração e perfil automático por jogo ----------

def carrega_config() -> dict:
    try:
        c = json.loads(ARQ_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        c = {}
    c.setdefault("jogos", {})          # {"acc.exe": "ACC"}
    c.setdefault("perfil_fora", "")    # perfil aplicado quando o jogo fecha
    c.setdefault("bandeja", True)      # fechar a janela manda para a bandeja
    c.setdefault("idioma", "pt")       # "pt" ou "en"
    return c


def salva_config(c: dict):
    ARQ_CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


IGNORAR_PROCESSOS = {
    "system", "registry", "idle", "svchost.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "smss.exe", "dwm.exe", "fontdrvhost.exe", "conhost.exe", "runtimebroker.exe", "sihost.exe",
    "taskhostw.exe", "ctfmon.exe", "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "dllhost.exe", "wudfhost.exe", "audiodg.exe", "spoolsv.exe", "msedgewebview2.exe",
    "python.exe", "pythonw.exe", "v99 wheel hub.exe", "memory compression", "secure system",
}


def processos() -> set[str]:
    import psutil
    nomes = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if n:
            nomes.add(n)
    return nomes


estado_jogo: dict = {"ativo": None}


async def vigia_jogos():
    """A cada 3 s, aplica o perfil do jogo aberto; ao fechar, aplica o perfil "fora de jogo"."""
    while True:
        await asyncio.sleep(3)
        cfg = carrega_config()
        if not cfg["jogos"] or pronto():
            continue
        try:
            abertos = await asyncio.get_running_loop().run_in_executor(None, processos)
        except Exception:
            continue
        jogo = next((exe for exe in cfg["jogos"] if exe.lower() in abertos), None)
        if jogo == estado_jogo["ativo"]:
            continue
        estado_jogo["ativo"] = jogo
        nome_perfil = cfg["jogos"][jogo] if jogo else cfg["perfil_fora"]
        p = carrega_perfis().get(nome_perfil) if nome_perfil else None
        if p is None:
            continue
        ok, msg = await aplica_perfil(p)
        modelo, campos = (("{jogo} aberto: perfil '{nome}' aplicado", {"jogo": jogo, "nome": nome_perfil}) if jogo
                          else ("Jogo fechado: perfil '{nome}' aplicado", {"nome": nome_perfil})) if ok \
            else ("Perfil automático falhou: {msg}", {"msg": msg})
        texto = t(modelo, **campos)
        log.info("Auto profile: %s", em_ingles(modelo, **campos))
        publica({"tipo": "aviso", "texto": texto})
        if "notificar" in ganchos:
            ganchos["notificar"](texto)


async def chama_gancho(nome: str, *args):
    f = ganchos.get(nome)
    if f is None:
        return None
    return await asyncio.get_running_loop().run_in_executor(None, f, *args)


# ---------- HTTP ----------

API_SILENCIOSA = {"/api/mostrar"}  # pedidos que não vão para o log


async def responde(writer, codigo: str, corpo: bytes, tipo="application/json; charset=utf-8"):
    writer.write(f"HTTP/1.1 {codigo}\r\nContent-Type: {tipo}\r\nContent-Length: {len(corpo)}\r\n"
                 "Cache-Control: no-store\r\n\r\n".encode() + corpo)
    await writer.drain()


async def api(caminho: str, dados: dict) -> tuple[bool, str] | dict:
    if caminho == "/api/gravar":
        return await grava(dados.get("nome", ""), int(dados.get("valor", -1)))
    if caminho == "/api/mapear":
        return await mapeia(int(dados.get("origem", -1)), int(dados.get("destino", -1)))
    if caminho == "/api/curva":
        return await grava_curva(str(dados.get("pedal")), dados.get("pontos") or [])
    if caminho == "/api/modo":
        return await troca_modo(int(dados.get("modo", -1)))
    if caminho == "/api/reset":
        return await reseta()
    if caminho == "/api/reler":
        if (erro := pronto()):
            return False, erro
        async with trava_escrita:
            ok = await le_perfil()
        return (True, t("perfil relido")) if ok else (False, t("o volante não respondeu"))
    if caminho == "/api/perfis":
        return carrega_perfis()
    if caminho == "/api/perfis/salvar":
        nome = str(dados.get("nome", "")).strip()[:40]
        if not nome or "angulo" not in perfil:
            return False, t("nome vazio ou volante não lido")
        p = carrega_perfis()
        p[nome] = foto_do_perfil()
        salva_perfis(p)
        return True, t("perfil '{nome}' salvo", nome=nome)
    if caminho == "/api/perfis/exportar":
        p = carrega_perfis()
        if not p:
            return False, t("nenhum perfil para exportar")
        destino = await chama_gancho("salvar_arquivo", t("perfis V99 Wheel Hub.json"))
        if not destino:
            return False, t("exportação cancelada" if "salvar_arquivo" in ganchos else "use o programa V99 Wheel Hub para exportar")
        pathlib.Path(destino).write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        return True, t("{n} perfil(is) exportado(s)", n=len(p))
    if caminho == "/api/perfis/importar":
        origem = await chama_gancho("abrir_arquivo")
        if not origem:
            return False, t("importação cancelada" if "abrir_arquivo" in ganchos else "use o programa V99 Wheel Hub para importar")
        try:
            novos = json.loads(pathlib.Path(origem).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, t("arquivo inválido")
        novos = {str(k)[:40]: v for k, v in novos.items() if perfil_valido(v)} if isinstance(novos, dict) else {}
        if not novos:
            return False, t("o arquivo não tem perfis válidos")
        p = carrega_perfis() | novos
        salva_perfis(p)
        return True, t("{n} perfil(is) importado(s)", n=len(novos))
    if caminho == "/api/config":
        cfg = carrega_config()
        auto = await chama_gancho("autostart_ler")
        return {**cfg, "autostart": auto, "tem_janela": "mostrar" in ganchos, "jogo_ativo": estado_jogo["ativo"],
                "versao": VERSAO}
    if caminho == "/api/config/gravar":
        cfg = carrega_config()
        if "perfil_fora" in dados:
            cfg["perfil_fora"] = str(dados["perfil_fora"])[:40]
        if "bandeja" in dados:
            cfg["bandeja"] = bool(dados["bandeja"])
        if "idioma" in dados:
            if dados["idioma"] not in idioma.IDIOMAS:
                return False, t("idioma inválido")
            cfg["idioma"] = idioma.atual = dados["idioma"]
            publica(msg_status())
            publica_perfil()
            await chama_gancho("idioma_mudou")
        if "jogo" in dados:
            exe = str(dados["jogo"]).strip().lower()[:80]
            if not exe.endswith(".exe"):
                return False, t("informe o nome do executável (ex.: acc.exe)")
            if dados.get("perfil"):
                cfg["jogos"][exe] = str(dados["perfil"])[:40]
            else:
                cfg["jogos"].pop(exe, None)
            estado_jogo["ativo"] = None  # reavalia na próxima volta
        salva_config(cfg)
        if "autostart" in dados:
            if "autostart_gravar" not in ganchos:
                return False, t("disponível só no programa V99 Wheel Hub")
            await chama_gancho("autostart_gravar", bool(dados["autostart"]))
        return True, "ok"
    if caminho == "/api/processos":
        try:
            nomes = await asyncio.get_running_loop().run_in_executor(None, processos)
        except Exception:
            nomes = set()
        return {"processos": sorted(n for n in nomes if n.endswith(".exe") and n not in IGNORAR_PROCESSOS)}
    if caminho == "/api/mostrar":
        await chama_gancho("mostrar")
        return True, "ok"
    if caminho == "/api/perfis/aplicar":
        p = carrega_perfis().get(str(dados.get("nome")))
        return (False, t("perfil não encontrado")) if p is None else await aplica_perfil(p)
    if caminho == "/api/perfis/apagar":
        p = carrega_perfis()
        ok = p.pop(str(dados.get("nome")), None) is not None
        salva_perfis(p)
        return ok, t("apagado" if ok else "perfil não encontrado")
    raise KeyError(caminho)


async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        linha = (await reader.readline()).decode(errors="replace").split(" ")
        cab = {}
        while (l := await reader.readline()) not in (b"\r\n", b"\n", b""):
            k, _, v = l.decode(errors="replace").partition(":")
            cab[k.strip().lower()] = v.strip()
        metodo, caminho = (linha + ["", ""])[:2]
        url = urlparse(caminho)
        corpo = await reader.readexactly(int(cab.get("content-length", 0) or 0))

        # Só aceita a própria página: bloqueia sites externos e DNS rebinding.
        if cab.get("host") not in (f"127.0.0.1:{PORTA}", f"localhost:{PORTA}"):
            return await responde(writer, "403 Forbidden", b"{}")

        if url.path == "/eventos":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Cache-Control: no-cache\r\nConnection: keep-alive\r\n\r\n")
            q: asyncio.Queue = asyncio.Queue()
            clientes.add(q)
            q.put_nowait(json.dumps(msg_status()))
            if perfil:
                q.put_nowait(json.dumps(msg_perfil()))
            try:
                while True:
                    writer.write(f"data: {await q.get()}\n\n".encode())
                    await writer.drain()
            finally:
                clientes.discard(q)

        if url.path.startswith("/api/"):
            if metodo != "POST" or cab.get("x-v99") != "1":
                return await responde(writer, "403 Forbidden", b"{}")
            dados = {}
            try:
                dados = json.loads(corpo or b"{}")
                r = await api(url.path, dados)
            except KeyError:
                return await responde(writer, "404 Not Found", b"{}")
            except (ValueError, TypeError) as e:
                r = (False, t("pedido inválido: {erro}", erro=e))
            if isinstance(r, dict):
                return await responde(writer, "200 OK", json.dumps(r).encode())
            ok, msg = r
            if url.path not in API_SILENCIOSA:
                log.info("Request %s %s -> %s", url.path, json.dumps(dados, ensure_ascii=False),
                         "ok" if ok else "failed")
            return await responde(writer, "200 OK" if ok else "409 Conflict",
                                  json.dumps({"ok": ok, "msg": msg}).encode())

        if url.path.startswith("/assets/"):
            arq = (ASSETS / url.path[len("/assets/"):]).resolve()
            if arq.parent != ASSETS.resolve() or not arq.is_file() or arq.suffix not in TIPOS:
                return await responde(writer, "404 Not Found", b"{}")
            return await responde(writer, "200 OK", arq.read_bytes(), TIPOS[arq.suffix])

        # A página lê o idioma do atributo lang (a tradução roda antes de tudo).
        pagina = PAGINA.read_bytes()
        if idioma.atual == "en":
            pagina = pagina.replace(b'<html lang="pt-BR">', b'<html lang="en">', 1)
        await responde(writer, "200 OK", pagina, "text/html; charset=utf-8")
    except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
        pass
    except Exception:
        log.exception("HTTP error")
    finally:
        writer.close()


# ---------- Bluetooth ----------

async def bluetooth():
    while True:
        status("procurando PXN-V99...")
        d = await BleakScanner.find_device_by_filter(
            lambda d, ad: (d.name or ad.local_name or "").upper() == NOME, timeout=10.0
        )
        if d is None:
            status("volante não encontrado — está ligado? o app do celular está fechado?")
            await asyncio.sleep(2)
            continue

        def ao_notificar(_, dados: bytearray):
            b = bytes(dados)
            if b[:2] == b"\xd4\x03":
                publica(decodifica_teste(b))
            elif b:
                ao_receber(b)

        try:
            status("conectando...")
            async with BleakClient(d) as c:
                await c.start_notify(CHAR_NOTIFICA, ao_notificar)
                conexao["cliente"] = c
                perfil.clear()
                try:
                    if not await le_perfil():
                        raise RuntimeError(t("volante não respondeu à leitura"))
                    if teste["ligado"]:
                        await c.write_gatt_char(CHAR_ESCRITA, bytes([0x54, 0x01]), response=True)
                    log.info("Wheel: mode %s, firmware %s, pedals %s, shifter %s, live data %s",
                             perfil.get("modo"), perfil.get("firmware"), perfil.get("pedal"),
                             perfil.get("alavanca"), "on" if teste["ligado"] else "off")
                    status("conectado ({end})", ok=True, end=d.address)
                    while c.is_connected:
                        await asyncio.sleep(0.5)
                finally:
                    conexao.pop("cliente", None)
                    if c.is_connected:
                        await c.write_gatt_char(CHAR_ESCRITA, bytes([0x54, 0x02]), response=True)
                        log.info("Live data (test mode) off, disconnecting")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # queda de conexão: tenta de novo
            status("conexão perdida: {erro}", erro=str(e))
            await asyncio.sleep(2)


URL = f"http://127.0.0.1:{PORTA}"


async def abre_servidor():
    """Abre o servidor local; None se a porta já estiver em uso (app já aberto)."""
    configura_log()
    idioma.atual = carrega_config()["idioma"]
    try:
        servidor = await asyncio.start_server(http, "127.0.0.1", PORTA)
    except OSError:
        log.info("Port %s already in use: another instance is running", PORTA)
        return None
    log.info("V99 Wheel Hub %s started (%s, language %s)", VERSAO, URL, idioma.atual)
    return servidor


async def servico(servidor, parar: asyncio.Event):
    """Roda Bluetooth + vigia de jogos até `parar`; ao sair, desliga o modo de teste."""
    async with servidor:
        tarefas = [asyncio.create_task(bluetooth()), asyncio.create_task(vigia_jogos())]
        await parar.wait()
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
    log.info("Service stopped")


async def main():
    servidor = await abre_servidor()
    if servidor is None:
        print(f"O app já está aberto em outra janela. Abrindo {URL} no navegador.")
        webbrowser.open(URL)
        return
    print(f"V99 Wheel Hub em {URL}  (Ctrl+C para sair)")
    if "--sem-navegador" not in sys.argv:
        webbrowser.open(URL)
    await servico(servidor, asyncio.Event())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
