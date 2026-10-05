"""
Generador del conjunto de entrenamiento.

POR QUE DATOS SINTETICOS Y NO MNIST
------------------------------------
MNIST son digitos de 28x28 en escala de grises sobre fondo negro, ya
centrados, sin ruido y escritos por personas distintas. La camara de este
proyecto ve otra cosa: papel, luz de techo en degradado, el borde del marco,
un boligrafo que no carga bien y el dedo del usuario todavia en la escena.

Un modelo entrenado solo con MNIST baja del 80% de acierto en cuanto la
iluminacion se mueve, porque se ha aprendido el fondo, no el digito. Aqui se
generan imagenes que ya tienen esos defectos y despues se les pasa el MISMO
preprocesado que en inferencia, de modo que la distribucion con la que se
entrena y la que se ve en produccion son la misma.

AQUI ESTA EL TRUCO IMPORTANTE
------------------------------
`sintetizar` devuelve el marco crudo de 240x320, no los 20x20. El recorte lo
hace `vision.preproceso.procesar`, el mismo codigo que corre contra la camara.
Si el dataset se armara con recortes hechos aparte, cualquier diferencia entre
los dos caminos se comeria la exactitud sin que se notara en el entrenamiento.
"""

from __future__ import annotations

import os

import cv2
import numpy as np

from vision import preproceso

# Fuentes de OpenCV con formas muy distintas: sin serifa, de trazo simple, con
# pie, de bloque, casi manuscrita. Mezclarlas es lo que evita que el modelo se
# case con una sola.
FUENTES = (
    cv2.FONT_HERSHEY_SIMPLEX,
    cv2.FONT_HERSHEY_PLAIN,
    cv2.FONT_HERSHEY_DUPLEX,
    cv2.FONT_HERSHEY_COMPLEX,
    cv2.FONT_HERSHEY_TRIPLEX,
    cv2.FONT_HERSHEY_COMPLEX_SMALL,
)

ANCHO = 240
ALTO = 320


# ------------------------------------------------------------------- escena ----

def _papel(rng: np.random.Generator) -> np.ndarray:
    """Fondo de papel con el foco de luz descentrado.

    LA INTENSIDAD DEL DEGRADADO ESTA LIMITADA A PROPOSITO
    ------------------------------------------------------
    Un tramo de papel bajo una lampara de mesa no varia mas de un 20% entre la
    zona iluminada y la que le queda en sombra. La primera version de este
    generador multiplicaba por hasta 0.72 y restaba otro 42% por el foco, con lo
    que el papelLlegaba a medir 132 en una esquina y 246 en la otra.

    Con esa dispersion Otsu deja de separar "tinta de papel" y separa "papel
    claro de papel oscuro": el umbral cae en medio del papel, la mitad del papel
    queda como tinta y el contorno mas grande de la imagen es medio pliego. Se
    ve mirando el recorte 20x20: en lugar de un digito sale un bulto.

    Que el dataset sea dificil es bueno; que sea fisicamente imposible no. Con
    estos valores el modelo tiene que aguantar ruido y variacion de tono de
    verdad, sin que la escena llegue a ser irreconocible.
    """
    vertical = np.linspace(0.88, 1.06, ALTO, dtype=np.float32)[:, None]
    horizontal = np.linspace(0.93, 1.07, ANCHO, dtype=np.float32)[None, :]
    base = (vertical * horizontal * float(rng.uniform(200, 245))).astype(np.float32)

    # El foco no esta en el centro: la sombra del usuario tapa una esquina.
    cx = float(rng.uniform(0.25, 0.75)) * ANCHO
    cy = float(rng.uniform(0.20, 0.55)) * ALTO
    yy, xx = np.mgrid[0:ALTO, 0:ANCHO].astype(np.float32)
    distancia = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / float(ANCHO)
    base *= 1.0 - 0.22 * np.clip(distancia - 0.25, 0.0, None) / 0.75

    base += rng.normal(0, float(rng.uniform(1.0, 4.0)), base.shape).astype(np.float32)
    base += cv2.GaussianBlur(base, (0, 0), float(rng.uniform(0.6, 3.0))) * 0.10
    return np.clip(base, 60, 255)


def _gris_de_tinta(rng: np.random.Generator) -> int:
    """Gris del trazo: boli casi negro, lapis gris claro, o marcador blanco
    sobre cartulina oscura."""
    eleccion = rng.random()
    if eleccion < 0.62:
        return int(rng.integers(8, 60))
    if eleccion < 0.92:
        return int(rng.integers(60, 130))
    return int(rng.integers(200, 245))


def _trazo(digito: int, rng: np.random.Generator) -> np.ndarray:
    """Mascara binaria 240x320 con el digito, rotado y centrado con ruido."""
    texto = str(digito)
    for _ in range(40):
        fuente = int(rng.choice(FUENTES))
        escala = float(rng.uniform(2.4, 6.0))
        grosor = 1 if fuente == cv2.FONT_HERSHEY_PLAIN else int(rng.integers(1, 4))

        (ancho_texto, alto_texto), base = cv2.getTextSize(texto, fuente, escala, grosor)
        if ancho_texto < 10 or alto_texto < 30:
            continue                                   # escala inutil, se prueba otra

        holgura = 24
        mosaico = np.zeros((alto_texto + base + 2 * holgura,
                            ancho_texto + 2 * holgura), np.uint8)
        cv2.putText(mosaico, texto, (holgura, holgura + alto_texto),
                    fuente, escala, 255, grosor, cv2.LINE_AA)

        angulo = float(rng.uniform(-18, 18))
        M = cv2.getRotationMatrix2D((mosaico.shape[1] / 2.0, mosaico.shape[0] / 2.0),
                                    angulo, 1.0)
        girado = cv2.warpAffine(mosaico, M, (mosaico.shape[1], mosaico.shape[0]),
                                flags=cv2.INTER_LINEAR, borderValue=0)

        if girado.shape[0] >= ALTO or girado.shape[1] >= ANCHO:
            continue

        x = int(round((ANCHO - girado.shape[1]) / 2.0 + float(rng.uniform(-14, 14))))
        y = int(round((ALTO - girado.shape[0]) / 2.0 + float(rng.uniform(-14, 14))))

        mascara = np.zeros((ALTO, ANCHO), np.uint8)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(ANCHO, x + girado.shape[1]), min(ALTO, y + girado.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue
        mascara[y0:y1, x0:x1] = girado[y0 - y:y1 - y, x0 - x:x1 - x]

        pixeles = cv2.countNonZero(mascara)
        if 0.02 * mascara.size <= pixeles <= 0.30 * mascara.size:
            return mascara
    return np.zeros((ALTO, ANCHO), np.uint8)


def sintetizar(digito: int, rng: np.random.Generator) -> np.ndarray:
    """Un marco de camara de 240x320 en BGR con un digito y sus defeitos."""
    gris = _papel(rng)
    mascara = _trazo(digito, rng)
    if cv2.countNonZero(mascara) == 0:
        return cv2.cvtColor(gris.astype(np.uint8), cv2.COLOR_GRAY2BGR)

    tinta = _gris_de_tinta(rng)
    alfa = mascara / 255.0                       # (ALTO, ANCHO), misma forma que `gris`
    gris = gris * (1.0 - alfa) + float(tinta) * alfa

    # Ligera perspectiva: la camara nunca esta perfectamente perpendicular.
    #
    # Se deforma en un lienzo AMPLIADO y luego se recorta el centro, de forma
    # que el papel llega hasta el borde en los cuatro lados. Las dos alternativas
    # que se probaron primero dejaban un marco negro alrededor, y ese marco
    # rompia la deteccion de polaridad del preprocesado: las esquinas son justo
    # la zona que se usa como muestra del fondo, y ahi habia relleno en vez de
    # papel. Con el recorte central el fondo es papel hasta el ultimo pixel.
    desv = float(rng.uniform(0, 7))
    origen = np.float32([[rng.uniform(0, desv), rng.uniform(0, desv)],
                         [ANCHO - rng.uniform(0, desv), rng.uniform(0, desv)],
                         [ANCHO - rng.uniform(0, desv), ALTO - rng.uniform(0, desv)],
                         [rng.uniform(0, desv), ALTO - rng.uniform(0, desv)]])
    destino = np.float32([[0, 0], [ANCHO, 0], [ANCHO, ALTO], [0, ALTO]])
    holgura = int(np.ceil(desv)) + 1
    H = cv2.getPerspectiveTransform(origen, destino)
    gris = cv2.warpPerspective(gris.astype(np.float32), H,
                               (ANCHO + 2 * holgura, ALTO + 2 * holgura),
                               borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    gris = gris[holgura:holgura + ALTO, holgura:holgura + ANCHO]

    marco = cv2.cvtColor(np.clip(gris, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    marco = cv2.GaussianBlur(marco, (3, 3), float(rng.uniform(0.3, 1.3)))
    ruido = rng.normal(0, float(rng.uniform(1.0, 7.0)), marco.shape)
    return np.clip(marco.astype(np.float32) + ruido, 0, 255).astype(np.uint8)


def muestra(digito: int, semilla: int = 0) -> np.ndarray:
    return sintetizar(digito, np.random.default_rng(semilla))


# ------------------------------------------------------------------ conjunto ---

def construir(n_por_digito: int = 800, semilla: int = 20260903,
              max_descartes: float = 0.25, cache: bool = True) -> tuple:
    """Arma (X, y, descartados). X con forma (N, 1, 20, 20) float32, y con (N,).

    Se descarta la muestra cuando el preprocesado no encuentra contorno. Eso
    pasa de verdad, sobre todo con el "1" en PLAIN, que es tan estrecho que el
    filtro de proporcion lo puede rechazar; y conviene mas descartar que
    meterle al modelo un 20x20 en negro con la etiqueta de un "3".

    El resultado se guarda en `cache_<n>_<semilla>.npz` junto al modulo.
    Generar 8.000 muestras cuesta unos cuatro minutos y es determinista, asi que
    repetirlo en cada entrenamiento solo wastes tiempo. Ademas, si el generador
    cambiara entre dos-entrenamientos, comparar sus numeros no significaria nada.
    """
    ruta = None
    if cache:
        os.makedirs(os.path.dirname(os.path.abspath(__file__)), exist_ok=True)
        ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cache_%d_%d.npz" % (n_por_digito, semilla))
        if os.path.exists(ruta):
            with np.load(ruta, allow_pickle=False) as paquete:
                return paquete["X"], paquete["y"], int(paquete["descartados"])

    rng = np.random.default_rng(semilla)
    xs, ys, descartados = [], [], 0
    techo = max(1, int(n_por_digito * max_descartes))

    for digito in range(10):
        descartados_clase = 0
        juntados = 0
        while juntados < n_por_digito:
            entrada, _, exito = preproceso.procesar(sintetizar(digito, rng))
            if not exito or float(entrada.max()) <= 0.05:
                descartados += 1
                descartados_clase += 1
                if descartados_clase > techo:
                    raise RuntimeError(
                        "vision/preproceso.py rechaza casi todas las muestras del "
                        "digito %d: revisa los filtros de proporcion y relleno"
                        % digito)
                continue
            xs.append(entrada[0])
            ys.append(digito)
            juntados += 1

    X = np.stack(xs).astype(np.float32)[:, None, :, :]
    y = np.array(ys, dtype=np.int64)

    # Barajar: sin esto el optimizador veria todos los 0, luego todos los 1, y
    # el ultimo lote de cada epoca seria completamente distinto a los demas.
    orden = rng.permutation(len(y))
    X, y = X[orden], y[orden]

    if ruta is not None:
        np.savez_compressed(ruta, X=X, y=y, descartados=descartados)
    return X, y, descartados
