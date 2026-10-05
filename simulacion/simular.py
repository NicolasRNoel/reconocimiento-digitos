"""
El simulador: las cuatro etapas en un solo proceso.

    python -m simulacion.simular --marcos 30
    python -m simulacion.simular --digitos 7 --marcos 10 --lento
    python -m simulacion.simular --corrupcion 0.2

QUE SE EJECUTA
--------------
    1. la PC       pc/app.py             camara sintetica -> CNN -> UART
    2. la ESP-A    firmware/esp_a/main   UART -> SPI maestra
    3. la ESP-B    firmware/esp_b/main   SPI esclava -> I2C -> LCD
    4. el LCD      simulacion/lcd.py     HD44780 virtual

Cada etapa corre en su propio hilo y solo se comunican por los buses. No hay
ningun atajo: si el hilo de la ESP-A no baja CS, la ESP-B no ve nada, igual que
en la placa.

POR QUE SE IMPORTA EL FIRMWARE Y NO SE REESCRIBE
------------------------------------------------
Si el simulador ejecutara una copia "simplificada" del firmware, estaria
validando esa copia. Los fallos apareceran al montar la placa, y seran
exactamente los que la simulacion no puede ver: el CS que aqui no importa, el
`any()` del UART que aqui devuelve lo que le de la gana, un `time.sleep()` que
en la placa no se puede saltar.

Este simulador importa `firmware/esp_a/main.py` y `firmware/esp_b/main.py` con
`importlib`, sin tocarlos, y pone un `machine` falso delante. Los mismos archivos
se copian a la ESP con `mpremote cp`.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import threading
import time
import traceback

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# `machine` tiene que existir ANTES de que se importe `firmware/common/hardware.py`,
# porque ese modulo hace `from machine import Pin`. Este es el unico punto del
# proyecto donde se sustituye un modulo de MicroPython, y por eso esta aqui y no
# dentro del propio modulo de hardware.
import simulacion.maquina as maquina                        # noqa: E402
maquina.registrar_en_python()

import configuracion                                         # noqa: E402
from nucleo.traza import Traza                              # noqa: E402
from pc import app                                          # noqa: E402
from simulacion.bus_i2c import BusI2C, LcdEsclavo           # noqa: E402
from simulacion.bus_spi import BusSPI                       # noqa: E402
from simulacion.lcd import LcdTexto                         # noqa: E402
from vision.cnn.modelo import DigitCNN                       # noqa: E402

COMUN = os.path.join(RAIZ, "firmware", "common")
if COMUN not in sys.path:
    sys.path.insert(0, COMUN)


# ------------------------------------------------------------------ registro ---

NIVELES = {"debug": 0, "info": 1, "aviso": 2, "error": 3}


class Registro:
    """Log con marca de tiempo y origen, protegido con un candado.

    Lo escriben cuatro hilos a la vez, asi que el candado no es opcional: sin el,
    dos lineas se mezclan a mitad de escribir y el log es ilegible.
    """

    def __init__(self, nivel: str = "info") -> None:
        self.nivel = NIVELES.get(nivel, 1)
        self.candado = threading.Lock()
        self.lineas = []
        self.t0 = time.time()

    def _escribir(self, origen: str, nivel: str, texto: str) -> None:
        if NIVELES.get(nivel, 1) < self.nivel:
            return
        ms = int((time.time() - self.t0) * 1000)
        linea = "[%7d ms] [%-5s] %s" % (ms, str(origen).upper(), texto)
        with self.candado:
            self.lineas.append(linea)
            print(linea, flush=True)

    def debug(self, origen, texto):
        self._escribir(origen, "debug", texto)

    def info(self, origen, texto):
        self._escribir(origen, "info", texto)

    def aviso(self, origen, texto):
        self._escribir(origen, "aviso", texto)

    def error(self, origen, texto):
        self._escribir(origen, "error", texto)

    # La convencion del proyecto: el callback de log siempre recibe
    # (origen, texto). Todos los constructores lo guardan tal cual.
    def __call__(self, origen, texto):
        self.info(origen, texto)


# ------------------------------------------------------- importar el firmware ---

def cargar_modulo(nombre: str, ruta: str):
    """Importa un archivo con un nombre propio.

    Los dos firmwares se llaman `main.py`. Importarlos con `import main` haria
    que el segundo fuera a buscar el primero en `sys.modules`, y la ESP-B
    ejecutaria el firmware de la ESP-A sin avisar. Con `importlib` y un nombre
    distinto cada uno, no hay forma de que se confundan.
    """
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


# ------------------------------------------------------------- la cadena -------

class CadenaSimulada:
    """Monta las cuatro etapas, las corre y las para. Un objeto, todo."""

    def __init__(self, digitos=None, nivel: str = "info", corromper: float = 0.0,
                 sin_lcd: bool = False) -> None:
        self.registro = Registro(nivel)
        self.corromper = corromper
        self.sin_lcd = sin_lcd
        self.digitos = digitos

        self.ruta_pesos = os.path.join(RAIZ, configuracion.RUTA_PESOS)
        self.modelo = DigitCNN.cargar(self.ruta_pesos)

        # Los buses se crean aqui, y no dentro del firmware, por dos razones:
        # para poder consultar sus contadores al final, y para poder desconectar
        # el LCD cuando se simula que el modulo no esta enchufado.
        self.bus_spi = BusSPI(configuracion.SPI_BAUDS_A)

        self.bus_i2c = BusI2C(velocidad=configuracion.FRECUENCIA_I2C)
        self.lcd = LcdEsclavo(configuracion.DIRECCION_I2C_LCD,
                              configuracion.LCD_COLUMNAS, configuracion.LCD_FILAS)
        self.bus_i2c.conectar(self.lcd)
        if sin_lcd:
            self.bus_i2c.desconectar(configuracion.DIRECCION_I2C_LCD)
            self.registro.aviso("sim", "el modulo I2C esta desconectado: "
                                      "la ESP-B no podra escribir en el LCD")

        # La UART: dos tuberias, una por sentido. Las crea `machine.UART` y se
        # cruzan despues, que es como se Cruzan dos cables.
        self.uart_esp = maquina.UART(1, configuracion.BAUDIOS)
        self.uart_pc = maquina.UART(0, configuracion.BAUDIOS)
        self.uart_esp.lectura = self.uart_pc.escritura
        self.uart_pc.lectura = self.uart_esp.escritura

        self.firmware_a = None
        self.firmware_b = None
        self.puente = None        # la ESP-A
        self.esclava = None       # la ESP-B
        self.pin_cs_b = None
        self.lector = None        # la PC
        self.fuente = None

        self.hilos = []
        self.detener = threading.Event()
        self.arrancados = False
        self.ultimo_estado = {}

    # ------------------------------------------------------------------mount--

    def montar(self) -> "CadenaSimulada":
        # `micro` parchea el modulo `time` de Python con lo que solo existe en
        # MicroPython (ticks_ms, sleep_ms, ticks_diff). Tiene que pasar ANTES de
        # cargar los firmwares, para que puedan usar `time.ticks_ms()` sin una
        # sola linea condicional.
        import micro
        micro.registro.al_escribir = lambda linea: print(linea, flush=True)
        import hardware

        self.firmware_a = cargar_modulo(
            "firmware_esp_a", os.path.join(RAIZ, "firmware", "esp_a", "main.py"))
        self.firmware_b = cargar_modulo(
            "firmware_esp_b", os.path.join(RAIZ, "firmware", "esp_b", "main.py"))

        self._montar_esp_a(hardware)
        self._montar_esp_b(hardware)
        self._compartir_cs()
        self._montar_pc()

        self.hilos = [
            threading.Thread(target=self._hilo_esp_a, name="esp-a", daemon=True),
            threading.Thread(target=self._hilo_esp_b, name="esp-b", daemon=True),
        ]

        # La ESP-B arranca ANTES que la ESP-A, y por un motivo concreto: su
        # `leer` es bloqueante, igual que en la placa, asi que en cuanto el hilo
        # existe ya esta POSICIONADO en la espera. Si arrancara la ESP-A primero,
        # las primeras tramas se enviarian a un bus donde nadie esta leyendo y se
        # quedarian en la cola; en la placa pasaria lo mismo, con el byte
        # atascado en el shift register del esclavo.
        #
        # Aqui la espera lleva timeout y no es infinita, asi que ningun hilo se
        # queda colgado para siempre aunque la otra mitad no exista.
        self.hilos[1].start()
        time.sleep(0.05)
        self.hilos[0].start()
        time.sleep(0.02)
        self.arrancados = True
        return self

    def _montar_esp_a(self, hardware):
        """ESP-A: UART (la que ya esta cruzada) y SPI maestra."""
        fw = self.firmware_a
        self.registro.info("sim", "levantando la ESP-A")

        uart = hardware.Uart(fw.PUERTO_UART, fw.BAUDIOS, fw.TIMEOUT_LECTURA_MS)
        uart._uart = self.uart_esp           # la UART que ya apunta a la PC
        spi = hardware.SpiMaestro(fw.SPI_BAUDS, fw.SPI_MODO, fw.SPI_PINOS, fw.CS_PIN)
        # El bus se inyecta en la VISTA del puerto (MaestroSPI), no en el
        # envoltorio `machine.SPI`. Cada vista guarda su propia referencia al
        # bus, y es la que usa para leer y escribir: si se cambiara solo la del
        # envoltorio, la ESP-A escribiria en un bus vacio y la ESP-B, que mira
        # otro distinto, no veria nada. Los dos tienen que mirar el mismo.
        spi._spi._puerto.bus = self.bus_spi
        pin_cs = hardware.PinESPSalida(fw.CS_PIN, 1)
        led = hardware.Led(fw.LED_PIN)

        self.puente = fw.Puente(uart, spi, pin_cs, led, self.registro)
        self.registro.info("sim", "ESP-A en pie. UART %d baudios, SPI a %d baudios"
                           % (fw.BAUDIOS, fw.SPI_BAUDS))

    def _compartir_cs(self):
        """El CS de la ESP-A y el de la ESP-B son el MISMO pin.

        En la placa, el GPIO 5 de cada ESP esta unido por un cable. En el
        simulador eso se representa con un unico objeto `Pin` que las dos ESP
        mueven. Si se construyeran dos pines distintos, mover el de la ESP-A no
        avisaria a la ESP-B y el bus se quedaria colgado esperando bytes.

        Se hace despues de montar las dos ESP porque la ESP-B se engancha a su
        pin al construirse, y hay que volver a engancharla al que va a mover la
        ESP-A. Al FINAL se llama `on` porque el `hardware.Pin` guarda un solo
        observador: si se llamara antes, se perderia el enganche de la ESP-B.
        """
        if self.pin_cs_b is None:
            return
        self.puente.pin_cs.pin = self.pin_cs_b
        self.pin_cs_b.on(self.esclava._al_cambiar_cs)

    def _montar_esp_b(self, hardware):
        """ESP-B: SPI esclava, I2C y el driver del LCD.

        Los dos buses se inyectan DESPUES de construir el firmware. La razon es
        que el firmware no sabe de donde salen sus buses, y en el simulador hay
        que forzarle los mismos que usan la PC y la ESP-A.

        El CS de la ESP-B se baja y se sube desde el hilo de la ESP-A, no desde
        el de la ESP-B. Por eso el pin tiene que ser el MISMO objeto para las dos
        ESP: si cada una creara el suyo, la ESP-B no se enteraria de que empieza
        una transaccion y se quedaria esperando para siempre.
        """
        fw = self.firmware_b
        self.registro.info("sim", "levantando la ESP-B")

        from lcd import Lcd as LcdDriver

        pin_cs = hardware.PinESPEntrada(fw.CS_PIN)
        spi = hardware.SpiEsclavo(fw.SPI_BAUDS, fw.SPI_MODO, fw.SPI_PINOS, pin_cs)
        spi._spi._puerto.bus = self.bus_spi   # el MISMO bus que ve la ESP-A
        i2c = hardware.I2cEsclavo(fw.I2C_PINOS, fw.I2C_FRECUENCIA)
        i2c._i2c.bus = self.bus_i2c         # el bus con el LCD ya conectado
        led = hardware.Led(fw.LED_PIN)
        lcd = LcdDriver(i2c, fw.I2C_DIRECCION, fw.COLUMNAS, fw.FILAS)

        self.esclava = fw.Esclava(spi, lcd, led, self.registro)
        self.pin_cs_b = pin_cs
        # Lo que se engancha al pin. Va en el `Esclava` y no en el SPI porque el
        # firmware no lo necesita y no se le mete codigo de simulador dentro.
        self.esclava._al_cambiar_cs = spi._cs_cambia

        # El LCD se enciende antes de que corra ningun hilo, y con el fallo
        # controlado: si el modulo no esta enchufado, la ESP-B tiene que seguir
        # leyendo el SPI y avisando por el log, no quedarse muerta. Es lo que
        # pasa en la placa con el modulo desenchufado, y es un fallo muy comun
        # (cable mal puesto, alimentacion que no llega).
        try:
            lcd.iniciar(fw.COLUMNAS, fw.FILAS)
            self.esclava.mostrar_mensaje("ESP-B lista", "SPI esclava OK")
            self.registro.info("sim", "ESP-B en pie. SPI esclava, LCD en 0x%02X"
                               % fw.I2C_DIRECCION)
        except OSError as exc:
            self.registro.aviso("sim", "el LCD en 0x%02X no responde: %s. "
                                       "La ESP-B sigue leyendo el SPI sin pintar."
                                % (fw.I2C_DIRECCION, exc))

    def _montar_pc(self):
        self.registro.info("sim", "levantando la PC")
        self.fuente = app.CamaraSintetica(self.digitos)
        transporte = _TransportePC(self.uart_pc)
        self.lector = app.LectorDigitos(self.modelo, transporte,
                                        al_registrar=self.registro)
        self.registro.info("sim", "PC en pie. Pesos de %s"
                           % os.path.relpath(self.ruta_pesos, RAIZ))

    # ------------------------------------------------------------------bucles--

    def _hilo_esp_a(self):
        while not self.detener.is_set():
            inicio = time.time()
            try:
                self.puente.paso()
                self.puente.vigilar()
            except Exception:                            # noqa: BLE001
                self.registro.error("ESP-A", "el hilo ha muerto:\n"
                                    + traceback.format_exc())
                return
            # El firmware real duerme hasta 5 ms entre vueltas. Se mantiene para
            # que el hilo no se coma la CPU de la maquina.
            dormir = 0.005 - (time.time() - inicio)
            if dormir > 0:
                time.sleep(dormir)

    def _hilo_esp_b(self):
        """El bucle de la ESP-B.

        `_leer_spi` es bloqueante a proposito, igual que en la placa: en un SPI
        en modo esclava no se puede preguntar "tienes algo?" sin bajar CS, y si
        la esclava lo hiciera la maestra se quedaria colgando dentro de su
        transaccion. Por eso el hilo esta POSICIONADO en la espera antes de que
        la ESP-A mande nada, en vez de ir a su ritmo.

        Se registra el fallo con la traza COMPLETA, no solo el tipo de
        excepcion: un hilo que muere se queda callado y el sintoma seria "la
        ESP-B no recibe nada", que no dice nada sobre el motivo.
        """
        while not self.detener.is_set():
            inicio = time.time()
            try:
                self.esclava.paso()
            except Exception:                            # noqa: BLE001
                self.registro.error("ESP-B", "el hilo ha muerto:\n"
                                    + traceback.format_exc())
                return
            transcurrido = time.time() - inicio
            if transcurrido < 0.010:
                time.sleep(0.010 - transcurrido)

    # ---------------------------------------------------------------ejecucion---

    def correr(self, marcos: int = 30, pausa_ms: int = 40, dibujar: bool = False):
        # Los hilos ya arrancan en `montar`, la ESP-B antes que la ESP-A.
        if not self.arrancados:
            for hilo in self.hilos:
                hilo.start()

        self.registro.info("sim", "-" * 66)
        self.registro.info("sim", "cuatro etapas en marcha. Ctrl-C para parar.")
        self.registro.info("sim", "-" * 66)

        errores_inyectados = 0
        for indice in range(marcos):
            marco = self.fuente.leer()
            if marco is None:
                break

            if self.corromper > 0 and indice % max(int(1 / self.corromper), 1) == 0:
                self._inyectar_ruido()

            self.lector.paso(marco)
            self.lector.recibir_acuses()
            self._vaciar_cadena()

            self.ultimo_estado = {
                "fotograma": indice,
                "verdad": self.fuente.verdad,
                "digito": self.lector.ultimo_digito,
                "confianza": self.lector.ultima_confianza,
                "linea_1": "".join(self.lcd.panel.ddram[0]).rstrip(),
                "linea_2": "".join(self.lcd.panel.ddram[1]).rstrip(),
            }

            if dibujar:
                self.dibujar_panel()

            time.sleep(pausa_ms / 1000.0)

        return errores_inyectados

    def _inyectar_ruido(self):
        """Mete un byte suelto delante de lo que espera la ESP-A.

        Para comprobar que el protocolo aguanta: un byte de relleno delante tiene
        que ser descartado por el despiece de tramas, y si un dia se corrompe un
        byte de DATOS, el CRC tiene que hacer que la ESP-B lo rechace. Sin las
        dos cosas, un cable flojo se traduce en un LCD que muestra numeros que
        nadie escribio.
        """
        pendientes = len(self.uart_pc.escritura)
        self.uart_pc.escritura.cola[:0] = bytes((0x00,))
        self.registro.aviso("sim", "ruido: un byte 0x00 delante de %d pendientes"
                            % pendientes)

    def _vaciar_cadena(self, timeout: float = 0.5):
        """Espera a que UART y SPI se queden sin nada pendiente.

        No es una sincronizacion del sistema: en la placa no existe. Es el
        equivalente a "dame unos milisegundos para que todo llegue", que es lo
        que hace falta para poder leer el panel sin que este a medio pintar.
        """
        limite = time.time() + timeout
        while time.time() < limite:
            if (len(self.uart_pc.escritura) == 0
                    and len(self.uart_esp.escritura) == 0
                    and len(self.bus_spi.entrada_esclava) == 0
                    and len(self.bus_spi.salida_esclava) == 0):
                time.sleep(0.002)          # margen para el I2C del LCD
                return
            time.sleep(0.001)

    def dibujar_panel(self):
        texto = LcdTexto().render(self.lcd.panel, "LCD 16x2 simulado")
        estado = self.ultimo_estado
        print(texto)
        print("  verdad %d  ->  red %s  %.0f%%   fotograma %d"
              % (estado.get("verdad", -1), estado.get("digito"),
                 estado.get("confianza", 0), estado.get("fotograma", 0)))
        print()

    # ---------------------------------------------------------------parada-----

    def parar(self):
        self.detener.set()
        # El bus SPI se para ANTES de unirse a los hilos: su `esperar_y_leer` esta
        # bloqueado en una condicion con timeout, y sin esto habria que esperar a
        # que ese timeout venciera para que el hilo saliera.
        self.bus_spi.parar()
        self.bus_i2c.parar()
        for hilo in self.hilos:
            if hilo.is_alive():
                hilo.join(timeout=2.0)

    def _informe_parcial(self) -> str:
        """Informe cuando la cadena no llego a montarse del todo.

        Montar puede fallar a mitad (un pin mal, un peso que no existe) y sin
        esto la traza del error se come el informe, que es justo lo que se
        necesita para saber que paso.
        """
        lineas = ["", "=" * 68, "INFORME DE LA CADENA (incompleta)", "=" * 68]
        if self.lector is not None:
            lineas.append("  PC     %s" % self.lector.resumen())
        if self.puente is not None:
            lineas.append("  ESP-A  %s" % self.puente.resumen())
        if self.esclava is not None:
            lineas.append("  ESP-B  %s" % self.esclava.resumen())
        lineas.append("  %s" % self.bus_spi.informe())
        lineas.append("=" * 68)
        return "\n".join(lineas)

    def informe(self) -> str:
        """El resumen de los cuatro eslabones. Es lo que se lee para decidir si
        la cadena funciono de punta a punta."""
        acuses = Traza()
        acuses.alimentar(self.uart_pc.lectura.leer(4096))
        recibidos = acuses.drenar()

        lineas = ["", "=" * 68, "INFORME DE LA CADENA", "=" * 68]
        lineas.append("  PC     %s" % self.lector.resumen())
        lineas.append("  ESP-A  %s" % self.puente.resumen())
        lineas.append("  ESP-B  %s" % self.esclava.resumen())
        lineas.append("  %s" % self.bus_spi.informe())
        for linea in self.bus_i2c.informe().split("\n"):
            lineas.append("  %s" % linea)
        lineas.append("  LCD    %s" % self.lcd.panel.informe())
        lineas.append("  acuses que llegaron de vuelta a la PC: %d" % len(recibidos))
        lineas.append("  log: %d lineas" % len(self.registro.lineas))
        lineas.append("=" * 68)
        return "\n".join(lineas)


class _TransportePC:
    """El lado de la PC del UART.

    En la PC real esto es un `serial.Serial`. Aqui es la UART falsa del modulo
    `machine`, que tiene la misma interfaz, de forma que `pc/app.py` no sabe la
    diferencia.

    Escribe en el registro de la UART de la PC, que esta cruzado con el de
    lectura de la ESP-A. Por eso la trama le llega.
    """

    def __init__(self, uart) -> None:
        self.uart = uart

    def escribir(self, datos) -> int:
        # `write` de la UART falsa ya trocea la escritura en trozos de 1 a 4
        # bytes. Es lo que pasa en el cable, y obliga a que el parser de tramas
        # de la ESP-A funcione con recepcion parcial.
        return self.uart.write(datos)

    def leer(self, cantidad: int, timeout: float = 0.0) -> bytes:
        return self.uart.read(cantidad) or b""

    def hay_datos(self) -> bool:
        return bool(self.uart.any())


# ------------------------------------------------------------------------ CLI ---

def main() -> int:
    analizador = argparse.ArgumentParser(
        description="Simula PC -> ESP-A (SPI) -> ESP-B -> LCD I2C")
    analizador.add_argument("--marcos", type=int, default=30,
                            help="fotogramas a capturar, 0 = hasta Ctrl-C")
    analizador.add_argument("--digitos", default="",
                            help="digitos a mostrar, p.ej. '7' o '1,2,3'")
    analizador.add_argument("--pausa", type=int, default=40, help="ms entre fotogramas")
    analizador.add_argument("--lento", action="store_true",
                            help="dibuja el LCD despues de cada fotograma")
    analizador.add_argument("--nivel", default="info",
                            choices=("debug", "info", "aviso", "error"))
    analizador.add_argument("--corrupcion", type=float, default=0.0,
                            help="frecuencia de ruido en el UART, de 0 a 1")
    analizador.add_argument("--sin-lcd", action="store_true",
                            help="simula el modulo I2C desconectado")
    argumentos = analizador.parse_args()

    digitos = [int(d) for d in argumentos.digitos.split(",") if d.strip().isdigit()]

    maquina.reiniciar()
    cadena = CadenaSimulada(digitos or None, argumentos.nivel,
                            argumentos.corrupcion, argumentos.sin_lcd)
    montada = False
    try:
        cadena.montar()
        montada = True
        cadena.correr(argumentos.marcos, argumentos.pausa, argumentos.lento)
    except KeyboardInterrupt:
        print("\ninterrumpido")
    finally:
        cadena.parar()
        print(cadena.informe() if montada else cadena._informe_parcial())
    return 0


if __name__ == "__main__":
    sys.exit(main())

