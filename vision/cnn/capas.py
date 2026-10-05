"""
Capas de la red convolucional, escritas con numpy.

POR QUE NUMPY Y NO TENSORFLOW
-----------------------------
El objetivo del proyecto es que hasta un ESP32 sin GPU decida el digito. Si la
CNN se entrena con PyTorch pero el firmware lleva el mismo modelo, hay dos
implementaciones que pueden divergir sin que nadie se entere. Aqui hay una
sola, en numpy, que corre en la PC de entrenamiento, en la de inferencia y (con
recortes) tambien en el firmware.

EL TRUCO: im2col
-----------------
Una convolucion son multiplicaciones y sumas sobre ventanas deslizantes.
`_im2col` copia esas ventanas a una matriz (N*H*W) x (C*kh*kw) y el paso se
convierte en un unico `matmul`. NumPy lo resuelve en BLAS, con codigo compilado
de verdad; el bucle explicito de ventanas en Python seria veinte veces mas
lento.

`_col2im` deshace lo mismo sumando las gradientes de vuelta en la posicion de la
que salieron. Para cada desplazamiento del kernel las posiciones destino son
disjuntas, asi que un `+=` normal basta y es mucho mas rapido que el
`np.add.at` de la primera version, que ademas acumulaba en el sitio equivocado
con una tupla de slices.
"""

from __future__ import annotations

import numpy as np

# ------------------------------------------------------- conv como multiplicacion --

def _im2col(x: np.ndarray, kh: int, kw: int, paso: int, holgura: int) -> np.ndarray:
    """(N, C, H, W) -> (N*oh*ow, C*kh*kw). El `holgura` se rellena con ceros."""
    n, c, h, w = x.shape
    oh = (h + 2 * holgura - kh) // paso + 1
    ow = (w + 2 * holgura - kw) // paso + 1
    if oh <= 0 or ow <= 0:
        raise ValueError("el kernel %dx%d no cabe en la entrada %dx%d" % (kh, kw, h, w))

    x = np.pad(x, ((0, 0), (0, 0), (holgura, holgura), (holgura, holgura)))
    piezas = np.empty((n, c, kh, kw, oh, ow), dtype=x.dtype)
    for i in range(kh):
        for j in range(kw):
            piezas[:, :, i, j] = x[:, :, i:i + paso * oh:paso, j:j + paso * ow:paso]
    return piezas.transpose(0, 4, 5, 1, 2, 3).reshape(n * oh * ow, c * kh * kw)


def _col2im(col: np.ndarray, forma, kh: int, kw: int, paso: int, holgura: int) -> np.ndarray:
    """La inversa de `_im2col`: reparte las gradientes de vuelta a las ventanas.

    Para un `(i, j)` fijo las posiciones destino son disjuntas, asi que un `+=`
    normal acumula exactamente una vez cada contribucion. Entre distintos `(i, j)`
    si hay solape, y ahi el `+=` tambien es lo correcto porque va leyendo y
    reescribiendo celda a celda.

    NO se usa `np.add.at` para esto: con una tupla de slices como indice se
    comporta de forma distinta a como uno espera y acumula en sitios equivocados.
    En la version anterior de este codigo eso hacia que la gradiente de la
    entrada saliera con un 1% de error mientras todos los pesos giveaban bien,
    que es el peor tipo de fallo: el entrenamiento baja, pero no bajaba lo
    suficiente.
    """
    n, c, h, w = forma
    oh = (h + 2 * holgura - kh) // paso + 1
    ow = (w + 2 * holgura - kw) // paso + 1
    piezas = col.reshape(n, oh, ow, c, kh, kw).transpose(0, 3, 4, 5, 1, 2)

    # El buffer tiene que llegar hasta el ultimo pixel que escribe la ventana
    # mas desplazada, que es `kh - 1 + (oh - 1) * paso`. Con paso 1 eso es
    # `h + 2 * holgura`, pero con paso 2 hace falta mas y por eso se calcula.
    filas = (kh - 1) + (oh - 1) * paso + 1
    columnas = (kw - 1) + (ow - 1) * paso + 1
    dx = np.zeros((n, c, filas, columnas), dtype=col.dtype)
    for i in range(kh):
        for j in range(kw):
            dx[:, :, i:i + (oh - 1) * paso + 1:paso,
               j:j + (ow - 1) * paso + 1:paso] += piezas[:, :, i, j]
    if holgura:
        return dx[:, :, holgura:holgura + h, holgura:holgura + w]
    return dx[:, :, :h, :w]


# ------------------------------------------------------------------ activaciones --

def softmax(x: np.ndarray) -> np.ndarray:
    """Fila a fila, restando el maximo antes del exponente.

    Sin ese `- max`, un logit de 80 da `exp(80)` = 5.5e34, y al normalizar sale
    NaN. Es el error clasico de la primera implementacion.
    """
    x = x - x.max(axis=1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=1, keepdims=True)


# ------------------------------------------------------------------------ capas --

class Capa:
    """Interfaz comun: `adelante` guarda lo necesario para que `atras` pueda
    devolver (dX, dW, db) o solo dX, segun la capa."""

    def __init__(self) -> None:
        self.nombre = type(self).__name__

    def parametros(self):
        return []

    def adelante(self, x: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def atras(self, dx: np.ndarray):
        raise NotImplementedError


class Densa(Capa):
    """Capa totalmente conectada, inicializada con He."""

    def __init__(self, entradas: int, salidas: int, semilla: int = 0) -> None:
        super().__init__()
        # He: varianza 2/n. Con la Xavier por defecto las capas relu se apagan
        # a mitad del entrenamiento, porque media y varianza se reducen en cada
        # capa y el gradiente se vuelve cero.
        escala = np.sqrt(2.0 / entradas)
        self.W = (np.random.default_rng(semilla).standard_normal((entradas, salidas))
                  * escala).astype(np.float32)
        self.b = np.zeros(salidas, np.float32)

    def parametros(self):
        return [self.W, self.b]

    def adelante(self, x):
        self.entrada = x
        return x @ self.W + self.b

    def atras(self, dx):
        # Y = X @ W + b  ->  dX = dY @ W.T ,  dW = X.T @ dY ,  db = suma de dY
        return dx @ self.W.T, self.entrada.T @ dx, dx.sum(axis=0)


class Convolucion(Capa):
    """'same', paso 1, kernel 3x3, borde relleno con ceros."""

    def __init__(self, canales: int, filtros: int, semilla: int = 0,
                 tamano: int = 3) -> None:
        super().__init__()
        self.canales = canales
        self.filtros = filtros
        self.tamano = tamano
        self.W = None
        self.b = None
        self._inicializar((filtros, canales, tamano, tamano), semilla)

    def _inicializar(self, forma, semilla) -> None:
        # He por neurona de entrada de la conv, no 2/n del total.
        escala = np.sqrt(2.0 / (forma[1] * forma[2] * forma[3]))
        self.W = (np.random.default_rng(semilla).standard_normal(forma)
                  * escala).astype(np.float32)
        self.b = np.zeros(forma[0], np.float32)

    def parametros(self):
        return [self.W, self.b]

    def adelante(self, x):
        self.entrada = x
        n, _, h, w = x.shape
        k = self.tamano
        col = _im2col(x, k, k, 1, k // 2)                  # (N*h*w, C*k*k)
        salida = col @ self.W.reshape(self.filtros, -1).T + self.b
        return salida.reshape(n, h, w, self.filtros).transpose(0, 3, 1, 2)

    def atras(self, dx):
        k = self.tamano
        n, _, h, w = dx.shape
        plano = dx.transpose(0, 2, 3, 1).reshape(n * h * w, self.filtros)
        dW = (plano.T @ _im2col(self.entrada, k, k, 1, k // 2)).reshape(self.W.shape)
        db = plano.sum(axis=0)
        dcol = plano @ self.W.reshape(self.filtros, -1)
        return _col2im(dcol, self.entrada.shape, k, k, 1, k // 2), dW, db


class Relu(Capa):
    def adelante(self, x):
        self.entrada = x
        return np.maximum(x, 0.0)

    def atras(self, dx):
        return dx * (self.entrada > 0.0)


class MaxPool2(Capa):
    """2x2 con paso 2. No tiene pesos.

    El que gana cada ventana se recuerda como indice, no como mascara booleana:
    con dos pixeles de igual valor una mascara marcaria las dos y al repartir
    el gradiente llegaria el doble de peso al pixel. `argmax` elige la primera
    y el reparto cuadra siempre.
    """

    def adelante(self, x):
        n, c, h, w = x.shape
        h -= h % 2
        w -= w % 2
        if (h, w) != x.shape[2:]:
            # Las formas de este proyecto siempre son pares, pero si alguien
            # cambia TAM_IMAGEN a un impar esto recorta en vez de reventar.
            x = x[:, :, :h, :w]
        self.forma_entrada = (n, c, h, w)

        # (n, c, hh, 2, ww, 2) -> (n, c, hh, ww, 2, 2) para agrupar la ventana
        # 2x2 en un unico eje de 4.
        paneles = x.reshape(n, c, h // 2, 2, w // 2, 2).transpose(0, 1, 2, 4, 3, 5)
        plano = paneles.reshape(n, c, (h // 2) * (w // 2), 4)
        self.ganador = plano.argmax(axis=-1)
        elegido = np.take_along_axis(plano, self.ganador[..., None], axis=-1)[..., 0]
        return elegido.reshape(n, c, h // 2, w // 2)

    def atras(self, dx):
        n, c, h, w = self.forma_entrada
        hh, ww = h // 2, w // 2
        plano = np.zeros((n, c, hh * ww, 4), dtype=dx.dtype)
        np.put_along_axis(plano, self.ganador[..., None],
                          dx.reshape(n, c, hh * ww, 1), axis=-1)
        paneles = plano.reshape(n, c, hh, ww, 2, 2).transpose(0, 1, 2, 4, 3, 5)
        return paneles.reshape(n, c, h, w)


class Flatten(Capa):
    def adelante(self, x):
        self.forma = x.shape
        return x.reshape(x.shape[0], -1)

    def atras(self, dx):
        return dx.reshape(self.forma)


class Softmax(Capa):
    def adelante(self, x):
        self.salida = softmax(x)
        return self.salida

    def atras(self, dx):
        # Con softmax seguido de entropia cruzada el gradiente se simplifica a
        # p_real - p_predicho: los dos terminos se cancelan y no hace falta
        # derivar el softmax explicitamente.
        return dx


def perdida_entropia(probabilidades: np.ndarray, etiquetas: np.ndarray) -> tuple:
    """Devuelve (perdida_media, gradiente respecto a las probabilidades).

    No se fuerza el tipo de dato: si el modelo esta en float64 el gradiente sale
    en float64, y eso es lo que hace que las diferencias finitas de la
    comprobacion de gradientes no se coman el resultado entre el ruido de
    coma flotante.
    """
    n = probabilidades.shape[0]
    logp = -np.log(np.clip(probabilidades[np.arange(n), etiquetas], 1e-12, None))
    p = probabilidades.copy()
    p[np.arange(n), etiquetas] -= 1.0
    return float(logp.mean()), p / n
