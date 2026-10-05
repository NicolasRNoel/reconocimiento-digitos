"""
La red: DigitCNN.

ARQUITECTURA
------------
    entrada     1 x 20 x 20   el recorte en gris, normalizado a 0..1
    conv 3x3     8 filtros    bordes y curvas: el arco del 3, la union del 5
    relu
    maxpool 2x2               8 x 10 x 10   se queda con lo que mas brilla
    conv 3x3    16 filtros    combinaciones: el ojo del 8, la barra del 7
    relu
    maxpool 2x2              16 x  5 x  5   400 numeros que describen la forma
    flatten      400
    densa        64           64 rasgos: angulos, trazos,_Intensidad
    relu
    densa        10           una neurona por digito
    softmax                    probabilidades que suman 1

POR QUE 20x20 Y NO 28x28
------------------------
El recorte ya viene alineado y centrado por el preprocesado, asi que la CNN no
necesita aprender a ser invariante a la traslacion. Bajando a 20x20 la primera
capa convolucional cuesta 8 parametros en vez de 25, y el total baja de unos
28.000 a 15.000. En una red tan pequena esa diferencia es la diferencia entre
acierto y ruido.

15.000 parametros contra 8.000 muestras de entrenamiento: la proporcion esta en
el punto en el que la red tiene capacidad de sobra para el problema y todavia
no se ha comido el fondo. Una capa mas y se sobreajusta al degradado de luz de
las muestras sinteticas, y despues falla con la camara real.
"""

from __future__ import annotations

import os

import numpy as np

from vision.cnn.capas import (Convolucion, Densa, Flatten, MaxPool2, Relu,
                              Softmax, perdida_entropia)

CLASES = 10


class DigitCNN:
    def __init__(self) -> None:
        self.capas = [
            Convolucion(1, 8, semilla=1),      # 1 -> 8   (80 parametros)
            Relu(),
            MaxPool2(),                        # 8 x 10 x 10
            Convolucion(8, 16, semilla=2),     # 8 -> 16  (1.168)
            Relu(),
            MaxPool2(),                        # 16 x 5 x 5 = 400
            Flatten(),
            Densa(400, 64, semilla=3),         # 25.664
            Relu(),
            Densa(64, 10, semilla=4),          # 650
            Softmax(),
        ]

    # -------------------------------------------------------------- estructura --

    def _con_pesos(self):
        return [(i, c) for i, c in enumerate(self.capas) if c.parametros()]

    def parametros(self):
        """Lista de (W, b) en orden de capa, la que recorre `atras`."""
        return [c.parametros() for _, c in self._con_pesos()]

    def resumen(self) -> str:
        lineas = []
        forma = (1, 1, 20, 20)
        for capa in self.capas:
            forma = _forma_salida(capa, forma)
            pesos = capa.parametros()
            cuantos = sum(p.size for p in pesos)
            sufijo = "  %6d pesos" % cuantos if pesos else ""
            lineas.append("    %-12s -> %-16s%s" % (capa.nombre, str(forma), sufijo))
        total = sum(p.size for _, c in self._con_pesos() for p in c.parametros())
        return "\n".join(lineas) + "\n    %d parametros en total" % total

    # -------------------------------------------------------------- propagacion --

    def adelante(self, x: np.ndarray) -> np.ndarray:
        """x con forma (N, 1, 20, 20) o (1, 20, 20). Devuelve (N, 10)."""
        x = np.asarray(x, dtype=np.float32)
        if x.ndim == 3:
            x = x[None, ...]
        for capa in self.capas:
            x = capa.adelante(x)
        return x

    def _retropropagar(self, dx: np.ndarray):
        """Recorre las capas de derecha a izquierda.

        Devuelve (gradiente respecto a la entrada, [(dW, db)]). La lista de
        gradientes vuelve a estar en orden de capa aunque el recorrido sea
        inverso: el optimizador empareja por posicion, asi que si se invirtiera
        aqui los pesos y los gradientes se emparejarian cruzados y el
        entrenamiento caeria sin dar ningun error.
        """
        gradientes = {}
        d = dx
        for indice in range(len(self.capas) - 1, -1, -1):
            capa = self.capas[indice]
            salida = capa.atras(d)
            if isinstance(salida, tuple):
                d = salida[0]
                gradientes[indice] = (salida[1], salida[2])
            else:
                d = salida
        return d, [gradientes[i] for i, _ in self._con_pesos()]

    def atras(self, dx: np.ndarray):
        """Solo los gradientes de los pesos, que es lo que consume el optimizador."""
        return self._retropropagar(dx)[1]

    def perdida_y_gradientes(self, X: np.ndarray, y: np.ndarray):
        """(perdida, dX, [(dW, db)]). Es lo que usa el entrenamiento.

        Va en una sola pasada porque `atras` consume las activaciones guardadas:
        llamar a `adelante` otra vez entre medias seria el doble de trabajo.
        """
        probabilidades = self.adelante(X)
        perdida, dprobabilidades = perdida_entropia(probabilidades, y)
        gradiente_entrada, gradientes_pesos = self._retropropagar(dprobabilidades)
        return perdida, gradiente_entrada, gradientes_pesos

    def a_precision(self, tipo=np.float64) -> "DigitCNN":
        """Devuelve el mismo modelo con todos los pesos en ese tipo de dato.

        Hace falta porque asignar in situ (`W[...] = W.astype(...)`) NO cambia
        el tipo de un array de numpy: se queda en el viejo y downcastea. Hay que
        reemplazar el objeto.

        En float64, la comprobacion de gradientes por diferencias finitas deja
        de estar dominada por el ruido de coma flotante. En float32 el gradiente
        de la entrada, que pasa por cuatro capas, sale con un error relativo del
        6% aunque el calculo sea correcto.
        """
        for _, capa in self._con_pesos():
            W, b = capa.parametros()
            capa.W = W.astype(tipo)
            capa.b = b.astype(tipo)
        return self

    def predecir(self, x: np.ndarray) -> tuple:
        """Devuelve (digito, confianza 0..100, probabilidades).

        La confianza es la probabilidad del digito ganador. No se inventa un
        margen entre las dos primeras: con 10 clases y softmax ese margen no
        correlates con nada util y hace que la LCD parpadee.
        """
        probabilidades = self.adelante(x)[0]
        digito = int(np.argmax(probabilidades))
        return digito, float(probabilidades[digito] * 100.0), probabilidades

    # ----------------------------------------------------------------- persisted --

    def guardar(self, ruta: str) -> str:
        """Pesos en .npz. `allow_pickle=False` porque solo son numeros."""
        os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
        datos = {}
        for indice, capa in self._con_pesos():
            W, b = capa.parametros()
            datos["%d_W" % indice] = W
            datos["%d_b" % indice] = b
        np.savez_compressed(ruta, **datos)
        return ruta

    @classmethod
    def cargar(cls, ruta: str) -> "DigitCNN":
        modelo = cls()
        if not os.path.exists(ruta):
            raise FileNotFoundError(
                "no existe %s. Entrena primero con:\n"
                "    python -m vision.cnn.entrenar" % ruta)
        with np.load(ruta, allow_pickle=False) as paquete:
            for indice, capa in modelo._con_pesos():
                W, b = capa.parametros()
                W[...] = paquete["%d_W" % indice]
                b[...] = paquete["%d_b" % indice]
        return modelo


def _forma_salida(capa, entrada):
    if isinstance(capa, Convolucion):
        n, _, h, w = entrada
        return (n, capa.filtros, h, w)
    if isinstance(capa, MaxPool2):
        n, c, h, w = entrada
        return (n, c, h // 2, w // 2)
    if isinstance(capa, Flatten):
        n = entrada[0]
        return (n, int(np.prod(entrada[1:])))
    if isinstance(capa, Densa):
        return (entrada[0], capa.W.shape[1])
    return entrada
