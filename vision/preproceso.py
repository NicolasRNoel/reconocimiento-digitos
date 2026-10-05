"""
Preprocesado de la imagen: de lo que ve la camara a los 20x20 que entra a la CNN.

ORDEN DE LAS OPERACIONES Y POR QUE EN ESE ORDEN
------------------------------------------------
1.gris        la camara entrega BGR de 3 canales y la CNN solo lee uno. Bajar a
              gris aqui ahorra dos tercios de memoria en los buffers siguientes.
2.desenfoque   un 3x3 de Gaussiana. Borra el ruido del sensor Y suaviza los
              bordes del digito, que es justo lo que sigue el detector de
              contornos despues.
3.Otsu        el umbral automatico. Con una hoja blanca y luz de techo el
              histograma es bimodal y Otsu clava el corte sin calibrar nada.
              Se fuerza la convencion "digito claro sobre fondo oscuro" y se
              recuerda con el signo de la media: si el recorte quedo mas oscuro
              que el fondo, se invierte. Asi el usuario escribe con cualquier
              rotulo y en cualquier color.
4.morfologia  apertura de 2x2 para borrar motas, luego cierre de 3x3 para tapar
              huecos del trazo. Sin esto un "8" con la curva sin cerrar genera
              dos contornos y el recorte se parte en dos.
5.contornos   el contorno mas grande que pase los filtros. La camara mira un
              solo digito, asi que el mas grande es el correcto; area,
              proporcion y relleno descartan el marco de la puerta, la mano y
              una mancha de sombra.
6.escudo      recorta la tinta real con un margen del 5%. Se recorta de los
              pixeles que de verdad hay, no del rectangulo teorico: ese viene en
              coma flotante y al pasarlo a indice se perdia hasta un pixel de
              la barra de un "7", que la CNN leia como un "1".
7.contraste    estirar el rango a 0..255. Con poca luz el trazo llega en gris
              medio y la CNN, entrenada con trazos sobre negro, pierde.
8.encuadre    escalar a 20x20 conservando la proporcion y rellenar con negro.
              Forzar el estiramiento parte los digitos estrechos como el "1",
              y la CNN nunca vio un "1" asi de gordo.
"""

from __future__ import annotations

import cv2
import numpy as np

TAM = 20

_MORFOLOGIA_ABIERTA = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
_MORFOLOGIA_CERRADA = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))


# ------------------------------------------------------------------ basicos ----

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
    """Las cuatro esquinas. Zona de la imagen que es fondo con certeza.

    El digito va en el centro, y las esquinas son el sitio donde menos
    probabilidad hay de que este. Se eligen las esquinas y no el anillo del
    borde porque el borde de la imagen puede haber sido tocado por una
    transformacion de perspectiva con relleno por replicacion: los pixeles
    repetidos pueden quedar mas claros o mas oscuros que el papel real y
    falsean cualquier media.
    """
    alto, ancho = gris.shape[:2]
    lado = max(1, min(alto, ancho) // 8)
    mascara = np.zeros(gris.shape[:2], bool)
    mascara[:lado, :lado] = True
    mascara[:lado, -lado:] = True
    mascara[-lado:, :lado] = True
    mascara[-lado:, -lado:] = True
    return mascara


def binarizar(gris: np.ndarray) -> np.ndarray:
    """Devuelve 255 para el trazo y 0 para el fondo, con umbral de Otsu.

    LA CONVENCION Y POR QUE SE COMPRUEBA
    -------------------------------------
    El objetivo es siempre "tinta blanca sobre fondo negro". Otsu con
    THRESH_BINARY devuelve lo que SUPERA el umbral en blanco, pero si el
    histograma tiene mas peso en la parte oscura (papel claro y mucha tinta
    delgada), el reparto se invierte y el papel sale en negro y la tinta en
    blanco. O sea: el resultado de Otsu no tiene convencion fija, depende de los
    datos.

    Por eso no se asume ninguna. Se mide el fondo en las cuatro esquinas, que
    es donde con mas seguridad hay papel y no digito, y se mira COMO quedo ese
    fondo en la binaria:

        el fondo quedo en BLANCO -> hay que invertir
        el fondo quedo en NEGRO  -> ya esta bien

    Que se mire en las esquinas y no en el centro importa: con un "8" el centro
    es un hueco y sale la lectura del fondo por casualidad, pero con un "0" a
    contraluz el centro cae justo encima del trazo. Y con el borde ya
    distorsionado por la perspectiva, cualquier media del anillo sale falseada
    por los pixeles replicados.
    """
    gris = cv2.GaussianBlur(gris, (3, 3), 0)
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Si el papel (las esquinas) salio en blanco, significa que la tinta es lo
    # oscuro y hay que dar vuelta para tenerla en blanco.
    if binaria[_esquinas(binaria)].mean() > 127.0:
        binaria = cv2.bitwise_not(binaria)

    binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, _MORFOLOGIA_ABIERTA)
    binaria = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, _MORFOLOGIA_CERRADA)
    return binaria


def area_de_interes(binaria: np.ndarray, area_min: float = 0.004,
                    area_max: float = 0.65):
    """Contorno mas grande que pase los filtros. Devuelve (recorte, exito).

    Los umbrales van en fraccion del area total y no en pixeles absolutos, para
    que el mismo codigo sirva con una webcam de 320x240 y con una de 1920x1080.
    """
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
        # Un digito es mas alto que ancho, pero no siempre: el "1" con pie es
        # casi cuadrado y un "4" escrito abierto puede salir tan ancho como
        # alto. Por debajo de 0.85 es una mancha; por encima de 4.0 es la mano,
        # el antebrazo o el marco de la puerta.
        proporcion = h / float(w)
        if not 0.85 <= proporcion <= 4.0:
            continue
        # El relleno mide cuanta tinta hay dentro del recuadro. Un digito lleno
        # ronda 0.35; un rectangulo solido llega a 1.0 y una sombra suelta baja
        # de 0.15.
        relleno = area / float(w * h)
        if not 0.12 <= relleno <= 0.88:
            continue
        if area > mejor_area:
            mejor_area = area
            mejor = contorno

    if mejor is None:
        return np.zeros((1, 1), np.uint8), False

    # `findContours` devuelve un array de forma (N, 1, 2). OpenCV interpreta eso
    # como una imagen de 2 canales y se niega a trabajar con el, asi que el
    # contorno se pinta primero en una mascara de un solo canal.
    mascara = np.zeros((alto, ancho), np.uint8)
    cv2.drawContours(mascara, [mejor], -1, 255, cv2.FILLED)
    mascara = _enderezar(mascara)

    # El recorte sale de los pixeles de tinta REALES y no del rectangulo teorico.
    # El rectangulo viene en coma flotante y al pasarlo a indice se perdia hasta
    # un pixel de la barra de un "7", que la CNN leia como un "1". Aki no hay
    # redondeo: se toma lo que hay.
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
    """Rota lo justo para dejar el digito vertical.

    Usa el angulo del rectangulo minimo de la tinta. OpenCV lo devuelve en el
    rango (0, 90]: si es mayor de 45 lo que en realidad esta girado es el eje
    corto, y el digito se ha intercambiado de eje. Por eso el angulo efectivo es
    `angulo` o `angulo - 90` segun de que lado cae.

    Se rota ANTES de recortar para que el pivote sea el centro de la tinta sobre
    la imagen entera. Rotar despues obligaria a recalcular el centro ya
    desplazado al origen del recorte, que es donde aparecen los recortes
    descentrados.
    """
    puntos = cv2.findNonZero(mascara)
    if puntos is None:
        return mascara

    angulo = float(cv2.minAreaRect(puntos)[-1])
    if angulo > 45.0:
        angulo -= 90.0
    # Por debajo de medio grado no vale la pena rotar y se come sharpness; por
    # encima de 20 grados ya no es un digito torcido sino que el contorno mas
    # grande de la escena era otra cosa.
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


# ------------------------------------------------------------------ completo ----

def procesar(marco) -> tuple:
    """Cadena completa. Devuelve (entrada_cnn, recorte_debug, exito)."""
    binaire = binarizar(a_gris(marco))
    recorte, exito = area_de_interes(binaire)
    lienzo = encuadrar(recorte) if exito else np.zeros((TAM, TAM), np.uint8)
    return normalizar(lienzo), recorte, exito


def a_20x20(marco) -> np.ndarray:
    """Atajo para el generador del dataset y para las pruebas."""
    return procesar(marco)[0]
