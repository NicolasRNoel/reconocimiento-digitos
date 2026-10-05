# Cómo probar el proyecto

Cinco niveles, de menos a más. Los tres primeros no necesitan ningún hardware.
No pases al siguiente hasta que el anterior esté en verde.

Todos los comandos se ejecutan **desde la carpeta `reconocimiento_digitos/`**.

---

## Nivel 0 — Preparación (una sola vez)

```bash
pip install numpy opencv-python
```

Si vas a usar cámara de verdad y ESP32, también:

```bash
pip install pyserial
```

---

## Nivel 1 — Los números están bien

Comprueba que la matemática y los protocolos no tienen errores.

### 1a. Los gradientes de la CNN

```bash
python pruebas/test_numerico.py
```

**Debe terminar con `VEREDICTO: todo correcto`.**

Comprueba que los gradientes analíticos cuadran con diferencias finitas (~1e-10).
Un error aquí NO da excepción: el entrenamiento baja, pero más despacio, y el
síntoma aparece semanas después como «el modelo no converge».

### 1b. Protocolo, buses y LCD

```bash
python pruebas/test_protocolo.py
```

**Debe decir `27 de 27 pruebas correctas`.**

Si alguna falla, el mensaje dice qué se rompió exactamente.

---

## Nivel 2 — El reconocimiento funciona

### 2a. Sin cámara y sin ESP: solo el modelo

```bash
python -m pc.app --fuente sim --sin-puerto --marcos 20
```

Verás líneas como:

```
[PC  ] digito 6  87%  seq 1  15 bytes
```

**Lo que mirar:** que el número que dice sea el que `--digitos` indica, y que la
confianza esté por encima de ~70%.

Si la confianza es baja o salen números equivocados, el modelo no está
entrenado con el generador actual. Reentrénalo:

```bash
python -m vision.cnn.entrenar
```

### 2b. Probar un solo dígito

```bash
python -m pc.app --fuente sim --digitos 3 --sin-puerto --marcos 15
```

### 2c. Ver el recorte 20×20 que ve la red

```bash
python -m pc.app --fuente sim --marcos 30
```

Se abre una ventana con dos paneles: la «cámara» y el recorte ampliado ×8.

**Esto es lo más útil para diagnosticar.** Si el recorte no se parece a un
dígito, el problema es el preprocesado, no el modelo.

---

## Nivel 3 — La cadena entera, sin hardware

Aquí ya están las cuatro etapas y el LCD.

```bash
python -m simulacion.simular --marcos 20 --lento
```

**Lo que mirar en el log:**

```
[PC   ] digito 7  78%  seq 1  15 bytes     ← la PC reconoce y manda
[ESP-A] SPI -> ESP-B  digito 7  78%  seq 1  ← la ESP-A reenvía
[ESP-B] LCD: digito 7 al 78%  (seq 1)      ← la ESP-B pinta
[PC   ] acuse de la ESP-A: seq 1 OK        ← vuelve el acuse
```

Y el panel:

```
|  DIGITO: 7   78%      |
|  seq 1    tot 1       |
```

**En el informe final, estos números deben ser coherentes:**

```
PC     ... 10 detecciones enviadas, ... 10 acuses recibidos
ESP-A  ... 10 recibidas, 10 por SPI, 10 acuses, 0 errores de CRC
ESP-B  ... 10 digitos, 10 acuses, 0 descartadas, 0 saltos de secuencia
```

### 3a. Probar que el protocolo aguanta un cable flojo

```bash
python -m simulacion.simular --marcos 20 --corrupcion 0.3
```

Inyecta un byte de ruido delante del 30% de las tramas.

**Correcto:** siguen llegando dígitos al LCD, con `0 errores de CRC`.

### 3b. Probar que pasa si desenchufas el LCD

```bash
python -m simulacion.simular --marcos 10 --sin-lcd
```

**Correcto:** la ESP-B sigue leyendo el SPI y avisa una vez por el log:

```
[SIM  ] el modulo I2C esta desconectado: la ESP-B no podra escribir en el LCD
```

Si la ESP-B se muriera, esto sería un bug.

---

## Nivel 4 — Con la cámara real (aún sin ESP)

Aquí ya usas tu webcam, pero la PC no manda nada a ningún sitio.

```bash
python -m pc.app --fuente sim --sin-puerto --marcos 20   # referencia sintética
python -m pc.app --marcos 60 --sin-puerto                # tu webcam de verdad
```

**Lo que mirar:** la diferencia de acierto entre las dos.

Escribe un dígito grande en un papel blanco, a unos 30 cm de la cámara, con buena
luz. Si falla, mira el panel del recorte.

**Ajustes si falla:**

| Síntoma | Qué hacer |
|---|---|
| Confianza baja | `--confianza 40` |
| «sin digito reconocible» siempre | El recorte está vacío: mira la ventana |
| Acierta con el 7 pero no con el 1 | Tu «1» es más estrecha de lo que cubre el dataset |

---

## Nivel 5 — Con el hardware

### 5a. Montar

Mira el apartado **Montar en hardware real** del README para el cableado.

Resumen:

```
PC  ──USB-TTL──►  ESP-A                    ESP-A  ══SPI══►  ESP-B  ──I2C──►  LCD
  TX ─► GPIO 3        GPIO 18 SCK ──────────────────► GPIO 18
  RX ◄─ GPIO 1        GPIO 19 MISO ◄──────────────── GPIO 19
  GND ─  GND          GPIO 23 MOSI ─────────────────► GPIO 23
                      GPIO  5 CS   ─────────────────► GPIO  5
                                                              GPIO 21 SDA ──►
                                                              GPIO 22 SCL ──►
```

### 5b. Flashear

En **cada** ESP:

```bash
mpremote connect
mpremote cp firmware/esp_a/main.py          :
mpremote cp firmware/common/protocolo.py    :protocolo.py
mpremote cp firmware/common/micro.py        :micro.py
mpremote cp firmware/common/hardware.py     :hardware.py
```

En la **ESP-B**, además:

```bash
mpremote cp firmware/esp_b/main.py          :
mpremote cp firmware/common/lcd.py          :lcd.py
```

### 5c. Probar el LCD antes de nada

Esto es lo primero, y evita 20 minutos de Auth. En la ESP-B:

```python
from machine import I2C, Pin
from lcd import Lcd

i2c = I2C(0, sda=Pin(21), scl=Pin(22), freq=400000)
print("direcciones:", [hex(d) for d in i2c.scan()])

lcd = Lcd(i2c, 0x27, 16, 2)
lcd.iniciar(16, 2)
lcd.goto(0, 0)
lcd.escribir("HOLA ESP-B")
```

**Si `scan()` no da `0x27`:** los jumpers del módulo están mal. Vienen a veces en
`0x20` o `0x3F`. Cambia `0x27` por lo que salga, o mueve los jumpers.

**Si sale una fila de bloques negros:** el contraste. Gira los cuatro
potenciómetros del módulo hasta que aparezca la línea de arriba.

### 5d. Correr la cadena real

```bash
python -m pc.app --puerto COM5
```

**Antes, mira cuál es tu puerto:**

```bash
python -m pc.app --listar
```

### 5e. Ver los logs de las ESP

```bash
mpremote connect
mpremote device
```

---

## Diagnóstico rápido

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| No abre el puerto COM | No hay conversor, o es otro COM | `python -m pc.app --listar` |
| El LCD sale en negro | Contraste | Girar los potenciómetros |
| El LCD sale con `0x20` o `0x3F` | Jumpers del módulo | `i2c.scan()` y cambiar la dirección |
| La ESP-B no recibe nada | MISO/MOSI cruzados | Cambiar los dos cables |
| El LCD no se actualiza | CS mal conectado | Comprobar GPIO 5 en ambas |
| Acierta el 7, falla el 1 | Tu letra es más estrecha | Ver abajo |
| `CRC incorrecto` en el log | Cable flojo | Ver «cableado» abajo |

### Si tu letra no la reconoce

El dataset se entrena con las fuentes de OpenCV, no con letras escritas a mano.
Es la limitación conocida del proyecto.

Solución rápida: baja el umbral y escribe más grande.

```bash
python -m pc.app --confianza 35
```

Solución buena: añadir 20 muestras de tu letra al entrenamiento. Ver el apartado
**Qué se podría mejorar** del README.

### Si hay errores de CRC

Los cables del SPI son cortos (menos de 20 cm). Si son largos:

- Baja la velocidad. En `firmware/esp_a/main.py` y `esp_b/main.py`:
  `SPI_BAUDS = 1000000` → `100000`
- Comprueba que MISO y MOSI no están intercambiados
- Comprueba que las masas están unidas entre las dos ESP

---

## Resumen

```
Nivel 0  pip install                    sin hardware
Nivel 1  pruebas/test_numerico.py       sin hardware      ~1 min
         pruebas/test_protocolo.py       sin hardware      ~20 s
Nivel 2  pc.app --fuente sim            sin hardware      ~30 s
Nivel 3  simulacion.simular             sin hardware      ~20 s
Nivel 4  pc.app (cámara real)           solo webcam       ~2 min
Nivel 5  flashear ESP + pc.app          todo              ~30 min
```

Si alguna de las etapas no da lo que se espera aquí, no pases a la siguiente:
cada una depende de que la anterior esté bien.
