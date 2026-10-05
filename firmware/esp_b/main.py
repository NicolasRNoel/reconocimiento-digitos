

import struct
import time

from protocolo import (ErrorTrama, TIPO_ACUSE, TIPO_CONTROL, TIPO_DIGITO,
                       crc16, desempaquetar_acuse, empaquetar_acuse, validar)
from micro import registrar
from hardware import Led, I2cEsclavo, PinESPEntrada, SpiEsclavo
from lcd import Lcd




SPI_PINOS = {"sck": 18, "miso": 19, "mosi": 23}
CS_PIN = 5
SPI_MODO = 1                  
SPI_BAUDS = 1000000

I2C_PINOS = {"sda": 21, "scl": 22}
I2C_DIRECCION = 0x27
I2C_FRECUENCIA = 400000

LED_PIN = 2
COLUMNAS = 16
FILAS = 2

MS_ENTRE_REFRESCOS = 200        


class Esclava:
   

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
        self.lcd_conectado = True
        self.aviso_lcd = False

    def registrar(self, texto: str) -> None:
        self.al_registrar("ESP-B", texto)



    def pintar(self, forzar: bool = False) -> None:
        
        ahora = time.ticks_ms()
        if not forzar and time.ticks_diff(ahora, self.ultimo_pintado_ms) < MS_ENTRE_REFRESCOS:
            return
        self.ultimo_pintado_ms = ahora

       
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
      
        saltos = "" if self.secuencias_perdidas == 0 else " s%d" % self.secuencias_perdidas
        self.linea_1 = "DIGITO: %d  %3d%%%s" % (digito, confianza, saltos)
        self.linea_2 = "seq %-4d tot %-4d" % (seq, self.digitos_recibidos)
        self.pintar(forzar=True)

    def mostrar_mensaje(self, titulo: str, detalle: str = "") -> None:
        self.linea_1 = titulo[:COLUMNAS]
        self.linea_2 = detalle[:COLUMNAS]
        self.pintar(forzar=True)

    

    def responder(self, seq: int, codigo: int = 0) -> None:
        
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
       
       
        self.buf_in.extend(self.spi.leer(1))

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

    def _al_recibir_digito(self, datos: bytes) -> None:
        from protocolo import desempaquetar_digito
        info = desempaquetar_digito(datos)

       
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
