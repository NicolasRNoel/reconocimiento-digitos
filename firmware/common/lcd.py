
INICIAR_8_BITS = 0x30
INICIAR_4_BITS = 0x20


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
