"""Correcao isolada em processo filho, com kill duro no timeout.

`asyncio.wait_for` so abandona a espera: a thread do OpenCV continua rodando e
prende a vaga. Aqui a correcao roda num processo filho persistente (contexto
"spawn", igual em Windows e Linux) que importa o servico uma vez e atende
pedidos por Pipe. Em timeout ou morte do filho, o pai faz kill + join e um
worker novo nasce na chamada seguinte. Em Linux o filho ainda tem teto de CPU
e de memoria (rlimit); no Windows vale so o kill pelo pai.
"""
import importlib
import multiprocessing as mp
import os
import signal
import threading

from app.servico import ImagemInvalida

TIMEOUT_S = float(os.environ.get("CLAVIA_TIMEOUT_S", "20"))
MEMORIA_MB = int(os.environ.get("CLAVIA_MEMORIA_MB", "700"))
CONCORRENCIA = int(os.environ.get("CLAVIA_CONCORRENCIA", "1"))
PARTIDA_S = 120.0  # prazo para o filho importar OpenCV/POC e avisar que esta pronto
ALVO = "app.servico:processar"  # "modulo:funcao" executada no filho (os testes trocam por uma lenta)

_ctx = mp.get_context("spawn")
_vagas = threading.BoundedSemaphore(CONCORRENCIA)
_trava = threading.Lock()
_livres = []  # workers ociosos (no maximo CONCORRENCIA)


class Ocupado(RuntimeError):
    pass


class TempoEsgotado(RuntimeError):
    pass


class FalhaNoWorker(RuntimeError):
    pass


# ---------------------------------------------------------------- processo filho

def _rlimit():
    try:
        import resource  # so existe em Unix
        return resource
    except ImportError:
        return None


def _limitar_memoria(memoria_mb):
    resource = _rlimit()
    if resource is None:
        return
    teto = memoria_mb * 1024 * 1024
    try:  # nunca abaixo do que o filho ja ocupa depois dos imports (senao todo malloc falharia)
        with open("/proc/self/statm") as f:
            atual = int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        teto = max(teto, atual + 256 * 1024 * 1024)
    except (OSError, ValueError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (teto, teto))
    except (ValueError, OSError):
        pass


def _limitar_cpu(timeout_s):
    """RLIMIT_CPU conta a vida inteira do processo: o teto e renovado a cada pedido (gasto ate aqui + timeout + 2 s)."""
    resource = _rlimit()
    if resource is None:
        return
    uso = resource.getrusage(resource.RUSAGE_SELF)
    teto = int(uso.ru_utime + uso.ru_stime + timeout_s + 2)
    try:
        _, duro = resource.getrlimit(resource.RLIMIT_CPU)
        resource.setrlimit(resource.RLIMIT_CPU, (teto, duro))
    except (ValueError, OSError):
        pass


def _laco(conn, alvo, timeout_s, memoria_mb):
    modulo, nome = alvo.split(":")
    funcao = getattr(importlib.import_module(modulo), nome)
    try:
        import cv2
        cv2.setNumThreads(1)  # servidor de 0,5 CPU; evita reservar pilha/arena por thread sob o RLIMIT_AS
    except Exception:  # noqa: BLE001
        pass
    _limitar_memoria(memoria_mb)
    conn.send("pronto")
    while True:
        try:
            dados, com_imagem = conn.recv()
        except (EOFError, OSError):
            return
        _limitar_cpu(timeout_s)
        try:
            resp = ("ok", funcao(dados, com_imagem))
        except ImagemInvalida as e:
            resp = ("invalida", str(e), e.status)
        except Exception as e:  # noqa: BLE001  (so o tipo sai do filho; nada da foto)
            resp = ("erro", type(e).__name__)
        del dados
        conn.send(resp)


# ---------------------------------------------------------------- processo pai

class _Worker:
    def __init__(self):
        self.conn, filho = _ctx.Pipe()
        self.p = _ctx.Process(target=_laco, args=(filho, ALVO, TIMEOUT_S, MEMORIA_MB),
                              daemon=True, name="clavia-correcao")
        self.p.start()
        filho.close()
        try:
            pronto = self.conn.poll(PARTIDA_S) and self.conn.recv() == "pronto"
        except (EOFError, OSError):
            pronto = False
        if not pronto:
            self.matar()
            raise FalhaNoWorker("o processo de correcao nao iniciou")

    def matar(self):
        try:
            self.p.kill()
        except (OSError, ValueError):
            pass
        self.p.join(5)
        self.conn.close()

    def morreu_por_cpu(self):
        sinal = getattr(signal, "SIGXCPU", None)
        return sinal is not None and self.p.exitcode == -sinal


def _pegar():
    with _trava:
        while _livres:
            w = _livres.pop()
            if w.p.is_alive():
                return w
            w.matar()
    return _Worker()  # fora da trava: a partida leva alguns segundos


def _devolver(w):
    with _trava:
        _livres.append(w)


def iniciar():
    """Aquece os workers (chamado no startup) para a primeira correcao nao pagar a importacao do OpenCV."""
    with _trava:
        faltam = CONCORRENCIA - len(_livres)
    for _ in range(max(faltam, 0)):
        _devolver(_Worker())


def encerrar():
    with _trava:
        workers, _livres[:] = list(_livres), []
    for w in workers:
        w.matar()


def executar(dados: bytes, com_imagem: bool = False) -> dict:
    """Corrige a foto num processo filho. Roda numa thread do pai (bloqueia ate TIMEOUT_S)."""
    if not _vagas.acquire(blocking=False):
        raise Ocupado()
    try:
        w = _pegar()
        try:
            w.conn.send((dados, com_imagem))
            if not w.conn.poll(TIMEOUT_S):
                w.matar()
                raise TempoEsgotado()
            resp = w.conn.recv()
        except (EOFError, OSError) as e:  # filho morreu (rlimit, OOM, crash nativo)
            w.matar()
            if w.morreu_por_cpu():
                raise TempoEsgotado() from e
            raise FalhaNoWorker("o processo de correcao terminou de forma inesperada") from e
        if resp[0] == "ok":
            _devolver(w)
            return resp[1]
        if resp[0] == "invalida":
            _devolver(w)
            raise ImagemInvalida(resp[1], status=resp[2])
        w.matar()  # erro inesperado (ex.: MemoryError): descarta o worker por precaucao
        raise FalhaNoWorker(resp[1])
    finally:
        _vagas.release()
