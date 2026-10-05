# Reconocimiento de dígitos con cámara

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

Integrantes: Nicolas Robayo ; Camilo Molano ; Jordán Alejandro Rodriguez
---

## Índice

- [Qué hace y por qué](#qué-hace-y-por-qué)
- [La simulación](#la-simulación)
- [Montar en hardware real](#montar-en-hardware-real)
- [Estructura de archivos](#estructura-de-archivos)


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


---

## Cómo probarlo


Resumen rápido, de menos a más:

| Nivel | Qué pruebas | Elementos |
|---|---|---|
| 1 | Los gradientes y los tests del protocolo | nada |
| 2 | Solo el reconocimiento | nada |
| 3 | La cadena entera con LCD emulado | nada |
| 4 | La cámara de verdad | solo webcam |
| 5 | Todo con las ESP | 2 ESP32 + LCD |

---



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
