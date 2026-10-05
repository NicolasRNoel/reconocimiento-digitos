"""
MicroPython de verdad: registro con marca de tiempo y lo que falta de CPython.

QUE SE USA Y QUE NO
-------------------
En la ESP32 hay `time.ticks_ms()` y `time.sleep_ms()`. En el PC hay
`time.monotonic()` y `time.sleep()`, y no hay los otros dos. Este modulo
define los nombres de la ESP con una implementacion equivalente para CPython, de
forma que el firmware se pueda ejecutar en los dos sitios sin una sola
condicional.

LA DIFERENCIA QUE MAS CUESTA
----------------------------
`ticks_ms()` en la ESP32 devuelve un entero de 32 bits que se desborda cada 49
dias. Si se comparan dos de esos valores con `<` sin mas, un salto del contador
da un resultado invertido. Por eso `time.ticks_diff(a, b)` es la unica forma
correcta de calcular "cuanto tiempo ha pasado", y `ticks_add` para sumar un
intervalo. En el PC tambien estan, pero como identities, de forma que el codigo
que los usa es el mismo en los dos sitios y no hay dos caminos que mantener.

LO QUE ESTA IMPLEMENTADO A MANO Y POR QUE
-----------------------------------------
`ticks_add`, `ticks_diff` y `ticks_cmp` son identidades aqui. Es lo correcto: en
CPython el reloj no se desborda. Si se quisieran las cuentas exactas del
desbordamiento, se implementarian en Python y el firmware no cambiaria ni una
coma.
"""

from __future__ import annotations

import time as _tiempo

# El firmware usa `time.ticks_ms()`, `time.sleep_ms()` y `time.ticks_diff()`, que
# solo existen en MicroPython. En vez de que cada archivo del firmware hiciera
#
#     from micro import ticks_ms, sleep_ms
#
# y cambiar una linea por archivo, seinyectan aqui en el modulo `time` de
# Python. El firmware queda entonces identico byte a byte al que va a la placa:
# `import time` y `time.ticks_ms()` funcionan en los dos sitios.
#
# Es la misma idea que registrar un `machine` falso, pero al reves: aqui lo que
# se completa es una libreria estandar para que le falten menos cosas.
time = _tiempo

# Cuanto mide el contador. En la ESP32 son 32 bits y desborda cada 49.7 dias.
# Aqui no desborda, pero se declara igual para que el codigo que compara
# teniendo en cuenta el ticks se lea natural.
ANCHO_TICKS = 1 << 32


def ticks_ms() -> int:
    """Milisegundos desde un origen fijo. Envuelve cada 2^32 ms."""
    return int(_tiempo.monotonic() * 1000) & 0xFFFFFFFF


def ticks_us() -> int:
    """Microsegundos. Envuelve cada ~71 minutos."""
    return int(_tiempo.monotonic() * 1000000) & 0xFFFFFFFF


def ticks_diff(a: int, b: int) -> int:
    """a - b, correcting el desbordamiento del contador.

    En CPython `a` y `b` vienen del mismo reloj y el rango es pequeno, asi que
    la resta normal sirve. Se deja el`if` explicito para que quede claro que la
    funcion existe por un motivo, no por adorno: si algun dia estos ticks
    empiezan a venir de un bus con contador propio, ya esta el hueco.
    """
    diferencia = a - b
    if diferencia > (ANCHO_TICKS // 2):
        diferencia -= ANCHO_TICKS
    elif diferencia < -(ANCHO_TICKS // 2):
        diferencia += ANCHO_TICKS
    return diferencia


def ticks_add(valor: int, delta: int) -> int:
    """valor + delta, dejando que el contador desborde como en la ESP."""
    return (valor + delta) & 0xFFFFFFFF


def ticks_cmp(a: int, b: int) -> int:
    """-1, 0 o 1, como en la ESP32. Util para ordenar marcas de tiempo."""
    if a == b:
        return 0
    return 1 if ticks_diff(a, b) > 0 else -1


def sleep_ms(ms) -> None:
    """Dormir milisegundos. El firmware lo llama en los bucles, asi que no
    puede ser un busy-wait: quemaria la CPU y el LED dejaria de parpadear."""
    if ms is None:
        ms = 0
    ms = float(ms)
    if ms <= 0:
        return
    _tiempo.sleep(ms / 1000.0)


def sleep_us(us) -> None:
    if us and us > 0:
        _tiempo.sleep(float(us) / 1_000_000.0)


# ------------------------------------------------------------- diagnostico ----

NIVELES = {"debug": 0, "info": 1, "aviso": 2, "error": 3}


class Registro:
    """Escribe por el puerto de diagnostico con marca de tiempo.

    El formato es el mismo en la ESP y en el PC, para que el log de una
    simulacion y el de una placa real se puedan leer con la misma cabeza:

        [  12345 ms] [ESP-A] texto

    En la ESP sale por el UART de diagnostico, que en la practica es el mismo
    cable que usa la PC para las tramas, y por eso va precedido por una marca
    reconocible. En el PC sale por consola. La ESP-A y la ESP-B escriben las dos
    en el mismo puerto, y es la PC la que las separa: asi los dos firmwares son
    identicos y quien ordena el log es el receptor.
    """

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


# Instancia global. El firmware la importa y llama a `registrar("ESP-A", ...)`.
registro = Registro()

# El nombre corto que usa el firmware. Firma: registrar(texto) o registrar( origen, texto).
_global = registro


def registrar(*argumentos) -> None:
    """`registrar("texto")` o `registrar("ESP-A", "texto")`.

    El firmware siempre usa la forma de dos argumentos, que deja claro de quien
    viene la linea. La de uno se acepta para los avisos sueltos.
    """
    if len(argumentos) == 1:
        registro.info("pc", argumentos[0])
    elif len(argumentos) >= 2:
        registro.info(str(argumentos[0]), " ".join(str(a) for a in argumentos[1:]))


def parchear_time() -> None:
    """Agrega a `time` lo que solo existe en MicroPython.

    Se llama al importar este modulo, que es lo que hace que
    `firmware/esp_a/main.py` pueda hacer `time.ticks_ms()` sin una sola linea
    condicional. En la placa ya existen y esta funcion no hace nada.
    """
    _tiempo.ticks_ms = ticks_ms
    _tiempo.ticks_us = ticks_us
    _tiempo.ticks_diff = ticks_diff
    _tiempo.ticks_add = ticks_add
    _tiempo.ticks_cmp = ticks_cmp
    _tiempo.sleep_ms = sleep_ms
    _tiempo.sleep_us = sleep_us


parchear_time()
