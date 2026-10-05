"""
Modulo `machine` falso para el simulador.

ESTO NO ES UN JUEGO DE DATOS: ES LA CAPA DE HARDWARE
---------------------------------------------------
El firmware de las ESP importa `machine`. En el PC no existe, asi que este modulo
se registra en `sys.modules` con ese nombre ANTES de importar el firmware. A
partir de ahi, `from machine import Pin, UART, SPI, I2C` funciona y devuelve
objetos que se comportan como los del Hardware.

La razon de hacerlo asi, y no con una API propia del simulador, es que el
firmware que se ejecuta en el PC es LITERALMENTE el mismo archivo que se copia a
la placa con `mpremote cp`. Si el simulador usara una API distinta, estariamos
validando una copia del firmware, no el firmware, y el unico que se enteraria de
la diferencia seria quien lo montara en la placa.

DONDE ESTA CADA COSA
--------------------
    Pin    aqui mismo
    UART   aqui mismo, sobre una tuberia de `pc/transportes.py`
    SPI    aqui mismo, sobre `simulacion/bus_spi.py`
    I2C    aqui mismo, sobre `simulacion/bus_i2c.py`

Lo que cambia entre la ESP y el PC son los retards, que se calibran abajo.
"""

from __future__ import annotations

import sys
import time as _tiempo

from simulacion.bus_i2c import BusI2C, EepromEsclavo, LcdEsclavo
from simulacion.bus_spi import BusSPI
from pc import transportes

# ---------------------------------------------------------------- retardos -----
# Cuanto se retrasa cada operacion para que el simulador tenga el ritmo del
# hardware. Todo a 0 = la simulacion corre mil veces mas rapido que la realidad y
# no sirve para nada. Todo a "real" = un LCD a 40 us por caracter hace la
# simulacion inservible para trabajar.
#
# Se eligieron estos valores, y no otros, mirando cuanto tarda cada cosa de
# verdad: la UART a 115200 son 87 us por byte, el SPI a 1 MHz son 8 us, y el HD44780
# unos 40 us por caracter. Con un factor de 100 las esperas son de milisegundos, se
# sienten en el log y nocaten.

RETRASO_UART = 0.0        # La UART real es de 87 us/byte, pero el byte se corta
                          # en trozos de 1 a 4 y eso ya mete ruido de sobra.
RETRASO_SPI = 0.0         # El SPI real son 8 us/byte. Se deja en 0 porque el
                          # hilo de la ESP-A ya espera a que la ESP-B lea, y esa
                          # espera es el retardo que importa.
RETRASO_I2C = 0.0         # El HD44780 real son 40 us por caracter.

# Se trocean las escrituras del UART para que el parser de tramas se ejercite.
# Es el detalle mas importante de todo el simulador: sin esto, las tramas
# llegarian enteras y alineadas, el despiece de `Traza` nunca se probaria, y
# entonces el firmware pasaria aqui y fallaria con la placa de verdad, que
# trocea igual o peor.
TROCEADO_UART = (1, 4)


# -------------------------------------------------------------------- Pin ------

class Pin:
    """Un pin GPIO. Lo que importa es que sea invocable: `cs(0)` y `cs(1)`."""

    MSB = 0
    LSB = 1
    OUT = 1
    IN = 0
    PULL_UP = 2
    PULL_DOWN = 3

    def __init__(self, numero: int, modo=None, pull=None, valor=None) -> None:
        self.numero = numero
        self.modo = modo
        self.valor_actual = 0 if valor is None else int(valor)
        self._oyentes = []
        self.cambios = 0

    def init(self, modo=None, pull=None, valor=None) -> None:
        self.modo = modo
        if valor is not None:
            self.value(valor)

    def value(self, valor=None):
        if valor is None:
            return self.valor_actual
        nuevo = int(valor)
        if nuevo != self.valor_actual:
            self.valor_actual = nuevo
            self.cambios += 1
            for oyente in list(self._oyentes):
                oyente(self.valor_actual)
        return None

    def on(self, oyente) -> None:
        """Registra un callback. Lo usa la ESP-B para enterarse de CS."""
        self._oyentes.append(oyente)

    def __call__(self, valor=None):
        return self.value(valor)

    def __repr__(self) -> str:
        return "Pin(%d)=%d" % (self.numero, self.valor_actual)


# ------------------------------------------------------------------- UART ------

class UART:
    """Puerto serie. Los dos extremos son `Tuberia`, que ya conoce el firmware."""

    def __init__(self, numero=1, baudios=115200, bits=8, parity=None, stop=1,
                 timeout=None, rxbuf=256) -> None:
        self.numero = numero
        self.baudios = baudios
        self.lectura = transportes.Tuberia("uart%d.rx" % numero)
        self.escritura = transportes.Tuberia("uart%d.tx" % numero)
        self.bytes_rx = 0
        self.bytes_tx = 0

    def any(self) -> int:
        return len(self.lectura)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self.lectura) or 1
        datos = self.lectura.leer(n, timeout=0.0)
        self.bytes_rx += len(datos)
        return datos

    def readline(self) -> bytes:
        return b""

    def write(self, datos) -> int:
        """Escribe TROCEADO, como un conversor USB-TTL de verdad.

        Es lo que obliga a que el parser de tramas de la ESP-A y de la ESP-B
        funcione con recepcion parcial. Sin trocear, las tramas llegarian enteras
        y alineadas, el camino de "a medio recibir" no se ejecutaria nunca, y el
        firmware pasaria la simulacion y fallaria en la placa.
        """
        datos = bytes(datos)
        self.bytes_tx += len(datos)
        if TROCEADO_UART:
            self.escritura.escribir_troceado(datos)
        else:
            self.escritura.escribir(datos)
        return len(datos)

    def deinit(self) -> None:
        self.lectura.cerrar()
        self.escritura.cerrar()

    def __repr__(self) -> str:
        return "UART(%d, %d baudios)" % (self.numero, self.baudios)


# -------------------------------------------------------------------- SPI ------

BUSES_SPI = {}


class SPI:
    """SPI. El registro por velocidad es lo que permite que las dos ESP, que se
    construyen por separado, encuentro el mismo bus.

    `MSB` y `LSB` existen para que el firmware pueda pasar `firstbit=SPI.MSB` como
    haria en la placa. En el bus virtual el orden de los bits no importa: los
    bytes viajan enteros, y el MSB es lo unico que usa el HD44780 y los dos
    ESP32.
    """

    MSB = 0
    LSB = 1

    def __init__(self, bus_id: int, baudios=1_000_000, polarity=0, phase=0,
                 bits=8, firstbit=None, sck=None, mosi=None, miso=None,
                 rxonly=False, pin_cs=None) -> None:
        self.bus_id = bus_id
        self.baudios = baudios
        self.polarity = polarity
        self.phase = phase
        self.rxonly = rxonly
        self.bits = bits

        bus = BUSES_SPI.get(baudios)
        if bus is None:
            bus = BusSPI(baudios)
            BUSES_SPI[baudios] = bus
        self.bus = bus

        # El CS lo aporta quien construye el SPI. Que se pase por aqui y no se
        # cree dentro es lo que permite que el hilo de la ESP-B se enganche a
        # los cambios del pin, que es como se entera de que empieza una
        # transaccion.
        cs = pin_cs if pin_cs is not None else Pin(5, Pin.OUT)

        if rxonly:
            # Modo esclava: hay que esperar a que la maestra escriba.
            from simulacion.bus_spi import SpiEsclavo
            self._puerto = SpiEsclavo(bus, cs)
        else:
            from simulacion.bus_spi import MaestroSPI
            self._puerto = MaestroSPI(bus, cs)

    def read(self, nbytes: int = 1) -> bytes:
        return self._puerto.read(nbytes)

    def readinto(self, buffer) -> int:
        return self._puerto.readinto(buffer)

    def write(self, datos) -> int:
        return self._puerto.write(bytes(datos))

    def write_readinto(self, salida, entrada=None) -> int:
        """Manda y lee a la vez, que es de lo que vive el SPI."""
        respuesta = self._puerto.write_read(bytes(salida),
                                            len(entrada) if entrada is not None else len(salida))
        if entrada is not None:
            for i, byte in enumerate(respuesta):
                entrada[i] = byte
        return len(salida)

    def deinit(self) -> None:
        pass


# -------------------------------------------------------------------- I2C ------

BUSES_I2C = {}
ESCLAVOS_I2C = {}


class I2C:
    """I2C. Igual que el SPI, se registra por indice de bus."""

    def __init__(self, bus_id: int = 0, sda=None, scl=None, freq=100_000,
                 pinos: dict = None) -> None:
        self.bus_id = bus_id
        self.freq = freq
        if pinos:
            sda = pinos.get("sda", sda)
            scl = pinos.get("scl", scl)
        self.sda = sda
        self.scl = scl

        bus = BUSES_I2C.get(bus_id)
        if bus is None:
            bus = BusI2C(velocidad=freq)
            BUSES_I2C[bus_id] = bus
            # El LCD y una EEPROM de prueba. Que haya dos direcciones distintas
            # es lo que permite comprobar que el maestro elige bien.
            bus.conectar(LcdEsclavo(0x27))
            bus.conectar(EepromEsclavo(0x57))
            ESCLAVOS_I2C[bus_id] = bus.esclavos
        self.bus = bus

    def scan(self) -> list:
        return sorted(self.bus.esclavos)

    def writeto(self, direccion: int, datos, stop: bool = True) -> int:
        if RETRASO_I2C and datos:
            _tiempo.sleep(RETRASO_I2C)
        return self.bus.escribir(direccion, datos)

    def readfrom(self, direccion: int, nbytes: int, stop: bool = True) -> bytes:
        return self.bus.leer(direccion, nbytes)

    def readfrom_into(self, direccion: int, buffer) -> int:
        datos = self.bus.leer(direccion, len(buffer))
        for i, byte in enumerate(datos):
            buffer[i] = byte
        return len(datos)

    def deinit(self) -> None:
        pass


# ------------------------------------------------------------- registros -------

def registrar_en_python() -> None:
    """Publica este modulo como `machine`. Se llama antes de importar el firmware."""
    sys.modules["machine"] = sys.modules[__name__]


def reiniciar() -> None:
    """Limpia los buses. Para tests que montan y desmontan la cadena."""
    BUSES_SPI.clear()
    BUSES_I2C.clear()
    ESCLAVOS_I2C.clear()

