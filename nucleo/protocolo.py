"""
El protocolo, del lado de la PC.

No hay ninguna implementacion aqui: todo sale de `firmware/common/protocolo.py`,
que es el archivo que se copia a las ESP con `mpremote cp`. Este modulo solo lo
carga y le pone los tipos, que son cosas que MicroPython no necesita y que en la
PC aidsan a que un error de nombre se vea al escribir el codigo.

POR QUE UN SOLO ARCHIVO Y NO UN PAQUETE COMPARTIDO
--------------------------------------------------
MicroPython importa modulos de archivos planos en la raiz de la flash, sin
paquetes ni rutas. Un `from nucleo.protocolo import ...` no funciona ahi, y un
`sys.path` con subdirectorios en la ESP es un rodeo que en cuanto se actualiza el
firmware se rompe. La solucion que no da problemas es que el archivo sea
autonomo y que se copie tal cual a las dos placas.

    mpremote cp firmware/common/protocolo.py :protocolo.py

La PC carga ese mismo archivo por ruta, asi que no puede haber dos versiones
divergentes. Si las hubiera, el sintoma seria un digito equivocado una vez de
cada mil y un CRC que "a veces no cuadra", que es el peor tipo de fallo posible.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Optional, Union

RUTA_PROTOCOLO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "firmware", "common", "protocolo.py")


def _cargar():
    especificacion = importlib.util.spec_from_file_location("_protocolo_pc", RUTA_PROTOCOLO)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


_p = _cargar()

# Constantes
SOF0 = _p.SOF0
SOF1 = _p.SOF1
CABECERA = _p.CABECERA
COLA = _p.COLA
SOBRA = _p.SOBRA
LEN_MAX = _p.LEN_MAX
TAMAÑO_TRAMA = _p.TAMAÑO_TRAMA

TIPO_DIGITO = _p.TIPO_DIGITO
TIPO_ACUSE = _p.TIPO_ACUSE
TIPO_CONTROL = _p.TIPO_CONTROL

ORDEN_REINICIAR = _p.ORDEN_REINICIAR
ORDEN_MENSAJE = _p.ORDEN_MENSAJE

ACUSE_OK = _p.ACUSE_OK
ACUSE_DIGITO_INVALIDO = _p.ACUSE_DIGITO_INVALIDO
ACUSE_CRC_MALO = _p.ACUSE_CRC_MALO
CODIGO_ACUSE = _p.CODIGO_ACUSE

FORMATO_DIGITO = _p.FORMATO_DIGITO
FORMATO_ACUSE = _p.FORMATO_ACUSE
FORMATO_CONTROL = _p.FORMATO_CONTROL
TAM_DIGITO = _p.TAM_DIGITO
TAM_ACUSE = _p.TAM_ACUSE
TAM_CONTROL = _p.TAM_CONTROL

ErrorTrama = _p.ErrorTrama

# Funciones
crc16 = _p.crc16
empaquetar_digito = _p.empaquetar_digito
empaquetar_acuse = _p.empaquetar_acuse
empaquetar_control = _p.empaquetar_control
leer_cabecera = _p.leer_cabecera
validar = _p.validar
desempaquetar_digito = _p.desempaquetar_digito
desempaquetar_acuse = _p.desempaquetar_acuse
desempaquetar_control = _p.desempaquetar_control
describir_trama = _p.describir_trama
DESEMPAQUETADORES = _p.DESEMPAQUETADORES

TRAZA_DIGITO = 18        # LEN(10) + 6, el tamano de una trama de digito


def trama_completa(tipo: int, datos: bytes) -> bytes:
    """Reconstruye una trama a partir de su tipo y sus DATOS.

    La ESP-A la usa para reenviar por SPI lo que recibio por UART sin tener que
    deserializar y volver a serializar: el dato pasa de byte a byte. Es la
    diferencia entre una pasarela que puede corromper algo y una que no.
    """
    return _p._envolver(tipo, datos)


def comprobar_integridad() -> str:
    """El CRC contra una referencia conocida, para descartar que el archivo
    canonico se haya editado mal.

    '123456789' con el CCITT-FALSE da 0x29B1. Es el valor de referencia de la
    especificacion, asi que si el resultado cambia, el CRC ha cambiado y todas
    las tramas de las dos ESP habran dejado de ser compatibles con la PC.
    """
    valor = crc16(b"123456789")
    if valor != 0x29B1:
        raise AssertionError(
            "el CRC-16 no da el valor de referencia 0x29B1, dio 0x%04X. "
            "Se ha modificado la tabla o el algoritmo del protocolo." % valor)
    return "CRC-16 CCITT-FALSE correcto (0x29B1 sobre '123456789')"


__all__ = [nombre for nombre in dir() if not nombre.startswith("_")]
