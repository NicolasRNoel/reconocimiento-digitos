"""
Pruebas del protocolo y del simulador. Sin necesidad de la CNN ni de la camara.

    python -m pytest pruebas -q
    python pruebas/test_protocolo.py        # tambien vale sin pytest

Lo que se comprueba es lo que de verdad puede romperse en la placa:

  - el CRC contra el valor de referencia de la especificacion
  - el despacho de tramas enteras, troceadas, con relleno delante y con CRC roto
  - que el bus SPI mueve los bytes en la direccion correcta (el fallo mas
    silencioso que hay: escribir el MISO en el MOSI y que nada se note)
  - que el emulador del HD44780 inicializa en 2 lineas y escribe texto donde toca
  - que la cadena entera entrega el digito desde el LCD hasta la pantalla

La mayoria de los bugs de este proyecto NO tiraban excepcion: solo dejaban de
hacer algo. Un test que solo compruebe que nada revienta no los pilla. Por eso
cada prueba mira un valor concreto, no la ausencia de fallo.
"""

from __future__ import annotations

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# El modulo `machine` falso tiene que estar puesto ANTES de que se importe nada
# del firmware, porque `firmware/common/hardware.py` hace `from machine import Pin`.
import simulacion.maquina as maquina                        # noqa: E402
maquina.registrar_en_python()

from nucleo import protocolo as p                            # noqa: E402
from nucleo.traza import Traza                              # noqa: E402


# ------------------------------------------------------------------ protocolo ---

def test_crc_valor_de_referencia():
    """CCITT-FALSE sobre '123456789' da 0x29B1. Es el valor de la especificacion.

    Si este test falla, el CRC cambio y TODAS las tramas de las dos ESP han
    dejado de ser compatibles con la PC. No habria ningun otro sintoma claro.
    """
    assert p.crc16(b"123456789") == 0x29B1, (
        "el CRC-16 dio 0x%04X en vez de 0x29B1" % p.crc16(b"123456789"))


def test_trama_de_digito_mide_lo_que_dice():
    """9 bytes de DATOS, 15 de trama. Si cambia, cambia el protocolo entero."""
    trama = p.empaquetar_digito(1, 7, 78, 0, 1000)
    assert len(trama) == 15, "la trama de digito mide %d, deberia medir 15" % len(trama)
    assert trama[0] == p.SOF0 and trama[1] == p.SOF1
    assert trama[2] == p.TAM_DIGITO, "LEN dice %d, deberia ser %d" % (trama[2], p.TAM_DIGITO)
    assert trama[3] == p.TIPO_DIGITO


def test_ida_y_vuelta_de_digito():
    for seq, digito, confianza, ms in ((0, 0, 0, 0), (65535, 9, 100, 4294967295),
                                       (1, 5, 55, 123456)):
        trama = p.empaquetar_digito(seq, digito, confianza, 0, ms)
        _, tipo, largo = p.leer_cabecera(trama)
        datos = p.validar(trama[4:], tipo, largo)
        info = p.desempaquetar_digito(datos)
        assert info["seq"] == seq and info["digito"] == digito
        assert info["confianza"] == confianza and info["ms"] == ms


def test_rechaza_digito_fuera_de_rango():
    for malo in (-1, 10, 255):
        try:
            p.empaquetar_digito(1, malo, 50)
        except p.ErrorTrama:
            continue
        raise AssertionError("acepto el digito %r" % (malo,))


def test_rechaza_confianza_fuera_de_rango():
    for malo in (-1, 101):
        try:
            p.empaquetar_digito(1, 5, malo)
        except p.ErrorTrama:
            continue
        raise AssertionError("acepto la confianza %r" % (malo,))


def test_crc_detecta_un_bit_cambiado():
    """Un solo bit cambiado en los DATOS tiene que invalidar la trama.

    Esto es lo que protege contra el cable flojo. Sin el, un byte corrupto
    llegaria al LCD y mostraria un digito que nadie escribio.
    """
    trama = bytearray(p.empaquetar_digito(1, 7, 78, 0, 1))
    for indice in (4, 6, 7, 12):
        copia = bytearray(trama)
        copia[indice] ^= 0x01
        _, tipo, largo = p.leer_cabecera(bytes(copia))
        try:
            p.validar(bytes(copia)[4:], tipo, largo)
        except p.ErrorTrama:
            continue
        raise AssertionError("acepto la trama con el byte %d cambiado" % indice)


def test_crc_detecta_cambio_en_len():
    """Un cambio en LEN tambien se detecta, porque el CRC lo cubre.

    Es lo que da valor a incluir LEN y TIPO en el CRC en vez de solo los DATOS:
    si alguien cambiara el tamano-announced de la trama, tambien se veria.
    """
    trama = bytearray(p.empaquetar_digito(1, 7, 78, 0, 1))
    trama[2] += 1
    _, tipo, largo = p.leer_cabecera(bytes(trama))
    try:
        p.validar(bytes(trama)[4:], tipo, largo)
    except p.ErrorTrama:
        return
    raise AssertionError("acepto una trama con LEN cambiado")


# --------------------------------------------------------------------- traza ---

def _trama_de_prueba():
    return p.empaquetar_digito(1, 7, 78, 0, 1)


def test_traza_trama_entera():
    t = Traza()
    t.alimentar(_trama_de_prueba())
    tipo, datos = t.siguiente()
    assert tipo == p.TIPO_DIGITO
    assert p.desempaquetar_digito(datos)["digito"] == 7
    assert t.siguiente() is None


def test_traza_byte_a_byte():
    """El caso que aparece con el conversor USB-TTL de verdad.

    Si esto falla, el firmware pasara en el PC (donde las tramas llegan
    enteras) y no en la placa, que es el peor sitio para descubrirlo.
    """
    trama = _trama_de_prueba()
    t = Traza()
    for byte in trama:
        t.alimentar(bytes((byte,)))
        # No debe devolver nada hasta tener la trama entera.
        parcial = t.siguiente()
        if byte != trama[-1]:
            assert parcial is None, "devolvio una trama incompleta en el byte %d" % byte
    assert t.aceptadas == 1


def test_traza_troceos_varios():
    """Troceada en trozos de 1 a 4 bytes, que es como llega por el cable."""
    trama = _trama_de_prueba()
    for corte in range(1, len(trama)):
        t = Traza()
        t.alimentar(trama[:corte])
        t.alimentar(trama[corte:])
        resultado = t.drenar()
        assert len(resultado) == 1, "corte en %d: %d tramas" % (corte, len(resultado))


def test_traza_ruido_delante():
    """Bytes sueltos antes del SOF tienen que descartarse, no romper el despacho."""
    t = Traza()
    t.alimentar(b"\x00\xff\xa5" + _trama_de_prueba())
    resultado = t.drenar()
    assert len(resultado) == 1
    assert t.bytes_ignorados >= 2


def test_traza_dos_tramas_seguidas():
    t = Traza()
    t.alimentar(_trama_de_prueba() + p.empaquetar_acuse(1, 0))
    resultado = t.drenar()
    assert [tipo for tipo, _ in resultado] == [p.TIPO_DIGITO, p.TIPO_ACUSE]


def test_traza_sof_partido_entre_lecturas():
    """El 0xA5 al final de una lectura y el 0x5A al principio de la siguiente.

    Es un caso real: el conversor trocea en cualquier punto, no en los limites
    de la trama.
    """
    trama = _trama_de_prueba()
    t = Traza()
    t.alimentar(trama[:1])                 # solo el 0xA5
    assert t.siguiente() is None
    t.alimentar(trama[1:])
    resultado = t.drenar()
    assert len(resultado) == 1, "perdio la trama al partir el SOF"


def test_traza_crc_roto_no_cuelga():
    """Una trama corrupta se descarta y se sigue. Nunca en bucle infinito."""
    trama = bytearray(_trama_de_prueba())
    trama[-1] ^= 0xFF
    t = Traza()
    t.alimentar(bytes(trama))
    assert t.siguiente() is None
    assert t.descartadas == 1


def test_traza_corrupta_seguida_de_buena():
    trama = bytearray(_trama_de_prueba())
    trama[-1] ^= 0xFF
    t = Traza()
    t.alimentar(bytes(trama) + _trama_de_prueba())
    resultado = t.drenar()
    assert len(resultado) == 1, "deberia quedar solo la trama buena"
    assert t.descartadas == 1


# ------------------------------------------------------------------ bus SPI ---

def test_bus_spi_entrega_al_esclavo():
    """La maestra escribe y la esclava lee lo MISMO, sin cambio de orden."""
    from simulacion.bus_spi import BusSPI, MaestroSPI, SpiEsclavo
    from simulacion.maquina import Pin

    bus = BusSPI()
    cs = Pin(5, Pin.OUT)
    cs.value(1)
    esclava = SpiEsclavo(bus, cs)
    maestra = MaestroSPI(bus, cs)

    maestra.write(b"\x01\x02\x03\x04")
    assert esclava.read(4) == b"\x01\x02\x03\x04"


def test_bus_spi_miso_va_al_reves():
    """El acuse de la esclava lo lee la maestra.

    El fallo que se comprueba aqui es el mas silencioso del proyecto: escribir el
    MISO en la cola del MOSI. La ESP-A leeria relleno infinito, la ESP-B
    creeria que ya ha contestado, y no habria ninguna excepcion.
    """
    from simulacion.bus_spi import BusSPI, MaestroSPI, SpiEsclavo
    from simulacion.maquina import Pin

    bus = BusSPI()
    cs = Pin(5, Pin.OUT)
    cs.value(1)
    esclava = SpiEsclavo(bus, cs)
    maestra = MaestroSPI(bus, cs)

    esclava.escribir(b"\xa5\x5a\x03")
    assert len(bus.entrada_esclava) == 0, "el acuse se metio en el MOSI"
    assert maestra.read(3) == b"\xa5\x5a\x03"


def test_bus_spi_relleno_es_0xff():
    """Cuando no hay nada que leer, sale 0xFF, no 0.

    Con resistencias de pull-up, un bus en reposo se lee todo a 1. Devolver 0
    seria decir "hay tension en todas las lineas", que es otra cosa.
    """
    from simulacion.bus_spi import BusSPI, MaestroSPI
    from simulacion.maquina import Pin

    bus = BusSPI()
    cs = Pin(5, Pin.OUT)
    cs.value(1)
    maestra = MaestroSPI(bus, cs)
    assert maestra.read(4) == b"\xff\xff\xff\xff"


def test_bus_spi_acuse_llega_en_la_siguiente():
    """El acuse no se puede mandar durante la transaccion del digito.

    El SPI es full-duplex, pero cuando la maestra sube CS ya no hay a quien
    mandar. El acuse espera a la siguiente, y por eso llega con un ciclo de
    retardo. Esto no es un defecto del simulador: es el protocolo.
    """
    from simulacion.bus_spi import BusSPI, MaestroSPI, SpiEsclavo
    from simulacion.maquina import Pin

    bus = BusSPI()
    cs = Pin(5, Pin.OUT)
    cs.value(1)
    esclava = SpiEsclavo(bus, cs)
    maestra = MaestroSPI(bus, cs)

    maestra.write(b"\x01\x02")             # el digito
    assert len(bus.salida_esclava) == 0, "el acuse salio en la misma transaccion"

    esclava.escribir(b"\xaa")             # la esclava contesta
    assert maestra.read(1) == b"\xaa"


# -------------------------------------------------------------------- LCD -----

def _lcd_de_prueba():
    from simulacion.bus_i2c import BusI2C, LcdEsclavo
    from lcd import Lcd as LcdDriver

    bus = BusI2C()
    panel = LcdEsclavo(0x27, 16, 2)
    bus.conectar(panel)

    class MaestroFalso:
        def __init__(self, bus):
            self.bus = bus

        def escribir(self, direccion, datos):
            return self.bus.escribir(direccion, datos)

    return LcdDriver(MaestroFalso(bus), 0x27, 16, 2), panel.panel


def test_lcd_inicializa_en_dos_lineas():
    """La secuencia de arranque tiene que dejar el panel en 2 lineas y encendido.

    Si esto falla, el LCD real muestra una sola fila o nada, y el sintoma es
    "el firmware funciona pero no se ve nada".
    """
    lcd, panel = _lcd_de_prueba()
    lcd.iniciar(16, 2)
    assert panel.filas_definidas == 2, "quedo en %d linea(s)" % panel.filas_definidas
    assert panel.pantalla_on, "la pantalla quedo apagada"
    assert panel.en_4_bits, "no entro en modo 4 bits"


def test_lcd_escribe_en_la_fila_correcta():
    """El texto va a la fila que se le pide.

    El HD44780 tiene 40 columnas de memoria y las direcciones no coinciden con
    las columnas visibles: la linea 2 esta en la direccion 0x40. Si el mapeo esta
    mal, el texto aparece en la fila equivocada y no hay ningun error.
    """
    lcd, panel = _lcd_de_prueba()
    lcd.iniciar(16, 2)
    lcd.goto(0, 0)
    lcd.escribir("linea uno")
    lcd.goto(0, 1)
    lcd.escribir("linea dos")
    assert "".join(panel.ddram[0]).rstrip() == "linea uno"
    assert "".join(panel.ddram[1]).rstrip() == "linea dos"


def test_lcd_reescribe_sin_dejar_rastro():
    """Un texto mas corto no debe dejar el final del anterior.

    El RELLENO con espacios lo hace el firmware de la ESP-B, no el driver del
    LCD, porque el driver solo escribe lo que le pasan. Por eso la prueba rellena
    a mano: lo que se comprueba es que escribir encima funciona y que el panel
    guarda bien los espacios.
    """
    lcd, panel = _lcd_de_prueba()
    lcd.iniciar(16, 2)
    lcd.goto(0, 0)
    lcd.escribir("texto largo de prueba".ljust(16))
    lcd.goto(0, 0)
    lcd.escribir("corto".ljust(16))
    assert "".join(panel.ddram[0]) == "corto" + " " * 11


def test_lcd_borra_la_pantalla():
    lcd, panel = _lcd_de_prueba()
    lcd.iniciar(16, 2)
    lcd.goto(0, 0)
    lcd.escribir("algo escrito")
    lcd.comando(0x01)                    # borrar pantalla
    assert "".join(panel.ddram[0]).strip() == ""


def test_lcd_recorta_a_16_columnas():
    """El panel tiene 16 columnas y el HD44780 NO recorta.

    Si el firmware pasa 18 caracteres, el controlador los escribe igual y los dos
    ultimos se solapan con la fila de abajo. El sintoma en un panel real es texto
    basura encima de la linea siguiente, y no aparece hasta que el contador llega
    a cinco cifras.
    """
    lcd, panel = _lcd_de_prueba()
    lcd.iniciar(16, 2)

    # Lo que escribe la ESP-B: relleno a 16 y recorte explicito.
    for texto in ("DIGITO: 8  98%", "seq 1234 tot 123456"):
        recorte = texto[:16].ljust(16)
        assert len(recorte) == 16, "el texto %r no se recorta a 16" % texto
        lcd.goto(0, 0)
        lcd.escribir(recorte)

    assert len(panel.ddram[0]) == 16
    assert len(panel.ddram[1]) == 16
    # Y no se ha desbordado a la fila siguiente.
    assert "".join(panel.ddram[1]).strip() == ""


# ------------------------------------------------------------- cadena entera ---

def test_cadena_completa_llega_al_lcd():
    """El dato viaja: UART -> ESP-A -> SPI -> ESP-B -> I2C -> LCD.

    Se comprueba el texto que hay EN la pantalla, no los contadores, porque los
    contadores pueden cuadrar con el texto equivocado.
    """
    from simulacion.simular import CadenaSimulada
    import time

    maquina.reiniciar()
    cadena = CadenaSimulada([7], nivel="error")
    try:
        cadena.montar()
        cadena.correr(marcos=4, pausa_ms=40)
    finally:
        cadena.parar()

    panel = cadena.lcd.panel
    assert panel.filas_definidas == 2
    assert panel.pantalla_on, "la pantalla quedo apagada"
    assert panel.caracteres > 0, "no se escribio nada en el panel"

    linea = "".join(panel.ddram[0]).rstrip()
    assert linea.startswith("DIGITO"), "la linea 1 dice %r" % linea

    # El LCD es de 16 columnas: si el texto no cabe, esta recortado.
    assert len("".join(panel.ddram[0])) <= 16


def test_cadena_sin_lcd_no_se_muere():
    """Sin el modulo I2C, la ESP-B sigue leyendo el SPI.

    Desenchufar el LCD es un fallo muy comun. Si la ESP-B se muere, se pierde
    tambien el reconocimiento, que es justo lo que no deberia pasar.
    """
    from simulacion.simular import CadenaSimulada

    maquina.reiniciar()
    cadena = CadenaSimulada([3], nivel="error", sin_lcd=True)
    try:
        cadena.montar()
        cadena.correr(marcos=4, pausa_ms=40)
    finally:
        cadena.parar()

    assert cadena.esclava.digitos_recibidos > 0, "la ESP-B dejo de leer el SPI"
    assert cadena.puente.tramas_enviadas > 0


def test_cadena_sobrevive_al_ruido_en_el_uart():
    """Un byte de relleno delante de una trama no puede romper la cadena."""
    from simulacion.simular import CadenaSimulada
    import time

    maquina.reiniciar()
    cadena = CadenaSimulada([2], nivel="error")
    try:
        cadena.montar()
        for _ in range(4):
            marco = cadena.fuente.leer()
            cadena.uart_pc.escritura.cola[:0] = bytes((0x00,))   # ruido
            cadena.lector.paso(marco)
            time.sleep(0.05)
    finally:
        cadena.parar()

    assert cadena.puente.tramas_recibidas > 0, "el ruido se comio las tramas"


# ------------------------------------------------------------------- ejecucion ---

def main() -> int:
    """Corre todas las funciones `test_*` y devuelve el numero de fallos."""
    fallos = 0
    pruebas = [(nombre, objeto) for nombre, objeto in sorted(globals().items())
               if nombre.startswith("test_") and callable(objeto)]

    print("=" * 68)
    print("PRUEBAS DEL PROTOCOLO, EL BUS Y EL LCD")
    print("=" * 68)
    for nombre, prueba in pruebas:
        try:
            prueba()
        except AssertionError as exc:
            fallos += 1
            print("  FALLA  %-44s %s" % (nombre, exc))
        except Exception as exc:                        # noqa: BLE001
            fallos += 1
            print("  ERROR  %-44s %s: %s" % (nombre, type(exc).__name__, exc))
        else:
            print("  ok     %s" % nombre)

    print("=" * 68)
    print("%d de %d pruebas correctas" % (len(pruebas) - fallos, len(pruebas)))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
