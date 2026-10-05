
from __future__ import annotations

import contextlib
import threading
import time


MOSI = 0
MISO = 1
SCK = 2
CS = 3
NOMBRES = ("MOSI", "MISO", "SCK", "CS")


class Transferencia:
   

    def __init__(self, bus, hacia_esclava: bytearray, desde_esclava: bytearray,
                 reloj) -> None:
        self.bus = bus
        self.hacia_esclava = hacia_esclava
        self.desde_esclava = desde_esclava
        self.reloj = reloj
        self.abierta = True
        self.terminada = False

    def escribir(self, datos: bytes) -> None:
        if self.terminada:
            raise RuntimeError("la transaccion ya se cerro")
        self.hacia_esclava.extend(datos)
        self.bus._registrar(MOSI, len(datos))

    def leer(self, cantidad: int) -> bytes:
       
        if self.terminada:
            raise RuntimeError("la transaccion ya se cerro")
        if len(self.desde_esclava) < cantidad:
            faltan = cantidad - len(self.desde_esclava)
            self.bus._registrar(MISO, faltan, relleno=0xFF)
            salida = bytes(self.desde_esclava) + bytes([0xFF]) * faltan
            del self.desde_esclava[:]
        else:
            self.bus._registrar(MISO, cantidad)
            salida = bytes(self.desde_esclava[:cantidad])
            del self.desde_esclava[:cantidad]
        return salida

    def cerrar(self) -> None:
        self.abierta = False
        self.terminada = True
        self.bus.cerrar_transaccion(self)


class SpiEsclavo:
    

    def __init__(self, bus: "BusSPI", pin_cs, al_registrar=None) -> None:
        self.bus = bus
        self.pin_cs = pin_cs
        self.al_registrar = al_registrar or (lambda texto: None)
       
        pin_cs.on(self.al_cambiar_cs)

    def al_cambiar_cs(self, valor: int) -> None:
        """Callback del pin de CS. Lo expone con nombre publico porque el
        orquestador lo necesita para enganchar las dos ESP al mismo pin."""
        self.bus.registrar("CS de la ESP-B = %s"
                           % ("LOW (transaccion)" if not valor else "HIGH"))
        if not valor:
            
            self.bus._despertar()

    def esperar_y_leer(self, cantidad: int, timeout: float = 5.0) -> bytes:
       
        limite = time.time() + timeout
        with self.bus.condicion:
            self.bus.consumidor = self
            self.bus.condicion.notify_all()
            while (len(self.bus.entrada_esclava) < cantidad
                   and time.time() < limite
                   and self.bus.sigue_vivo):
                self.bus.condicion.wait(0.02)

            datos = bytes(self.bus.entrada_esclava[:cantidad])
            del self.bus.entrada_esclava[:cantidad]
            self.bus.consumidor = None
            self.bus.condicion.notify_all()
        return datos

    def responder(self, datos: bytes) -> None:
        
        with self.bus.condicion:
            self.bus.salida_esclava.extend(datos)
            self.bus.condicion.notify_all()
        self.bus.registrar("la ESP-B responde %d bytes por MISO" % len(datos))
        return len(datos)

  
    def read(self, n: int = 1) -> bytes:
 
        return self.esperar_y_leer(n)

    def write(self, datos: bytes) -> int:
       
        self.responder(datos)
        return len(datos)

  

    def escribir(self, datos: bytes) -> int:
       
        return self.write(datos)

    def readinto(self, buffer) -> int:
        datos = self.read(len(buffer))
        for i, byte in enumerate(datos):
            buffer[i] = byte
        return len(datos)


class MaestroSPI:
 

    def __init__(self, bus: "BusSPI", pin_cs, al_registrar=None) -> None:
        self.bus = bus
        self.pin_cs = pin_cs
        self.al_registrar = al_registrar or (lambda texto: None)

    @contextlib.contextmanager
    def _transaccion(self):
       se le avise.
      
        self.pin_cs(0)
        transaccion = self.bus.en_transferencia()
        try:
            yield transaccion
        finally:
            self.bus.cerrar_transaccion(transaccion)
            self.pin_cs(1)

    def write(self, datos: bytes) -> int:
        with self._transaccion() as t:
            t.escribir(datos)
        return len(datos)

    def write_read(self, saliente: bytes, longitud: int = None):
        longitud = longitud if longitud is not None else len(saliente)
        with self._transaccion() as t:
            t.escribir(saliente)
            return t.leer(longitud)

    def read(self, n: int) -> bytes:
        with self._transaccion() as t:
            return t.leer(n)

    def readinto(self, buffer) -> int:
        datos = self.read(len(buffer))
        for i, byte in enumerate(datos):
            buffer[i] = byte
        return len(datos)

    def write_readinto(self, saliente, entrada=None) -> int:

        respuesta = self.write_read(
            bytes(saliente), len(entrada) if entrada is not None else len(saliente))
        if entrada is not None:
            for i, byte in enumerate(respuesta):
                entrada[i] = byte
        return len(saliente)

    def deinit(self) -> None:
        self.pin_cs(1)


class BusSPI:
  

    def __init__(self, velocidad: int = 1_000_000, al_registrar=None) -> None:
        self.velocidad = velocidad
        self.al_registrar = al_registrar or (lambda texto: None)
        self.condicion = threading.Condition()
        self.velocidad_real = velocidad

        self.entrada_esclava = bytearray()      
        self.salida_esclava = bytearray()      

        self.transacciones = 0
        self.bytes_mosi = 0
        self.bytes_miso = 0
        self.sigue_vivo = True
        self.consumidor = None    

    
    def registrar(self, texto: str) -> None:
        self.al_registrar(texto)

    def _registrar(self, senal: int, cantidad: int, relleno: int = 0) -> None:
        if senal == MOSI:
            self.bytes_mosi += cantidad
        else:
            self.bytes_miso += cantidad
        self.al_registrar("SPI %s %d byte(s)%s"
                          % (NOMBRES[senal], cantidad,
                             " (relleno 0xFF)" if relleno else ""))

   

    def _despertar(self) -> None:
        with self.condicion:
            self.condicion.notify_all()

    

    def en_transferencia(self):
       
        with self.condicion:
            hacia = bytearray()
            desde = bytes(self.salida_esclava)   # lo que quedo de la anterior
            del self.salida_esclava[:]
            self.transacciones += 1
            return Transferencia(self, hacia, bytearray(desde), self.condicion)

    def cerrar_transaccion(self, t: Transferencia) -> None:
        with self.condicion:
            self.entrada_esclava.extend(t.hacia_esclava)
            self.condicion.notify_all()

    def retardo_transaccion(self, nbytes: int) -> float:
      
        if not self.velocidad_real:
            return 0.0
        return (nbytes * 8.0) / float(self.velocidad_real)

 
    def informe(self) -> str:
        media = (self.velocidad / 8.0) if self.velocidad else 0
        return ("SPI: %d transaccion(es), %d bytes MOSI, %d bytes MISO, "
                "%.0f kB/s teoricos por byte" % (self.transacciones, self.bytes_mosi,
                                                 self.bytes_miso, media / 1000.0))

    def parar(self) -> None:
        with self.condicion:
            self.sigue_vivo = False
            self.condicion.notify_all()
