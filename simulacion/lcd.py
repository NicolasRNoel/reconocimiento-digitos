"""
Emulador del LCD 16x2 con backpack I2C (PCF8574 + HD44780).

QUE SE SIMULA Y POR QUE NO ES UN DIBUJO EN PANTALLA
----------------------------------------------------
El modulo I2C que se compra es un PCF8574 de 8 lineas conectado a un HD44780.
El PCF8574 no sabe de caracteres: es un puerto de 8 bits. El PC recibe un byte,
lo reparte en D4-D7 (el nibble alto, que va a los pines de datos del HD44780) y
RS, RW, EN y el transistor de la luz (los cuatro bits bajos), y hace un pulso
de reloj en EN. El HD44780, al ver el flanco de EN, captura ese nibble y lo
ejecuta como comando o como caracter.

Lo que se modela aqui es ESE protocolo byte a byte:

    dato = ((nibble & 0x0F) << 4) | (bit EN << 2) | (bit luz << 3) | (bit RS)

    byte = dato con el nibble arriba

Asi el firmware tiene que hacer la secuencia de inicializacion de verdad
(0x30, 0x30, 0x30, 0x20 y luego 0x28, 0x0C, 0x06...) y no puede saltarsela
poniendo texto en una variable. Si el firmware se equivoca al inicializar, el
LCD virtual se queda en blanco o con la lininga corrida, igual que el de verdad.

LA SECUENCIA DE INICIALIZACION ES OBLIGATORIA
---------------------------------------------
Un HD44780 arranca en modo 8 bits, pero los jumpers del backpack suelen dejar
el bus en 4 bits. La secuencia de cuatro "0x30" fool al controlador para que
vuelva a 8, y ahi se le mete el 0x20 para entrar en 4 bits. Si se salta, el
LCD interpretara cada nibble como dos caracteres y escribira Basura intercalada.
"""

from __future__ import annotations

import time

# ------------------------------------------------------------- mapa del bus ----
# Bit a bit del PCF8574, tal como solda el modulo de 4 potenciometros.
BIT_RS = 0x01       # 0 = comando, 1 = dato
BIT_RW = 0x02       # 0 = escritura. El HD44780 nunca se lee en este modulo
BIT_EN = 0x04       # el flanco de subida captura el nibble
BIT_LUZ = 0x08      # transistor de la retroiluminacion
MASCARA_NIBBLE = 0xF0        # los 4 bits altos son D4-D7
DESPLAZAMIENTO_NIBBLE = 4

# Modos del HD44780 que este proyecto usa.
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
    """El panel. Habla el idioma del backpack, no el del HD44780."""

    # La DDRAM del HD44780 tiene 40 columnas, no 16. El panel visible usa las
    # primeras, pero la memoria existe entera: por eso una direccion como 0x10
    # (columna 16) es valida aunque no se vea. Modelar la memoria completa es lo
    # que hace que `goto` falle de forma realista si alguien pasa una columna
    # que no cabe, en vez de envolverla en silencio.
    MEMORIA_DDRAM = 40

    def __init__(self, columnas: int = 16, filas: int = 2, luz: bool = True,
                 al_registrar=None) -> None:
        self.columnas = columnas
        self.filas = filas
        self.memoria_ddram = self.MEMORIA_DDRAM
        self.al_registrar = al_registrar or (lambda texto: None)

        self.ddram = [[" "] * columnas for _ in range(filas)]
        self.cursores = [[0] * columnas for _ in range(filas)]
        self.cursores[0][0] = 1          # el bit de parpadeo del HD44780
        self.fila = 0
        self.columna = 0
        self.pantalla_on = False
        self.luz = luz
        self.incremento = 0x00

        # Estado de la inicializacion.
        self.en_4_bits = False
        self.filas_definidas = 1
        self.inicializado = False
        # Nibble alto a la espera del bajo. En 4 bits cada byte llega partido en
        # dos y hay que juntarlos antes de hacer nada.
        self.alto_pendiente = None

        # Contadores.
        self.comandos = 0
        self.caracteres = 0
        self.bytes_i2c = 0
        self.pulsos_en = 0

    # --------------------------------------------------------------- utilities --

    @property
    def texto(self) -> str:
        return "\n".join("".join(fila).rstrip() for fila in self.ddram)

    def registrar(self, texto: str) -> None:
        self.al_registrar(texto)

    # ------------------------------------------------------- el byte del bus ---

    def escribir_byte_i2c(self, dato: int) -> None:
        """Recibe un byte del PCF8574 y lo ejecuta. Es la entrada del emulador.

        El nibble se toma YA DESPLAZADO: el backpack pone D4-D7 del HD44780 en los
        cuatro bits altos del byte (`dato & 0xF0`), y ese nibble es el comando. No
        hay que volver a desplazarlo, porque el hardware del modulo ya lo hizo. Si
        se hiciera, el nibble valido seria 0 y la pantalla se llenaria de
        caracteres de control.
        """
        self.bytes_i2c += 1
        nibble = dato & MASCARA_NIBBLE
        es_dato = bool(dato & BIT_RS)
        en = bool(dato & BIT_EN)
        luz = bool(dato & BIT_LUZ)

        if luz != self.luz:
            self.luz = luz
            self.registrar("retroiluminacion %s" % ("ON" if luz else "OFF"))

        if not en:
            return                       # EN a 0 no hace nada: solo importan los flancos

        self.pulsos_en += 1
        if es_dato:
            self._ejecutar_dato(nibble >> 4)
        else:
            self._ejecutar_comando(nibble >> 4)

    # ------------------------------------------------------------ HD44780 ------

    def _ejecutar_dato(self, nibble: int) -> None:
        """Un caracter, que tambien llega en dos nibbles.

        El acumulado es el MISMO que usan los comandos: el HD44780 no sabe si lo
        que esta llegando por D4-D7 es un comando o un dato hasta que estan los
        ocho bits, y RS solo dice cual de las dos cosas es.
        """
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

        # MODO DE 8 BITS, EL ESTADO DE ARRANQUE
        # --------------------------------------
        # El HD44780 arranca en 8 bits. Los dos "0x30" de la secuencia de
        # arranque valen porque el driver los manda byte a byte y, estando en
        # 8 bits, el controlador los lee completos.
        #
        # OJO: el backpack SIEMPRE manda dos bytes I2C por byte (el pulso de EN
        # son dos), asi que un byte entra como dos nibbles tambien en 8 bits.
        # Por eso aqui tambien se juntan, y el "0x30" se reconoce entero.
        if not self.en_4_bits:
            if self.alto_pendiente is None:
                self.alto_pendiente = nibble
                return
            completo8 = (self.alto_pendiente << 4) | nibble
            self.alto_pendiente = None

            if completo8 == 0x30:
                return                  # "sigo en 8 bits", no hace nada aun
            if completo8 == 0x20:
                self.en_4_bits = True
                self.registrar("el HD44780 pasa a modo 4 bits")
            return
            # Un 0x33 aqui no se reconoce nunca. El driver envia 0x30, que es lo
            # que dice la especificacion. Si alguien "arregla" el driver poniendo
            # 0x33, el controlador se queda en 8 bits para siempre y cada byte
            # posterior se interpreta como dos comandos distintos.

        # MODO DE 4 BITS: AHORA HAY QUE JUNTAR DOS NIBBLES
        # --------------------------------------------------
        # Cada byte se manda en dos nibbles, el alto primero. El HD44780 los
        # acumula hasta tener los 8 bits y solo entonces ejecuta. Sin este
        # acumulado, el "0x0C" (encender pantalla) se ejecutaria como nibble alto
        # "0x0" y luego como nibble bajo "0xC", que son dos comandos que no existen,
        # y la pantalla se queda con basura de caracteres de control.
        if self.alto_pendiente is None:
            self.alto_pendiente = nibble
            return
        completo = (self.alto_pendiente << 4) | nibble
        self.alto_pendiente = None

        # EL ORDEN DE ESTOS `if` NO ES COSMETICO
        # ---------------------------------------
        # El "Function Set" (0x20-0x38) tiene bit 3 a 1, igual que "Display On/Off"
        # (0x08-0x0F). Si se comprobara el display antes, el 0x28 se tomaria por un
        # comando de pantalla y el LCD se quedaria en una sola linea y apagado, sin
        # que nada fallara. Por eso el Function Set va PRIMERO y con igualdad
        # exacta, no con mascara de bits.
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
            # Return-home. El HD44780 tarda hasta 2 ms, pero el emulador no
            # simula tiempos, asi que se aplica al instante.
            self.cursores = [[0] * self.columnas for _ in range(self.filas)]
            self.fila, self.columna = 0, 0
            self.cursores[0][0] = 1
        elif completo in (ENTRADA_NORMAL, ENTRADA_MODO_SHIFT):
            self.incremento = 0x01 if completo == ENTRADA_MODO_SHIFT else 0x00
        elif completo == 0x10:
            self._mover(-1, 0)           # desplaza la pantalla a la izquierda
        elif completo == 0x18:
            self._mover(1, 0)            # desplaza la pantalla a la derecha
        elif completo & 0x80:
            # "Set DDRAM address": bit 7 a 1, bit 6 a 0.
            # "Set CGRAM address": bit 7 a 0, bit 6 a 1.
            #
            # Los dos casos se distinguen por el bit 7, no por el 6. El puntero de
            # la linea 2 de la pantalla es 0x40 y produce un comando 0xC0, que tiene
            # los dos bits a 1: si se mirara el bit 6 para decidir, la linea 2 se
            # tomaria por un puntero de CGRAM y su texto apareceria encima de la
            # linea 1. Ese fue el bug: `goto(0, 1)` no funcionaba y no fallaba nada.
            if completo & 0x40 and not completo & 0x80:
                self._ir_a_cgram(completo & 0x0F)     # glifos propios
            else:
                self._ir_a_ddram(completo & 0x7F)
        else:
            self.registrar("comando desconocido 0x%02X" % completo)

    def _avanzar(self) -> None:
        if self.incremento & 0x01:
            self._mover(-1, 0)           # en modo shift el display se arrastra
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
        """A donde lleva un puntero de la DDRAM.

        `comando` es el byte entero con el bit 7 puesto, y la direccion va
        DESPLAZADA: el HD44780 la guarda en los bits 6..3 y la columna en los
        bits 2..0. Por eso "ir a la linea 2, columna 0" es el comando 0xC0 y no
        0x40, y por eso "columna 8" seria 0x88.

        Con el modulo en 16 columnas y los jumperes en la fila 0:

            0x80 -> linea 1, columna 0        (lo que pone el driver)
            0xC0 -> linea 2, columna 0

        Las direcciones 0x10 y 0x50 son esas mismas lineas desplazadas 16
        columnas, fuera de un panel de 16. Los jumperes en la fila 1 cambian los
        offsets a 0x10 y 0x50, que es lo que hace falta para un panel de 20.
        """
        direccion = comando & 0x78       # bits 6..3, en su sitio
        if direccion < 0x40:
            self.fila = 0
            self.columna = min(direccion + (comando & 0x07),
                               self.columnas - 1)
        else:
            self.fila = 1
            self.columna = min(direccion - 0x40 + (comando & 0x07),
                               self.columnas - 1)

    def _ir_a_cgram(self, direccion: int) -> None:
        """Puntero de la CGRAM, donde viven los glifos propios.

        El proyecto no los usa. Se acepta el comando y no se dibuja nada, con tal
        de que escribir ahi no rompa la pantalla ni mueva el cursor visible.
        """
        self.registrar("puntero de la CGRAM a 0x%02X (no usado)" % direccion)

    # ------------------------------------------------------------- informe ----

    def informe(self) -> str:
        return ("LCD: %d bytes I2C, %d comandos, %d caracteres, %d pulsos EN, "
                "%d lineas, luz %s"
                % (self.bytes_i2c, self.comandos, self.caracteres, self.pulsos_en,
                   self.filas_definidas, "ON" if self.luz else "OFF"))


class Lcd16x2:
    """
    La clase que ve el firmware: habla nibbles, como un controlador real.

    Escribe en el backpack byte a byte haciendo el pulso de EN. Ese pulso es lo
    que obliga al firmware a hacer la inicializacion completa; no hay atajo por
    software, igual que en el panel fisico.
    """

    def __init__(self, bus, columna: int = 16, fila: int = 2, al_registrar=None) -> None:
        self.bus = bus
        self.columna = columna
        self.fila = fila
        self.al_registrar = al_registrar or (lambda texto: None)
        self.luz = True

    # ------------------------------------------------------------- bajo nivel --

    def _enviar_nibble(self, nibble: int, es_dato: bool) -> None:
        """Un nibble son dos escrituras I2C: EN sube, EN baja."""
        base = ((nibble & 0x0F) << DESPLAZAMIENTO_NIBBLE)
        if es_dato:
            base |= BIT_RS
        if self.luz:
            base |= BIT_LUZ

        self.bus.escribir([base | BIT_EN])        # flanco de subida: captura
        self.bus.escribir([base])                 # flanco de bajada: ejecuta

    def enviar_byte(self, valor: int, es_dato: bool = False) -> None:
        """Un byte se manda en dos nibbles, el alto primero."""
        self._enviar_nibble((valor >> 4) & 0x0F, es_dato)
        self._enviar_nibble(valor & 0x0F, es_dato)

    def comando(self, valor: int) -> None:
        self.enviar_byte(valor & 0xFF, es_dato=False)

    def texto(self, cadena) -> None:
        for caracter in str(cadena):
            self.enviar_byte(ord(caracter) & 0xFF, es_dato=True)

    # ------------------------------------------------------------ alto nivel --

    def iniciar(self, lineas: int = 2, columnas: int = 16) -> None:
        """La secuencia de inicializacion. Hay que hacerla cada vez que se enciende."""
        # 1. Espera del HD44780 tras la alimentacion: 40 ms.
        time.sleep(0.04)
        # 2. Tres veces 0x30 en modo 8 bits para sincronizar.
        for _ in range(3):
            self.comando(MODO_8_BITS)
            time.sleep(0.004)
        # 3. A 4 bits.
        self.comando(MODO_4_BITS)
        time.sleep(0.004)
        # 4. Ahora si, la configuracion completa en 4 bits.
        self.comando(LINEAS_2 if lineas > 1 else LINEAS_1)
        time.sleep(0.001)
        self.comando(ENTRADA_NORMAL)          # "0x06": deja el cursor donde esta
        self.comando(PANTALLA_ON)              # "0x0C": sin cursor, sin parpadeo
        self.comando(BORRADOR_PANTALLA)
        self.comando(BORRADOR_INICIO)
        time.sleep(0.002)

    def goto(self, columna: int, fila: int = 0) -> None:
        base = 0x80 | (0x00 if fila == 0 else 0x40) | (columna & 0x0F)
        self.comando(base)

    def apagar(self) -> None:
        """Apaga pantalla y luz. El HD44780 se queda sin del todo, como si se
        cortara la alimentacion del modulo."""
        self.comando(PANTALLA_OFF)
        self.luz = False

    def encender(self) -> None:
        self.comando(PANTALLA_ON)
        self.luz = True


class LcdTexto:
    """Dibuja el estado del panel. Para consola, sin dependencias."""

    def __init__(self, ancho: int = 62) -> None:
        self.ancho = ancho

    def render(self, lcd: LcdVirtual, titulo: str = "") -> str:
        """Dibuja el panel como lo veria alguien de frente.

        Cada fila del LCD es una linea de texto aqui. El titulo va arriba y se
        deja una linea en blanco entre filas, pero no se añade ninguna linea de
        contenido: si el panel tiene 2 filas, salen 2 lineas con texto.
        """
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
