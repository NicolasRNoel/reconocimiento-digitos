

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


import simulacion.maquina as maquina                        
maquina.registrar_en_python()

import configuracion                                         
from nucleo.traza import Traza                             
from pc import app                                          
from simulacion.bus_i2c import BusI2C, LcdEsclavo          
from simulacion.bus_spi import BusSPI                       
from simulacion.lcd import LcdTexto                        
from vision.cnn.modelo import DigitCNN                       

COMUN = os.path.join(RAIZ, "firmware", "common")
if COMUN not in sys.path:
    sys.path.insert(0, COMUN)




NIVELES = {"debug": 0, "info": 1, "aviso": 2, "error": 3}


class Registro:
    

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

  
    def __call__(self, origen, texto):
        self.info(origen, texto)




def cargar_modulo(nombre: str, ruta: str):
   
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo




class CadenaSimulada:
    

    def __init__(self, digitos=None, nivel: str = "info", corromper: float = 0.0,
                 sin_lcd: bool = False) -> None:
        self.registro = Registro(nivel)
        self.corromper = corromper
        self.sin_lcd = sin_lcd
        self.digitos = digitos

        self.ruta_pesos = os.path.join(RAIZ, configuracion.RUTA_PESOS)
        self.modelo = DigitCNN.cargar(self.ruta_pesos)

      
        self.bus_spi = BusSPI(configuracion.SPI_BAUDS_A)

        self.bus_i2c = BusI2C(velocidad=configuracion.FRECUENCIA_I2C)
        self.lcd = LcdEsclavo(configuracion.DIRECCION_I2C_LCD,
                              configuracion.LCD_COLUMNAS, configuracion.LCD_FILAS)
        self.bus_i2c.conectar(self.lcd)
        if sin_lcd:
            self.bus_i2c.desconectar(configuracion.DIRECCION_I2C_LCD)
            self.registro.aviso("sim", "el modulo I2C esta desconectado: "
                                      "la ESP-B no podra escribir en el LCD")

       
        self.uart_esp = maquina.UART(1, configuracion.BAUDIOS)
        self.uart_pc = maquina.UART(0, configuracion.BAUDIOS)
        self.uart_esp.lectura = self.uart_pc.escritura
        self.uart_pc.lectura = self.uart_esp.escritura

        self.firmware_a = None
        self.firmware_b = None
        self.puente = None        
        self.esclava = None      
        self.pin_cs_b = None
        self.lector = None       
        self.fuente = None

        self.hilos = []
        self.detener = threading.Event()
        self.arrancados = False
        self.ultimo_estado = {}

    
    def montar(self) -> "CadenaSimulada":
       
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

       
        self.hilos[1].start()
        time.sleep(0.05)
        self.hilos[0].start()
        time.sleep(0.02)
        self.arrancados = True
        return self

    def _montar_esp_a(self, hardware):
        
        fw = self.firmware_a
        self.registro.info("sim", "levantando la ESP-A")

        uart = hardware.Uart(fw.PUERTO_UART, fw.BAUDIOS, fw.TIMEOUT_LECTURA_MS)
        uart._uart = self.uart_esp           # la UART que ya apunta a la PC
        spi = hardware.SpiMaestro(fw.SPI_BAUDS, fw.SPI_MODO, fw.SPI_PINOS, fw.CS_PIN)
        spi._spi._puerto.bus = self.bus_spi
        pin_cs = hardware.PinESPSalida(fw.CS_PIN, 1)
        led = hardware.Led(fw.LED_PIN)

        self.puente = fw.Puente(uart, spi, pin_cs, led, self.registro)
        self.registro.info("sim", "ESP-A en pie. UART %d baudios, SPI a %d baudios"
                           % (fw.BAUDIOS, fw.SPI_BAUDS))

    def _compartir_cs(self):

        if self.pin_cs_b is None:
            return
        self.puente.pin_cs.pin = self.pin_cs_b
        self.pin_cs_b.on(self.esclava._al_cambiar_cs)

    def _montar_esp_b(self, hardware):
    
        fw = self.firmware_b
        self.registro.info("sim", "levantando la ESP-B")

        from lcd import Lcd as LcdDriver

        pin_cs = hardware.PinESPEntrada(fw.CS_PIN)
        spi = hardware.SpiEsclavo(fw.SPI_BAUDS, fw.SPI_MODO, fw.SPI_PINOS, pin_cs)
        spi._spi._puerto.bus = self.bus_spi   
        i2c = hardware.I2cEsclavo(fw.I2C_PINOS, fw.I2C_FRECUENCIA)
        i2c._i2c.bus = self.bus_i2c         
        led = hardware.Led(fw.LED_PIN)
        lcd = LcdDriver(i2c, fw.I2C_DIRECCION, fw.COLUMNAS, fw.FILAS)

        self.esclava = fw.Esclava(spi, lcd, led, self.registro)
        self.pin_cs_b = pin_cs
       
        self.esclava._al_cambiar_cs = spi._cs_cambia


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


    def _hilo_esp_a(self):
        while not self.detener.is_set():
            inicio = time.time()
            try:
                self.puente.paso()
                self.puente.vigilar()
            except Exception:                           
                self.registro.error("ESP-A", "el hilo ha muerto:\n"
                                    + traceback.format_exc())
                return
    
            dormir = 0.005 - (time.time() - inicio)
            if dormir > 0:
                time.sleep(dormir)

    def _hilo_esp_b(self):
        while not self.detener.is_set():
            inicio = time.time()
            try:
                self.esclava.paso()
            except Exception:                          
                self.registro.error("ESP-B", "el hilo ha muerto:\n"
                                    + traceback.format_exc())
                return
            transcurrido = time.time() - inicio
            if transcurrido < 0.010:
                time.sleep(0.010 - transcurrido)

  
    def correr(self, marcos: int = 30, pausa_ms: int = 40, dibujar: bool = False):
      
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
    
        pendientes = len(self.uart_pc.escritura)
        self.uart_pc.escritura.cola[:0] = bytes((0x00,))
        self.registro.aviso("sim", "ruido: un byte 0x00 delante de %d pendientes"
                            % pendientes)

    def _vaciar_cadena(self, timeout: float = 0.5):

        limite = time.time() + timeout
        while time.time() < limite:
            if (len(self.uart_pc.escritura) == 0
                    and len(self.uart_esp.escritura) == 0
                    and len(self.bus_spi.entrada_esclava) == 0
                    and len(self.bus_spi.salida_esclava) == 0):
                time.sleep(0.002)          
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

   

    def parar(self):
        self.detener.set()
        self.bus_spi.parar()
        self.bus_i2c.parar()
        for hilo in self.hilos:
            if hilo.is_alive():
                hilo.join(timeout=2.0)

    def _informe_parcial(self) -> str:
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

    def __init__(self, uart) -> None:
        self.uart = uart

    def escribir(self, datos) -> int:
       
        return self.uart.write(datos)

    def leer(self, cantidad: int, timeout: float = 0.0) -> bytes:
        return self.uart.read(cantidad) or b""

    def hay_datos(self) -> bool:
        return bool(self.uart.any())



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

