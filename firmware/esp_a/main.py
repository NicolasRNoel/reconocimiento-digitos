
import struct
import time

from protocolo import (ErrorTrama, TIPO_ACUSE, TIPO_CONTROL, TIPO_DIGITO,
                       crc16, desempaquetar_control, desempaquetar_digito,
                       leer_cabecera, validar)
from micro import registrar
from hardware import Led, PinESPSalida, SpiMaestro, Uart




PUERTO_UART = 1                 
BAUDIOS = 115200
TIMEOUT_LECTURA_MS = 20
LED_PIN = 2

SPI_PINOS = {"sck": 18, "miso": 19, "mosi": 23}
CS_PIN = 5
SPI_MODO = 1                   
SPI_BAUDS = 1000000
SPI_DESTINO = 1                 

MS_SIN_RESPUESTA_AVISO = 10000  




class Puente:
   

    def __init__(self, uart, spi, pin_cs, led, al_registrar=None) -> None:
        self.uart = uart
        self.spi = spi
        self.pin_cs = pin_cs
        self.led = led
        self.al_registrar = al_registrar or registrar

        self.buf_in = bytearray()    
        self.buf_out = bytearray()    

        self.tramas_recibidas = 0
        self.tramas_enviadas = 0
        self.acuses_recibidos = 0
        self.errores_crc = 0
        self.descartadas = 0
        self.ultimo_acuse_ms = time.ticks_ms()
        self.ultima_trama_ms = 0

    def registrar(self, texto: str) -> None:
        self.al_registrar("ESP-A", texto)

   

    def _leer_uart(self) -> None:
       
        for _ in range(8):
            datos = self.uart.leer()
            if not datos:
                return
            self.buf_in.extend(datos)

    def _extraer_tramas(self):
       
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

   

    def _enviar_trama(self, tipo: int, datos: bytes) -> None:
        
        self.pin_cs(0)
        try:
            for byte in _trama_completa(tipo, datos):
                self.spi.escribir(bytes((byte,)))
        finally:
            self.pin_cs(1)
        self.tramas_enviadas += 1

    def _leer_miso(self, cuantos: int) -> bytes:
       
        self.pin_cs(0)
        try:
            return self.spi.leer(cuantos)
        finally:
            self.pin_cs(1)

  

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

        
        if not datos or datos == b"\xff" * len(datos):
            return

        self.buf_out.extend(datos)
        while len(self.buf_out) >= 9:          
            if self.buf_out[0] != 0xA5 or self.buf_out[1] != 0x5A:
                del self.buf_out[0]
                continue
            largo = self.buf_out[2]
            if len(self.buf_out) < largo + 6:  
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


def main() -> int:
    registrar("ESP-A", "arrancando. UART %d baudios, SPI modo %d a %d baudios"
              % (BAUDIOS, SPI_MODO, SPI_BAUDS))

    uart = Uart(PUERTO_UART, BAUDIOS, TIMEOUT_LECTURA_MS)
    spi = SpiMaestro(SPI_BAUDS, SPI_MODO, SPI_PINOS)
    pin_cs = PinESPSalida(CS_PIN, 1)     
    led = Led(LED_PIN)

    puente = Puente(uart, spi, pin_cs, led)

    
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
