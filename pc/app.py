"""
Aplicacion de la PC: camara -> OpenCV -> CNN -> puerto serie.

    python -m pc.app --listar
    python -m pc.app --puerto COM5
    python -m pc.app --fuente sim --marcos 40

QUE HACE, PASO A PASO
---------------------
1. Abre la camara y lee un fotograma.
2. Lo pasa por `vision.preproceso.procesar`, que recorta el digito.
3. Si no hay contorno, espera. ENCENDER Y APAGAR LA LUZ no produce detecciones.
4. Si hay digito, la CNN devuelve (digito, confianza).
5. Con la confianza por encima del umbral, empaqueta la trama y la escribe en
   el UART. El numero de secuencia sube para que la ESP-B pueda contestar.

POR QUE SOLO SE MANDA CUANDO LA CONFIANZA ALCANZA EL UMBRAL
------------------------------------------------------------
La ESP-B no puede dudar: si la PC le manda todo, el LCD va a mostrar numeros
que nadie escribio y no hay forma de distinguirlos. Mandando solo lo que la red
tiene claro, el LCD es un espejo de la realidad y el silencio significa "no hay
digito". Con CONFIANZA_MINIMA en 55 se descarta cerca del 3% en pruebas
sinteticas y sube ese numero con poca luz.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cv2                                                    # noqa: E402
import numpy as np                                            # noqa: E402

import configuracion                                         # noqa: E402
from nucleo import protocolo, traza                          # noqa: E402
from pc import transportes                                   # noqa: E402
from vision import preproceso                                # noqa: E402
from vision.cnn.modelo import DigitCNN                       # noqa: E402


# ------------------------------------------------------------------- fuentes ---

class CamaraReal:
    """La webcam de verdad. No necesita mas que cv2."""

    def __init__(self, indice: int = 0, ancho: int = 640, alto: int = 480) -> None:
        self.cap = cv2.VideoCapture(indice)
        if not self.cap.isOpened():
            raise RuntimeError(
                "no se pudo abrir la camara %d. En Windows se elige con --fuente cam:N"
                % indice)
        # Estos dos `set` son una peticion, no una garantia. Muchas webcam
        # ignoran la resolucion y entregan 1280x720. El preprocesado trabaja con
        # proporcion de areas y con angulos, asi que aguanta cualquier tamano;
        # lo unico que se pierde es detalle del digito.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)

    def leer(self):
        ok, marco = self.cap.read()
        return marco if ok else None

    def mostrar_marcos(self, marco, recorte) -> None:
        self._ultimo = marco
        self._recorte = recorte
        cv2.imshow("camara", marco)
        ventana = cv2.resize(recorte, (160, 160), interpolation=cv2.INTER_NEAREST)
        cv2.imshow("recorte 20x20 (x8)", ventana)

    def cerrar(self) -> None:
        self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()


class CamaraSintetica:
    """El generador del dataset usada como si fuera una camara.

    Es lo que permite probar todo el sistema sin ningun hardware: no es un
    mock que devuelve respuestas fijas, es el MISMO generador con el que se
    entreno, pasando despues por el MISMO preprocesado. Si el reconocimiento
    funciona aqui con una webcam real deberia funcionar alli tambien, salvo que
    la letra de la persona este fuera de lo que el dataset cubre.
    """

    def __init__(self, digitos=None, semilla: int = 4242) -> None:
        from vision.cnn import dataset
        self.dataset = dataset
        self.rng = np.random.default_rng(semilla)
        self.digitos = list(digitos) if digitos else list(range(10))
        self._verdad = 0
        self._cambia_en = 0
        # El generador entrega 240x320 (vertical), que es lo que ve la webcam
        # apuntando a una hoja. No se redimensiona nada: la CNN se entrena con
        # los bordes que da el generador, y Escalarlos despues con un
        # interpolador cualquiera los Pondria borrosos.

    def _siguiente(self):
        self._cambia_en -= 1
        if self._cambia_en <= 0:
            self._verdad = int(self.digitos[int(self.rng.integers(len(self.digitos)))])
            self._cambia_en = int(self.rng.integers(8, 26))   # cambia cada ~1 s a 16 fps
        return self.dataset.sintetizar(self._verdad, self.rng)

    def leer(self):
        return self._siguiente()

    def mostrar_marcos(self, marco, recorte) -> None:
        self._ultimo = marco
        self._recorte = recorte
        grande = cv2.resize(recorte, (160, 160), interpolation=cv2.INTER_NEAREST)
        cv2.imshow("camara sintetica", marco)
        cv2.imshow("recorte 20x20 (x8)", grande)

    def cerrar(self) -> None:
        cv2.destroyAllWindows()

    @property
    def verdad(self) -> int:
        """El digito que hay en escena. Solo existe para las pruebas."""
        return self._verdad

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.cerrar()


# ------------------------------------------------------------------ aplicacion --

class LectorDigitos:
    """El bucle de la PC, sin Atributos de camara ni de transporte.

    Se separa para que las pruebas puedan Injectarle un transporte falso y
    comprobar el protocolo entero sin abrir ningun puerto.
    """

    def __init__(self, modelo: DigitCNN, transporte, confianza_minima: int = None,
                 al_registrar=None) -> None:
        self.modelo = modelo
        self.transporte = transporte
        self.confianza_minima = (configuracion.CONFIANZA_MINIMA
                                if confianza_minima is None else confianza_minima)
        self.al_registrar = al_registrar or (lambda origen, texto: None)

        self.seq = 0
        self.enviados = 0
        self.descartados = 0
        self.inicio_ms = 0
        self.ultimo_digito = None
        self.ultima_confianza = 0.0
        self.acuses = traza.Traza()

    def registrar(self, texto: str) -> None:
        self.al_registrar("pc", texto)

    def _ahora_ms(self) -> int:
        return int((time.time() - self.inicio_ms) * 1000) & 0xFFFFFFFF

    def analizar(self, marco):
        """(digito, confianza, recorte) o (None, 0, recorte) si no hay nada."""
        entrada, recorte, exito = preproceso.procesar(marco)
        if not exito:
            return None, 0.0, recorte

        digito, confianza, _ = self.modelo.predecir(entrada)
        if confianza < self.confianza_minima:
            return None, confianza, recorte
        return digito, confianza, recorte

    def paso(self, marco) -> dict:
        """Un fotograma completo. Devuelve lo que paso, para el log y las pruebas."""
        digito, confianza, recorte = self.analizar(marco)

        if digito is None:
            self.descartados += 1
            if self.descartados % 20 == 1:
                self.registrar("sin digito reconocible (confianza %.0f%%)" % confianza)
            return {"ok": False, "digito": None, "confianza": confianza,
                    "recorte": recorte, "trama": b""}

        self.seq = (self.seq + 1) & 0xFFFF
        trama = protocolo.empaquetar_digito(self.seq, digito,
                                             int(round(confianza)),
                                             tipo_fuente=0, ms=self._ahora_ms())
        self.transporte.escribir(trama)
        self.enviados += 1
        self.ultimo_digito = digito
        self.ultima_confianza = confianza
        self.registrar("digito %d  %.0f%%  seq %d  %d bytes"
                       % (digito, confianza, self.seq, len(trama)))
        return {"ok": True, "digito": digito, "confianza": confianza,
                "recorte": recorte, "trama": trama}

    def recibir_acuses(self) -> int:
        """Lee lo que devuelve la ESP-A. Devuelve cuantos acuses llegaron."""
        self.acuses.alimentar(self.transporte.leer(64, timeout=0.0))
        cuantos = 0
        for tipo, datos in self.acuses.drenar():
            if tipo != protocolo.TIPO_ACUSE:
                continue
            acuse = protocolo.desempaquetar_acuse(datos)
            cuantos += 1
            estado = "OK" if acuse["codigo"] == protocolo.ACUSE_OK else acuse["texto"]
            self.registrar("acuse de la ESP-A: seq %d %s" % (acuse["seq"], estado))
        return cuantos

    def bucle(self, fuente, max_marcos: int = 0, verbose: bool = True,
              pausa_ms: int = configuracion.MS_ENTRE_DISPAROS) -> None:
        """Captura continua. `max_marcos` de 0 significa hasta que se cierre."""
        self.inicio_ms = time.time()
        marcos = 0
        anterior = time.time()
        while max_marcos == 0 or marcos < max_marcos:
            marco = fuente.leer()
            if marco is None:
                self.registrar("la camara dejo de entregar fotogramas")
                break

            resultado = self.paso(marco)
            self.recibir_acuses()

            if verbose:
                fuente.mostrar_marcos(marco, resultado["recorte"])
                cv2.waitKey(1)

            marcos += 1
            # El limite de fotogramas por segundo va aqui y no en la camara: la
            # CNN es de microsegundos en la PC pero el LCD es de 20 Hz y nadie
            # gana mandandole 60 detecciones por segundo.
            transcurrido = (time.time() - anterior) * 1000.0
            if transcurrido < pausa_ms:
                time.sleep((pausa_ms - transcurrido) / 1000.0)
            anterior = time.time()

        self.registrar("fin: %d enviados, %d descartados" % (self.enviados, self.descartados))

    def resumen(self) -> str:
        """Una linea con los numeros de la etapa. La usa el informe del simulador."""
        return ("PC: %d detecciones enviadas, %d descartadas por confianza baja, "
                "%d acuses recibidos, %s"
                % (self.enviados, self.descartados,
                   self.acuses.aceptadas, self.acuses.resumen()))


# ------------------------------------------------------------------------ CLI ---

def registrar_consola(origen: str, texto: str) -> None:
    print("[%s] %s" % (origen.upper().ljust(4), texto))


class _AVeredas:
    """Un transporte que se traga lo que se le manda.

    Sirve para `--sin-puerto`: comprobar el reconocimiento por separado, sin
    tener las ESP conectadas. Si se usara un `Serial` de verdad, habria que tener
    el cable puesto solo para ver que lee la camara, que es la mitad del
    desarrollo del proyecto.
    """

    def __init__(self) -> None:
        self.escritos = 0

    def escribir(self, datos) -> int:
        self.escritos += len(datos)
        return len(datos)

    def leer(self, cantidad: int, timeout: float = 0.0) -> bytes:
        return b""

    def hay_datos(self) -> bool:
        return False

    def cerrar(self) -> None:
        pass


def main() -> int:
    analizador = argparse.ArgumentParser(
        description="Lee digitos con la camara y los manda por serie a la ESP-A")
    analizador.add_argument("--puerto", default=configuracion.PUERTO_SERIE)
    analizador.add_argument("--baudios", type=int, default=configuracion.BAUDIOS)
    analizador.add_argument("--fuente", default="cam:0",
                            help="'cam:N' para la webcam N, 'sim' para la camara sintetica")
    analizador.add_argument("--digitos", default="",
                            help="con --fuente sim: digitos a mostrar, p.ej. '3' o '0,1,2'")
    analizador.add_argument("--marcos", type=int, default=0, help="0 = infinito")
    analizador.add_argument("--confianza", type=int, default=configuracion.CONFIANZA_MINIMA)
    analizador.add_argument("--sin-ventana", action="store_true")
    analizador.add_argument("--sin-puerto", action="store_true",
                            help="no manda nada: solo mira y dice que ve. "
                                 "Para comprobar el reconocimiento sin ESPs")
    analizador.add_argument("--listar", action="store_true", help="lista los puertos y sale")
    argumentos = analizador.parse_args()

    if argumentos.listar:
        puertos = transportes.listar_puertos()
        if not puertos:
            print("No hay ningun puerto serie.")
            print("Conecta un conversor USB-TTL y vuelve a intentarlo.")
            print("En Windows, mira tambien el Administrador de dispositivos.")
        else:
            for linea in puertos:
                print("  " + linea)
        return 0

    pesos = os.path.join(RAIZ, configuracion.RUTA_PESOS)
    try:
        modelo = DigitCNN.cargar(pesos)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1
    print("Pesos cargados: %s" % os.path.relpath(pesos, RAIZ))

    if argumentos.fuente == "sim":
        digitos = [int(d) for d in argumentos.digitos.split(",") if d.strip().isdigit()]
        fuente = CamaraSintetica(digitos or None)
        print("Fuente: camara sintetica (mismos digitos y preprocesado que el entrenamiento)")
    else:
        try:
            indice = int(argumentos.fuente.split(":")[1])
        except (IndexError, ValueError):
            indice = 0
        try:
            fuente = CamaraReal(indice)
        except RuntimeError as exc:
            # Sin webcam no se puede seguir por aqui. Se dice por que y se ofrece
            # la camara sintetica, que es el mismo generador del entrenamiento y
            # por eso sirve para comprobar el reconocimiento entero.
            print(str(exc))
            print("Si solo quieres probar el reconocimiento, la camara sintetica")
            print("usa las mismas imagenes con las que se entreno:")
            print("    python -m pc.app --fuente sim --puerto COM99 --marcos 10")
            print("O la cadena completa, con las dos ESP simuladas:")
            print("    python -m simulacion.simular --marcos 10")
            return 3

    if argumentos.sin_puerto:
        transporte = _AVeredas()
        print("Modo sin puerto: se reconoce pero no se manda nada.")
        print("Cada linea dice que ha visto el modelo. Para la cadena entera,")
        print("usa el simulador:  python -m simulacion.simular --marcos 10")
    else:
        transporte = transportes.Serial(argumentos.puerto, argumentos.baudios)
        if not transporte.conectado:
            print("No se pudo abrir %s: %s" % (argumentos.puerto, transporte.ultimo_error))
            print("Puertos que hay ahora mismo:")
            for linea in transportes.listar_puertos() or ["  ninguno"]:
                print("  " + linea)
            print("El correcto se indica con --puerto. Y si solo quieres probar")
            print("el reconocimiento sin cable:")
            print("    python -m pc.app --fuente sim --sin-puerto --marcos 10")
            print("Y la cadena entera, con las dos ESP simuladas:")
            print("    python -m simulacion.simular --marcos 10")
            transporte.cerrar()
            return 2
        print("Puerto serie abierto: %s a %d baudios"
              % (argumentos.puerto, argumentos.baudios))

    lector = LectorDigitos(modelo, transporte, argumentos.confianza, registrar_consola)
    try:
        with fuente:
            if argumentos.sin_ventana or argumentos.fuente == "sim":
                lector.bucle(fuente, argumentos.marcos, verbose=False, pausa_ms=40)
            else:
                print("Escribe un digito del 0 al 9 delante de la camara.")
                print("Q para salir.")
                lector.bucle(fuente, argumentos.marcos, verbose=True)
    except KeyboardInterrupt:
        print("\ninterrumpido")
    finally:
        transporte.cerrar()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
