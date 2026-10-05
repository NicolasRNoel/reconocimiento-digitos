

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
   
    return _p._envolver(tipo, datos)


def comprobar_integridad() -> str:
    
    valor = crc16(b"123456789")
    if valor != 0x29B1:
        raise AssertionError(
            "el CRC-16 no da el valor de referencia 0x29B1, dio 0x%04X. "
            "Se ha modificado la tabla o el algoritmo del protocolo." % valor)
    return "CRC-16 CCITT-FALSE correcto (0x29B1 sobre '123456789')"


__all__ = [nombre for nombre in dir() if not nombre.startswith("_")]
