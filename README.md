# Agente: informe técnico → Formulario de Ticket (Cechriza)

Lee la foto de tu informe técnico con Claude (visión), te muestra los datos en una
tabla editable (`datos.csv`) y rellena el formulario de la página, subiendo también las fotos.

## Instalación (una sola vez)

```bash
pip install -r requirements.txt
playwright install chromium
```

**Lector de la foto (gratis):** crea una clave gratuita en https://aistudio.google.com/apikey y guárdala:

```bash
setx GEMINI_API_KEY "tu-clave"        # Windows (cierra y abre la terminal)
export GEMINI_API_KEY="tu-clave"      # Mac/Linux
```

En `config.yaml`, `proveedor` puede ser `gemini` (gratis, por defecto), `anthropic` (Claude, de pago) o
`ninguno` (sin IA: llenas `datos.csv` a mano). Nota de privacidad: en el plan gratis de Gemini, Google puede
usar las fotos que envías para mejorar sus productos; si prefieres que el informe no salga de tu PC, usa `ninguno`.

En `config.yaml` cambia `url_formulario` por la URL de inicio de tu sistema.

## Uso diario

Una carpeta por visita con las fotos:

```
visitas/2026-10-12_molina/
  informe.jpg      <- foto del informe (obligatoria)
  contador.jpg     <- foto del contador         (también vale .png o .pdf)
  software.jpg     <- foto de la versión de software
  acta.jpg         <- foto del acta de conformidad
```

```bash
python main.py extraer  visitas/2026-10-12_molina    # el LLM lee el informe -> datos.csv
# Abre datos.csv en Excel: corrige lo marcado "SI" y completa los campos 'manual'
python main.py rellenar visitas/2026-10-12_molina    # llena el formulario y sube las fotos
```

Al ejecutar `rellenar` se abre Chrome: inicias sesión, pulsas ENTER en la terminal y el agente hace el resto:

1. Va a **Tickets Asignados → Rutas** y lista las tarjetas de tickets.
2. Abre el ticket que le indiques: `--ticket OS0134427` (si no lo indicas, te pide el número de fila;
   usa `--ruta manana` para buscar en "Ruta de Mañana").
3. Si el ticket aún no tiene **hora de llegada**, la registra (sin ella el formulario no se abre completo).
   Usa `--hora-llegada 09:30` o te la pregunta (por defecto, la hora actual). **Siempre pide confirmación
   antes de registrarla**, porque queda guardada en el sistema.
4. Llena el formulario.

```bash
python main.py rellenar visitas/2026-10-12_molina --ticket OS0134427 --hora-llegada 09:30
```

Si algo no cuadra con la navegación (otra pestaña, otra pantalla), el agente te lo dice y te deja abrir
el formulario a mano y seguir con ENTER. También puedes saltarte la navegación con `--sin-navegar`.

Por defecto NO pulsa "Registrar Formulario": revisas en pantalla y lo envías tú.
Con `--enviar` lo pulsa por ti, tras pedirte confirmación.

## El agente aprende con el tiempo

No reentrena el modelo: guarda una **memoria** (`memoria.json`) que mejora con cada informe que corriges.

1. `extraer` guarda lo que leyó la IA en `datos_llm.json` (dentro de la carpeta de la visita).
2. Tú corriges `datos.csv` en Excel.
3. Al ejecutar `rellenar` y responder `s` ("datos correctos"), el agente compara ambos y **aprende**:
   - **Tus correcciones** (leyó "X", era "Y"): se las muestra al modelo en la siguiente lectura.
   - **Tu estilo** al redactar detalle, recomendaciones y solución: lo imita.
   - **Cada equipo** (por N° de serie): recuerda tipo/n.º de ventanilla, versión de software, toma
     estabilizada y conectividad, y los rellena solo la próxima vez. Lo que cambia en cada visita
     (falla encontrada, horas, etc.) no se recuerda.
4. `python main.py memoria` muestra lo aprendido. Para empezar de cero, borra `memoria.json`.

Mejora cuantas más visitas corrijas, sobre todo si usas siempre el mismo N° de serie bien escrito.
Privacidad: las correcciones recordadas se envían a Gemini como parte de las instrucciones; no guardes
en la memoria nada que no quieras enviar. `memoria.json` es un archivo local en tu PC.

## Qué lee el modelo y qué escribes tú

| Del informe (LLM) | Tú en datos.csv (manual) |
|---|---|
| Hora inicio / fin, Nº de informe, Contador, Estado del equipo, Estado previo, Tipo de evaluación, Solución, Detalle de procedimiento, Recomendaciones, Repuestos | Tipo y número de ventanilla, Versión de software, Toma estabilizada, Conectividad, En Uso/En Desuso, Falla encontrada, Comentario de falla |

- **Solución** admite varias opciones separadas con ` | `.
- Los valores de los desplegables se comparan por texto (sin importar mayúsculas), así que escribe el
  nombre tal como aparece en la página. Si no existe, el agente te lo dice en la tabla de resultados.
- Falla encontrada trae por defecto "Ninguna falla" y su comentario "PRUEBA" (el sistema exige elegir uno).
  Si el equipo estaba En Desuso, cambia la falla: las opciones dependen de En Uso/En Desuso.

## Cosas de la página que el agente ya maneja

- Al cambiar **Hora de Inicio/Fin** la página muestra "¡Éxito!" y se recarga: el agente pulsa Aceptar y
  espera. Por eso las horas se llenan primero.
- Las **fotos se suben al elegirlas**: el agente espera a que termine y te avisa si hay error.
- Cualquier aviso de error/advertencia de la página aparece en la tabla de resultados.
- Tu sesión queda guardada en `perfil_chrome/`, no repites el login.

La letra manuscrita puede leerse mal: revisa siempre la tabla antes de rellenar.