# Subir a GitHub

El repositorio ya está inicializado y con un commit. Solo falta conectarlo al
remoto y subirlo.

## Qué se sube

34 archivos, 373 KB. Lo importante:

| Qué | Por qué |
|---|---|
| `firmware/esp_a/main.py`, `firmware/esp_b/main.py` | El firmware de las dos ESP32 |
| `firmware/common/protocolo.py` | **El** protocolo. El mismo archivo en PC y ESP |
| `vision/preproceso.py` | El preprocesado OpenCV |
| `vision/cnn/capas.py`, `modelo.py` | La CNN en numpy |
| `vision/cnn/pesos.npz` (101 KB) | Los pesos Entrenados. Sin ellos no se ejecuta |
| `simulacion/` | Los buses virtuales y el emulador del LCD |
| `pruebas/` | Las 27 pruebas |
| `README.md`, `COMO-PROBAR.md` | La documentación |

Lo que **no** se sube:

| Qué | Por qué |
|---|---|
| `vision/cnn/cache_800_7.npz` (1,5 MB) | El dataset de entrenamiento. Se regenera en 4 minutos y cambia con cada versión del generador. Si se subiera, dos personas tendrían datos distintos |
| `__pycache__/` | Caché de Python |

La decisión de subir `pesos.npz` es consciente. Normalmente no se versionan
pesos binarios, pero aquí son el resultado del proyecto, ocupan 101 KB y sin ellos
el repositorio no se puede ejecutar. Si prefieres que no estén, bórralo del
índice con:

```bash
git rm --cached vision/cnn/pesos.npz
echo "vision/cnn/pesos.npz" >> .gitignore
```

## Paso 1 — Crear el repositorio en GitHub

En el navegador, en <https://github.com/new>:

- **Nombre**: `reconocimiento-digitos` (o el que quieras)
- **Descripción**: `Cámara → CNN → ESP-A (SPI) → ESP-B → LCD I2C, con simulación completa en software`
- **Visibilidad**: pública o privada, lo que prefieras
- **No marques** «Add a README» ni «Add .gitignore»: ya tienes archivos locales y GitHub no debe crear un `.gitignore` que los borre

Dale a **Create repository**.

## Paso 2 — Conectar y subir

Copia la URL del repositorio que acabas de crear (algo como
`https://github.com/TU_USUARIO/reconocimiento-digitos.git`) y ejecuta:

```bash
git remote add origin https://github.com/TU_USUARIO/reconocimiento-digitos.git
git branch -M main
git push -u origin main
```

Si Git pide usuario y contraseña:
- **Usuario**: tu nombre de usuario de GitHub
- **Contraseña**: **no** es tu contraseña de GitHub. GitHub ya no la acepta.

En su lugar, usa un **token personal**:

1. En GitHub: *Settings* → *Developer settings* → *Personal access tokens* → *Tokens (classic)*
2. *Generate new token*, marca el alcance `repo`
3. Copia el token (sale una sola vez)
4. Cuando `git push` pida contraseña, pega el token ahí

O, más cómodo, configura SSH una vez y no vuelves a escribir nada:

```bash
ssh-keygen -t ed25519 -C "tu@email.com"
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copias lo que sale en <https://github.com/settings/keys> (*New SSH key*), y luego:

```bash
git remote set-url origin git@github.com:TU_USUARIO/reconocimiento-digitos.git
git push -u origin main
```

## Comprobar que funcionó

```bash
git status
```

Debe decir `nothing to commit, working tree clean` y `Your branch is up to
with 'origin/main'`.

## Si quieres que otra persona lo clone y lo ejecute

Eso ya funciona con lo que hay subido. En una máquina nueva:

```bash
git clone https://github.com/TU_USUARIO/reconocimiento-digitos.git
cd reconocimiento-digitos
pip install -r requirements.txt
python -m simulacion.simular --marcos 10 --lento
```

Los pesos vienen en el repositorio, así que no hace falta entrenar nada para ver
la cadena funcionando.

---

## Nota

Dejé el repositorio git inicializado y con un commit hecho, pero **sin remoto**:
no sé tu nombre de usuario de GitHub y no quiero adivinarlo ni tocar tus
credenciales. El paso 2 es lo único que falta.

Si prefieres que lo haga yo, dime tu nombre de usuario de GitHub y el nombre que
le pusiste al repositorio, y lo conecto. Para el push necesitaría que
configures tú un token o una clave SSH, que eso sí es cosa tuya.
