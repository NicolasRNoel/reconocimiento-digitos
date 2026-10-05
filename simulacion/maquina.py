

from __future__ import annotations

import sys
import time as _tiempo

from simulacion.bus_i2c import BusI2C, EepromEsclavo, LcdEsclavo
from simulacion.bus_spi import BusSPI
from pc import transportes


RETRASO_UART = 0.0        
RETRASO_SPI = 0.0         
RETRASO_I2C = 0.0         


TROCEADO_UART = (1, 4)




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




BUSES_SPI = {}


class SPI:
    

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



def registrar_en_python() -> None:
    """Publica este modulo como `machine`. Se llama antes de importar el firmware."""
    sys.modules["machine"] = sys.modules[__name__]


def reiniciar() -> None:
    """Limpia los buses. Para tests que montan y desmontan la cadena."""
    BUSES_SPI.clear()
    BUSES_I2C.clear()
    ESCLAVOS_I2C.clear()

