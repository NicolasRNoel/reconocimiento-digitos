"""
Un solo lugar donde estan los numeros de la cadena.

Se importa desde la PC, desde el simulador y desde el firmware. Los pines son
los que se documentan en el README para cada placa; si cambias la placa cambias
solo este archivo.
"""

from __future__ import annotations

# --------------------------------------------------------------- PC -> ESP-A ---

PUERTO_SERIE = "COM5"                 # Windows. En Linux seria /dev/ttyUSB0
BAUDIOS = 115200
TIMEOUT_SERIE_S = 0.05
REINTENTOS_SERIE = 3                 # cuantos veces se busca la ESP-A al arrancar

# ------------------------------------------------------------------ ESP-A ------
# Placa de referencia: ESP32 DevKit v1 / WROOM-32, 30 pines.
#
#   UART (RX desde la PC)
#   GPIO 3  RX   <-  TX del conversor USB-TTL
#   GPIO 1  TX   ->  RX del conversor (diagnostico)
#
#   SPI maestro hacia la ESP-B
#   GPIO 18 SCK
#   GPIO 19 MISO
#   GPIO 23 MOSI
#   GPIO 5  CS   (chip select, LOW = esclavo seleccionado)
#
#   LED de actividad
#   GPIO 2  LED
PINES_UART_A = {"rx": 3, "tx": 1}
PINES_SPI_A = {"sck": 18, "miso": 19, "mosi": 23, "cs": 5}
PIN_LED_A = 2
SPI_MODO_A = 1                      # maestro, CPOL=0 CPHA=1, el modo que pide el LCD y la ESP-B
SPI_BAUDS_A = 1_000_000

# ------------------------------------------------------------------ ESP-B ------
#   SPI esclavo
#   GPIO 18 SCK
#   GPIO 19 MISO
#   GPIO 23 MOSI
#   GPIO 5  CS
#
#   I2C hacia el LCD (backpack PCF8574)
#   GPIO 21 SDA
#   GPIO 22 SCL
#
#   LED de actividad
#   GPIO 2  LED
PINES_SPI_B = {"sck": 18, "miso": 19, "mosi": 23, "cs": 5}
PINES_I2C_B = {"sda": 21, "scl": 22}
PIN_LED_B = 2
SPI_MODO_B = 1
SPI_BAUDS_B = 1_000_000

DIRECCION_I2C_LCD = 0x27            # backpack PCF8574 estandar, jumperes en 0
DIRECCION_I2C_EEPROM = 0x57          # 24C32 opcional, para probar ruteo de direcciones
FRECUENCIA_I2C = 400_000

# ------------------------------------------------------------------ LCD --------
LCD_COLUMNAS = 16
LCD_FILAS = 2
# Mapeo del backpack PCF8574 tal cual viene en el modulo: los 4 bits altos
# alimentan D4-D7 del HD44780 y los 4 bajos son RS, RW, EN y backlight.
LCD_RS = 0x01
LCD_RW = 0x02
LCD_EN = 0x04
LCD_LUZ = 0x08

# ------------------------------------------------------------- vision ---------
TAM_IMAGEN = 20                      # lado de la entrada de la CNN, en pixeles
CONFIANZA_MINIMA = 55                # por debajo de esto se considera "no hay digito"
MS_ENTRE_DISPAROS = 60               # 16 detecciones por segundo como maximo

# ------------------------------------------------------------- CNN ------------
RUTA_PESOS = "vision/cnn/pesos.npz"
FORMAS_CNN = ((1, TAM_IMAGEN, TAM_IMAGEN), (8, 10, 10), (16, 5, 5), 64, 10)
