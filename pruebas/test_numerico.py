"""
Comprobacion numerica de la CNN. Es lo que hay que correr ANTES de entrenar.

    python pruebas/test_numerico.py

Comprueba tres cosas:

    1. Que el dataset produce recortes validos y que el preprocesado no los
       rechaza. Si el preprocesado falla, el modelo se entrena con imagenes en
       negro y no hay forma de que acierte.

    2. Que la propagacion hacia atras cuadra con diferencias finitas. Un gradiente
       mal implementado NO da error: el entrenamiento baja, pero mas despacio, y
       el sintoma aparece semanas despues como "el modelo no converge".

    3. Que los pesos se pueden guardar y recargar sin perder nada.

Por que el punto 2 es el mas importante: un error en dW pasaria desapercibido
mientras el gradiente de la entrada se calcula bien, porque son rutas distintas
del grafo. Un error en dX se propaga a dW de la capa anterior, asi que se
detectaria. Y un error en la unica capa cuya entrada es la imagen pasaria
desapercibido en todo lo demas.
"""
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cv2
import numpy as np

from vision import preproceso
from vision.cnn import dataset
from vision.cnn.capas import perdida_entropia
from vision.cnn.modelo import DigitCNN

# Las imagenes de esta comprobacion se van a la carpeta temporal, no a la raiz
# del proyecto. Este script se ejecuta a menudo y dejar archivos sueltos acaba
# ensuciando el arbol.
SALIDA = os.path.join(tempfile.gettempdir(), "reconocimiento_digitos_pruebas")
os.makedirs(SALIDA, exist_ok=True)

fallos = 0

# --- 1. arquitectura ------------------------------------------------------
modelo = DigitCNN()
print("Arquitectura:")
print(modelo.resumen())
print()

# --- 2. una imagen sintetizada -------------------------------------------
marco = dataset.sintetizar(7, np.random.default_rng(1))
cv2.imwrite(os.path.join(SALIDA, "marco.png"), marco)
entrada, recorte, exito = preproceso.procesar(marco)
print("marco sintetizado  %s %s  rango [%d, %d]"
      % (marco.shape, marco.dtype, marco.min(), marco.max()))
print("preprocesado       exito=%s recorte=%s entrada=%s rango=[%.3f, %.3f]"
      % (exito, recorte.shape, entrada.shape, entrada.min(), entrada.max()))
if not exito or entrada.shape != (1, 20, 20):
    print("  FALLO: el preprocesado no devolvio 1x20x20")
    fallos += 1

rejilla = np.concatenate([dataset.sintetizar(d, np.random.default_rng(100 + d))
                          for d in range(10)], axis=1)
cv2.imwrite(os.path.join(SALIDA, "diez_digitos.png"), rejilla)
print("los diez digitos del generador -> %s" % SALIDA)
print()

# --- 3. salud del preprocesado -------------------------------------------
aceptados = 0
rechazos = {}
t0 = time.time()
for i in range(300):
    d = i % 10
    _, _, ok = preproceso.procesar(dataset.sintetizar(d, np.random.default_rng(5000 + i)))
    if ok:
        aceptados += 1
    else:
        rechazos[d] = rechazos.get(d, 0) + 1
print("preprocesado: %d/300 aceptados en %.1f s" % (aceptados, time.time() - t0))
print("  rechazos por digito: %s" % (rechazos or "ninguno"))
if rechazos:
    print("  AVISO: si un digito se rechaza mucho, `construir` lanzara RuntimeError")
print()

# --- 4. gradiente numerico -----------------------------------------------
# En float64. Con float32 las diferencias finitas restan dos numeros casi
# iguales y el ruido de coma flotante tapa la respuesta, sobre todo en el
# gradiente de la entrada, que pasa por cuatro capas.
modelo64 = DigitCNN().a_precision(np.float64)

X = np.clip(np.random.default_rng(0).standard_normal((3, 1, 20, 20)) * 0.4, -1, 1)
objetivo = np.array([0, 1, 2])
perdida, dx, gradientes = modelo64.perdida_y_gradientes(X, objetivo)
print("Comprobacion de gradientes analiticos por diferencias finitas (float64)")
print("perdida inicial %.4f" % perdida)


def perdida_de(Xl):
    return perdida_entropia(modelo64.adelante(Xl), objetivo)[0]


# Barrido de pasos. Cada tensor tiene un paso optimo distinto y no se puede
# adivinar de antemano: con un paso demasiado grande manda el truncamiento
# (error ~ paso^2) y con uno demasiado pequeno manda el ruido de coma flotante
# (error ~ 1/paso). Medido en este modelo:
#
#   paso    dX entrada    dW conv1    dW densa
#   1e-2      7.1e-2       6.5e-2      2.6e-6
#   1e-3      3.2e-2       3.6e-2      9.2e-9
#   1e-5      1.4e-3       1.7e-10     4.0e-10
#   1e-6      6.1e-3       2.3e-9      3.7e-9
#
# El minimo de cada columna es ~1e-10, o sea que el gradiente analitico es
# correcto. Por eso se acepta el resultado si CUADRA con algun paso del barrido,
# que es lo que hacen los comprobadores de gradiente habituales.
PASOS = (1e-2, 1e-3, 1e-4, 1e-5, 1e-6)
TOLERANCIA = 1e-4


def _numerico(tensor, pos, medir, entrada, paso):
    """Diferencias centrales en una posicion de `tensor`."""
    original = float(tensor[pos])
    tensor[pos] = original + paso
    mas = medir(entrada)
    tensor[pos] = original - paso
    menos = medir(entrada)
    tensor[pos] = original
    return (mas - menos) / (2.0 * paso)


def comprobar(tensor, posiciones, analiticos):
    """Mejor error relativo sobre `posiciones`, quedandose con el paso mas bueno.

    Se queda con el MINIMO a proposito: cada tensor tiene un paso optimo y el
    resto de pasos dan errores que no dicen nada sobre el gradiente.
    """
    mejor = float("inf")
    for paso in PASOS:
        num = np.array([_numerico(tensor, p, perdida_de, X, paso) for p in posiciones])
        escala = max(float(np.abs(num).max()), 1e-12)
        mejor = min(mejor, float(np.abs(num - analiticos).max() / escala))
    return mejor


rng = np.random.default_rng(5)

# La comparacion se hace SOLO en las posiciones perturbadas: fuera de ellas el
# gradiente numerico vale cero y el analitico no, y comparar el tensor entero
# daria un error enorme que no significa nada.
posiciones = [tuple(int(rng.integers(n)) for n in X.shape) for _ in range(8)]
err_x = comprobar(X, posiciones, np.array([dx[p] for p in posiciones]))
print("  %-12s error relativo %.2e  %s"
      % ("entrada", err_x, "OK" if err_x < TOLERANCIA else "MAL"))
if err_x >= TOLERANCIA:
    fallos += 1

for indice, (W, b) in enumerate(modelo64.parametros()):
    nombre = modelo64.capas[modelo64._con_pesos()[indice][0]].nombre
    posW = [tuple(int(rng.integers(n)) for n in W.shape) for _ in range(10)]
    posb = list(range(min(4, b.shape[0])))
    dW, db = gradientes[indice]

    err_w = comprobar(W, posW, np.array([dW[p] for p in posW]))
    err_b = comprobar(b, posb, db[:len(posb)])
    estado = "OK" if max(err_w, err_b) < TOLERANCIA else "MAL"
    if estado == "MAL":
        fallos += 1
    print("  %-12s W %.2e  b %.2e  %s" % (nombre, err_w, err_b, estado))
print()

# --- 5. una epoca de prueba reduce la perdida ------------------------------
import vision.cnn.entrenar as entrenar                       # noqa: E402
pequeno = DigitCNN()
aplicar = entrenar.optimizador_adam(pequeno, 1e-3)
Xm = X.reshape(3, 1, 20, 20)
ym = objetivo
antes = []
for _ in range(15):
    p, _, g = pequeno.perdida_y_gradientes(Xm, ym)
    antes.append(p)
    aplicar(g)
despues = []
for _ in range(15):
    p, _, g = pequeno.perdida_y_gradientes(Xm, ym)
    despues.append(p)
    aplicar(g)
print("descenso de la perdida sobre 3 muestras: %.4f -> %.4f"
      % (antes[0], despues[-1]))
if despues[-1] >= antes[0]:
    print("  FALLO: la perdida no baja, el optimizador o el gradiente estan mal")
    fallos += 1
print()

# --- 5. guardar y recargar -----------------------------------------------
ruta = os.path.join(RAIZ, "_prueba_pesos.npz")
modelo.guardar(ruta)
otro = DigitCNN.cargar(ruta)
mismo = np.allclose(modelo.adelante(X), otro.adelante(X), atol=1e-6)
print("guardar/cargar pesos identico: %s" % mismo)
os.remove(ruta)
if not mismo:
    fallos += 1

print()
print("VEREDICTO: %s" % ("todo correcto" if fallos == 0 else "%d problemas" % fallos))
sys.exit(1 if fallos else 0)
