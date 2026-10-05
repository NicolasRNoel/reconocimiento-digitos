"""
Firmware de la ESP-A. MicroPython. Se copia a la placa tal cual.

QUE HACE
--------
Es un puente. No decide nada: lee tramas del UART que le mando la PC, las
reenvía por SPI a la ESP-B y devuelve el acuse. Todo el trabajo esta en otra
parte, y esa es la decision de diseno del proyecto.

POR QUE LA ESP-A NO INTERPRETA NADA
-----------------------------------
Podria correrse la CNN en el ESP32. No se hace por tres motivos concretos:

1. La CNN son 27.562 pesos en float32, 110 kB. Cabe de sobra en la flash, pero
   la inferencia en MicroPython puro (sin un modulo de linalg acelerado) tarda
   del orden de un segundo por digito en un ESP32 a 240 MHz. El LCD se quedaria
   congelado medio segundo entre numero y numero.

2. El preprocesado de OpenCV (umbral de Otsu, contornos, rectangulo minimo) no
   existe en MicroPython. Habria que reescribirlo entero con arrays planos, y
   el resultado ya no seria el mismo que el que se probo en la PC. Es
   exactamente el tipo de divergencia que hace que un sistema "funcione en el
   banco de pruebas" y falle en la instalacion.

3. La ESP-A queda con un trabajo tan simple que se puede auditar en un rato:
   si una trama no cuadra, se cuenta y se avisa. En una cadena de cinco etapas,
   la etapa mas sencilla es la que no falla.

LA ESP-A SI HACE UNA COSA UTIL: el acuse
-----------------------------------------
Cada trama que reenvia queda anotada en un contador, y si en 10 segundos no le
llega respuesta de la ESP-B, avisa por el puerto de diagnostico. Asi un cable
flojo entre las dos ESP se ve como "la ESP-A no recibe acuse" y no como "el LCD
no se actualiza", que es el sintoma mucho mas dificil de diagnosticar.

REPARTO DE PINES (ESP32 DevKit v1, 30 pines)
-------------------------------------------
    GPIO  3  RX    <- TX del conversor USB-TTL   (tramas de la PC)
    GPIO  1  TX    -> RX del conversor            (log de diagnostico)
    GPIO 18  SCK
    GPIO 19  MISO
    GPIO 23  MOSI
    GPIO  5  CS
    GPIO  2  LED   (el LED integrado de muchas placas)

PARA FLASHEAR
-------------
    mpremote connect
    mpremote cp firmware/esp_a/main.py          :
    mpremote cp firmware/common/protocolo.py    :protocolo.py
    mpremote cp firmware/common/micro.py        :micro.py
    mpremote cp firmware/common/hardware.py     :hardware.py
    mpremote reset
"""

import struct
import time

from protocolo import (ErrorTrama, TIPO_ACUSE, TIPO_CONTROL, TIPO_DIGITO,
                       crc16, desempaquetar_control, desempaquetar_digito,
                       leer_cabecera, validar)
from micro import registrar
from hardware import Led, PinESPSalida, SpiMaestro, Uart


# ------------------------------------------------------------- configuracion ---

# Se declara aqui y no en configuracion.py porque ese archivo importa numpy y
# pathlib para el lado de la PC. En la ESP no hay nada de eso, asi que los pines
# van duplicados en un modulo que solo usa numeros. Es una copia a proposito: los
# dos lados deben poder tener pines distintos sin que nada se rompa, y no vale
# la pena arrastrar un modulo de la PC a la placa por seis enteros.

PUERTO_UART = 1                 # GPIO 1 y 3 en la mayoria de placas
BAUDIOS = 115200
TIMEOUT_LECTURA_MS = 20
LED_PIN = 2

SPI_PINOS = {"sck": 18, "miso": 19, "mosi": 23}
CS_PIN = 5
SPI_MODO = 1                    # CPOL=0, CPHA=1
SPI_BAUDS = 1000000
SPI_DESTINO = 1                 # pin CS

MS_SIN_RESPUESTA_AVISO = 10000  # avisar si pasan 10 s sin acuse de la ESP-B


# ------------------------------------------------------------------ puente -----

class Puente:
    """UART -> SPI -> UART. La logica completa de la ESP-A."""

    def __init__(self, uart, spi, pin_cs, led, al_registrar=None) -> None:
        self.uart = uart
        self.spi = spi
        self.pin_cs = pin_cs
        self.led = led
        self.al_registrar = al_registrar or registrar

        self.buf_in = bytearray()      # bytes del UART a medio recibir
        self.buf_out = bytearray()     # lo que la ESP-B ya contesto

        self.tramas_recibidas = 0
        self.tramas_enviadas = 0
        self.acuses_recibidos = 0
        self.errores_crc = 0
        self.descartadas = 0
        self.ultimo_acuse_ms = time.ticks_ms()
        self.ultima_trama_ms = 0

    def registrar(self, texto: str) -> None:
        self.al_registrar("ESP-A", texto)

    # ------------------------------------------------------------- entrada ----

    def _leer_uart(self) -> None:
        """Mete en el buzon todo lo que haya en el UART.

        Se lee en un bucle hasta que no quede NADA, y no una vez. Una trama de 15
        bytes troceada en trozos de 1 a 4 tarda varios ciclos del bucle en llegar
        entera: leer una sola vez por vuelta dejaria bytes en la tuberia y la
        trama se rearmaria en varias vueltas, con lo que parece que el UART va a
        tirones cuando lo que pasa es que el bucle es mas rapido que el cable.

        El limite de vueltas evita que un bucle se quede aqui para siempre si el
        otro extremo no deja de enviar.
        """
        for _ in range(8):
            datos = self.uart.leer()
            if not datos:
                return
            self.buf_in.extend(datos)

    def _extraer_tramas(self):
        """Saca del buzon las tramas completas y validas.

        Se come las invalidas y sigue. Si una trama llega con un byte de ruido
        delante, el SOF la reencuadra sola; si llega con el CRC roto, se
        descarta y se pasa a la siguiente, en vez de quedarse esperando una
        trama que ya no va a llegar.

        SOBRE COPIAS Y POR QUE NO SE CONSUME HASTA EL FINAL
        ---------------------------------------------------
        Se busca el SOF sobre una COPIA del buzon y solo se borra del buzon real
        cuando hay una trama completa y con el CRC bien. Si se consumiera la
        cabecera y luego, al ver que el cuerpo no estaba, se devolvieran los
        bytes por el principio, el buzon no avanzaria nunca y la ESP-A se quedaria
        colgada sin decir por que.

        Ese fallo no aparece si el UART trocea mucho, porque cada lectura trae
        pocos bytes y el camino problematico no se ejecuta. Solo asoma cuando el
        otro extremo entrega la trama entera de golpe, que es lo que hace un
        conversor USB-TTL. Por eso es importante: el simulador trocea para
        ejercitarlo, no para hacerlo mas dificil.
        """
        while True:
            vista = bytes(self.buf_in)
            posicion = -1
            for i in range(len(vista) - 1):
                if vista[i] == 0xA5 and vista[i + 1] == 0x5A:
                    posicion = i
                    break

            if posicion < 0:
                # No hay SOF. Se conserva un posible 0xA5 al final, por si el
                # 0x5A llega en la siguiente lectura.
                self.buf_in[:] = vista[-1:] if vista[-1:] == b"\xa5" else b""
                return

            if len(vista) - posicion < 4:
                return                       # cabecera partida, se espera

            largo = vista[posicion + 2]
            tipo = vista[posicion + 3]
            total = 4 + largo + 2

            if len(vista) - posicion < total:
                return                       # cuerpo a medias, se espera

            cuerpo = vista[posicion + 4:posicion + total]
            try:
                datos = validar(cuerpo, tipo, largo)
            except ErrorTrama as exc:
                self.errores_crc += 1
                self.registrar("trama descartada: %s" % exc)
                # Se descarta UNA trama entera y se sigue desde el byte
                # siguiente. Sin avanzar, una trama corrupta cuyo cuerpo
                # contenga otro SOF podria reintentarse eternamente.
                self.buf_in[:] = vista[posicion + 1:]
                continue

            self.buf_in[:] = vista[posicion + total:]
            yield tipo, datos

    # -------------------------------------------------------------- salida ----

    def _enviar_trama(self, tipo: int, datos: bytes) -> None:
        """Manda la trama por el SPI con el CS bajado antes del primer byte.

        En hardware se podria enviar la trama entera de golpe. Byte a byte hace
        visible en el log del simulador como va saliendo y no cuesta mas: 16
        bytes a 1 MHz son 128 microsegundos.
        """
        self.pin_cs(0)
        try:
            for byte in _trama_completa(tipo, datos):
                self.spi.escribir(bytes((byte,)))
        finally:
            self.pin_cs(1)
        self.tramas_enviadas += 1

    def _leer_miso(self, cuantos: int) -> bytes:
        """El acuse de la ESP-B. Viene en la siguiente transaccion."""
        self.pin_cs(0)
        try:
            return self.spi.leer(cuantos)
        finally:
            self.pin_cs(1)

    # ----------------------------------------------------------------- bucle ---

    def paso(self) -> None:
        self._leer_uart()

        for tipo, datos in self._extraer_tramas():
            self.ultima_trama_ms = time.ticks_ms()

            if tipo == TIPO_DIGITO:
                info = desempaquetar_digito(datos)
                self._enviar_trama(TIPO_DIGITO, datos)
                self.led.parpadeo(12)
                self.tramas_recibidas += 1
                self.registrar("SPI -> ESP-B  digito %d  %.0f%%  seq %d"
                               % (info["digito"], info["confianza"], info["seq"]))

            elif tipo == TIPO_CONTROL:
                info = desempaquetar_control(datos)
                self._enviar_trama(TIPO_CONTROL, datos)
                self.registrar("SPI -> ESP-B  control orden %d arg %d"
                               % (info["orden"], info["arg"]))

            else:
                # La ESP-A no espera recibir digitos desde la ESP-B: si llega
                # uno, es que las dos placas estan con los cables cruzados.
                self.descartadas += 1
                self.registrar("tipo %d inesperado desde la PC, ignorado" % tipo)

        self._atender_acuses()

    def _atender_acuses(self) -> None:
        """Lee el MISO y reenvia los acuses a la PC por el UART."""
        self.pin_cs(0)
        try:
            datos = self.spi.leer(32)
        finally:
            self.pin_cs(1)

        # b"\xff" lleno es lo que devuelve un esclavo que no tiene nada que
        # decir. Es el valor de reposo de un bus SPI con resistencias de pull-up,
        # asi que no se puede confundir con datos de verdad.
        if not datos or datos == b"\xff" * len(datos):
            return

        self.buf_out.extend(datos)
        while len(self.buf_out) >= 9:          # lo mas corto: un acuse son 9
            if self.buf_out[0] != 0xA5 or self.buf_out[1] != 0x5A:
                del self.buf_out[0]
                continue
            largo = self.buf_out[2]
            if len(self.buf_out) < largo + 6:   # 2 de SOF + LEN + TIPO + LEN + CRC
                return
            trama = bytes(self.buf_out[:largo + 6])
            del self.buf_out[:largo + 6]
            self.uart.escribir(trama)
            self.acuses_recibidos += 1
            self.ultimo_acuse_ms = time.ticks_ms()

    def vigilar(self) -> None:
        """Si la ESP-B lleva mucho callada, lo dice. El cable flojo se ve aqui."""
        ahora = time.ticks_ms()
        if self.tramas_recibidas and ahora - self.ultimo_acuse_ms > MS_SIN_RESPUESTA_AVISO:
            self.ultimo_acuse_ms = ahora
            self.led.parpadeo(400)
            self.registrar("AVISO: la ESP-B no contesta desde hace %d ms. "
                           "Revisa MISO, CS y la alimentacion."
                           % MS_SIN_RESPUESTA_AVISO)

    def resumen(self) -> str:
        return ("ESP-A: %d recibidas, %d por SPI, %d acuses, %d descartadas, "
                "%d errores de CRC" % (self.tramas_recibidas, self.tramas_enviadas,
                                       self.acuses_recibidos, self.descartadas,
                                       self.errores_crc))


def _trama_completa(tipo: int, datos: bytes) -> bytes:
    """Reconstruye SOF + LEN + TIPO + DATOS + CRC. Sin esto, no se podria reenviar."""
    cuerpo = bytes((len(datos), tipo)) + datos
    return b"\xa5\x5a" + cuerpo + struct.pack(">H", crc16(cuerpo))


# ------------------------------------------------------------------ arranque ---

def main() -> int:
    registrar("ESP-A", "arrancando. UART %d baudios, SPI modo %d a %d baudios"
              % (BAUDIOS, SPI_MODO, SPI_BAUDS))

    uart = Uart(PUERTO_UART, BAUDIOS, TIMEOUT_LECTURA_MS)
    spi = SpiMaestro(SPI_BAUDS, SPI_MODO, SPI_PINOS)
    pin_cs = PinESPSalida(CS_PIN, 1)      #_CS en alto = esclava desconectada_
    led = Led(LED_PIN)

    puente = Puente(uart, spi, pin_cs, led)

    # Un par de parpadeos al arrancar para distinguir "no arranca" de
    # "arranca y no recibe nada". Con el LED apagado no se sabe.
    led.parpadeo(60)
    puente.registrar("listo")

    while True:
        inicio = time.ticks_ms()
        puente.paso()
        puente.vigilar()
        transcurrido = time.ticks_diff(time.ticks_ms(), inicio)
        if transcurrido < 5:
            time.sleep_ms(5 - transcurrido)


if __name__ == "__main__":
    main()
