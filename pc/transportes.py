

from __future__ import annotations

import random
import time


class Tuberia:
   

    def __init__(self, nombre: str = "tuberia", troceado: tuple = (1, 4)) -> None:
        self.nombre = nombre
        self.cola = bytearray()
        self.bytes_totales = 0
        self.escrituras = 0
        self._cerrada = False
        self.troceado = troceado
     
        self.rng = random.Random(hash(nombre) & 0xFFFF)

 

    def escribir(self, datos) -> int:
        if self._cerrada:
            raise RuntimeError("la tuberia %s esta cerrada" % self.nombre)
        self.cola.extend(datos)
        self.bytes_totales += len(datos)
        self.escrituras += 1
        return len(datos)

    def escribir_troceado(self, datos, espera=0.0) -> int:
        """Escribe en trozos, como el hardware. Lo usa el simulador."""
        if not self.troceado:
            return self.escribir(datos)
        total = 0
        pendientes = bytes(datos)
        while pendientes:
            n = self.rng.randint(self.troceado[0], self.troceado[1])
            trozo = pendientes[:n]
            pendientes = pendientes[n:]
            total += self.escribir(trozo)
            if espera:
                time.sleep(espera)
        return total



    def hay_datos(self) -> bool:
        return bool(self.cola)

    def leer(self, cantidad: int, timeout: float = 0.05) -> bytes:
       


        limite = time.time() + max(timeout, 0.0)
        while not self.cola and time.time() < limite and not self._cerrada:
            time.sleep(0.0005)
        if not self.cola:
            return b""
        n = min(cantidad, len(self.cola))
        salida = bytes(self.cola[:n])
        del self.cola[:n]
        return salida

    def cerrar(self) -> None:
        self._cerrada = True

    @property
    def cerrada(self) -> bool:
        return self._cerrada

    def __len__(self) -> int:
        return len(self.cola)


def listar_puertos() -> list:
    
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return ["%s  %s" % (p.device, p.description) for p in list_ports.comports()]


class Serial:
   

    def __init__(self, puerto: str, baudios: int = 115200,
                 timeout: float = 0.05, reintentos: int = 3) -> None:
        self.puerto = puerto
        self.baudios = baudios
        self.timeout = timeout
        self.reintentos = reintentos
        self.bytes_totales = 0
        self.ultimo_error = ""
        self._puerto = None
        self._connect()

    def _connect(self) -> bool:
        import serial                     

        ultimo = "no se pudo abrir"
        for intento in range(max(1, self.reintentos)):
            try:
                self._puerto = serial.Serial(
                    self.puerto, self.baudios, timeout=self.timeout)
            
                self._puerto.reset_input_buffer()
                self.ultimo_error = ""
                return True
            except Exception as exc:               
                ultimo = "%s: %s" % (type(exc).__name__, exc)
                time.sleep(0.3 * (intento + 1))
        self.ultimo_error = ultimo
        return False

    @property
    def conectado(self) -> bool:
        return self._puerto is not None and self._puerto.is_open

    def escribir(self, datos) -> int:
        if not self.conectado:
            self._connect()
        if not self.conectado:
            return 0
        n = self._puerto.write(datos)
        self.bytes_totales += n or 0
        return n or 0

    def hay_datos(self) -> bool:
        return bool(self.conectado and self._puerto.in_waiting)

    def leer(self, cantidad: int, timeout: float = 0.05) -> bytes:
        if not self.conectado:
            return b""
        self._puerto.timeout = max(timeout, 0.01)
        return self._puerto.read(cantidad) or b""

    def cerrar(self) -> None:
        if self._puerto is not None:
            try:
                self._puerto.close()
            except Exception:                      
                pass
        self._puerto = None

    def puertos_disponibles(self) -> list:
        return listar_puertos()
