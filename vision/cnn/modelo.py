

from __future__ import annotations

import os

import numpy as np

from vision.cnn.capas import (Convolucion, Densa, Flatten, MaxPool2, Relu,
                              Softmax, perdida_entropia)

CLASES = 10


class DigitCNN:
    def __init__(self) -> None:
        self.capas = [
            Convolucion(1, 8, semilla=1),     
            Relu(),
            MaxPool2(),                      
            Convolucion(8, 16, semilla=2),     
            Relu(),
            MaxPool2(),                     
            Flatten(),
            Densa(400, 64, semilla=3),      
            Relu(),
            Densa(64, 10, semilla=4), 
            Softmax(),
        ]


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



    def adelante(self, x: np.ndarray) -> np.ndarray:
        """x con forma (N, 1, 20, 20) o (1, 20, 20). Devuelve (N, 10)."""
        x = np.asarray(x, dtype=np.float32)
        if x.ndim == 3:
            x = x[None, ...]
        for capa in self.capas:
            x = capa.adelante(x)
        return x

    def _retropropagar(self, dx: np.ndarray):
    
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
    
        return self._retropropagar(dx)[1]

    def perdida_y_gradientes(self, X: np.ndarray, y: np.ndarray):
       
        probabilidades = self.adelante(X)
        perdida, dprobabilidades = perdida_entropia(probabilidades, y)
        gradiente_entrada, gradientes_pesos = self._retropropagar(dprobabilidades)
        return perdida, gradiente_entrada, gradientes_pesos

    def a_precision(self, tipo=np.float64) -> "DigitCNN":
      
        for _, capa in self._con_pesos():
            W, b = capa.parametros()
            capa.W = W.astype(tipo)
            capa.b = b.astype(tipo)
        return self

    def predecir(self, x: np.ndarray) -> tuple:
      
        probabilidades = self.adelante(x)[0]
        digito = int(np.argmax(probabilidades))
        return digito, float(probabilidades[digito] * 100.0), probabilidades



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
