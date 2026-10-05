"""
Capa de hardware de las ESP. MicroPython.

UN ARCHIVO, TRES AMBIENTES
-------------------------
Este modulo es la UNICA pieza que cambia entre la ESP32 real y el simulador.
`main.py` de la ESP-A y de la ESP-B no saben en que estan: piden un `Uart`, un
`SpiMaestro` y un `Led` y aqui se decide de donde salen.

    ESP32 real   ->  `from machine import UART, SPI, Pin`
    simulador    ->  `from simulacion.maquina import UART, SPI, Pin`

La clase de cada periférico tiene los mismos metodos con el mismo nombre. Si
algun dia el simulador se queda corto, el arreglo se hace aqui y no en el
firmware, que es justo lo que se quiere.

POR QUE NO SE USA `machine` DIRECTAMENTE EN EL FIRMWARE
--------------------------------------------------------
Porque `machine` no existe fuera de la placa, y entonces el firmware solo se
puede probar en la placa. Con esta capa, el `main.py` de cada ESP se importa y
se ejecuta en el PC de escritorio sin tocar una linea, que es lo que permite
que el simulador use el firmware real y no una copia.
"""

# En la ESP32 existe `machine`. En el PC, el simulador lo registra en
# `sys.modules` ANTES de importar este archivo (ver `simulacion/maquina.py`).
#
# Por eso este modulo no decide nada: importa `machine` como si fuera la placa,
# y en los dos casos lo que hay dentro implementa lo mismo. Si aqui hubiera un
# `if HAY_MACHINE`, existiria una rama que solo se ejecuta en un lado, y un error
# en ella no apareceria hasta que se montase el hardware real.
try:
    import machine                        # noqa: F401
except ImportError:
    raise ImportError(
        "no se encuentra el modulo 'machine'. En la ESP32 viene con el firmware; "
        "en el PC lo registra simulacion.maquina.registrar_en_python(), que se "
        "llama desde simulacion/simular.py antes de importar nada del firmware.")


# ---------------------------------------------------------------------- LED ---

class Pin:
    """Un pin GPIO.

    Envuelve `machine.Pin` y anade dos cosas:

    1. Se puede LLAMAR: `cs(0)` y `cs(1)`. `machine.Pin` tambien lo permite en la
       ESP32, asi que el firmware usa esa forma en los dos sitios.

    2. Avisa de los cambios a un observador. Eso es lo que permite que la ESP-B se
       entere de que empieza una transaccion de SPI. Lo hace el pin y no el
       firmware, porque en la placa el CS es un pin y no hay nadie a quien
       preguntar.

    La envoltura es IGUAL en el PC y en la placa. Por eso la ESP-B no tiene dos
    versiones de su codigo segun donde corra, y por eso el simulador puede
    ejercitar el firmware de verdad.
    """

    def __init__(self, numero: int, salida: bool, valor: int = 0,
                 al_cambiar=None) -> None:
        from machine import Pin as PinMaquina

        self.numero = numero
        self.salida = salida
        self.valor = int(valor)
        self.al_cambiar = al_cambiar

        self._pin = PinMaquina(numero, PinMaquina.OUT if salida else PinMaquina.IN)
        self._pin.value(self.valor)

    def _fijar(self, valor: int) -> None:
        self.valor = int(valor)
        self._pin.value(self.valor)
        if self.al_cambiar is not None:
            self.al_cambiar(self.valor)

    def __call__(self, valor=None):
        """`cs(0)` pone el pin a 0. Sin argumento, devuelve el valor."""
        if valor is None:
            return self.valor
        nuevo = int(valor)
        if nuevo != self.valor:
            self._fijar(nuevo)
        return None

    def valor_actual(self) -> int:
        return self.valor

    def on(self, oyente) -> None:
        """Registra un callback para los cambios. Lo usa la ESP-B con el CS."""
        self.al_cambiar = oyente

    def __repr__(self) -> str:
        return "GPIO %d = %d (%s)" % (self.numero, self.valor,
                                      "salida" if self.salida else "entrada")


class PinESPSalida(Pin):
    """Un pin de salida. El chip select de la ESP-A y el LED."""

    def __init__(self, numero: int, valor: int = 0, al_cambiar=None) -> None:
        super().__init__(numero, salida=True, valor=valor, al_cambiar=al_cambiar)


class PinESPEntrada(Pin):
    """Un pin de entrada. El chip select de la ESP-B, que no lo controla ella."""

    def __init__(self, numero: int, valor: int = 1, al_cambiar=None) -> None:
        # El CS de la ESP-B arranca en ALTO: la esclava esta desconectada hasta
        # que la ESP-A lo baja. Si arrancara en bajo, la ESP-B creeria que hay
        # una transaccion en curso antes de que llegue la primera.
        super().__init__(numero, salida=False, valor=valor, al_cambiar=al_cambiar)


class Led:
    """Un pin con parpadeo por tiempo, no por retardo.

    `parpadeo(ms)` apaga el LED, espera la mitad, lo enciende y espera la otra
    mitad. Es BLOQUEANTE a proposito: en la ESP-B, el LED marca cada digito
    recibido, y si el parpadeo no bloqueara el bucle se acumularian las marcas y
    el LED perderia la funcion de indicar actividad.

    En el simulador el pin es real y tambien parpadea, pero lo que se ve en el log
    es el texto que escribe el firmware, no la luz.
    """

    def __init__(self, pin: int, activo_alto: bool = True, al_registrar=None) -> None:
        self.pin = PinESPSalida(pin, 1 if activo_alto else 0)
        self.activo_alto = activo_alto
        self.al_registrar = al_registrar
        self.encendido = False
        self.parpadeos = 0

    def _nivel(self, valor: bool) -> int:
        return 1 if (valor == self.activo_alto) else 0

    def encender(self) -> None:
        self.pin(self._nivel(True))
        self.encendido = True

    def apagar(self) -> None:
        self.pin(self._nivel(False))
        self.encendido = False

    def parpadeo(self, ms: int) -> None:
        import time
        medio = max(1, int(ms / 2))
        self.apagar()
        time.sleep_ms(medio)
        self.encender()
        time.sleep_ms(medio)
        self.parpadeos += 1

    def estado(self) -> str:
        return "encendido" if self.encendido else "apagado"


# ---------------------------------------------------------------------- UART ---

class Uart:
    """El puerto serie. Envuelve `machine.UART` con la misma API en los dos lados.

    El buffer de recepcion se pone a 1024 bytes, el maximo del ESP32. Con el
    valor por defecto (256) una rafaga de ocho tramas de 18 bytes lo llenaria y
    las siguientes se perderian: en la placa eso aparece como digitos que no
    llegan al LCD, sin ningun error en ningun lado.
    """

    def __init__(self, numero: int = 1, baudios: int = 115200, timeout_ms: int = 20,
                 bits: int = 8, paridad=None, parada: int = 1) -> None:
        from machine import UART

        self._uart = UART(numero, baudios=baudios, bits=bits, parity=paridad,
                          stop=parada, timeout=timeout_ms, rxbuf=1024)
        self.baudios = baudios
        self.bytes_rx = 0
        self.bytes_tx = 0

    def leer(self) -> bytes:
        """Lo que haya disponible. Puede ser vacio; no se queda esperando.

        En un UART de verdad no hay forma de "leer hasta el final de una trama",
        asi que el firmware tiene que tolerar reception parcial. La simulacion
        trocea las escrituras a proposito, en bloques de 1 a 4 bytes, para que
        ese codigo se ejercite siempre.
        """
        datos = self._uart.read() or b""
        self.bytes_rx += len(datos)
        return datos

    def escribir(self, datos: bytes) -> int:
        n = self._uart.write(datos) or len(datos)
        self.bytes_tx += n
        return n

    def disponible(self) -> bool:
        """Hay algo pendiente. Solo informativo: el bucle usa `leer`."""
        try:
            return bool(self._uart.any())
        except AttributeError:
            return True

    def desconectar(self) -> None:
        self._uart.deinit()


# ----------------------------------------------------------------------- SPI ---

class SpiMaestro:
    """SPI en modo maestro. Lo usa la ESP-A para hablar con la ESP-B.

    NO HAY NINGUN `if` DENTRO. Se construye siempre con `machine.SPI`, y lo que
    cambia es lo que hay detras: el SPI de verdad en la placa, el bus virtual en
    el PC. La razon es que una rama `if HAY_MACHINE` aqui seria una rama que
    solo se ejecuta en un lado, y cualquier error en ella no aparece hasta que
    se monta la placa.
    """

    def __init__(self, baudios: int = 1_000_000, modo: int = 1,
                 pinos: dict = None, pin_cs: int = None) -> None:
        from machine import SPI
        pinos = pinos or {"sck": 18, "miso": 19, "mosi": 23}
        self.baudios = baudios
        self.modo = modo
        self._spi = SPI(1, baudios=baudios, polarity=polaridad(modo),
                        phase=fase(modo), bits=8, firstbit=SPI.MSB,
                        sck=pinos["sck"], mosi=pinos["mosi"], miso=pinos["miso"])

    def escribir(self, datos: bytes) -> int:
        return self._spi.write(datos)

    def leer(self, n: int) -> bytes:
        return self._spi.read(n)

    def escribir_y_leer(self, saliente: bytes, longitud: int = None):
        """Manda y lee en la misma transaccion. Es el caso normal del SPI."""
        longitud = longitud if longitud is not None else len(saliente)
        buffer = bytearray(longitud)
        self._spi.write_readinto(saliente, buffer)
        return bytes(buffer)

    def velocidad(self) -> int:
        return self.baudios


class SpiEsclavo:
    """SPI en modo esclava. Lo usa la ESP-B.

    `leer()` es BLOQUEANTE y sin sondeo: en la ESP32 el periferico en modo esclava
    no tiene forma de decir "no hay nada" sin que la maestra baje CS. El firmware
    depende de ese comportamiento, y el simulador lo reproduce bloqueando el
    hilo hasta que llegan bytes.
    """

    def __init__(self, baudios: int = 1_000_000, modo: int = 1, pinos: dict = None,
                 pin_cs=None) -> None:
        from machine import SPI
        pinos = pinos or {"sck": 18, "miso": 19, "mosi": 23}
        self.baudios = baudios
        self.modo = modo
        self._spi = SPI(2, baudios=baudios, polarity=polaridad(modo),
                        phase=fase(modo), bits=8, firstbit=SPI.MSB,
                        sck=pinos["sck"], mosi=pinos["mosi"], miso=pinos["miso"],
                        # Solo recepcion: la ESP-B responde por el MISO en la
                        # SIGUIENTE transaccion, no durante esta.
                        rxonly=True, pin_cs=pin_cs)

        # Que avise cuando baja el CS. En la placa el periferico ya lo sabe, pero
        # el simulador necesita que se lo digan para despertar al hilo esclavo,
        # y el firmware no debe enterarse de esa diferencia.
        self._cs_cambia = getattr(self._spi, "_puerto", self._spi)
        if hasattr(self._cs_cambia, "al_cambiar_cs"):
            self._cs_cambia = self._cs_cambia.al_cambiar_cs

    def leer(self, n: int = 1) -> bytes:
        """Bloquea hasta que lleguen `n` bytes.

        No hay forma de pedir "lo que haya": en modo esclava no existe `any()`, y
        la razon es el protocolo. El CS lo baja la maestra; si la esclava pudiera
        decir "no tengo nada" tendria que bajar CS para hacerlo, y entonces la
        maestra se quedaria colgando dentro de su transaccion.
        """
        return self._spi.read(n)

    def escribir(self, datos: bytes) -> int:
        """Pone bytes en el MISO. Por aqui contesta la ESP-B.

        En la ESP32 un esclavo SPI puede escribir, pero solo si el pin MISO tiene
        la salida habilitada, y eso hay que pedirlo explicitamente al periferico.
        Sin ese paso el pin se queda en entrada y el byte no sale nunca.
        """
        return self._spi.write(datos)


def polaridad(modo: int) -> int:
    """Modo de MicroPython (0-3) -> `polarity` de `machine.SPI`.

    El bit alto del modo es CPOL. MicroPython lo empaqueta en un solo numero y
    `machine.SPI` lo quiere en dos argumentos, asi que hay que separarlo.
    """
    return 1 if modo in (2, 3) else 0


def fase(modo: int) -> int:
    """Modo de MicroPython (0-3) -> `phase` de `machine.SPI`. Es el bit bajo."""
    return 1 if modo in (1, 3) else 0


# ----------------------------------------------------------------------- I2C ---

class I2cEsclavo:
    """Maestro I2C de la ESP-B hacia el LCD. Se llama "esclavo" por el otro lado.

    Igual que el SPI, sin `if`: se construye siempre con `machine.I2C` y lo que
    cambia es el bus que hay detras.
    """

    def __init__(self, pinos: dict = None, frecuencia: int = 400_000) -> None:
        from machine import I2C

        pinos = pinos or {"sda": 21, "scl": 22}
        self.frecuencia = frecuencia
        self._i2c = I2C(0, freq=frecuencia, pinos=pinos)

        self.transacciones = 0
        self.bytes_enviados = 0

    def escribir(self, direccion: int, datos) -> int:
        self.transacciones += 1
        self.bytes_enviados += len(datos)
        return self._i2c.writeto(direccion, datos)

    def leer(self, direccion: int, cantidad: int) -> bytes:
        self.transacciones += 1
        return self._i2c.readfrom(direccion, cantidad)

    def detectar(self, direccion: int, intentos: int = 3) -> bool:
        """Existe algo en esa direccion?

        Se hace una escritura de un solo byte nulo. En un LCD eso NO es un
        comando: el HD44780 ignores los bytes con RS=0 durante la fase de
        inicializacion, y los cuatro jumperes del backpack estan en 0x27. Asi
        que el sondeo no daña nada y sirve para saber si el cable esta bien.
        """
        import time
        for intento in range(intentos):
            try:
                self._i2c.writeto(direccion, b"\x00")
                return True
            except OSError:
                time.sleep_ms(10)
        return False
