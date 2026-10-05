

import struct

# ---------------------------------------------------------------- constantes ---

SOF0 = 0xA5
SOF1 = 0x5A
CABECERA = 4         
COLA = 2             
SOBRA = CABECERA + COLA

LEN_MAX = 64
TAMAÑO_TRAMA = SOBRA + LEN_MAX      

TIPO_DIGITO = 0x10
TIPO_ACUSE = 0x20
TIPO_CONTROL = 0x30

ORDEN_REINICIAR = 0x01
ORDEN_MENSAJE = 0x02

ACUSE_OK = 0x00
ACUSE_DIGITO_INVALIDO = 0x01
ACUSE_CRC_MALO = 0x02

CODIGO_ACUSE = {0x00: "OK", 0x01: "DIGITO INVALIDO", 0x02: "CRC MALO"}


FORMATO_DIGITO = "<HBBBI"       
FORMATO_ACUSE = "<HB"           
FORMATO_CONTROL = "<BH"       
TAM_DIGITO = struct.calcsize(FORMATO_DIGITO)
TAM_ACUSE = struct.calcsize(FORMATO_ACUSE)
TAM_CONTROL = struct.calcsize(FORMATO_CONTROL)


class ErrorTrama(ValueError):
   



def _construir_tabla_crc():
   
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
  
    if datos is None:
        return crc_inicial & 0xFFFF
    for byte in datos:
        if not isinstance(byte, int):
            byte = ord(byte)
        crc_inicial = _TABLA_CRC[((crc_inicial >> 8) ^ byte) & 0xFF] ^ ((crc_inicial << 8) & 0xFFFF)
    return crc_inicial & 0xFFFF




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
   
    if pendiente and pendiente[-1] == SOF0:
        return bytes(pendiente[-1:]), 0, 0
    return b"", 0, 0


def validar(cuerpo, tipo, largo):
   
    if len(cuerpo) != largo + COLA:
        raise ErrorTrama("cuerpo de %d bytes, se esperaban %d"
                         % (len(cuerpo), largo + COLA))
    recibido = struct.unpack(">H", bytes(cuerpo[largo:largo + COLA]))[0]

   
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
