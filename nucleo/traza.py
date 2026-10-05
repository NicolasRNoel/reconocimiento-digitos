

from __future__ import annotations

from nucleo import protocolo


class Traza:
   

    def __init__(self, max_buzon: int = protocolo.TAMAÑO_TRAMA * 8) -> None:
        self.buzon = bytearray()
        self.max_buzon = max_buzon
       
        self.aceptadas = 0
        self.descartadas = 0
        self.bytes_ignorados = 0

    def alimentar(self, datos) -> None:
        """Meter bytes del bus. Se ignoran los vacios, que `any()` produce."""
        if not datos:
            return
        self.buzon.extend(datos)
        if len(self.buzon) > self.max_buzon:
          
            self.bytes_ignorados += len(self.buzon) - self.max_buzon
            del self.buzon[:len(self.buzon) - self.max_buzon]

    def siguiente(self):
       
        while True:
            
            vista = bytes(self.buzon)
            posicion = -1
            for i in range(len(vista) - 1):
                if vista[i] == protocolo.SOF0 and vista[i + 1] == protocolo.SOF1:
                    posicion = i
                    break

            if posicion < 0:
               
                self.bytes_ignorados += len(vista)
                conservar = vista[-1:] if vista[-1:] == bytes((protocolo.SOF0,)) else b""
                self.buzon[:] = conservar
                return None

            
            if posicion:
                self.bytes_ignorados += posicion

            
            if len(vista) - posicion < protocolo.CABECERA:
                
                return None

            largo = vista[posicion + 2]
            tipo = vista[posicion + 3]

            total = protocolo.CABECERA + largo + protocolo.COLA
            if len(vista) - posicion < total:
              
                return None

            cuerpo = vista[posicion + protocolo.CABECERA:
                           posicion + protocolo.CABECERA + largo + protocolo.COLA]
            try:
                datos = protocolo.validar(cuerpo, tipo, largo)
            except protocolo.ErrorTrama:
               
                self.descartadas += 1
                self.buzon[:] = vista[posicion + 1:]
                continue

           
            self.buzon[:] = vista[posicion + total:]
            self.aceptadas += 1
            return tipo, datos

    def drenar(self) -> list:
       
        salida = []
        while True:
            trama = self.siguiente()
            if trama is None:
                return salida
            salida.append(trama)

    def resumen(self) -> str:
        return ("tramas OK %d, descartadas %d, bytes de relleno %d"
                % (self.aceptadas, self.descartadas, self.bytes_ignorados))

    def vaciar(self) -> None:
        self.buzon.clear()
