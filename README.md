# Reconocimiento de dígitos con cámara, CNN y dos ESP32

Una cadena de cinco etapas que va de la cámara del portátil a un LCD de 16×2,
pasando por dos microcontroladores, y que **se puede ejecutar entera en software
sin tocar ni un cable**.

```
┌──────────┐   UART     ┌──────────┐   SPI     ┌──────────┐   I2C   ┌───────┐
│    PC    │  115200    │   ESP-A  │  maestra  │   ESP-B  │  400k   │ LCD   │
│          │ ─────────► │          │ ────────► │          │ ───────► │ 16x2  │
│ cámara   │            │ puente   │           │ esclava  │         │       │
│ OpenCV   │            │ sinCNS   │           │ + LCD    │         │       │
│ CNN      │ ◄───────── │          │ ◄──────── │          │         │       │
└──────────┘   acuse    └──────────┘   MISO    └──────────┘         └───────┘
```

La decisión de arquitectura más importante: **la ESP-A no interpreta nada**. Es
un puente. Todo el reconocimiento está en la PC.

---

## Índice

- [Qué hace y por qué](#qué-hace-y-por-qué)
- [Empezar en 5 minutos](#empezar-en-5-minutos)
- [La cadena explicada etapa por etapa](#la-cadena-explicada-etapa-por-etapa)
- [El protocolo](#el-protocolo)
- [La simulación](#la-simulación)
- [Montar en hardware real](#montar-en-hardware-real)
- [Estructura de archivos](#estructura-de-archivos)
- [Los errores que costaron tiempo](#los-errores-que-costaron-tiempo)
- [Qué está y qué no está verificado](#qué-está-y-qué-no-está-verificado)

---

## Qué hace y por qué

Escribes un dígito del 0 al 9 delante de la webcam. El PC lo recorta, lo pasa por
una red convolucional y lo manda por el puerto serie. La ESP-A lo reenvía por SPI
a la ESP-B, que lo pinta en el LCD.

**Por qué el reconocimiento está en la PC y no en la ESP:**

| | En la PC | En un ESP32 |
|---|---|---|
| CNN de 27.562 pesos | 110 kB, sobra | Cabe, pero sin aceleración |
| Inferencia | 2 ms | ~1 s en MicroPython puro |
| OpenCV (Otsu, contornos, rectángulo mínimo) | Nativo | Hay que reescribirlo entero |
| Riesgo de divergencia | — | El preprocesado del ESP no sería el que probaste |

El punto 3 es el que más pesa. Reescribir el preprocesado para MicroPython
significa que el resultado ya no es el mismo que validaste en el escritorio. Es
justo el tipo de divergencia que hace que un sistema «funcione en el banco de
pruebas» y falle en la instalación.

**Por qué una pasarela con tan poco trabajo.** La ESP-A es la etapa más sencilla
de la cadena y por eso es la que se puede auditar de un vistazo: si una trama no
cuadra, se cuenta y se avisa. En una cadena de cinco piezas, la pieza más simple
es la que no falla.

---

## Cómo probarlo

**El plan completo de pruebas, nivel por nivel, está en [COMO-PROBAR.md](COMO-PROBAR.md).**

Resumen rápido, de menos a más:

| Nivel | Qué pruebas | Necesitas |
|---|---|---|
| 1 | Los gradientes y los tests del protocolo | nada |
| 2 | Solo el reconocimiento | nada |
| 3 | La cadena entera con LCD emulado | nada |
| 4 | La cámara de verdad | solo webcam |
| 5 | Todo con las ESP | 2 ESP32 + LCD |

Los tres primeros niveles no necesitan ningún hardware. Empieza por el 3:

```bash
pip install -r requirements.txt
python -m simulacion.simular --marcos 10 --lento
```

---

## Empezar en 5 minutos

```bash
pip install -r requirements.txt

# 1. Ver la cadena entera funcionando, sin hardware y sin entrenar nada
#    (los pesos vienen en el repositorio)
python -m simulacion.simular --marcos 20 --lento
```

Eso es todo para verlo funcionar. Los pesos de la CNN están en el repositorio
(`vision/cnn/pesos.npz`, 101 KB), así que no hace falta entrenar.

Si quieres reentrenar o comprobar el modelo:

```bash
# Entrenar la CNN (~5 min: genera 8.000 imágenes y entrena 30 épocas)
python -m vision.cnn.entrenar
```

Salida de la cadena:

```
[    904 ms] [PC   ] digito 7  71%  seq 2  15 bytes
[    922 ms] [ESP-A] SPI -> ESP-B  digito 7  71%  seq 2
+--------------------------------------------------------------+
|                      LCD 16x2 simulado                       |
+--------------------------------------------------------------+
|  DIGITO: 7    71%                                            |
|                                                              |
|  seq 2    tot 2                                             |
+--------------------------------------------------------------+
```

### Ver un dígito fijo

```bash
python -m simulacion.simular --digitos 3 --marcos 6 --lento
```

### Probar que el protocolo aguanta un cable flojo

```bash
python -m simulacion.simular --marcos 20 --corrupcion 0.25
```

Inyecta un byte de ruido delante de cada trama. El informe debe seguir mostrando
`0 errores de CRC` y `0 tramas descartadas`.

### Probar que pasa si desenchufas el LCD

```bash
python -m simulacion.simular --marcos 10 --sin-lcd
```

La ESP-B sigue leyendo el SPI y avisando una vez por el log. No se muere.

---

## La cadena explicada etapa por etapa

### 1. La cámara y OpenCV

`vision/preproceso.py`. Ocho operaciones en un orden que importa:

1. **gris** — la cámara da BGR de 3 canales y la CNN lee uno
2. **desenfoque 3×3** — quita ruido del sensor y suaviza el borde del trazo
3. **Otsu** — umbral automático; con un histograma bimodal clava el corte sin calibrar nada
4. **polaridad** — fuerza «tinta blanca sobre fondo negro»
5. **morfología** — apertura 2×2 (borra motas) y cierre 3×3 (tapa huecos del trazo)
6. **contornos** — el más grande que pase los filtros de área, proporción y relleno
7. **recorte y enderezar** — recorte de la tinta real y corrección del ángulo
8. **encuadre 20×20** — escala conservando la proporción, rellenando con negro

Dos decisiones que parecen detalles y no lo son:

**La polaridad se decide mirando las cuatro esquinas de la *binaria*, no la
media del gris.** Es la clase de error que no da ningún aviso: si el papel queda
en blanco, el contorno más grande de la imagen es la hoja entera, el recorte sale
mal y la CNN aprende a leer «media hoja de papel». En el camino de este proyecto
pasó, y el síntoma era un acierto del 57% con una matriz de confusión donde todo
se iba al «3».

**Se recorta de los píxeles de tinta reales, no del rectángulo teórico.** El
rectángulo viene en coma flotante y al pasarlo a índice se perdía hasta un píxel
de la barra de un «7», que la CNN leía como un «1».

### 2. La CNN

`vision/cnn/`. Escrita con numpy, sin PyTorch ni TensorFlow.

```
entrada     1 × 20 × 20
conv 3×3      8 filtros    →  8 × 20 × 20      80 pesos
relu
maxpool 2×2                →  8 × 10 × 10
conv 3×3     16 filtros    → 16 × 10 × 10   1.168 pesos
relu
maxpool 2×2                → 16 ×  5 ×  5
flatten                       400
densa          64                            25.664 pesos
relu
densa          10                             650 pesos
softmax
                          ────────────────
                          27.562 parámetros
```

**Se entrena con datos sintéticos, no con MNIST.** MNIST son dígitos ya
centrados, sin ruido y sobre fondo negro. La cámara ve papel con foco de luz
descentrado, ruido de sensor y el borde del marco. Un modelo entrenado solo con
MNIST baja del 80% en cuanto la luz se mueve, porque se ha aprendido el fondo.

`vision/cnn/dataset.py` genera imágenes con las seis fuentes de OpenCV, sobre un
papel con degradado, con rotación, perspectiva, desenfoque y ruido. **Y pasan
después por el mismo preprocesado que usa la cámara**, de forma que la
distribución con la que se entrena y la que se ve en producción son la misma.

Resultado actual con 8.000 muestras, 30 épocas:

```
Mejor acierto en prueba: 0.9992
```

Lo que **no** cubre el dataset: la letra humana. Si escribes un «1» con el ángulo
muy marcado o un «7» sin travesaño, hay que generar muestras de ese estilo. Es el
único dato que una simulación no puede inventar.

### 3. El protocolo

Binario, no texto. Por el SPI pasan 15 bytes por dígito; un JSON de 40
caracteres tardaría 3,5 ms a 115200 baud. Y el HD44780 solo imprime caracteres
entre 0x20 y 0x7E, así que cualquier byte alto se vería como basura.

```
offset 0   0xA5     marca de inicio
    1     0x5A     segunda marca: filtra el relleno
    2     LEN      longitud de DATOS (9)
    3     TIPO     0x10 dígito, 0x20 acuse, 0x30 control
    4..   DATOS    SEQ u16 | DIGITO u8 | CONF u8 | FUENTE u8 | MS u32
    -2..  CRC16    CCITT-FALSE sobre LEN, TIPO y DATOS
```

**El CRC cubre LEN y TIPO, no solo DATOS.** Así, si un byte se cambiara en la
cabecera, también se detectaría.

El archivo del protocolo vive en `firmware/common/protocolo.py` y es **el mismo**
en los tres sitios: la PC lo carga por ruta (`nucleo/protocolo.py` solo lo
reexporta y le pone tipos) y las ESP lo reciben con `mpremote cp`. Si hubiera dos
copias, el firmware acabaría serializando un campo en un orden distinto del que
deserializa la PC, y eso no falla nunca: falla con un dígito equivocado una vez de
cada mil.

### 4. La ESP-A, puente

Lee tramas del UART, las reenvía por SPI byte a byte y devuelve el acuse. Lo
interesante es lo que hace cuando algo falla:

- Si pasan 10 segundos sin acuse de la ESP-B, avisa por el puerto de diagnóstico.
  Un cable flojo entre las dos ESP se ve como «la ESP-A no recibe acuse» y no
  como «el LCD no se actualiza», que es mucho más difícil de diagnosticar.

### 5. La ESP-B y el LCD

Recibe por SPI como esclava, pinta el LCD y manda el acuse por el MISO.

El **acuse tarda un ciclo**, y no es un defecto: el SPI es full-duplex, pero
cuando la ESP-A termina su trama y sube CS ya no hay a quién mandarle el MISO.
El único modo de recibirlo es que la ESP-A baje CS otra vez. Es el protocolo.

El LCD es un HD44780 detrás de un backpack PCF8574, y el emulador implementa el
protocolo byte a byte, no un `print`:

```
byte = ((nibble & 0x0F) << 4) | (RS << 0) | (RW << 1) | (EN << 2) | (luz << 3)
```

La secuencia de inicialización es obligatoria. El controlador arranca en 8 bits
y el módulo va en 4, así que hay que mandar tres `0x30`, luego `0x20`, y solo
entonces la configuración de verdad. Si se salta, el LCD interpreta cada nibble
como dos caracteres y escribe basura intercalada. En el emulador se ve igual,
porque implementa el protocolo del HD44780 y no una pantalla de texto.

---

## La simulación

La regla del simulador: **se importa el firmware real, no una copia**.

`simulacion/maquina.py` registra un módulo `machine` falso en `sys.modules` antes
de que se importe nada del firmware. A partir de ahí, `from machine import Pin,
UART, SPI, I2C` funciona y devuelve objetos que se comportan como los del
hardware. `simulacion/simular.py` carga `firmware/esp_a/main.py` y
`firmware/esp_b/main.py` con `importlib`, sin tocarlos.

Si el simulador ejecutara una copia «simplificada», estaría validando esa copia.
Los fallos aparecerían al montar la placa, y serían exactamente los que la
simulación no puede ver: el CS que aquí no importa, el `any()` del UART que aquí
devuelve lo que le da la gana, un `sleep()` que en la placa no se puede saltar.

### Qué se simula de verdad

| | Detalle |
|---|---|
| **UART** | Las escrituras se **trocean en bloques de 1 a 4 bytes**, como un conversor USB-TTL. Es el detalle más importante: obliga a que el parser de tramas funcione con recepción parcial. Sin trocear, el firmware pasaría aquí y fallaría en la placa. |
| **SPI** | Modelo completo con CS: una transacción empieza cuando CS baja y termina cuando sube. El MISO se entrega en la transacción **siguiente**, no en la misma. El reposo es `0xFF`, no `0`. |
| **I2C** | Direcciones reales. Hay un LCD en `0x27` y una EEPROM en `0x57`, para que se vea que el maestro elige bien. |
| **LCD** | El protocolo del PCF8574 y del HD44780, byte a byte, con el acumulado de nibbles del modo 4 bits. |

Cada etapa corre en su hilo. La ESP-B arranca antes que la ESP-A, porque su
`leer` es bloqueante (igual que en la placa) y tiene que estar posicionada en la
espera cuando llegue el primer byte.

### Qué NO se simula, y por qué no importa

Los retardos reales del bus. Un byte son 8 µs a 1 MHz y un carácter del HD44780
unos 40 µs; una trama entera son 128 µs. En una cadena que ya va a 16 detecciones
por segundo, ignorarlo no cambia ninguna conclusión. Si hiciera falta, el sitio
natural para meterlo es `escribir()`, que es donde estaría la espera.

---

## Montar en hardware real

### Material

- 2 × ESP32 DevKit v1 (o cualquier otra con 30 pines)
- 1 × módulo I2C para LCD 16×2 (PCF8574, jumperes en `0x27`)
- 1 × conversor USB-TTL (TX/RX/GND) para hablar con cada ESP
- La webcam del portátil

### Cableado

**PC → ESP-A** (por USB-TTL):

```
PC TX  ──►  GPIO 3  (RX)
PC RX  ◄──  GPIO 1  (TX)
GND    ───  GND
```

**ESP-A ↔ ESP-B por SPI:**

| Señal | ESP-A | ESP-B |
|---|---|---|
| SCK | GPIO 18 | GPIO 18 |
| MISO | GPIO 19 | GPIO 19 |
| MOSI | GPIO 23 | GPIO 23 |
| CS | GPIO 5 | GPIO 5 |

**ESP-B → LCD (I2C):**

| | GPIO |
|---|---|
| SDA | 21 |
| SCL | 22 |

Los cuatro potenciómetros del módulo son el contraste. Si la pantalla sale en una
fila de bloques negros, sube el contraste hasta que aparezca la línea de arriba.

### Poner el firmware

En **cada** ESP:

```bash
mpremote connect
mpremote cp firmware/esp_a/main.py          :
mpremote cp firmware/common/protocolo.py    :protocolo.py
mpremote cp firmware/common/micro.py        :micro.py
mpremote cp firmware/common/hardware.py     :hardware.py
mpremote reset
```

En la **ESP-B**, además del driver del LCD:

```bash
mpremote cp firmware/esp_b/main.py          :
mpremote cp firmware/common/lcd.py          :lcd.py
```

Para ver los logs, hay un puerto de diagnóstico (GPIO 1). Con el `monitor`:

```bash
mpremote connect
mpremote device
```

### Cambiar el puerto serie

En `configuracion.py`, y en la placa que uses:

```python
PUERTO_SERIE = "COM5"      # Windows. En Linux, /dev/ttyUSB0
```

Si no sabes cuál es:

```bash
python -m pc.app --listar
```

### Correr con la cámara real

```bash
python -m pc.app --puerto COM5
```

Se abre una ventana con la cámara y el recorte 20×20 ampliado ×8, para ver qué
está viendo el modelo. Si la confianza no llega al umbral (55% por defecto), no
se manda nada: el LCD callado significa «no hay dígito claro».

Ajustes útiles:

```bash
python -m pc.app --confianza 40        # más permisivo
python -m pc.app --fuente cam:1        # otra cámara
python -m pc.app --sin-ventana         # sin ventanas
```

### Reentrenar

```bash
python -m vision.cnn.entrenar --n 2000 --epocas 40
```

El conjunto se cachea en `vision/cnn/cache_<n>_<semilla>.npz`. Si cambias el
generador, bórralo o el entrenamiento seguirá con los datos viejos.

---

## Estructura de archivos

```
reconocimiento_digitos/
├── configuracion.py          pines, puertos, umbrales, formatos
│
├── nucleo/
│   ├── protocolo.py          carga firmware/common/protocolo.py y le pone tipos
│   └── traza.py              despacho de tramas con realineación
│
├── vision/
│   ├── preproceso.py         gris → Otsu → contorno → 20×20
│   └── cnn/
│       ├── capas.py           conv, pool, densa en numpy (fwd + bwd)
│       ├── modelo.py          la arquitectura y el guardado de pesos
│       ├── dataset.py         dígitos sintéticos con defectos de verdad
│       └── entrenar.py        entrenamiento
│
├── pc/
│   ├── app.py                cámara → CNN → UART
│   └── transportes.py        pyserial y tubería con troceado
│
├── firmware/
│   ├── common/
│   │   ├── protocolo.py      EL protocolo. Va tal cual a las dos ESP.
│   │   ├── hardware.py       Pin, UART, SPI, I2C, LED
│   │   ├── micro.py          ticks_ms, sleep_ms, registro
│   │   └── lcd.py            driver del HD44780
│   ├── esp_a/main.py         puente UART → SPI
│   └── esp_b/main.py         esclava SPI → I2C → LCD
│
├── simulacion/
│   ├── maquina.py            el módulo `machine` falso
│   ├── bus_spi.py            bus SPI con CS y MISO diferido
│   ├── bus_i2c.py            bus I2C con LCD y EEPROM
│   ├── lcd.py                emulador del HD44780
│   └── simular.py            orquesta los cuatro hilos
│
└── pruebas/                  solo en local, no en el repositorio
    ├── test_numerico.py      gradientes por diferencias finitas
    └── test_protocolo.py     protocolo, bus, LCD y cadena completa
```

Las pruebas no están en el repositorio: son de desarrollo, no de ejecución, y
ocupan 24 KB. Se quedan en tu copia local y siguen siendo ejecutables con
`python pruebas/test_protocolo.py`.

---

## Los errores que costaron tiempo

Se documentan porque son los que no dan error visible, solo hacen que algo no
funcione. Son la razón de que el simulador sea fiel.

### 1. `IndexError` silencioso que mata el hilo

`leer_cabecera` leía `sobra[3]` (el byte TIPO) sin comprobar que hubieran
llegado 4 bytes. En CPython es una excepción. **En la ESP32 un `IndexError` en un
bucle principal mata el hilo entero, sin reiniciar y sin avisar.** El síntoma era
«la ESP-A no recibe nada», que no dice nada sobre el motivo.

Por eso la comparación es de 4 bytes y no de 3. Y por eso el simulador trocea el
UART a propósito: un fallo como este no se puede ver en un entorno donde las
tramas llegan enteras.

### 2. Bucle infinito en el parser de tramas

El parser consumía la cabecera, veía que el cuerpo no estaba, y devolvía los
bytes al principio del buffer. El buffer no avanzaba nunca.

Este fallo **nunca aparece si el UART siempre trocea**, porque cada lectura trae
pocos bytes. Solo asoma cuando el otro extremo entrega la trama entera de golpe,
que es justo lo que hace el conversor USB-TTL.

La solución: trabajar sobre una **copia** del buffer y no consumir hasta saber que
hay una trama completa y con el CRC bien.

### 3. Polaridad de la binarización

El síntoma era un acierto del 57% y una matriz de confusión donde casi todo se
iba al «3». La causa: al decidir si había que invertir, se miraba la luminancia
absoluta del borde. Con un degradado de luz fuerte, el papel en sombra daba un
«fondo oscuro» y no se invertía, con lo que el contorno más grande de la imagen
era media hoja de papel.

La regla correcta es preguntarse por la **binaria** del fondo, no por el gris:
¿el fondo quedó en blanco o en negro? La respuesta no depende de si la escena es
clara u oscura.

### 4. El formato de la trama

`FORMATO_DIGITO = "<HBBII"` ponía el campo `FUENTE` como un entero de 4 bytes
cuando la tabla lo documentaba como 1 byte. La trama medía 18 bytes en vez de 15.

No fallaba por sí solo: la ESP-B calculaba el CRC sobre los bytes que recibía y
cuadraba. Lo que se rompía era al reenviar, y el síntoma era que la ESP-B
descartaba todas las tramas por CRC incorrecto, sin ninguna otra pista.

### 5. `MISO` escrito en la cola del `MOSI`

El acuse de la ESP-B se metía en `entrada_esclava` (lo que lee la esclava) en vez
de `salida_esclava` (lo que lee la maestra). La ESP-A leía relleno infinito, la
ESP-B creía que ya había contestado y no se producía ninguna excepción.

Es el bug más silencioso del proyecto, y por eso hay una prueba que lo mira
explícitamente.

### 6. `0x30` y no `0x33` en el arranque del LCD

El driver mandaba `0x33` en la secuencia de inicialización. La especificación dice
`0x30`: el bit `DL` del medio solo existe en el modo de instrucción de 8 bits y no
se debe tocar al sincronizar. El emulador no reconocía la cabecera y cada byte
posterior se tomaba como dos comandos distintos.

### 7. El HD44780 no recorta, y los formatos `%` engañan

`"seq %-4d  total %-4d"` son 18 caracteres en un panel de 16. El HD44780 escribe
los 18 igualmente y los dos últimos se solapan con la fila de abajo. No da ningún
error, y con un contador de un solo dígito no se nota; aparece a los 10.000
dígitos.

Por eso el recorte a 16 columnas es explícito en el firmware y hay una prueba que
lo comprueba con un contador de seis cifras.

---

## Qué está y qué no está verificado

**Verificado:**

- Gradientes de la CNN contra diferencias finitas, error relativo ~1e-10
- 99,9% de acierto en el conjunto de prueba sintético
- Las 27 pruebas del protocolo, el bus y el LCD (en local, fuera del repo)
- La cadena completa en software, con acuse de vuelta
- La cadena con ruido en el UART (30% de tramas con byte de relleno delante):
  13 dígitos entregados, 0 errores de CRC, 0 tramas descartadas
- La cadena sin el módulo LCD conectado: la ESP-B sigue leyendo el SPI

**No verificado, porque no hay hardware:**

- Que el SPI funcione a 1 MHz entre dos ESP32 con cables de 30 cm
- Que el HD44780 real acepte la secuencia de inicialización (el emulador la
  implementa según la especificación, pero un panel real puede ser más exquisito)
- La latencia real de extremo a extremo
- El rendimiento de la inferencia en el PC con la cámara de verdad

**Lo más probable que falle al montarlo**, por orden:

1. Los jumpers del backpack I2C, que vienen a veces en `0x20` o `0x3F` en vez de `0x27`
2. El contraste del LCD (los cuatro potenciómetros)
3. Cruce de MISO y MOSI, que da un LCD que no se actualiza sin ningún error

---

## Cosas que se podrían mejorar

- **El preprocesado en la ESP**, si hiciera falta latencia menor. Habría que
  portar Otsu y el contorno a MicroPython asumiendo que el resultado será
  ligeramente distinto al probado.
- **Letras humanas.** El dataset no las cubre. Con una hoja de estilos escrita a
  mano y 20 muestras por dígito, se puede añadir al entrenamiento.
- **Un tercer tramo en el protocolo** para pedir a la ESP-B que mande una
  miniatura por el SPI, y ver el recorte en el LCD en vez de solo el número.
- **El acuse perdiéndose.** Ahora se detecta por el número de secuencia, pero un
  reintento explícito lo arreglaría del todo.
