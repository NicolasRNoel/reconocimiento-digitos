"""
Entrena la CNN y guarda los pesos en vision/cnn/pesos.npz.

    python -m vision.cnn.entrenar                      # 800 por digito, 25 epocas
    python -m vision.cnn.entrenar --n 2000 --epocas 40  # mas datos, mas epocas

QUE SE ESTA ENTRENANDO Y QUE NO
-------------------------------
Se entrena con imagenes sinteticas de digitos escritos con las fuentes de
OpenCV sobre un papel con el foco de luz descentrado, y pasando despues por el
MISMO preprocesado que usa la camara. Eso cubre el trazo, el papel, la
iluminacion y el ruido del sensor.

NO cubre la letra humana. Si el usuario escribe el "1" con el angulo muy
marcado, o el "7" sin travesano, hay que generar muestras de ese estilo y
añadirlas: es el unico dato que esta simulacion no puede inventar. La funcion
`dataset.sintetizar` admite fuentes y grosores, asi que se pueden generar
variantes extra y concatenarlas antes de entrenar.

TAMANO DEL CONJUNTO
-------------------
Con 8.000 muestras la red es pequena para el problema y todavia no se ha comido
el fondo. Por encima de unos 40.000 se nota el sobreajuste a las muestras
sinteticas: sube el acierto en prueba sintetica y baja con la camara real.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import configuracion                                        # noqa: E402
from vision.cnn import dataset                              # noqa: E402
from vision.cnn.capas import perdida_entropia               # noqa: E402
from vision.cnn.modelo import DigitCNN                      # noqa: E402


# --------------------------------------------------------------- optimizador ----

def optimizador_adam(modelo: DigitCNN, tasa: float = 1e-3):
    """Adam a mano, para no arrastrar PyTorch solo por el optimizador.

    Guarda dos momentos por parametro. El sesgo y los pesos se mueven distinto:
    el sesgo suele tener mucha menos norma y con Adam los dos convergen a la
    misma velocidad.
    """
    grupos = modelo.parametros()
    planos = [p for grupo in grupos for p in grupo]
    m = [np.zeros_like(p) for p in planos]
    v = [np.zeros_like(p) for p in planos]
    beta1, beta2, epsilon, paso = 0.9, 0.999, 1e-8, 0

    def aplicar(gradientes):
        nonlocal paso
        paso += 1
        i = 0
        for grupo in gradientes:
            for g in grupo:
                m[i] = beta1 * m[i] + (1.0 - beta1) * g
                v[i] = beta2 * v[i] + (1.0 - beta2) * (g * g)
                m_hat = m[i] / (1.0 - beta1 ** paso)
                v_hat = v[i] / (1.0 - beta2 ** paso)
                planos[i] -= tasa * m_hat / (np.sqrt(v_hat) + epsilon)
                i += 1

    return aplicar


# ------------------------------------------------------------------ metricas ----

def exactitud(modelo: DigitCNN, X: np.ndarray, y: np.ndarray) -> float:
    if len(y) == 0:
        return 0.0
    return float((modelo.adelante(X).argmax(axis=1) == y).mean())


def matriz_confusion(modelo: DigitCNN, X: np.ndarray, y: np.ndarray) -> np.ndarray:
    predicho = modelo.adelante(X).argmax(axis=1)
    confusion = np.zeros((10, 10), dtype=int)
    for real, adivinado in zip(y, predicho):
        confusion[real, adivinado] += 1
    return confusion


# ------------------------------------------------------------------------ main --

def main() -> int:
    analizador = argparse.ArgumentParser(description="Entrena la CNN de digitos")
    analizador.add_argument("--n", type=int, default=800, help="muestras por digito")
    analizador.add_argument("--epocas", type=int, default=25)
    analizador.add_argument("--lote", type=int, default=128)
    analizador.add_argument("--tasa", type=float, default=1e-3)
    analizador.add_argument("--semilla", type=int, default=7)
    analizador.add_argument("--prueba", type=float, default=0.15,
                            help="fraccion reservada para el conjunto de prueba")
    analizador.add_argument("--salida", default=os.path.join(RAIZ, configuracion.RUTA_PESOS))
    argumentos = analizador.parse_args()

    print("=" * 72)
    print("ENTRENAMIENTO DE LA CNN DE DIGITOS")
    print("=" * 72)
    print("Arquitectura:")
    print(DigitCNN().resumen())
    print()

    print("Generando el conjunto (%d por digito, semilla %d)..."
          % (argumentos.n, argumentos.semilla))
    inicio = time.time()
    X, y, descartados = dataset.construir(argumentos.n, semilla=argumentos.semilla)
    print("  %d muestras en %.1f s, %d descartadas por el preprocesado"
          % (len(y), time.time() - inicio, descartados))

    rng = np.random.default_rng(argumentos.semilla)
    indices = rng.permutation(len(y))
    corte = int(len(y) * (1.0 - argumentos.prueba))
    X_ent, y_ent = X[indices[:corte]], y[indices[:corte]]
    X_pru, y_pru = X[indices[corte:]], y[indices[corte:]]

    # Se normaliza con la media del ENTRENAMIENTO y se aplica la misma a prueba.
    # Normalizar cada parte por su cuenta hace que la prueba sea mas facil que
    # el entrenamiento y el acierto sale inflado.
    media = float(X_ent.mean())
    desvio = float(X_ent.std()) + 1e-6
    X_ent = ((X_ent - media) / desvio).astype(np.float32)
    X_pru = ((X_pru - media) / desvio).astype(np.float32)
    print("  normalizacion: media %.4f, desvio %.4f   (entren %d / prueba %d)"
          % (media, desvio, len(y_ent), len(y_pru)))
    print()

    modelo = DigitCNN()
    aplicar = optimizador_adam(modelo, argumentos.tasa)

    mejor = -1.0
    for epoca in range(1, argumentos.epocas + 1):
        inicio = time.time()
        orden = rng.permutation(len(y_ent))
        perdida_total = 0.0
        bloques = 0

        for comienzo in range(0, len(orden), argumentos.lote):
            lote = orden[comienzo:comienzo + argumentos.lote]
            probabilidades = modelo.adelante(X_ent[lote])
            perdida, dprobabilidades = perdida_entropia(probabilidades, y_ent[lote])
            aplicar(modelo.atras(dprobabilidades))
            perdida_total += perdida
            bloques += 1

        acierto_ent = exactitud(modelo, X_ent, y_ent)
        acierto_pru = exactitud(modelo, X_pru, y_pru)
        marca = ""
        if acierto_pru > mejor:
            mejor = acierto_pru
            modelo.guardar(argumentos.salida)
            marca = "   <- pesos guardados"

        print("epoca %2d/%d  perdida %.4f  entrada %.4f  prueba %.4f  %5.1f s%s"
              % (epoca, argumentos.epocas, perdida_total / max(bloques, 1),
                 acierto_ent, acierto_pru, time.time() - inicio, marca))

    modelo = DigitCNN.cargar(argumentos.salida)
    print()
    print("Pesos en %s" % os.path.relpath(argumentos.salida, RAIZ))
    print("Mejor acierto en prueba: %.4f" % mejor)
    print()
    print("Matriz de confusion   filas = digito real, columnas = lo que dijo la red")
    print("      " + "".join("%6d" % i for i in range(10)))
    confusion = matriz_confusion(modelo, X_pru, y_pru)
    for real in range(10):
        print("real %d %s   aciertos %4d" % (
            real, "".join("%6d" % v for v in confusion[real]), confusion[real, real]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
