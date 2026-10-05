"""
Transportes hacia la ESP-A.

DOS FORMAS DE ENVIAR, UNA SOLA INTERFAZ
---------------------------------------
`Serial` habla con pyserial contra un puerto real. `Tuberia` guarda los bytes
en memoria y los entrega a quien este al otro lado; la usa el simulador para
meter la ESP-A en el mismo proceso.

Ambas exponen `escribir(datos)` y `leer(cantidad, timeout)`, que es
exactamente lo que necesita el bucle de la ESP-A. Asi el firmware no sabe si
detras hay un cable USB o un `threading.Queue` de Python, y el mismo
`firmware/esp_a/main.py` sirve para las dos cosas.
"""

from __future__ import annotations

import random
import time


class Tuberia:
    """FIFO de bytes con tiempos de espera, para el simulador en un proceso.

    DOS COSAS QUE NO SON COSMÉTICAS

    1. `leer` ESPERA. Si no lo hiciera, la ESP-A se pasaria el 99% del tiempo
       leyendo cero bytes de una cola vacia, y la simulacion no representaria
       nada del retardo de un UART real.

    2. `escribir` TROCEA. Una escritura de 15 bytes se parte en trozos de 1 a 4,
       como hace un conversor USB-TTL. Sin esto el parser de tramas recibiria
       siempre tramas enteras y alineadas, no se probaria nunca el camino de
       "llegan a medias", y en la placa, donde si llega asi, dejaria de
       funcionar. Es el detalle mas importante de todo el simulador.
    """

    def __init__(self, nombre: str = "tuberia", troceado: tuple = (1, 4)) -> None:
        self.nombre = nombre
        self.cola = bytearray()
        self.bytes_totales = 0
        self.escrituras = 0
        self._cerrada = False
        self.troceado = troceado
        # El generador se fija por tuberia, no global, para que el troceo sea
        # reproducible entre ejecuciones y los tests salgan siempre igual.
        self.rng = random.Random(hash(nombre) & 0xFFFF)

    # ------------------------------------------------------------- escritura --

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

    # -------------------------------------------------------------- lectura ---

    def hay_datos(self) -> bool:
        return bool(self.cola)

    def leer(self, cantidad: int, timeout: float = 0.05) -> bytes:
        """Devuelve hasta `cantidad` bytes, o vacio si no hay.

        Sale en cuanto hay algo, sin esperar a llenar el buffer. Es lo que hace
        un UART de verdad y lo que evita que la ESP-A espere 50 ms por un byte
        que ya llego hace 1 ms.
        """
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
    """Los puertos serie presentes, como texto listo para leer.

    Se usa desde la CLI (`--listar`), que todavia no tiene un puerto abierto, y
    desde el mensaje de error cuando no se pudo conectar. Por eso es una funcion
    suelta y no un metodo de `Serial`: en la CLI no hay ninguna instancia todavia.

    Si pyserial no esta instalado devuelve una lista vacia en vez de fallar: el
    simulador no lo necesita y no debe romperse por eso.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return ["%s  %s" % (p.device, p.description) for p in list_ports.comports()]


class Serial:
    """pyserial con los reintentos de apertura y de escritura.

    La ESP32 se reinicia sola cuando se le corta la alimentacion, y algunos
    conversores USB-TTL enumeran el puerto un par de veces durante ese arranque.
    Sin reintentos la aplicacion tendria que se relanzar a mano, y en una
    instalacion donde el ESP lo controla otro proceso eso no es opcion.
    """

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
        import serial                     # se importa aqui: solo hace falta con puerto real

        ultimo = "no se pudo abrir"
        for intento in range(max(1, self.reintentos)):
            try:
                self._puerto = serial.Serial(
                    self.puerto, self.baudios, timeout=self.timeout)
                # El ESP32 se reinicia al abrir el puerto y manda un byte de
                # arranque. Se descarta para que no se cuele en la primera trama.
                self._puerto.reset_input_buffer()
                self.ultimo_error = ""
                return True
            except Exception as exc:                # noqa: BLE001
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
            except Exception:                       # noqa: BLE001
                pass
        self._puerto = None

    def puertos_disponibles(self) -> list:
        """Los puertos serie que hay ahora mismo. Ayuda a encontrar el COM."""
        return listar_puertos()
