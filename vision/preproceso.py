
from __future__ import annotations

import cv2
import numpy as np

TAM = 20

_MORFOLOGIA_ABIERTA = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
_MORFOLOGIA_CERRADA = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))



def a_gris(marco) -> np.ndarray:
    """BGR/BGRA de la camara, o gris tal cual si ya lo es."""
    if marco is None or marco.size == 0:
        return np.zeros((TAM, TAM), np.uint8)
    if marco.ndim == 3:
        if marco.shape[2] == 4:
            return cv2.cvtColor(marco, cv2.COLOR_BGRA2GRAY)
        return cv2.cvtColor(marco, cv2.COLOR_BGR2GRAY)
    return marco.copy()


def _esquinas(gris: np.ndarray) -> np.ndarray:
 
    alto, ancho = gris.shape[:2]
    lado = max(1, min(alto, ancho) // 8)
    mascara = np.zeros(gris.shape[:2], bool)
    mascara[:lado, :lado] = True
    mascara[:lado, -lado:] = True
    mascara[-lado:, :lado] = True
    mascara[-lado:, -lado:] = True
    return mascara


def binarizar(gris: np.ndarray) -> np.ndarray:
   
    gris = cv2.GaussianBlur(gris, (3, 3), 0)
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

  
    if binaria[_esquinas(binaria)].mean() > 127.0:
        binaria = cv2.bitwise_not(binaria)

    binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, _MORFOLOGIA_ABIERTA)
    binaria = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, _MORFOLOGIA_CERRADA)
    return binaria


def area_de_interes(binaria: np.ndarray, area_min: float = 0.004,
                    area_max: float = 0.65):
   
    alto, ancho = binaria.shape[:2]
    total = float(alto * ancho)
    contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    mejor = None
    mejor_area = 0.0
    for contorno in contornos:
        area = cv2.contourArea(contorno)
        if not area_min * total <= area <= area_max * total:
            continue
        x, y, w, h = cv2.boundingRect(contorno)
        if w < 2 or h < 2:
            continue
       
        proporcion = h / float(w)
        if not 0.85 <= proporcion <= 4.0:
            continue
        
        relleno = area / float(w * h)
        if not 0.12 <= relleno <= 0.88:
            continue
        if area > mejor_area:
            mejor_area = area
            mejor = contorno

    if mejor is None:
        return np.zeros((1, 1), np.uint8), False

   
    mascara = np.zeros((alto, ancho), np.uint8)
    cv2.drawContours(mascara, [mejor], -1, 255, cv2.FILLED)
    mascara = _enderezar(mascara)

   
    filas, columnas = np.nonzero(mascara)
    if filas.size == 0:
        return np.zeros((1, 1), np.uint8), False

    margen = max(2, int(round(0.05 * max(int(columnas.max()) - int(columnas.min()),
                                        int(filas.max()) - int(filas.min())))))
    y0 = max(0, int(filas.min()) - margen)
    y1 = min(alto, int(filas.max()) + margen + 1)
    x0 = max(0, int(columnas.min()) - margen)
    x1 = min(ancho, int(columnas.max()) + margen + 1)
    return mascara[y0:y1, x0:x1], True


def _enderezar(mascara: np.ndarray) -> np.ndarray:
  

   
    puntos = cv2.findNonZero(mascara)
    if puntos is None:
        return mascara

    angulo = float(cv2.minAreaRect(puntos)[-1])
    if angulo > 45.0:
        angulo -= 90.0
    
    if not 0.4 <= abs(angulo) <= 20.0:
        return mascara

    momentos = cv2.moments(mascara, binaryImage=True)
    if momentos["m00"] <= 0:
        return mascara
    centro = (momentos["m10"] / momentos["m00"], momentos["m01"] / momentos["m00"])
    M = cv2.getRotationMatrix2D(centro, angulo, 1.0)
    return cv2.warpAffine(mascara, M, (mascara.shape[1], mascara.shape[0]),
                          flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=0)


def estirar_contraste(imagen: np.ndarray) -> np.ndarray:
    """Lleva el rango util a 0..255. Sin luz el trazo llega en gris medio."""
    if imagen.size == 0 or imagen.max() <= 0:
        return imagen
    return cv2.normalize(imagen, None, 0, 255, cv2.NORM_MINMAX)


def encuadrar(imagen: np.ndarray, tam: int = TAM) -> np.ndarray:
    """Escala a tam x tam conservando la proporcion y centra con relleno negro.

    Ese relleno es lo que mantiene la escala constante entre digitos: el "1"
    ocupa la misma altura que el "8" y solo es mas estrecho, asi que la CNN ve
    la misma tipografia a distinta altura y generaliza.
    """
    if imagen.size == 0:
        return np.zeros((tam, tam), np.uint8)

    estira = estirar_contraste(imagen)
    alto, ancho = estira.shape[:2]
    escala = min((tam - 2) / float(ancho), (tam - 2) / float(alto))
    nuevo_ancho = max(1, min(tam - 2, int(round(ancho * escala))))
    nuevo_alto = max(1, min(tam - 2, int(round(alto * escala))))
    redimensionado = cv2.resize(estira, (nuevo_ancho, nuevo_alto),
                                interpolation=cv2.INTER_AREA)

    lienzo = np.zeros((tam, tam), np.uint8)
    y = (tam - nuevo_alto) // 2
    x = (tam - nuevo_ancho) // 2
    lienzo[y:y + nuevo_alto, x:x + nuevo_ancho] = redimensionado
    return lienzo


def normalizar(imagen: np.ndarray) -> np.ndarray:
    """uint8 0..255 -> float32 0..1 con la forma (1, 20, 20) que espera la CNN."""
    return (imagen.astype(np.float32) / 255.0).reshape(1, imagen.shape[0], imagen.shape[1])

def procesar(marco) -> tuple:
    """Cadena completa. Devuelve (entrada_cnn, recorte_debug, exito)."""
    binaire = binarizar(a_gris(marco))
    recorte, exito = area_de_interes(binaire)
    lienzo = encuadrar(recorte) if exito else np.zeros((TAM, TAM), np.uint8)
    return normalizar(lienzo), recorte, exito


def a_20x20(marco) -> np.ndarray:
    """Atajo para el generador del dataset y para las pruebas."""
    return procesar(marco)[0]
