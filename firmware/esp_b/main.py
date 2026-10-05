"""
Firmware de la ESP-B. MicroPython. Se copia a la placa tal cual.

QUE HACE
--------
Recibe tramas por SPI como esclava, las pinta en el LCD I2C y manda un acuse de
vuelta por el MISO.

REPARTO DE PINES (ESP32 DevKit v1, 30 pines)
-------------------------------------------
    GPIO 18  SCK
    GPIO 19  MISO
    GPIO 23  MOSI
    GPIO  5  CS
    GPIO 21  SDA      LCD I2C
    GPIO 22  SCL      LCD I2C
    GPIO  2  LED

EL LCD
------
Backpack PCF8574 en 0x27, 16x2, cuatro potenciometros en el contraste. Si la
pantalla sale en una fila de bloques negros, el contraste esta mal: se ajusta
con el potenciometro hasta que aparezca la linea de arriba.

El firmware inicializa el HD44780 byte a byte, incluida la secuencia de paso de
8 a 4 bits, porque el modulo se cablea en 4 bits y el controlador arranca en 8.
Ese detalle no es cosmetico: si el firmware se salta la secuencia, el LCD
interpreta cada nibble como dos caracteres y escribe Basura intercalada. En el
simulador se ve igual, porque el emulador implementa el protocolo del HD44780 y
no un `print`.

PARA FLASHEAR
-------------
    mpremote connect
    mpremote cp firmware/esp_b/main.py          :
    mpremote cp firmware/common/protocolo.py    :protocolo.py
    mpremote cp firmware/common/micro.py        :micro.py
    mpremote cp firmware/common/hardware.py     :hardware.py
    mpremote cp firmware/common/lcd.py          :lcd.py
    mpremote reset
"""

import struct
import time

from protocolo import (ErrorTrama, TIPO_ACUSE, TIPO_CONTROL, TIPO_DIGITO,
                       crc16, desempaquetar_acuse, empaquetar_acuse, validar)
from micro import registrar
from hardware import Led, I2cEsclavo, PinESPEntrada, SpiEsclavo
from lcd import Lcd


# ------------------------------------------------------------- configuracion ---

SPI_PINOS = {"sck": 18, "miso": 19, "mosi": 23}
CS_PIN = 5
SPI_MODO = 1                    # CPOL=0, CPHA=1: el mismo que usa la ESP-A
SPI_BAUDS = 1000000

I2C_PINOS = {"sda": 21, "scl": 22}
I2C_DIRECCION = 0x27
I2C_FRECUENCIA = 400000

LED_PIN = 2
COLUMNAS = 16
FILAS = 2

MS_ENTRE_REFRESCOS = 200        # no repintar la fila de abajo mas de 5 veces/s


class Esclava:
    """SPI -> LCD -> MISO. La logica completa de la ESP-B."""

    def __init__(self, spi, lcd, led, al_registrar=None) -> None:
        self.spi = spi
        self.lcd = lcd
        self.led = led
        self.al_registrar = al_registrar or registrar

        self.buf_in = bytearray()
        self.buf_out = bytearray()

        self.digitos_recibidos = 0
        self.acuses_enviados = 0
        self.errores_crc = 0
        self.descartadas = 0
        self.ultimo_seq = -1
        self.secuencias_perdidas = 0

        self.linea_1 = "ESP-B lista"
        self.linea_2 = "esperando digito"
        self.ultimo_pintado_ms = 0
        # Si el modulo I2C no responde, la ESP-B sigue leyendo el SPI. Se guarda
        # para avisar UNA vez y no llenar el log de miles de lineas iguales.
        self.lcd_conectado = True
        self.aviso_lcd = False

    def registrar(self, texto: str) -> None:
        self.al_registrar("ESP-B", texto)

    # ---------------------------------------------------------------- LCD -----

    def pintar(self, forzar: bool = False) -> None:
        """Repinta las dos lineas.

        El LCD se escribe con un retardo de ~40 microsegundos por caracter
        (el HD44780 no acepta el siguiente hasta que termina el anterior). Repintar
        las 32 celdas en cada trama bloquearia el SPI, asi que se limita la
        frecuencia: con un fotograma cada 60 ms, dos lineas cada 200 ms es
        imperceptible y deja el bus libre.
        """
        ahora = time.ticks_ms()
        if not forzar and time.ticks_diff(ahora, self.ultimo_pintado_ms) < MS_ENTRE_REFRESCOS:
            return
        self.ultimo_pintado_ms = ahora

        # Un LCD desconectado NO puede tumbar la ESP-B. Perder el cable del
        # modulo I2C es un fallo muy comun y la ESP-B tiene que seguir leyendo
        # el SPI, que es la parte que si importa. Se avisa una sola vez y se
        # deja de intentar pintar: reintentar en cada digito llenaria el log de
        # miles de lineas identicas y no aportaria nada.
        # Se rellena con espacios hasta el final de la linea. Sin esto, al pasar
        # de un texto largo a uno corto se queda el final del anterior:
        # "DIGITO: 7  58%" seguido de "DIGITO: 3 100%" dejaria un "%" colgando
        # al final, que es el sintoma clasico de un LCD mal gestionado.
        #
        # El recorte a COLUMNAS no es una precaution. El HD44780 NO recorta lo que
        # se le pasa: escribe los 18 caracteres aunque el panel sea de 16, y los
        # dos ultimos se solapan con la fila de abajo. Pasa con facilidad porque
        # los formatos "%-4d" parecen cortos y no lo son cuando el contador llega
        # a cinco cifras.
        #
        # Son 16 caracteres por linea y cada uno tarda unos 40 microsegundos en el
        # HD44780: 1.3 ms por repintado. El limite de MS_ENTRE_REFRESCOS evita
        # hacerlo mas de 5 veces por segundo, que es de sobra para un LCD.
        try:
            self.lcd.goto(0, 0)
            self.lcd.escribir(self.linea_1[:COLUMNAS].ljust(COLUMNAS))
            self.lcd.goto(0, 1)
            self.lcd.escribir(self.linea_2[:COLUMNAS].ljust(COLUMNAS))
            self.lcd_conectado = True
        except OSError as exc:
            if self.lcd_conectado or not self.aviso_lcd:
                self.registrar("AVISO: el LCD no responde (%s). Sigo leyendo el SPI."
                               % exc)
                self.aviso_lcd = True
            self.lcd_conectado = False

    def mostrar_digito(self, digito: int, confianza: int, seq: int) -> None:
        """Las dos lineas del panel.

        El ancho es de 16 columnas y NO se rellena con espacios a mano: el
        driver ya rellena hasta el final de la linea. Aqui solo se compose el
        texto, porque si se rellenara aqui y el driver tambien, cada caracter
        pasaria dos veces por el HD44780 y la escritura tardaria el doble.

        La deteccion de una secuencia perdida va en la linea 1. Si aparece
        "s:3", se sabe que se perdio informacion por el SPI y no que fallo el
        modelo, que es un sintoma completamente distinto.
        """
        # Los dos textos tienen que caber en COLUMNAS. Si se pasan, el HD44780 no
        # los recorta: los escribe igual y se solapan con la fila de abajo. Con 5
        # digitos el contador llega a "tot 100000" y empuja la linea. Es la razon
        # de los formatos en %.
        saltos = "" if self.secuencias_perdidas == 0 else " s%d" % self.secuencias_perdidas
        self.linea_1 = "DIGITO: %d  %3d%%%s" % (digito, confianza, saltos)
        self.linea_2 = "seq %-4d tot %-4d" % (seq, self.digitos_recibidos)
        self.pintar(forzar=True)

    def mostrar_mensaje(self, titulo: str, detalle: str = "") -> None:
        self.linea_1 = titulo[:COLUMNAS]
        self.linea_2 = detalle[:COLUMNAS]
        self.pintar(forzar=True)

    # ---------------------------------------------------------------- MISO ----

    def responder(self, seq: int, codigo: int = 0) -> None:
        """Encola un acuse. Sale por el MISO en la siguiente transaccion.

        El SPI es full-duplex, pero la ESP-A no esta leyendo mientras envia: en
        cuanto termina su trama y sube CS, ya no hay a quien mandarle el MISO.
        El unico modo de recibirlo es que la ESP-A baje CS otra vez, que es
        justo lo que hace al ir a buscar acuses. Por eso el acuse llega con un
        ciclo de retardo, y no es un defecto del simulador: es el protocolo.

        El acuse va a `self.spi` (el puerto), no a un buffer propio. Si se
        guardara en un buffer local sin pasarlo al bus, se quedaria ahi para
        siempre: en la placa no existe ningun sitio donde meterlo, el unico
        registro de salida es el MISO del SPI.
        """
        self.spi.escribir(empaquetar_acuse(seq, codigo))
        self.acuses_enviados += 1

    # --------------------------------------------------------------- bucle ----

    def paso(self) -> None:
        self._leer_spi()

        for tipo, datos in self._extraer_tramas():
            if tipo == TIPO_DIGITO:
                self._al_recibir_digito(datos)
            elif tipo == TIPO_CONTROL:
                self._al_recibir_control(datos)
            else:
                self.descartadas += 1
                self.registrar("tipo 0x%02X inesperado, ignorado" % tipo)

        self.pintar()

    def _leer_spi(self) -> None:
        """Toma lo que haya en el buffer del SPI. Es bloqueante si no hay nada.

        Aqui NO se puede hacer sondeo activo con `any()` como en el UART: el
        pin CS lo controla la ESP-A, no la ESP-B. La ESP-B no puede decir "ahora
        no hay nada" sin bajar CS, y si lo hiciera dejaria a la ESP-A colgada
        dentro de la transaccion. Por eso se lee de forma bloqueante y se
        despierta cuando llegan los bytes.
        """
        # Se pide un byte. Es lo que devuelve `spi.read(1)` en modo esclava, y
        # es bloqueante: el hilo espera a que la ESP-A mande algo. Pedir "lo que
        # haya" no es posible, y la razon esta en hardware.py.
        self.buf_in.extend(self.spi.leer(1))

    def _extraer_tramas(self):
        """Saca del buzon las tramas completas y validas.

        Trabaja sobre COPIAS del buzon y solo borra cuando hay una trama entera
        con el CRC bien. Si consumiera la cabecera y luego devolviera los bytes
        por el principio al ver que el cuerpo no estaba, no avanzaria nunca.

        Se come las invalidas y sigue, en vez de quedarse esperando una trama que
        ya no va a llegar.
        """
        while True:
            vista = bytes(self.buf_in)
            posicion = -1
            for i in range(len(vista) - 1):
                if vista[i] == 0xA5 and vista[i + 1] == 0x5A:
                    posicion = i
                    break

            if posicion < 0:
                self.buf_in[:] = vista[-1:] if vista[-1:] == b"\xa5" else b""
                return

            if len(vista) - posicion < 4:
                return

            largo = vista[posicion + 2]
            tipo = vista[posicion + 3]
            total = 4 + largo + 2

            if len(vista) - posicion < total:
                return

            cuerpo = vista[posicion + 4:posicion + total]
            try:
                datos = validar(cuerpo, tipo, largo)
            except ErrorTrama as exc:
                self.errores_crc += 1
                self.registrar("trama descartada: %s" % exc)
                self.buf_in[:] = vista[posicion + 1:]
                continue

            self.buf_in[:] = vista[posicion + total:]
            yield tipo, datos

    def _al_recibir_digito(self, datos: bytes) -> None:
        from protocolo import desempaquetar_digito
        info = desempaquetar_digito(datos)

        # Se comprueba que la secuencia no tenga saltos. Un salto significa que
        # se perdio una trama por el SPI, y eso hay que verlo.
        if self.ultimo_seq >= 0 and info["seq"] != (self.ultimo_seq + 1) & 0xFFFF:
            self.secuencias_perdidas += 1
            self.registrar("salto de secuencia: %d -> %d"
                           % (self.ultimo_seq, info["seq"]))
        self.ultimo_seq = info["seq"]

        self.digitos_recibidos += 1
        self.led.parpadeo(15)
        self.mostrar_digito(info["digito"], info["confianza"], info["seq"])
        self.responder(info["seq"], 0)
        self.registrar("LCD: digito %d al %d%%  (seq %d)"
                       % (info["digito"], info["confianza"], info["seq"]))

    def _al_recibir_control(self, datos: bytes) -> None:
        from protocolo import desempaquetar_control
        info = desempaquetar_control(datos)
        if info["orden"] == 1:
            self.mostrar_mensaje("LCD reiniciado", "seq %d" % info["arg"])
        elif info["orden"] == 2:
            self.mostrar_mensaje("mensaje de la PC", "arg %d" % info["arg"])
        else:
            self.registrar("orden de control %d desconocida" % info["orden"])
        self.responder(info["arg"], 0)

    def resumen(self) -> str:
        return ("ESP-B: %d digitos, %d acuses, %d descartadas, %d errores de CRC, "
                "%d saltos de secuencia" % (self.digitos_recibidos, self.acuses_enviados,
                                            self.descartadas, self.errores_crc,
                                            self.secuencias_perdidas))


# ------------------------------------------------------------------ arranque ---

def main() -> int:
    registrar("ESP-B", "arrancando. SPI esclava modo %d, LCD I2C en 0x%02X"
              % (SPI_MODO, I2C_DIRECCION))

    pin_cs = PinESPEntrada(CS_PIN)
    spi = SpiEsclavo(SPI_BAUDS, SPI_MODO, SPI_PINOS, pin_cs)
    i2c = I2cEsclavo(I2C_PINOS, I2C_FRECUENCIA)
    led = Led(LED_PIN)
    lcd = Lcd(i2c, I2C_DIRECCION, COLUMNAS, FILAS)

    esclava = Esclava(spi, lcd, led)
    led.parpadeo(60)

    lcd.iniciar(COLUMNAS, FILAS)
    esclava.mostrar_mensaje("ESP-B lista", "SPI esclava OK")
    registrar("ESP-B", "listo")

    while True:
        inicio = time.ticks_ms()
        esclava.paso()
        transcurrido = time.ticks_diff(time.ticks_ms(), inicio)
        if transcurrido < 10:
            time.sleep_ms(10 - transcurrido)


if __name__ == "__main__":
    main()
