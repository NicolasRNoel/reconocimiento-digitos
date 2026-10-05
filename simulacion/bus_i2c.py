

from __future__ import annotations

import threading
import time

from simulacion.lcd import LcdVirtual


class EsclavoI2C:
    """Lo que hay en una direccion del bus."""

    def __init__(self, direccion: int, nombre: str) -> None:
        self.direccion = direccion
        self.nombre = nombre
        self.escrituras = 0
        self.lecturas = 0
        self.ultimo = b""

    def escribir(self, datos: bytes) -> int:
        self.escrituras += 1
        self.ultimo = bytes(datos)
        return len(datos)

    def leer(self, cantidad: int) -> bytes:
        self.lecturas += 1
        return bytes(cantidad)

    def informe(self) -> str:
        return "%s en 0x%02X: %d escrituras, %d lecturas" % (
            self.nombre, self.direccion, self.escrituras, self.lecturas)


class LcdEsclavo(EsclavoI2C):
   

    def __init__(self, direccion: int, columnas: int = 16, filas: int = 2,
                 al_registrar=None) -> None:
        super().__init__(direccion, "LCD")
        self.panel = LcdVirtual(columnas, filas, al_registrar=al_registrar)

    def escribir(self, datos: bytes) -> int:
        super().escribir(datos)
        for byte in datos:
            self.panel.escribir_byte_i2c(byte)
        return len(datos)


class EepromEsclavo(EsclavoI2C):
  

    TAMANO = 4096

    def __init__(self, direccion: int) -> None:
        super().__init__(direccion, "EEPROM 24C32")
        self.memoria = bytearray(self.TAMANO)
        self.puntero = 0

    def escribir(self, datos: bytes) -> int:
        super().escribir(datos)
        if len(datos) == 1:
          
            self.puntero = datos[0] * 16
        else:
            for byte in datos:
                if 0 <= self.puntero < self.TAMANO:
                    self.memoria[self.puntero] = byte
                self.puntero = (self.puntero + 1) % self.TAMANO
        return len(datos)

    def leer(self, cantidad: int) -> bytes:
        super().leer(cantidad)
        salida = bytes(self.memoria[self.puntero:self.puntero + cantidad])
        self.puntero = (self.puntero + cantidad) % self.TAMANO
        return salida + bytes(max(0, cantidad - len(salida)))

    def mostrar(self, posiciones: int = 16) -> str:
        return self.memoria[:posiciones].hex()


class BusI2C:


    def __init__(self, al_registrar=None, velocidad: int = 400_000) -> None:
        self.al_registrar = al_registrar or (lambda texto: None)
        self.velocidad = velocidad
        self.esclavos = {}
        self.transacciones = 0
        self.bytes = 0
        self.sigue_vivo = True

    def registrar(self, texto: str) -> None:
        self.al_registrar(texto)

    def conectar(self, esclavo: EsclavoI2C) -> None:
        self.esclavos[esclavo.direccion] = esclavo

    def desconectar(self, direccion: int) -> None:
        self.esclavos.pop(direccion, None)

    # ------------------------------------------------------------- maestro ----

    def escribir(self, direccion: int, datos) -> int:
        """`i2c.writeto(direccion, datos)`.

        Lanza OSError si no hay nadie en esa direccion, que es lo que hace
        `machine.I2C` y lo que el firmware comprueba con `detectar`.
        """
        self.transacciones += 1
        datos = bytes(datos)
        self.bytes += len(datos)
        esclavo = self.esclavos.get(direccion)
        if esclavo is None:
            raise OSError(5, "no ACK de 0x%02X" % direccion)
        self.registrar("I2C 0x%02X <- %s" % (direccion, datos.hex(" ")))
        return esclavo.escribir(datos)

    def leer(self, direccion: int, cantidad: int) -> bytes:
        self.transacciones += 1
        self.bytes += cantidad
        esclavo = self.esclavos.get(direccion)
        if esclavo is None:
            raise OSError(5, "no ACK de 0x%02X" % direccion)
        salida = esclavo.leer(cantidad)
        self.registrar("I2C 0x%02X -> %s" % (direccion, salida.hex(" ")))
        return salida

    # -------------------------------------------------------------- informes --

    def informe(self) -> str:
        lineas = ["I2C: %d transacciones, %d bytes a %d Hz"
                  % (self.transacciones, self.bytes, self.velocidad)]
        for direccion in sorted(self.esclavos):
            lineas.append("  " + self.esclavos[direccion].informe())
        return "\n".join(lineas)

    def parar(self) -> None:
        self.sigue_vivo = False


class PinI2C:
    """El pin que ve el maestro. Solo para que el constructor tenga su firma."""

    def __init__(self, numero: int, nombre: str = "") -> None:
        self.numero = numero
        self.nombre = nombre or ("SDA" if numero == 21 else "SCL")

    def value(self, valor=None):
        return 1

    def __call__(self, valor=None):
        return 1
