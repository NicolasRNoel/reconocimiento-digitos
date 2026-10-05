"""
Emulador del LCD 16x2 con backpack I2C (PCF8574 + HD44780).



"""

from __future__ import annotations

import time


BIT_RS = 0x01       
BIT_RW = 0x02       
BIT_EN = 0x04       
BIT_LUZ = 0x08      
MASCARA_NIBBLE = 0xF0        
DESPLAZAMIENTO_NIBBLE = 4


MODO_8_BITS = 0x33
LINEAS_1 = 0x20
LINEAS_2 = 0x28
PANTALLA_ON = 0x0C
PANTALLA_OFF = 0x08
CURSOR_ON = 0x0E
CURSOR_OFF = 0x0C
CURSOR_ON = 0x0E
CURSOR_OFF = 0x0C
BORRADOR_PANTALLA = 0x01
BORRADOR_INICIO = 0x02
ENTRADA_NORMAL = 0x06
ENTRADA_MODO_SHIFT = 0x07


class LcdVirtual:
   
    MEMORIA_DDRAM = 40

    def __init__(self, columnas: int = 16, filas: int = 2, luz: bool = True,
                 al_registrar=None) -> None:
        self.columnas = columnas
        self.filas = filas
        self.memoria_ddram = self.MEMORIA_DDRAM
        self.al_registrar = al_registrar or (lambda texto: None)

        self.ddram = [[" "] * columnas for _ in range(filas)]
        self.cursores = [[0] * columnas for _ in range(filas)]
        self.cursores[0][0] = 1         
        self.fila = 0
        self.columna = 0
        self.pantalla_on = False
        self.luz = luz
        self.incremento = 0x00

       
        self.en_4_bits = False
        self.filas_definidas = 1
        self.inicializado = False
     
        self.alto_pendiente = None

        self.comandos = 0
        self.caracteres = 0
        self.bytes_i2c = 0
        self.pulsos_en = 0

  

    @property
    def texto(self) -> str:
        return "\n".join("".join(fila).rstrip() for fila in self.ddram)

    def registrar(self, texto: str) -> None:
        self.al_registrar(texto)

  
    def escribir_byte_i2c(self, dato: int) -> None:
       
        self.bytes_i2c += 1
        nibble = dato & MASCARA_NIBBLE
        es_dato = bool(dato & BIT_RS)
        en = bool(dato & BIT_EN)
        luz = bool(dato & BIT_LUZ)

        if luz != self.luz:
            self.luz = luz
            self.registrar("retroiluminacion %s" % ("ON" if luz else "OFF"))

        if not en:
            return                      

        self.pulsos_en += 1
        if es_dato:
            self._ejecutar_dato(nibble >> 4)
        else:
            self._ejecutar_comando(nibble >> 4)

    

    def _ejecutar_dato(self, nibble: int) -> None:
      
        if self.alto_pendiente is None:
            self.alto_pendiente = nibble
            return
        caracter = (self.alto_pendiente << 4) | nibble
        self.alto_pendiente = None

        self.caracteres += 1
        if caracter:
            self.ddram[self.fila][self.columna] = chr(caracter)
        self._avanzar()

    def _ejecutar_comando(self, nibble: int) -> None:
        self.comandos += 1

       
        if not self.en_4_bits:
            if self.alto_pendiente is None:
                self.alto_pendiente = nibble
                return
            completo8 = (self.alto_pendiente << 4) | nibble
            self.alto_pendiente = None

            if completo8 == 0x30:
                return                  
            if completo8 == 0x20:
                self.en_4_bits = True
                self.registrar("el HD44780 pasa a modo 4 bits")
            return
          
       
        if self.alto_pendiente is None:
            self.alto_pendiente = nibble
            return
        completo = (self.alto_pendiente << 4) | nibble
        self.alto_pendiente = None

      
        if completo in (LINEAS_1, LINEAS_2):
            self.filas_definidas = 1 if completo == LINEAS_1 else 2
            self.registrar("HD44780 configurado para %d linea(s)"
                           % self.filas_definidas)
        elif completo in (PANTALLA_ON, PANTALLA_OFF):
            self.pantalla_on = completo == PANTALLA_ON
            self.registrar("pantalla %s"
                           % ("encendida" if self.pantalla_on else "apagada"))
            self.inicializado = True
        elif completo == BORRADOR_PANTALLA:
            self.ddram = [[" "] * self.columnas for _ in range(self.filas)]
            self.registrar("pantalla borrada")
        elif completo == BORRADOR_INICIO:
          
            self.cursores = [[0] * self.columnas for _ in range(self.filas)]
            self.fila, self.columna = 0, 0
            self.cursores[0][0] = 1
        elif completo in (ENTRADA_NORMAL, ENTRADA_MODO_SHIFT):
            self.incremento = 0x01 if completo == ENTRADA_MODO_SHIFT else 0x00
        elif completo == 0x10:
            self._mover(-1, 0)           
        elif completo == 0x18:
            self._mover(1, 0)          
        elif completo & 0x80:
            
            if completo & 0x40 and not completo & 0x80:
                self._ir_a_cgram(completo & 0x0F)     
            else:
                self._ir_a_ddram(completo & 0x7F)
        else:
            self.registrar("comando desconocido 0x%02X" % completo)

    def _avanzar(self) -> None:
        if self.incremento & 0x01:
            self._mover(-1, 0)          
        self.columna += 1
        if self.columna >= self.columnas:
            self.columna = 0
            self.fila = (self.fila + 1) % max(self.filas, self.filas_definidas)
        self.cursores[self.fila][self.columna] = 1

    def _mover(self, delta_columna: int, delta_fila: int) -> None:
        self.columna = (self.columna + delta_columna) % self.columnas
        self.fila = (self.fila + delta_fila) % max(self.filas, self.filas_definidas)
        self.cursores[self.fila][self.columna] = 1

    def _ir_a_ddram(self, comando: int) -> None:
      
        direccion = comando & 0x78       
        if direccion < 0x40:
            self.fila = 0
            self.columna = min(direccion + (comando & 0x07),
                               self.columnas - 1)
        else:
            self.fila = 1
            self.columna = min(direccion - 0x40 + (comando & 0x07),
                               self.columnas - 1)

    def _ir_a_cgram(self, direccion: int) -> None:
       
        self.registrar("puntero de la CGRAM a 0x%02X (no usado)" % direccion)


    def informe(self) -> str:
        return ("LCD: %d bytes I2C, %d comandos, %d caracteres, %d pulsos EN, "
                "%d lineas, luz %s"
                % (self.bytes_i2c, self.comandos, self.caracteres, self.pulsos_en,
                   self.filas_definidas, "ON" if self.luz else "OFF"))


class Lcd16x2:
   

    def __init__(self, bus, columna: int = 16, fila: int = 2, al_registrar=None) -> None:
        self.bus = bus
        self.columna = columna
        self.fila = fila
        self.al_registrar = al_registrar or (lambda texto: None)
        self.luz = True

  
    def _enviar_nibble(self, nibble: int, es_dato: bool) -> None:
        """Un nibble son dos escrituras I2C: EN sube, EN baja."""
        base = ((nibble & 0x0F) << DESPLAZAMIENTO_NIBBLE)
        if es_dato:
            base |= BIT_RS
        if self.luz:
            base |= BIT_LUZ

        self.bus.escribir([base | BIT_EN])       
        self.bus.escribir([base])                

    def enviar_byte(self, valor: int, es_dato: bool = False) -> None:
        """Un byte se manda en dos nibbles, el alto primero."""
        self._enviar_nibble((valor >> 4) & 0x0F, es_dato)
        self._enviar_nibble(valor & 0x0F, es_dato)

    def comando(self, valor: int) -> None:
        self.enviar_byte(valor & 0xFF, es_dato=False)

    def texto(self, cadena) -> None:
        for caracter in str(cadena):
            self.enviar_byte(ord(caracter) & 0xFF, es_dato=True)

  

    def iniciar(self, lineas: int = 2, columnas: int = 16) -> None:
        """La secuencia de inicializacion. Hay que hacerla cada vez que se enciende."""
     
        time.sleep(0.04)
        
        for _ in range(3):
            self.comando(MODO_8_BITS)
            time.sleep(0.004)
       
        self.comando(MODO_4_BITS)
        time.sleep(0.004)
       
        self.comando(LINEAS_2 if lineas > 1 else LINEAS_1)
        time.sleep(0.001)
        self.comando(ENTRADA_NORMAL)         
        self.comando(PANTALLA_ON)             
        self.comando(BORRADOR_PANTALLA)
        self.comando(BORRADOR_INICIO)
        time.sleep(0.002)

    def goto(self, columna: int, fila: int = 0) -> None:
        base = 0x80 | (0x00 if fila == 0 else 0x40) | (columna & 0x0F)
        self.comando(base)

    def apagar(self) -> None:
       
        self.comando(PANTALLA_OFF)
        self.luz = False

    def encender(self) -> None:
        self.comando(PANTALLA_ON)
        self.luz = True


class LcdTexto:
  

    def __init__(self, ancho: int = 62) -> None:
        self.ancho = ancho

    def render(self, lcd: LcdVirtual, titulo: str = "") -> str:
       
        lineas = []
        borde = "+" + "-" * self.ancho + "+"
        lineas.append(borde)
        if titulo:
            lineas.append("|" + titulo.center(self.ancho) + "|")
            lineas.append(borde)

        for indice, fila in enumerate(lcd.ddram):
            if indice and lcd.filas > 1:
                lineas.append("|" + " " * self.ancho + "|")
            visible = "".join(fila).rstrip()[:self.ancho - 4]
            lineas.append("|  " + visible.ljust(self.ancho - 4) + "  |")

        lineas.append(borde)
        return "\n".join(lineas)
