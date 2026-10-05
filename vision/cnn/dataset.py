
from __future__ import annotations

import os

import cv2
import numpy as np

from vision import preproceso

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




def _papel(rng: np.random.Generator) -> np.ndarray:
    
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
   
    eleccion = rng.random()
    if eleccion < 0.62:
        return int(rng.integers(8, 60))
    if eleccion < 0.92:
        return int(rng.integers(60, 130))
    return int(rng.integers(200, 245))


def _trazo(digito: int, rng: np.random.Generator) -> np.ndarray:
    
    texto = str(digito)
    for _ in range(40):
        fuente = int(rng.choice(FUENTES))
        escala = float(rng.uniform(2.4, 6.0))
        grosor = 1 if fuente == cv2.FONT_HERSHEY_PLAIN else int(rng.integers(1, 4))

        (ancho_texto, alto_texto), base = cv2.getTextSize(texto, fuente, escala, grosor)
        if ancho_texto < 10 or alto_texto < 30:
            continue                                  

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
  
    gris = _papel(rng)
    mascara = _trazo(digito, rng)
    if cv2.countNonZero(mascara) == 0:
        return cv2.cvtColor(gris.astype(np.uint8), cv2.COLOR_GRAY2BGR)

    tinta = _gris_de_tinta(rng)
    alfa = mascara / 255.0                      
    gris = gris * (1.0 - alfa) + float(tinta) * alfa

   
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




def construir(n_por_digito: int = 800, semilla: int = 20260903,
              max_descartes: float = 0.25, cache: bool = True) -> tuple:
   
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
                       
                        % digito)
                continue
            xs.append(entrada[0])
            ys.append(digito)
            juntados += 1

    X = np.stack(xs).astype(np.float32)[:, None, :, :]
    y = np.array(ys, dtype=np.int64)

    
    orden = rng.permutation(len(y))
    X, y = X[orden], y[orden]

    if ruta is not None:
        np.savez_compressed(ruta, X=X, y=y, descartados=descartados)
    return X, y, descartados
