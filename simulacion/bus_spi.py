"""
Bus SPI virtual entre la ESP-A (maestra) y la ESP-B (esclava).

POR QUE NO BASTA CON UNA COLA
-----------------------------
El SPI es full-duplex y ADEMAS tiene un pin de chip select. Estas dos cosas
importan para el firmware y por eso se modelan:

1. UnaTransferencia empieza cuando CS baja y termina cuando sube. Lo que haya
   en la cola del MOSI antes de bajar CS es basura de una transaccion anterior.
   Si el codigo no respeta CS, en hardware real la ESP-B pierde el primer byte
   de cada trama, porque la capturo en la transaccion previa.

2. El MISO lo lee la maestra en la MISMA transaccion en la que escribe, no
   despues. El modelo entrega lo que el esclavo haya puesto en su MISO al bajar
   CS, y el esclavo responde durante la transaccion SIGUIENTE. Por eso el acuse
   de la ESP-B tarda un fotograma en volver, y eso no es un fallo del simulador:
   es lo que hace el bus.

LA REGLA DE ORO DEL SIMULADOR
-----------------------------
Lo que se escribe aqui tiene que ser el mismo codigo que corre en la ESP. Si el
firmware usa `machine.SPI` y aqui se usa una API distinta, el simulador valida
una fiction. Por eso la clase se llama igual y expone la misma superficie
(`init`, `read`, `write`, `readinto`), y el codigo del bus se mete dentro del
firmware, no aqui.
"""

from __future__ import annotations

import contextlib
import threading
import time

# Indices de bit del byte de datos de una transaccion SPI
MOSI = 0
MISO = 1
SCK = 2
CS = 3
NOMBRES = ("MOSI", "MISO", "SCK", "CS")


class Transferencia:
    """Una comunicacion completa con la esclava.

    Se construye YA ABIERTA, porque en el simulador la abre el pin de CS al bajar
    y no hay un `abrir()` que alguien pueda llamar: el pin no sabe de
    transacciones, solo de niveles. La maestra la baja con `pin_cs(0)`, pide una
    con `en_transferencia()`, escribe, lee y sube con `pin_cs(1)`.
    """

    def __init__(self, bus, hacia_esclava: bytearray, desde_esclava: bytearray,
                 reloj) -> None:
        self.bus = bus
        self.hacia_esclava = hacia_esclava
        self.desde_esclava = desde_esclava
        self.reloj = reloj
        self.abierta = True
        self.terminada = False

    def escribir(self, datos: bytes) -> None:
        if self.terminada:
            raise RuntimeError("la transaccion ya se cerro")
        self.hacia_esclava.extend(datos)
        self.bus._registrar(MOSI, len(datos))

    def leer(self, cantidad: int) -> bytes:
        """Lo que la esclava devuelve. Los huecos salen como 0xFF, no como 0.

        El HD44780 y casi todos los dispositivos LCD leen 1 como bit de "no hay
        nada". Devolver 0 seria decir "hay tension en todas las lineas", que es
        una senal distinta y puede colgar el panel.
        """
        if self.terminada:
            raise RuntimeError("la transaccion ya se cerro")
        if len(self.desde_esclava) < cantidad:
            faltan = cantidad - len(self.desde_esclava)
            self.bus._registrar(MISO, faltan, relleno=0xFF)
            salida = bytes(self.desde_esclava) + bytes([0xFF]) * faltan
            del self.desde_esclava[:]
        else:
            self.bus._registrar(MISO, cantidad)
            salida = bytes(self.desde_esclava[:cantidad])
            del self.desde_esclava[:cantidad]
        return salida

    def cerrar(self) -> None:
        self.abierta = False
        self.terminada = True
        self.bus.cerrar_transaccion(self)


class SpiEsclavo:
    """Lo que ve el firmware de la ESP-B.

    `read` ESPERA hasta que llegan bytes. No es una decision de diseno: en la
    ESP32 el periferico SPI bloquea al esclavo si no hay nada en el buffer, y el
    firmware de la ESP-B cuenta con eso para no hacer sondeo activo del MISO. En
    la placa no existe `any()` para un esclavo, asi que no puede.
    """

    def __init__(self, bus: "BusSPI", pin_cs, al_registrar=None) -> None:
        self.bus = bus
        self.pin_cs = pin_cs
        self.al_registrar = al_registrar or (lambda texto: None)
        # Se engancha al pin de CS. El pin es quien avisa de que empieza una
        # transaccion, y no al reves: en la placa el CS lo lleva el pin y no hay
        # nadie a quien preguntar.
        pin_cs.on(self.al_cambiar_cs)

    def al_cambiar_cs(self, valor: int) -> None:
        """Callback del pin de CS. Lo expone con nombre publico porque el
        orquestador lo necesita para enganchar las dos ESP al mismo pin."""
        self.bus.registrar("CS de la ESP-B = %s"
                           % ("LOW (transaccion)" if not valor else "HIGH"))
        if not valor:
            # CS baja: despierta a quien este esperando bytes.
            self.bus._despertar()

    def esperar_y_leer(self, cantidad: int, timeout: float = 5.0) -> bytes:
        """Bloquea hasta que la maestra mande `cantidad` bytes.

        La espera se hace con la condicion del bus y con un tiempo maximo, en vez
        de con un bucle de `sleep`. Con un bucle alto la ESP-B esta al 100% de
        la CPU mientras espera, y en una maquina donde ademas corre el
        reconocimiento, eso hace que la simulacion vaya mas lenta que la placa.
        Con la condicion, el hilo se duerme hasta que la maestra escribe.

        El tiempo maximo no es cosmetico: si el cable se queda colgado, el hilo
        tiene que poder salir igual. Sin el, la simulacion se queda pegada
        esperando y no hay forma de parar con Ctrl-C.
        """
        limite = time.time() + timeout
        with self.bus.condicion:
            self.bus.consumidor = self
            self.bus.condicion.notify_all()
            while (len(self.bus.entrada_esclava) < cantidad
                   and time.time() < limite
                   and self.bus.sigue_vivo):
                self.bus.condicion.wait(0.02)

            datos = bytes(self.bus.entrada_esclava[:cantidad])
            del self.bus.entrada_esclava[:cantidad]
            self.bus.consumidor = None
            self.bus.condicion.notify_all()
        return datos

    def responder(self, datos: bytes) -> None:
        """Pone bytes en el MISO. Salen en la siguiente transaccion.

        El acuse NO se manda durante la transaccion en curso, y no por decision
        del simulador: es la naturaleza del protocolo. El SPI es full-duplex,
        pero cuando la ESP-A ha terminado de escribir su trama y sube CS, ya no
        hay a quien mandarle el MISO. Lo unico que puede hacer es bajar CS otra
        vez para leer. Por eso el acuse se queda en el bus esperando a la
        siguiente transaccion, y por eso la ESP-B tarda un ciclo en responder.
        """
        with self.bus.condicion:
            self.bus.salida_esclava.extend(datos)
            self.bus.condicion.notify_all()
        self.bus.registrar("la ESP-B responde %d bytes por MISO" % len(datos))
        return len(datos)

    # ------------------------------------------------------ API tipo machine --

    def read(self, n: int = 1) -> bytes:
        """Equivale a `spi.read(n)` de MicroPython en modo RXONLY."""
        return self.esperar_y_leer(n)

    def write(self, datos: bytes) -> int:
        """La ESP-B no escribe por SPI: responde por el MISO, que en la placa es
        el mismo bus. Se mantiene por completitud de la API."""
        self.responder(datos)
        return len(datos)

    # El orquestador y las pruebas necesitan escribir a mano en el bus para
    # comprobar el camino sin pasar por la maestra. Se llama `escribir` y no
    # `write` para que no se confunda con la API de `machine`.

    def escribir(self, datos: bytes) -> int:
        """Escribe al MISO. Es lo que usa la ESP-B para mandar el acuse.

        OJO CON LA DIRECCION: esto va a `salida_esclava` (lo que lee la maestra),
        NO a `entrada_esclava` (lo que la maestra manda). Ponerlo al reves hace
        que la ESP-A lea relleno infinito y que el acuse no llegue nunca, sin
        ningun error visible.
        """
        return self.write(datos)

    def readinto(self, buffer) -> int:
        datos = self.read(len(buffer))
        for i, byte in enumerate(datos):
            buffer[i] = byte
        return len(datos)


class MaestroSPI:
    """Lo que ve el firmware de la ESP-A."""

    def __init__(self, bus: "BusSPI", pin_cs, al_registrar=None) -> None:
        self.bus = bus
        self.pin_cs = pin_cs
        self.al_registrar = al_registrar or (lambda texto: None)

    @contextlib.contextmanager
    def _transaccion(self):
        """CS abajo, cuerpo, CS arriba. Contexto: todo lo que pase en medio.

        Se baja CS antes de abrir la transaccion del bus, igual que en la
        ESP32: el pin y el periferico SPI son cosas distintas y el hardware no
        espera a que se le avise.
        """
        self.pin_cs(0)
        transaccion = self.bus.en_transferencia()
        try:
            yield transaccion
        finally:
            self.bus.cerrar_transaccion(transaccion)
            self.pin_cs(1)

    def write(self, datos: bytes) -> int:
        with self._transaccion() as t:
            t.escribir(datos)
        return len(datos)

    def write_read(self, saliente: bytes, longitud: int = None):
        longitud = longitud if longitud is not None else len(saliente)
        with self._transaccion() as t:
            t.escribir(saliente)
            return t.leer(longitud)

    def read(self, n: int) -> bytes:
        with self._transaccion() as t:
            return t.leer(n)

    def readinto(self, buffer) -> int:
        datos = self.read(len(buffer))
        for i, byte in enumerate(datos):
            buffer[i] = byte
        return len(datos)

    def write_readinto(self, saliente, entrada=None) -> int:
        """La API de `machine.SPI`. Es la que usa el firmware a traves de
        `hardware.SpiMaestro`, asi que es la que de verdad importa: si esto
        funciona, el firmware que corre aqui es el de la placa."""
        respuesta = self.write_read(
            bytes(saliente), len(entrada) if entrada is not None else len(saliente))
        if entrada is not None:
            for i, byte in enumerate(respuesta):
                entrada[i] = byte
        return len(saliente)

    def deinit(self) -> None:
        self.pin_cs(1)


class BusSPI:
    """El bus compartido. Un unico objeto, dos vistas."""

    def __init__(self, velocidad: int = 1_000_000, al_registrar=None) -> None:
        self.velocidad = velocidad
        self.al_registrar = al_registrar or (lambda texto: None)
        self.condicion = threading.Condition()
        self.velocidad_real = velocidad

        self.entrada_esclava = bytearray()      # lo que escribio la maestra
        self.salida_esclava = bytearray()       # lo que respondio la esclava

        self.transacciones = 0
        self.bytes_mosi = 0
        self.bytes_miso = 0
        self.sigue_vivo = True
        self.consumidor = None      # la esclava que esta esperando bytes

    # ------------------------------------------------------------- registro ---

    def registrar(self, texto: str) -> None:
        self.al_registrar(texto)

    def _registrar(self, senal: int, cantidad: int, relleno: int = 0) -> None:
        if senal == MOSI:
            self.bytes_mosi += cantidad
        else:
            self.bytes_miso += cantidad
        self.al_registrar("SPI %s %d byte(s)%s"
                          % (NOMBRES[senal], cantidad,
                             " (relleno 0xFF)" if relleno else ""))

    # ------------------------------------------------------- sincronizacion ---

    def _despertar(self) -> None:
        with self.condicion:
            self.condicion.notify_all()

    # -------------------------------------------------------- transacciones ---

    def en_transferencia(self):
        """Abre una transaccion y la devuelve. Se cierra con `cerrar_transaccion`.

        La parte del MISO se queda en una COPIA y se saca del bus de una vez. Es
        lo que representa el hardware: los bytes que la esclava ofrece se leen
        UNO POR TRANSACCION. Si se dejaran en la cola, la siguiente transaccion
        de la ESP-A, que solo va a enviar un digito, se llevaria el acuse
        entero como si fuera relleno, y se perderia.
        """
        with self.condicion:
            hacia = bytearray()
            desde = bytes(self.salida_esclava)   # lo que quedo de la anterior
            del self.salida_esclava[:]
            self.transacciones += 1
            return Transferencia(self, hacia, bytearray(desde), self.condicion)

    def cerrar_transaccion(self, t: Transferencia) -> None:
        with self.condicion:
            self.entrada_esclava.extend(t.hacia_esclava)
            self.condicion.notify_all()

    def retardo_transaccion(self, nbytes: int) -> float:
        """Cuanto tardo una transferencia de `nbytes` en el cable de verdad.

        Un byte son 8 pulsos de reloj, y entre bytes hay al menos un turno de CS.
        A 1 MHz son 8 microsegundos por byte, asi que una trama de 16 bytes son
        128 microsegundos: en la simulacion no se nota, y ese es el punto. Si se
        notara, habria que=subir la velocidad del bus, porque un LCD no pide
        refresco de 20 Hz.
        """
        if not self.velocidad_real:
            return 0.0
        return (nbytes * 8.0) / float(self.velocidad_real)

    # ---------------------------------------------------------------- informe --

    def informe(self) -> str:
        media = (self.velocidad / 8.0) if self.velocidad else 0
        return ("SPI: %d transaccion(es), %d bytes MOSI, %d bytes MISO, "
                "%.0f kB/s teoricos por byte" % (self.transacciones, self.bytes_mosi,
                                                 self.bytes_miso, media / 1000.0))

    def parar(self) -> None:
        with self.condicion:
            self.sigue_vivo = False
            self.condicion.notify_all()
