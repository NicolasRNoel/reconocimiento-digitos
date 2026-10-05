
try:
    import machine                        # noqa: F401
except ImportError:
    raise ImportError(
        "no se encuentra el modulo 'machine'. En la ESP32 viene con el firmware; "
        "en el PC lo registra simulacion.maquina.registrar_en_python(), que se "
        "llama desde simulacion/simular.py antes de importar nada del firmware.")


# ---------------------------------------------------------------------- LED ---

class Pin:
  
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
    

    def __init__(self, numero: int = 1, baudios: int = 115200, timeout_ms: int = 20,
                 bits: int = 8, paridad=None, parada: int = 1) -> None:
        from machine import UART

        self._uart = UART(numero, baudios=baudios, bits=bits, parity=paridad,
                          stop=parada, timeout=timeout_ms, rxbuf=1024)
        self.baudios = baudios
        self.bytes_rx = 0
        self.bytes_tx = 0

    def leer(self) -> bytes:
      
        datos = self._uart.read() or b""
        self.bytes_rx += len(datos)
        return datos

    def escribir(self, datos: bytes) -> int:
        n = self._uart.write(datos) or len(datos)
        self.bytes_tx += n
        return n

    def disponible(self) -> bool:
       
        try:
            return bool(self._uart.any())
        except AttributeError:
            return True

    def desconectar(self) -> None:
        self._uart.deinit()


# ----------------------------------------------------------------------- SPI ---

class SpiMaestro:
   

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
   

    def __init__(self, baudios: int = 1_000_000, modo: int = 1, pinos: dict = None,
                 pin_cs=None) -> None:
        from machine import SPI
        pinos = pinos or {"sck": 18, "miso": 19, "mosi": 23}
        self.baudios = baudios
        self.modo = modo
        self._spi = SPI(2, baudios=baudios, polarity=polaridad(modo),
                        phase=fase(modo), bits=8, firstbit=SPI.MSB,
                        sck=pinos["sck"], mosi=pinos["mosi"], miso=pinos["miso"],
                        
                        rxonly=True, pin_cs=pin_cs)

        self._cs_cambia = getattr(self._spi, "_puerto", self._spi)
        if hasattr(self._cs_cambia, "al_cambiar_cs"):
            self._cs_cambia = self._cs_cambia.al_cambiar_cs

    def leer(self, n: int = 1) -> bytes:
       
        return self._spi.read(n)

    def escribir(self, datos: bytes) -> int:
      
        return self._spi.write(datos)


def polaridad(modo: int) -> int:
    
    return 1 if modo in (2, 3) else 0


def fase(modo: int) -> int:
    
    return 1 if modo in (1, 3) else 0


# ----------------------------------------------------------------------- I2C ---

class I2cEsclavo:
   
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
      
        import time
        for intento in range(intentos):
            try:
                self._i2c.writeto(direccion, b"\x00")
                return True
            except OSError:
                time.sleep_ms(10)
        return False
