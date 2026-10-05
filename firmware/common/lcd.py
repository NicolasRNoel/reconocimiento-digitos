"""
Driver del LCD 16x2 con backpack I2C. MicroPython.

COMO SE USA EN LA ESP
---------------------
    from hardware import I2cEsclavo
    from lcd import Lcd

    i2c = I2cEsclavo({"sda": 21, "scl": 22}, 400000)
    lcd = Lcd(i2c, 0x27, 16, 2)
    lcd.iniciar(16, 2)
    lcd.goto(0, 0)
    lcd.escribir("hola")

EN EL SIMULADOR, EL MISMO ARCHIVO
--------------------------------
`simulacion/lcd.py` trae una clase `LcdVirtual` que implementa el protocolo del
HD44780 byte a byte, y este archivo trae la clase `Lcd` que habla ese protocolo.
El simulador conecta una con la otra. Por eso el firmware de la ESP-B puede
importar ESTE archivo y no un copia: la logica de inicializar, de mover el
cursor y de escribir texto es la misma en los dos sitios, y el simulador ejercita
la de verdad.

LA SECUENCIA DE INICIALIZACION Y POR QUE NO SE PUEDE SALTAR
----------------------------------------------------------
El HD44780 arranca en modo de 8 bits. El backpack PCF8574 solo tiene 4 lineas de
datos libres (D4-D7), asi que el bus va en 4 bits y hay que convencer al
controlador. Se le mandan tres "0x30" en 8 bits para que sincronice, y luego un
"0x20" para pasar a 4 bits.

Si el firmware se salta esa secuencia, el LCD interpreta cada nibble como si
fueran dos caracteres y escribe basura intercalada. No es un problema de
"pantalla en blanco": es contenido equivocado, que es peor. El emulador del
simulador reproduce esto, asi que el fallo se ve sin necesidad de tener el panel
en la mesa.
"""

# Comandos del HD44780.
#
# `0x30` es el "Function Set" de la secuencia de arranque en modo 8 bits, y NO
# `0x33`. El bit DL del medio solo existe en el modo de instruccion de 8 bits y no
# se debe tocar al sincronizar. Mandar 0x33 deja al controlador en un estado que
# la especificacion no describe: en el emulador se traduce en "nunca se reconoce
# la cabecera", y en un panel real es la causa clasica de que el LCD escriba
# caracteres de control en vez de fallar limpiamente.
INICIAR_8_BITS = 0x30
INICIAR_4_BITS = 0x20

# Ya en 4 bits, los comandos completos. El "Function Set" distingue una o dos
# lineas con el bit 3.
LINEAS_1 = 0x20
LINEAS_2 = 0x28
PANTALLA_ON_SIN_CURSOR = 0x0C
PANTALLA_OFF = 0x08
BORRADOR_PANTALLA = 0x01
BORRADOR_INICIO = 0x02
ENTRADA_NORMAL = 0x06


class Lcd:
    """El panel, visto desde el firmware."""

    def __init__(self, i2c, direccion=0x27, columnas=16, filas=2, luz=True) -> None:
        self.i2c = i2c
        self.direccion = direccion
        self.columnas = columnas
        self.filas = filas
        self.luz = luz
        self.caracteres = 0

    # ------------------------------------------------------------ bajo nivel --

    def _nibble(self, valor, es_dato):
        """Un nibble son dos escrituras I2C: EN sube y EN baja.

        El backpack translates el byte asi:

            bit 0  RS    0 = comando, 1 = dato
            bit 1  RW    0 = escritura (el HD44780 no se lee nunca aqui)
            bit 2  EN    el flanco de subida captura el nibble
            bit 3  luz   transistor de la retroiluminacion
            bits 4-7     D4-D7 del HD44780

        Se manda el nibble YA desplazado 4 posiciones porque en este modulo los
        cuatro bits altos son directamente las lineas de datos.
        """
        byte = (valor & 0x0F) << 4
        if es_dato:
            byte |= 0x01
        if self.luz:
            byte |= 0x08

        self.i2c.escribir(self.direccion, bytes((byte | 0x04,)))   # EN en alto
        self.i2c.escribir(self.direccion, bytes((byte,)))           # EN en bajo

    def _byte(self, valor, es_dato=False):
        """Un byte va en dos nibbles, el alto primero. Asi es como espera el HD44780
        cuando esta en modo 4 bits."""
        self._nibble((valor >> 4) & 0x0F, es_dato)
        self._nibble(valor & 0x0F, es_dato)

    # ------------------------------------------------------------ alto nivel --

    def comando(self, valor):
        self._byte(valor, es_dato=False)

    def caracter(self, valor):
        self._byte(ord(valor) if isinstance(valor, str) else valor, es_dato=True)
        self.caracteres += 1

    def escribir(self, texto):
        for c in str(texto):
            self.caracter(c)

    def goto(self, columna, fila=0):
        """Mueve el cursor a una posicion de la pantalla.

        El HD44780 tiene 40 columnas de memoria por fila y las direcciones NO
        coinciden con las columnas visibles. Con un panel de 16 columnas y los
        jumperes del modulo en la fila 0:

            linea 1 del LCD = direccion 0x00
            linea 2 del LCD = direccion 0x40

        que es lo que pone aqui. Los jumperes en la fila 1 cambian los offsets a
        0x10 y 0x50, que es lo que hace falta para un panel de 20 columnas.

        `columna` se limita a 15 porque es el unico campo de 4 bits del comando.
        """
        base = 0x80 | (0x00 if fila == 0 else 0x40) | (columna & 0x0F)
        self.comando(base)

    def iniciar(self, columnas=16, filas=2):
        """La secuencia de arranque. Hay que hacerla SIEMPRE que se alimente."""
        import time

        time.sleep(0.04)                      # el HD44780 necesita 40 ms tras power-on

        for _ in range(3):                    # tres 0x30 en 8 bits
            self.comando(INICIAR_8_BITS)
            time.sleep(0.004)

        self.comando(INICIAR_4_BITS)          # y ahora a 4 bits
        time.sleep(0.004)

        self.comando(LINEAS_2 if filas > 1 else LINEAS_1)
        self.comando(ENTRADA_NORMAL)
        self.comando(PANTALLA_ON_SIN_CURSOR)
        time.sleep(0.001)
        self.comando(BORRADOR_PANTALLA)
        self.comando(BORRADOR_INICIO)
        time.sleep(0.002)

        self.columnas = columnas
        self.filas = filas

    def apagar(self):
        self.comando(PANTALLA_OFF)
        self.luz = False

    def encender(self):
        self.comando(PANTALLA_ON_SIN_CURSOR)
        self.luz = True

    def luz(self, encendida):
        if encendida != self.luz:
            self.luz = encendida
            # Con la luz apagada el byte no lleva el bit 3. El HD44780 no se
            # entera, pero el transistor del modulo si.
            self._byte(0x00, es_dato=False)
