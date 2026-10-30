"""V99 Wheel Hub — janela própria, ícone na bandeja e inicialização com o Windows.

Roda o serviço do volante (v99_app) numa thread com o próprio loop asyncio e mostra
a interface numa janela WebView2 (pywebview). Fechar a janela esconde na bandeja
(se ativado); "Sair" na bandeja desconecta o volante e encerra.

Uso:  V99 Wheel Hub.exe [--minimizado]
"""

import asyncio
import json
import sys
import threading
import urllib.request
import winreg

import pystray
import webview
from PIL import Image

import v99_app
from v99_idioma import t

NOME = "V99 Wheel Hub"
CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def comando_autostart() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimizado'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" "{v99_app.pathlib.Path(__file__).resolve()}" --minimizado'


def autostart_ler() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            return winreg.QueryValueEx(k, NOME)[0] == comando_autostart()
    except OSError:
        return False


def autostart_gravar(ligar: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
        if ligar:
            winreg.SetValueEx(k, NOME, 0, winreg.REG_SZ, comando_autostart())
        else:
            try:
                winreg.DeleteValue(k, NOME)
            except FileNotFoundError:
                pass


def avisa_instancia_aberta():
    """Já existe um Hub aberto: pede para ele mostrar a janela."""
    req = urllib.request.Request(f"{v99_app.URL}/api/mostrar", data=b"{}", method="POST",
                                 headers={"X-V99": "1", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except OSError:
        pass


class Hub:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.parar: asyncio.Event | None = None
        self.saindo = False
        self.janela = None
        self.bandeja = None

    # ---------- serviço do volante ----------
    def inicia_servico(self) -> bool:
        pronto = threading.Event()
        resultado = {}

        def roda():
            asyncio.set_event_loop(self.loop)
            servidor = self.loop.run_until_complete(v99_app.abre_servidor())
            resultado["ok"] = servidor is not None
            pronto.set()
            if servidor is None:
                return
            self.parar = asyncio.Event()
            self.loop.run_until_complete(v99_app.servico(servidor, self.parar))

        self.thread = threading.Thread(target=roda, name="servico-v99", daemon=True)
        self.thread.start()
        pronto.wait(10)
        return resultado.get("ok", False)

    def leitura_ao_vivo(self, ligar: bool):
        """Liga a leitura ao vivo só com a janela visível (em segundo plano não gasta nada)."""
        if self.parar is not None and not self.saindo:
            asyncio.run_coroutine_threadsafe(v99_app.modo_teste(ligar), self.loop)

    def para_servico(self):
        if self.parar is not None:
            self.loop.call_soon_threadsafe(self.parar.set)
            self.thread.join(timeout=5)  # dá tempo de desligar o modo de teste (54 02)

    # ---------- janela ----------
    def mostrar(self):
        if self.janela:
            self.janela.show()
            self.janela.restore()
            self.leitura_ao_vivo(True)

    def ao_fechar(self):
        if self.saindo or not v99_app.carrega_config().get("bandeja", True):
            self.sair(da_janela=True)
            return True
        self.janela.hide()
        self.leitura_ao_vivo(False)
        v99_app.log.info("Window hidden in the tray")
        if not getattr(self, "avisou_bandeja", False):
            self.avisou_bandeja = True
            self.notificar(t("Continua rodando aqui na bandeja. Clique com o botão direito para sair."))
        return False

    def salvar_arquivo(self, sugestao: str):
        r = self.janela.create_file_dialog(webview.FileDialog.SAVE, save_filename=sugestao,
                                           file_types=(t("Perfis (*.json)"),))
        return (r[0] if isinstance(r, (list, tuple)) else r) or None

    def abrir_arquivo(self):
        r = self.janela.create_file_dialog(webview.FileDialog.OPEN, file_types=(t("Perfis (*.json)"),))
        return r[0] if r else None

    # ---------- bandeja ----------
    def notificar(self, texto: str):
        if self.bandeja:
            try:
                self.bandeja.notify(texto, NOME)
            except Exception:
                pass

    def aplica_da_bandeja(self, nome_perfil: str):
        async def aplica():
            p = v99_app.carrega_perfis().get(nome_perfil)
            if p is None:
                return
            ok, msg = await v99_app.aplica_perfil(p)
            texto = t("Perfil '{nome}' aplicado", nome=nome_perfil) if ok else t("Falhou: {msg}", msg=msg)
            v99_app.publica({"tipo": "aviso", "texto": texto})
            self.notificar(texto)
        asyncio.run_coroutine_threadsafe(aplica(), self.loop)

    def menu(self):
        def item_perfil(nome):
            return pystray.MenuItem(nome, lambda *_: self.aplica_da_bandeja(nome))

        def perfis():
            nomes = sorted(v99_app.carrega_perfis(), key=str.lower)
            return [item_perfil(n) for n in nomes] or [pystray.MenuItem(t("(nenhum perfil salvo)"), None, enabled=False)]

        # Textos como função: o pystray os relê a cada update_menu() (troca de idioma).
        return pystray.Menu(
            pystray.MenuItem(lambda _: t("Abrir"), lambda *_: self.mostrar(), default=True),
            pystray.MenuItem(lambda _: t("Aplicar perfil"), pystray.Menu(perfis)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: t("Sair"), lambda *_: self.sair()),
        )

    def idioma_mudou(self):
        if self.bandeja:
            self.bandeja.update_menu()

    def sair(self, da_janela=False):
        if self.saindo and not da_janela:
            return
        self.saindo = True
        v99_app.log.info("Exiting")
        self.para_servico()
        if self.bandeja:
            self.bandeja.stop()
        if self.janela and not da_janela:
            self.janela.destroy()

    # ---------- início ----------
    def roda(self):
        minimizado = "--minimizado" in sys.argv
        v99_app.teste["ligado"] = not minimizado  # aberto na bandeja: sem leitura ao vivo
        if not self.inicia_servico():
            avisa_instancia_aberta()
            return
        v99_app.ganchos.update({
            "mostrar": self.mostrar, "notificar": self.notificar,
            "salvar_arquivo": self.salvar_arquivo, "abrir_arquivo": self.abrir_arquivo,
            "autostart_ler": autostart_ler, "autostart_gravar": autostart_gravar,
            "idioma_mudou": self.idioma_mudou,
        })
        icone = Image.open(v99_app.ASSETS / "icone.png")
        self.bandeja = pystray.Icon("v99hub", icone, NOME, self.menu())
        self.bandeja.run_detached()

        self.janela = webview.create_window(
            f"{NOME} {v99_app.VERSAO}", v99_app.URL, width=1320, height=900, min_size=(960, 680),
            background_color="#121215", hidden=minimizado)
        self.janela.events.closing += self.ao_fechar
        self.janela.events.minimized += lambda: self.leitura_ao_vivo(False)
        self.janela.events.restored += lambda: self.leitura_ao_vivo(True)
        pasta_web = v99_app.pathlib.Path.home() / "AppData" / "Local" / NOME
        webview.start(private_mode=False, storage_path=str(pasta_web), icon=str(v99_app.ASSETS / "icone.ico"))
        if not self.saindo:  # janela fechada sem passar por sair()
            self.sair(da_janela=True)


def registra_erros():
    """Erros não tratados vão para o log (o .exe não tem console)."""
    def erro(tipo, valor, tb):
        v99_app.log.critical("Unhandled error", exc_info=(tipo, valor, tb))
    sys.excepthook = erro
    threading.excepthook = lambda a: erro(a.exc_type, a.exc_value, a.exc_traceback)


if __name__ == "__main__":
    v99_app.configura_log()
    registra_erros()
    Hub().roda()
