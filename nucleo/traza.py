"""
Traza de tramas: convierte un flujo de bytes en mensajes.

POR QUE HACE FALTA Y POR QUE NO ES UN `while buf.startswith(SOF)`
-------------------------------------------------------------
Por UART las tramas llegan partidas y desalignadas: un byte de una trama, luego
el resto de la anterior, y stuffed de relleno en medio. Un codigo que solo mira
el principio del buffer no se recupera de eso.

Este modulo mantiene un buffer, busca el SOF byte a byte, y devuelve los DATOS
de cada trama valida.

LO MAS DELICADO: EL BUFFER TIENE QUE PODER "RETROCEDER"
-------------------------------------------------------
Cuando llega una trama completa de golpe, el buzon tiene la cabecera Y el cuerpo.
Si el parser lee la cabecera, ve que aun no hay suficientes bytes para el cuerpo
(y porque los acaba de consumir, ya no estan), y los devuelve al buzon por el
principio, entonces nunca avanza: los ve otra vez, los devuelve otra vez, y el
firmware se queda colgado para siempre sin decir nada.

Ese fallo NUNCA aparece si el UART siempre trocea, porque cada lectura trae
pocos bytes y el camino problematico no se ejecuta. Solo aparece cuando el otro
extremo entrega la trama entera de una vez, que es justo lo que hace el
conversor USB-TTL. Por eso el parser trabaja sobre una COPIA del buzon y decide
que devolver segun lo que tiene, en vez de consumir y devolver.
"""

from __future__ import annotations

from nucleo import protocolo


class Traza:
    """Acumula bytes y entrega cuerpos validos de uno en uno."""

    def __init__(self, max_buzon: int = protocolo.TAMAÑO_TRAMA * 8) -> None:
        self.buzon = bytearray()
        self.max_buzon = max_buzon
        # `descartadas` son tramas que llegaron con el CRC roto o con un cuerpo
        # que no cuadra. Si crece mucho respecto a `aceptadas`, hay ruido en el
        # cable o un problema de alimentacion en el otro extremo.
        self.aceptadas = 0
        self.descartadas = 0
        self.bytes_ignorados = 0

    def alimentar(self, datos) -> None:
        """Meter bytes del bus. Se ignoran los vacios, que `any()` produce."""
        if not datos:
            return
        self.buzon.extend(datos)
        if len(self.buzon) > self.max_buzon:
            # El desborde significa que se perdio el SOF de algo y se acumulado
            # basura. Se recorta lo que se pueda conservar sin romper una trama a
            # medias; el parser lo sweatyara como bytes de relleno.
            self.bytes_ignorados += len(self.buzon) - self.max_buzon
            del self.buzon[:len(self.buzon) - self.max_buzon]

    def siguiente(self):
        """(tipo, datos) de la siguiente trama valida, o None si no hay ninguna.

        Se come las tramas invalidas y sigue hasta encontrar una buena, con lo
        que el firmware se recupera solo de un byte corrupto en vez de quedarse
        esperando una trama que ya no va a llegar.

        TRABAJA SOBRE UNA COPIA del buzon. Eso es lo que evita el bucle
        infinito descrito en la cabecera del modulo: si el cuerpo esta entero,
        lo consume de verdad; si esta a medias, deja el buzon intacto y devuelve
        None para que se espere mas. Nunca consume algo que luego devuelve.
        """
        while True:
            # Se busca el SOF sobre una copia. Asi el estado del buzon no cambia
            # hasta que se sabe que hay una trama completa.
            vista = bytes(self.buzon)
            posicion = -1
            for i in range(len(vista) - 1):
                if vista[i] == protocolo.SOF0 and vista[i + 1] == protocolo.SOF1:
                    posicion = i
                    break

            if posicion < 0:
                # No hay SOF. Puede quedar un 0xA5 suelto al final, que es el
                # primer byte de un SOF partido; se conserva por si el siguiente
                # byte llega en la proxima lectura.
                self.bytes_ignorados += len(vista)
                conservar = vista[-1:] if vista[-1:] == bytes((protocolo.SOF0,)) else b""
                self.buzon[:] = conservar
                return None

            # Habia relleno delante del SOF: son bytes perdidos.
            if posicion:
                self.bytes_ignorados += posicion

            # A partir del SOF, la cabecera son 4 bytes: SOF0, SOF1, LEN, TIPO.
            if len(vista) - posicion < protocolo.CABECERA:
                # Cabecera partida. No se consume nada: se espera mas.
                return None

            largo = vista[posicion + 2]
            tipo = vista[posicion + 3]

            total = protocolo.CABECERA + largo + protocolo.COLA
            if len(vista) - posicion < total:
                # El cuerpo no ha llegado entero. No se consume nada: se espera.
                return None

            cuerpo = vista[posicion + protocolo.CABECERA:
                           posicion + protocolo.CABECERA + largo + protocolo.COLA]
            try:
                datos = protocolo.validar(cuerpo, tipo, largo)
            except protocolo.ErrorTrama:
                # La trama esta corrupta. Se descarta UNA trama entera a partir
                # de este SOF y se sigue buscando desde el siguiente byte. Sin
                # este `avanzar`, si el cuerpo corrupto vuelve a contener un SOF
                # se podria reintentar la misma trama una y otra vez.
                self.descartadas += 1
                self.buzon[:] = vista[posicion + 1:]
                continue

            # Ahora si: la trama es valida. Se consume TODO lo que iba hasta el
            # final de ella, incluidos los bytes de relleno que quedaban delante.
            self.buzon[:] = vista[posicion + total:]
            self.aceptadas += 1
            return tipo, datos

    def drenar(self) -> list:
        """Saca todas las tramas completas que haya ahora mismo."""
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
