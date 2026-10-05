
from __future__ import annotations

import time as _tiempo


time = _tiempo


ANCHO_TICKS = 1 << 32


def ticks_ms() -> int:
    """Milisegundos desde un origen fijo. Envuelve cada 2^32 ms."""
    return int(_tiempo.monotonic() * 1000) & 0xFFFFFFFF


def ticks_us() -> int:
    """Microsegundos. Envuelve cada ~71 minutos."""
    return int(_tiempo.monotonic() * 1000000) & 0xFFFFFFFF


def ticks_diff(a: int, b: int) -> int:
   
    diferencia = a - b
    if diferencia > (ANCHO_TICKS // 2):
        diferencia -= ANCHO_TICKS
    elif diferencia < -(ANCHO_TICKS // 2):
        diferencia += ANCHO_TICKS
    return diferencia


def ticks_add(valor: int, delta: int) -> int:
    
    return (valor + delta) & 0xFFFFFFFF


def ticks_cmp(a: int, b: int) -> int:
   
    if a == b:
        return 0
    return 1 if ticks_diff(a, b) > 0 else -1


def sleep_ms(ms) -> None:
   
    if ms is None:
        ms = 0
    ms = float(ms)
    if ms <= 0:
        return
    _tiempo.sleep(ms / 1000.0)


def sleep_us(us) -> None:
    if us and us > 0:
        _tiempo.sleep(float(us) / 1_000_000.0)



NIVELES = {"debug": 0, "info": 1, "aviso": 2, "error": 3}


class Registro:
  

    def __init__(self, nivel: str = "info", al_escribir=None) -> None:
        self.nivel = NIVELES.get(nivel, 1)
        self.al_escribir = al_escribir or _consola
        self.lineas = 0

    def _emitir(self, origen: str, nivel: str, texto: str) -> None:
        if NIVELES.get(nivel, 1) < self.nivel:
            return
        self.lineas += 1
        self.al_escribir("[%8d ms] [%-5s] %s" % (ticks_ms(), origen.upper(), texto))

    def debug(self, origen: str, texto: str) -> None:
        self._emitir(origen, "debug", texto)

    def info(self, origen: str, texto: str) -> None:
        self._emitir(origen, "info", texto)

    def aviso(self, origen: str, texto: str) -> None:
        self._emitir(origen, "aviso", texto)

    def error(self, origen: str, texto: str) -> None:
        self._emitir(origen, "error", texto)


def _consola(linea: str) -> None:
    print(linea)



registro = Registro()


_global = registro


def registrar(*argumentos) -> None:
    
    if len(argumentos) == 1:
        registro.info("pc", argumentos[0])
    elif len(argumentos) >= 2:
        registro.info(str(argumentos[0]), " ".join(str(a) for a in argumentos[1:]))


def parchear_time() -> None:
  
    _tiempo.ticks_ms = ticks_ms
    _tiempo.ticks_us = ticks_us
    _tiempo.ticks_diff = ticks_diff
    _tiempo.ticks_add = ticks_add
    _tiempo.ticks_cmp = ticks_cmp
    _tiempo.sleep_ms = sleep_ms
    _tiempo.sleep_us = sleep_us


parchear_time()
