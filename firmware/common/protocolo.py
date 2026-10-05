"""
Protocolo de la cadena PC -> ESP-A (UART) -> ESP-B (SPI) -> LCD (I2C).

Este es EL archivo del protocolo. Vive en `firmware/common/` porque asi es como
viaja a la placa:

    mpremote cp firmware/common/protocolo.py :protocolo.py

Y la PC lo carga con `nucleo/protocolo.py`, que solo lo reexporta. Un unico
sitio con el formato de la trama: si hubiera dos copias, el firmware acabaria
serializando un campo en un orden distinto del que deserializa la PC, y eso no
falla nunca: falla con un digito equivocado una vez cada mil.

POR QUE BINARIO Y NO TEXTO
--------------------------
Por el SPI pasan 16 bytes por digito. A 115200 baud un JSON de 40 caracteres
tardaria 3.5 ms, y el LCD no necesita nada mas que el numero. Ademas el HD44780
solo sabe imprimir caracteres entre 0x20 y 0x7E, asi que cualquier byte alto en
la trama acabaria mostrando basura en pantalla si se imprimiera entero.

LA TRAMA
--------
    offset 0   0xA5     marca de inicio (SOF)
    offset 1   0x5A     segunda marca: filtra los bytes de relleno
    offset 2   LEN      longitud del campo DATOS, de 0 a 64
    offset 3   TIPO     0x10 digito, 0x20 acuse, 0x30 control
    offset 4   DATOS    LEN bytes, ver la tabla de cada TIPO
    ultimos 2  CRC16    CCITT-FALSE sobre LEN, TIPO y DATOS, big endian

    longitud total = LEN + 6

TABLA DE DATOS POR TIPO
-----------------------
    0x10 DIGITO   SEQ u16 | DIGITO u8 | CONF u8 | FUENTE u8 | MS u32   = 9 B
    0x20 ACUSE    SEQ u16 | CODIGO u8                                     = 3 B
                  CODIGO: 0x00 aceptado, 0x01 digito invalido, 0x02 crc malo
    0x30 CONTROL  ORDEN u8 | ARG u16                                     = 3 B
                  ORDEN: 0x01 reiniciar el LCD, 0x02 mostrar un mensaje

    longitudes de trama: digito 15, acuse 9, control 9

POR QUE SOF DE DOS BYTES
------------------------
Con un solo byte de SOF, cualquier 0xA5 que aparezca dentro de los datos de una
tramaAlignment lo dejaria desalineado para siempre. Con dos, hace falta que
aparezcan 0xA5 seguido de 0x5A, y como los datos empiezan por un LEN pequeno
(< 0x40) eso no ocurre nunca por casualidad.

SIN DEPENDENCIAS
---------------
Solo `struct`, que existe en CPython y en MicroPython. Sin anotaciones de tipo,
sin `from __future__`, sin f-strings con especificacion de formato: nada de lo
que hay aqui necesita un microcontrolador con compilador de C.
"""

import struct

# ---------------------------------------------------------------- constantes ---

SOF0 = 0xA5
SOF1 = 0x5A
CABECERA = 4          # SOF0, SOF1, LEN, TIPO
COLA = 2             # CRC16
SOBRA = CABECERA + COLA

LEN_MAX = 64
TAMAÑO_TRAMA = SOBRA + LEN_MAX      # 70, tope del buffer circular del UART

TIPO_DIGITO = 0x10
TIPO_ACUSE = 0x20
TIPO_CONTROL = 0x30

ORDEN_REINICIAR = 0x01
ORDEN_MENSAJE = 0x02

ACUSE_OK = 0x00
ACUSE_DIGITO_INVALIDO = 0x01
ACUSE_CRC_MALO = 0x02

CODIGO_ACUSE = {0x00: "OK", 0x01: "DIGITO INVALIDO", 0x02: "CRC MALO"}

# Empaquetado de DATOS por tipo. '<' es little endian, que es el nativo del
# ESP32 y de x86, asi que desempaquetar no cuesta nada.
#
# OJO CON EL FORMATO DEL DIGITO: los tres campos de en medio son UN byte cada uno
# (`B`), no cuatro. Poner `I` en `FUENTE` hace que la trama mida 18 bytes en vez
# de 16, y no falla nunca por si sola: la ESP-B calcula el CRC sobre los bytes que
# le llegan y el CRC cuadra. Lo unico que se rompe es que la ESP-A, al
# recomponer la trama para reenviarla, mete 4 bytes de mas en el campo equivocado
# y entonces el CRC que produce NO es el que recibio. El sintoma es que la ESP-B
# descarta todas las tramas por CRC incorrecto, sin ninguna otra pista.
#
# Secuencia, digito, confianza, fuente y milisegundos:
#   2 + 1 + 1 + 1 + 4 = 9 bytes de DATOS, 15 de trama.
FORMATO_DIGITO = "<HBBBI"       # SEQ u16 | DIGITO u8 | CONF u8 | FUENTE u8 | MS u32
FORMATO_ACUSE = "<HB"            # 3 bytes
FORMATO_CONTROL = "<BH"          # 3 bytes
TAM_DIGITO = struct.calcsize(FORMATO_DIGITO)
TAM_ACUSE = struct.calcsize(FORMATO_ACUSE)
TAM_CONTROL = struct.calcsize(FORMATO_CONTROL)


class ErrorTrama(ValueError):
    """La trama no cumple el formato.

    Es una excepcion y no un `assert` porque el firmware la captura para
    reportar el fallo por el puerto de diagnostico, y en MicroPython los
    asserts se pueden compilar fuera.
    """


# -------------------------------------------------------------------- CRC-16 ---

def _construir_tabla_crc():
    """Tabla de 256 entradas, calculada una vez al importar el modulo.

    La version con un bit a la vez serian 16 iteraciones por byte. En un ESP32 a
    240 MHz, con el bucle de captura a 16 Hz, son unos 400 microsegundos por
    trama y se notan; con la tabla, un CRC de 14 bytes cuesta 14 sumas.
    """
    tabla = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
        tabla.append(crc)
    return tuple(tabla)


_TABLA_CRC = _construir_tabla_crc()


def crc16(datos, crc_inicial=0xFFFF):
    """CRC-16/CCITT-FALSE: polinomio 0x1021, semilla 0xFFFF, sin reflect.

    Se eligio este y no el Modbus porque el hardware del ESP32 no lo trae en el
    modulo `binascii`, asi que se implementa a mano en unas lineas y funciona
    igual en las tres maquinas (la PC y las dos ESP).
    """
    if datos is None:
        return crc_inicial & 0xFFFF
    for byte in datos:
        if not isinstance(byte, int):
            byte = ord(byte)
        crc_inicial = _TABLA_CRC[((crc_inicial >> 8) ^ byte) & 0xFF] ^ ((crc_inicial << 8) & 0xFFFF)
    return crc_inicial & 0xFFFF


# --------------------------------------------------------------- empaquetado ---

def empaquetar_digito(seq, digito, confianza, tipo_fuente=0, ms=0):
    """Arma la trama completa de un digito. `confianza` de 0 a 100."""
    if digito < 0 or digito > 9:
        raise ErrorTrama("el digito tiene que estar entre 0 y 9, recibio %r" % (digito,))
    if confianza < 0 or confianza > 100:
        raise ErrorTrama("la confianza tiene que estar entre 0 y 100, recibio %r" % (confianza,))
    datos = struct.pack(FORMATO_DIGITO, seq & 0xFFFF, digito, confianza,
                        tipo_fuente & 0xFF, ms & 0xFFFFFFFF)
    return _envolver(TIPO_DIGITO, datos)


def empaquetar_acuse(seq, codigo=ACUSE_OK):
    """Respuesta de la ESP-B. `seq` es el del digito que responde."""
    datos = struct.pack(FORMATO_ACUSE, seq & 0xFFFF, codigo & 0xFF)
    return _envolver(TIPO_ACUSE, datos)


def empaquetar_control(orden, arg=0):
    datos = struct.pack(FORMATO_CONTROL, orden & 0xFF, arg & 0xFFFF)
    return _envolver(TIPO_CONTROL, datos)


def _envolver(tipo, datos):
    if len(datos) > LEN_MAX:
        raise ErrorTrama("datos de %d bytes, el maximo es %d" % (len(datos), LEN_MAX))
    cuerpo = bytes((len(datos), tipo)) + datos
    crc = crc16(cuerpo)
    return bytes((SOF0, SOF1)) + cuerpo + struct.pack(">H", crc)


# -------------------------------------------------------------- desempaquetado --

def leer_cabecera(cabecera):
    """Busca el SOF y devuelve (resto, tipo, largo).

    TRES VALORES, Y POR QUE NO HAY UN CUARTO
    -----------------------------------------
    `resto` son los bytes que quedan SIN interpretar, a partir del final de la
    cabecera. El llamante tiene que seguirlos, porque las tramas llegan
    partidas y desalignadas por el UART.

    Una version anterior devolvia tambien el cuerpo que ya venia en el buffer.
    Se quito porque NADA lo usaba: los tres llamadores (los dos firmwares y
    `nucleo/traza.py`) trabajan sobre una COPIA del buzon y deciden cuanto queda
    mirando cuantos bytes hay, en lugar de consumir y devolver. Un cuarto valor
    que nadie lee es una trampa para el proximo que lo escriba y se equate a que
    hace falta.

    `largo = 0` significa que no hay cabecera completa todavia.

    La comprobacion de "hay cabecera" es de 4 bytes y no de 3, porque el cuarto es
    TIPO: con solo 3 disponibles, `pendiente[3]` es un IndexError, y en la ESP32
    eso no lanza una excepcion recuperable, mata el hilo entero y la placa deja de
    responder sin decir por que.
    """
    pendiente = bytearray(cabecera)
    while len(pendiente) >= 2:
        if pendiente[0] == SOF0 and pendiente[1] == SOF1:
            if len(pendiente) < 4:
                return bytes(pendiente), 0, 0    # falta TIPO
            largo = pendiente[2]
            tipo = pendiente[3]
            del pendiente[:4]
            return bytes(pendiente), tipo, largo
        del pendiente[0]
    # No hay SOF. Puede quedar un 0xA5 suelto al final, que es el primer byte de
    # un SOF partido entre dos lecturas; se conserva por si el 0x5A llega luego.
    if pendiente and pendiente[-1] == SOF0:
        return bytes(pendiente[-1:]), 0, 0
    return b"", 0, 0


def validar(cuerpo, tipo, largo):
    """Comprueba el CRC de un cuerpo y devuelve los DATOS.

    `cuerpo` son los bytes que quedan DESPUES de los 4 de cabecera, o sea
    DATOS + CRC: `largo + COLA` bytes. LEN y TIPO ya se consumieron al leer la
    cabecera, asi que no estan aqui.

    PERO el CRC protege LEN y TIPO tambien, y para recalcularlo hay que
    reconstruirlos delante. Por eso la funcion los vuelve a poner:
    `_envolver` calculo el CRC sobre LEN + TIPO + DATOS, y aqui hay que calcular
    exactamente sobre lo mismo o el valor no saldra igual y todas las tramas se
    rechazarian.

    Ojo con esto, porque es la clase de error que no se ve: si se calculara solo
    sobre DATOS, el CRC no cuadraria con ninguna trama que hubiera creado esta
    misma funcion, y la unica pista seria "CRC incorrecto" en todo.
    """
    if len(cuerpo) != largo + COLA:
        raise ErrorTrama("cuerpo de %d bytes, se esperaban %d"
                         % (len(cuerpo), largo + COLA))
    recibido = struct.unpack(">H", bytes(cuerpo[largo:largo + COLA]))[0]

    # Se reconstruye lo que el CRC protege: LEN + TIPO + DATOS.
    protegido = bytes((largo, tipo)) + bytes(cuerpo[:largo])
    if crc16(protegido) != recibido:
        raise ErrorTrama("CRC incorrecto")
    return bytes(cuerpo[:largo])


def desempaquetar_digito(datos):
    if len(datos) != TAM_DIGITO:
        raise ErrorTrama("el cuerpo del digito mide %d, deberia medir %d" % (len(datos), TAM_DIGITO))
    seq, digito, confianza, fuente, ms = struct.unpack(FORMATO_DIGITO, datos)
    return {"seq": seq, "digito": digito, "confianza": confianza,
            "fuente": fuente, "ms": ms}


def desempaquetar_acuse(datos):
    if len(datos) != TAM_ACUSE:
        raise ErrorTrama("el cuerpo del acuse mide %d, deberia medir %d" % (len(datos), TAM_ACUSE))
    seq, codigo = struct.unpack(FORMATO_ACUSE, datos)
    return {"seq": seq, "codigo": codigo,
            "texto": CODIGO_ACUSE.get(codigo, "DESCONOCIDO")}


def desempaquetar_control(datos):
    if len(datos) != TAM_CONTROL:
        raise ErrorTrama("el cuerpo del control mide %d, deberia medir %d" % (len(datos), TAM_CONTROL))
    orden, arg = struct.unpack(FORMATO_CONTROL, datos)
    return {"orden": orden, "arg": arg}


DESEMPAQUETADORES = {
    TIPO_DIGITO: desempaquetar_digito,
    TIPO_ACUSE: desempaquetar_acuse,
    TIPO_CONTROL: desempaquetar_control,
}


def describir_trama(trama):
    """Version legible para el log. Si la trama esta rota lo dice, no revienta."""
    trama = bytes(trama)
    if len(trama) < CABECERA:
        return "fragmento de %d bytes" % (len(trama),)
    try:
        largo = trama[2]
        tipo = trama[3]
        cuerpo = validar(trama[CABECERA:], tipo, largo)
    except ErrorTrama as exc:
        return "trama invalida: %s" % (exc,)

    if tipo == TIPO_DIGITO:
        d = desempaquetar_digito(cuerpo)
        return "DIGITO seq=%d n=%d conf=%d%%" % (d["seq"], d["digito"], d["confianza"])
    if tipo == TIPO_ACUSE:
        d = desempaquetar_acuse(cuerpo)
        return "ACUSE seq=%d %s" % (d["seq"], d["texto"])
    if tipo == TIPO_CONTROL:
        d = desempaquetar_control(cuerpo)
        return "CONTROL orden=%d arg=%d" % (d["orden"], d["arg"])
    return "TIPO 0x%02X de %d bytes" % (tipo, largo)
