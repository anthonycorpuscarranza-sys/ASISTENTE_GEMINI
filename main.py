#!/usr/bin/env python3
"""
Agente para rellenar el Formulario de Ticket (Cechriza) desde la foto del informe técnico.

Uso:
  python main.py extraer   visitas/2026-10-12_molina    # foto -> datos.csv (LLM)
  python main.py rellenar  visitas/2026-10-12_molina --ticket OS0134427
        # entra a Tickets Asignados > Rutas, abre ese ticket, registra la hora de llegada y llena el formulario
  python main.py rellenar  visitas/2026-10-12_molina --ticket OS0134427 --hora-llegada 09:30
  python main.py rellenar  visitas/2026-10-12_molina --ticket OS0134427 --ruta manana
  python main.py rellenar  visitas/2026-10-12_molina --sin-navegar   # tú abres el formulario a mano
  python main.py rellenar  visitas/2026-10-12_molina --enviar
  python main.py inspeccionar                           # lista los campos reales de la página
"""
import argparse
import base64
import csv
import datetime
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import yaml
from rich.console import Console
from rich.table import Table

console = Console()
BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
CAMPOS = CFG["campos"]
SEP = "|"  # separador para campos de varias opciones (Solución)


def norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# ------------------------------------------------------------------ #
# 1) EXTRAER: foto del informe -> JSON (LLM de visión)
# ------------------------------------------------------------------ #
def construir_prompt() -> str:
    lineas = []
    for k, c in CAMPOS.items():
        if c["origen"] != "llm":
            continue
        txt = f'- "{k}": {c["ayuda"]}'
        if c.get("opciones"):
            txt += "  Opciones válidas (copia el texto exacto): " + " | ".join(map(str, c["opciones"]))
        lineas.append(txt)
    return (
        "Eres un asistente que lee fotos de INFORMES TÉCNICOS manuscritos (empresa Cechriza, Perú).\n"
        "Extrae SOLO los siguientes campos y responde con un único objeto JSON, sin texto extra:\n"
        + "\n".join(lineas)
        + '\n\nAdemás incluye "dudosos": lista con las claves cuya lectura sea incierta '
        "(letra poco legible, sellos o firmas encima, etc.).\n"
        "Reglas: si un campo está vacío o ilegible usa null (no inventes). "
        "Casillas marcadas con X o tachadas cuentan como marcadas. Escribe en español."
    )


def llamar_gemini(ruta_imagen: Path) -> dict:
    """Lee la foto con la API de Gemini (tiene plan gratuito). Sin dependencias extra."""
    import os
    import urllib.error
    import urllib.request
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")

    clave = os.environ.get("GEMINI_API_KEY")
    if not clave:
        sys.exit("Falta la variable GEMINI_API_KEY (clave gratis en https://aistudio.google.com/apikey).")
    mime = mimetypes.guess_type(ruta_imagen)[0] or "image/jpeg"
    cuerpo = json.dumps({
        "contents": [{"parts": [
            {"text": construir_prompt()},
            {"inline_data": {"mime_type": mime,
                             "data": base64.standard_b64encode(ruta_imagen.read_bytes()).decode()}},
        ]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }).encode()

    ultimo_error = ""
    for modelo in CFG.get("modelos_gemini", ["gemini-3.8-flash"]):
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
            data=cuerpo, method="POST",
            headers={"x-goog-api-key": clave, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                datos = json.loads(r.read().decode())
            texto = datos["candidates"][0]["content"]["parts"][0]["text"]
            ini, fin = texto.find("{"), texto.rfind("}")
            console.print(f"[dim]Modelo usado: {modelo}[/]")
            return json.loads(texto[ini:fin + 1])
        except urllib.error.HTTPError as e:
            detalle = e.read().decode(errors="ignore")[:200]
            ultimo_error = f"{modelo}: HTTP {e.code} {detalle}"
            if e.code in (400, 401, 403) and "API key" in detalle:
                sys.exit("La clave GEMINI_API_KEY no es válida.")
            console.print(f"[yellow]{modelo} no disponible (HTTP {e.code}), pruebo el siguiente...[/]")
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            ultimo_error = f"{modelo}: respuesta inesperada ({type(e).__name__})"
            console.print(f"[yellow]{ultimo_error}, pruebo el siguiente...[/]")
    sys.exit(f"No pude leer el informe con Gemini. Último error: {ultimo_error}\n"
             "Si es límite de uso (429) espera un minuto; o usa proveedor: ninguno y llena datos.csv a mano.")


def llamar_llm(ruta_imagen: Path) -> dict:
    proveedor = CFG.get("proveedor", "gemini")
    if proveedor == "ninguno":
        return {}  # sin IA: datos.csv sale con los valores por defecto y lo llenas tú
    if proveedor == "anthropic":
        return llamar_anthropic(ruta_imagen)
    return llamar_gemini(ruta_imagen)


def llamar_anthropic(ruta_imagen: Path) -> dict:
    import anthropic  # import tardío: solo hace falta si usas proveedor: anthropic

    mime = mimetypes.guess_type(ruta_imagen)[0] or "image/jpeg"
    datos_b64 = base64.standard_b64encode(ruta_imagen.read_bytes()).decode()
    client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=CFG["modelo_llm"],
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": mime, "data": datos_b64}},
                {"type": "text", "text": construir_prompt()},
            ],
        }],
    )
    texto = resp.content[0].text
    ini, fin = texto.find("{"), texto.rfind("}")
    return json.loads(texto[ini:fin + 1])


def cmd_extraer(carpeta: Path):
    img = carpeta / CFG["imagen_informe"]
    if not img.exists():
        sys.exit(f"No encuentro la foto del informe: {img}")
    console.print(f"[cyan]Leyendo {img.name} (proveedor: {CFG.get('proveedor', 'gemini')})...[/]")
    leido = llamar_llm(img)
    dudosos = set(leido.get("dudosos") or [])

    filas = []
    for k, c in CAMPOS.items():
        valor = leido.get(k) if c["origen"] == "llm" else None
        if isinstance(valor, list):
            valor = f" {SEP} ".join(map(str, valor))
        if valor in (None, ""):
            valor = c.get("defecto", "")
        vacio = not str(valor).strip()
        revisar = "SI" if (k in dudosos or (vacio and c.get("obligatorio"))) else ""
        filas.append({"campo": k, "etiqueta": c["etiqueta"], "valor": valor,
                      "origen": c["origen"], "revisar": revisar})

    guardar_csv(carpeta / "datos.csv", filas)
    mostrar_tabla(filas)
    console.print(f"\n[green]Guardado en {carpeta / 'datos.csv'}[/]\n"
                  "Ábrelo en Excel, corrige/completa lo marcado 'SI' y luego ejecuta 'rellenar'.")


# ------------------------------------------------------------------ #
# 2) Tabla (CSV editable)
# ------------------------------------------------------------------ #
def guardar_csv(ruta: Path, filas):
    with ruta.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["campo", "etiqueta", "valor", "origen", "revisar"])
        w.writeheader()
        w.writerows(filas)


def leer_csv(ruta: Path):
    if not ruta.exists():
        sys.exit(f"Falta {ruta}. Ejecuta primero: python main.py extraer {ruta.parent}")
    with ruta.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def mostrar_tabla(filas):
    t = Table(title="Datos para el formulario", show_lines=False)
    for col in ("Campo", "Valor", "Origen", "Revisar"):
        t.add_column(col)
    for r in filas:
        estilo = "bold red" if r["revisar"] == "SI" else None
        t.add_row(r["etiqueta"], str(r["valor"]), r["origen"], r["revisar"], style=estilo)
    console.print(t)


# ------------------------------------------------------------------ #
# 3) RELLENAR: Playwright
# ------------------------------------------------------------------ #
def elegir_opcion(page, sel: str, texto: str, multiple: bool = False):
    """Elige opción(es) de un <select> (aunque Select2 lo oculte) comparando por texto."""
    opts = page.eval_on_selector_all(
        f"{sel} option", "els => els.map(e => [e.value, e.textContent])")
    deseados = [t.strip() for t in texto.split(SEP)] if multiple else [texto.strip()]
    valores = []
    for t in deseados:
        if not t:
            continue
        n = norm(t)
        hit = next((v for v, lab in opts if norm(lab) == n), None) \
            or next((v for v, lab in opts if n and n in norm(lab)), None)
        if hit is None:
            disponibles = ", ".join(norm(lab) for _, lab in opts[:12])
            raise ValueError(f"opción '{t}' no existe (hay: {disponibles}...)")
        valores.append(hit)
    # select_option dispara 'change' -> Select2 y el autoguardado de la página reaccionan
    page.select_option(sel, value=valores if multiple else valores[0], force=True)


def revisar_avisos(page, segundos: float = 3.0) -> str | None:
    """Espera a que se cierren los avisos SweetAlert. Si hay uno de error/advertencia,
    lo cierra y devuelve su texto."""
    import time
    fin = time.time() + segundos
    while time.time() < fin:
        pop = page.locator(".swal2-popup:visible")
        if pop.count() == 0:
            return None
        if page.locator(".swal2-icon.swal2-error:visible, .swal2-icon.swal2-warning:visible").count():
            msg = " ".join(pop.first.inner_text().split())
            btn = page.locator(".swal2-confirm:visible")
            if btn.count():
                btn.first.click()
            return msg
        page.wait_for_timeout(300)
    return None


def poner_hora(page, sel: str, valor: str) -> str:
    """Las horas muestran un aviso '¡Éxito!' (con botón Aceptar) y la página se recarga."""
    from playwright.sync_api import TimeoutError as PWTimeout
    page.fill(sel, valor[:5])
    try:
        page.wait_for_selector(".swal2-popup:visible", timeout=15000)
    except PWTimeout:
        return "ok (sin aviso)"
    if page.locator(".swal2-icon.swal2-error:visible").count():
        msg = " ".join(page.locator(".swal2-popup:visible").first.inner_text().split())
        page.click(".swal2-confirm")
        return f"AVISO: {msg}"
    try:
        with page.expect_navigation(timeout=20000):
            page.click(".swal2-confirm")
    except PWTimeout:
        pass
    page.wait_for_load_state("load")
    page.wait_for_timeout(1200)  # deja que Select2 y los scripts de la página arranquen
    return "ok"


def buscar_archivo(carpeta: Path, nombre: str) -> Path | None:
    p = carpeta / nombre
    if p.exists():
        return p
    for ext in (".jpg", ".jpeg", ".png", ".pdf", ".JPG", ".JPEG", ".PNG", ".PDF"):
        q = carpeta / (Path(nombre).stem + ext)
        if q.exists():
            return q
    return None


def rellenar_campo(page, c, valor, carpeta: Path) -> str:
    sel, tipo = c["selector"], c["tipo"]
    if tipo == "hora":
        return poner_hora(page, sel, valor)
    if tipo == "texto":
        page.fill(sel, valor)
    elif tipo == "select":
        elegir_opcion(page, sel, valor)
    elif tipo == "multiselect":
        elegir_opcion(page, sel, valor, multiple=True)
    elif tipo == "radio":
        mapa = {norm(k): v for k, v in (c.get("mapa") or {}).items()}
        v = mapa.get(norm(valor))
        if v is None:
            raise ValueError(f"valor '{valor}' no válido (usa: {', '.join(c['mapa'])})")
        page.check(sel.format(valor=v))
    elif tipo == "archivo":
        ruta = buscar_archivo(carpeta, valor)
        if ruta is None:
            return f"archivo no encontrado: {valor}"
        page.set_input_files(sel, str(ruta))  # la página sube la foto al instante
        msg = revisar_avisos(page, segundos=25)
        return f"AVISO: {msg}" if msg else "ok"
    page.wait_for_timeout(350)  # la página autoguarda al cambiar cada campo
    msg = revisar_avisos(page, segundos=3)
    return f"AVISO: {msg}" if msg else "ok"


# ------------------------------------------------------------------ #
# 3b) NAVEGAR: Tickets Asignados > Rutas > ticket > hora de llegada
# ------------------------------------------------------------------ #
JS_TICKETS = """
() => {
  const out = [];
  const propio = e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ').trim();
  const cabezas = [...document.querySelectorAll('*')].filter(e => /^Ticket\\s*#\\S+/.test(propio(e)));
  cabezas.forEach((h, i) => {
    let c = h;
    for (let k = 0; k < 8 && c.parentElement; k++) {
      c = c.parentElement;
      const t = c.innerText || '';
      if (/Agencia/.test(t) && /Serie/.test(t)) break;
    }
    c.setAttribute('data-agente-idx', String(i));
    h.setAttribute('data-agente-head', String(i));
    const id = (propio(h).match(/#\\s*(\\S+)/) || [])[1] || '';
    out.push({idx: i, id: id, texto: (c.innerText || '').replace(/\\s+/g, ' ').trim()});
  });
  return out;
}
"""


def abrir_ticket(ctx, page, ticket, ruta):
    """Entra a Tickets Asignados > Rutas, elige la tarjeta del ticket y la abre.
    Devuelve la página donde quedó el formulario."""
    from playwright.sync_api import TimeoutError as PWTimeout

    nav = CFG.get("navegacion", {})
    ruta_url = nav.get("ruta_url", "ticket_asignado")
    try:  # como lo haces tú: menú lateral
        page.click("a.sidebar-title:has-text('Tickets Asignados')", timeout=3000)
        page.click(f"a[href='{ruta_url}']", timeout=3000)
        page.wait_for_load_state("load")
    except Exception:  # si el menú no responde, directo por la dirección
        page.goto(urljoin(page.url, ruta_url))
    if ruta == "manana":
        page.click(f"text={nav.get('tab_manana', 'Ruta de Mañana')}")
        page.wait_for_timeout(800)
    try:
        page.get_by_text(re.compile(r"Ticket\s*#")).first.wait_for(timeout=15000)
    except PWTimeout:
        raise RuntimeError(f"No veo tickets en la ruta '{ruta}'.")
    page.wait_for_timeout(800)

    tickets = page.evaluate(JS_TICKETS)
    t = Table(title=f"Tickets asignados ({'Ruta de Mañana' if ruta == 'manana' else 'Ruta del Día'})")
    t.add_column("#"); t.add_column("Ticket"); t.add_column("Detalle")
    for i, x in enumerate(tickets, 1):
        t.add_row(str(i), x["id"], x["texto"][:120])
    console.print(t)

    if ticket:
        buscado = ticket.strip().lstrip("#").upper()
        elegido = next((x for x in tickets if x["id"].upper() == buscado), None)
        if not elegido:
            raise RuntimeError(f"El ticket {ticket} no está en esta ruta (hay: {', '.join(x['id'] for x in tickets)}).")
    elif len(tickets) == 1:
        elegido = tickets[0]
    else:
        n = input("Número (#) de la fila del ticket a abrir: ").strip()
        if not n.isdigit() or not 1 <= int(n) <= len(tickets):
            raise RuntimeError("Número de fila no válido.")
        elegido = tickets[int(n) - 1]
    console.print(f"[cyan]Abriendo ticket {elegido['id']}...[/]")

    i, n0 = elegido["idx"], len(ctx.pages)
    objetivos = [f"[data-agente-idx='{i}']", f"[data-agente-head='{i}']",
                 f"[data-agente-idx='{i}'] a, [data-agente-idx='{i}'] button"]
    for sel in objetivos:
        try:
            page.locator(sel).first.click(timeout=3000)
        except Exception:
            continue
        page.wait_for_timeout(1500)
        if len(ctx.pages) > n0:  # el ticket se abrió en otra pestaña
            page = ctx.pages[-1]
            page.wait_for_load_state("load")
        try:
            page.wait_for_selector("#hora_llegada", state="attached", timeout=8000)
            return page
        except PWTimeout:
            continue
    raise RuntimeError("Abrí el ticket pero no encontré el formulario (campo Hora Llegada).")


def registrar_llegada(page, hora: str) -> str:
    """Registra la hora de llegada; sin ella la página no muestra el formulario completo."""
    btn = page.locator("#registrar_hora_llegada")
    if btn.count() == 0 or not btn.is_visible():
        actual = (page.input_value("#hora_llegada") or "")[:5]
        if actual:
            return f"ya estaba registrada ({actual})"
        return "ERROR: no hay hora de llegada ni botón para registrarla"
    page.fill("#hora_llegada", hora)
    btn.click()
    page.wait_for_selector(".swal2-confirm", state="visible", timeout=10000)
    page.click(".swal2-confirm")  # "¿Está seguro? -> Sí, registrar"
    try:
        page.wait_for_selector(".swal2-icon.swal2-success, .swal2-icon.swal2-error", state="attached", timeout=15000)
    except Exception:
        return "ERROR: la página no respondió al registrar la hora"
    if page.locator(".swal2-icon.swal2-error:visible").count():
        msg = " ".join(page.locator(".swal2-popup:visible").first.inner_text().split())
        page.click(".swal2-confirm")
        return f"ERROR: {msg}"
    with page.expect_navigation(timeout=20000):  # tras "OK" la página se recarga
        page.click(".swal2-confirm")
    page.wait_for_load_state("load")
    page.wait_for_timeout(1500)
    return f"registrada ({hora})"


def cmd_rellenar(carpeta: Path, enviar: bool, ticket=None, ruta="dia", hora_llegada=None, navegar=True):
    from playwright.sync_api import sync_playwright

    filas = leer_csv(carpeta / "datos.csv")
    mostrar_tabla(filas)
    if input("\n¿Los datos están correctos? (s/n): ").strip().lower() != "s":
        sys.exit("Corrige datos.csv y vuelve a ejecutar.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(BASE / CFG["perfil_navegador"]), headless=False, viewport={"width": 1400, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CFG["url_formulario"])
        input("\nInicia sesión en el sistema (si no lo está) y pulsa ENTER aquí... ")

        try:
            if not navegar:
                raise RuntimeError("modo manual (--sin-navegar)")
            page = abrir_ticket(ctx, page, ticket, ruta)
            necesita = page.locator("#registrar_hora_llegada").count() and page.locator("#registrar_hora_llegada").is_visible()
            if necesita:
                ahora = datetime.datetime.now().strftime("%H:%M")
                hora = hora_llegada or input(f"Hora de llegada a registrar HH:MM [{ahora}]: ").strip() or ahora
                if input(f"Se REGISTRARÁ la hora de llegada {hora} en el ticket (queda guardada en el sistema). ¿Continuar? (s/n): ").strip().lower() != "s":
                    sys.exit("Cancelado antes de registrar la hora de llegada.")
                estado = registrar_llegada(page, hora)
            else:
                estado = registrar_llegada(page, hora_llegada or "")
            console.print(f"[cyan]Hora de llegada: {estado}[/]")
            if estado.startswith("ERROR"):
                sys.exit("Corrige la hora de llegada y vuelve a ejecutar.")
        except RuntimeError as e:
            console.print(f"[yellow]{e}[/]")
            input("Abre tú el Formulario de Ticket (con la hora de llegada registrada) y pulsa ENTER aquí... ")
            page = ctx.pages[-1]

        resultados = []
        for r in filas:
            c, valor = CAMPOS[r["campo"]], (r["valor"] or "").strip()
            if not valor:
                resultados.append((r["etiqueta"], "vacío, omitido"))
                continue
            try:
                resultados.append((r["etiqueta"], rellenar_campo(page, c, valor, carpeta)))
            except Exception as e:
                resultados.append((r["etiqueta"], f"ERROR: {str(e).splitlines()[0][:110]}"))

        t = Table(title="Resultado del llenado")
        t.add_column("Campo"); t.add_column("Estado")
        for campo, estado in resultados:
            t.add_row(campo, estado, style=None if estado.startswith("ok") else "yellow")
        console.print(t)

        if enviar:
            if input("¿Registrar el formulario ahora? (s/n): ").strip().lower() == "s":
                page.click(CFG["boton_registrar"])
                page.wait_for_timeout(1500)
                aviso = revisar_avisos(page, segundos=10)
                console.print(f"[yellow]La página respondió: {aviso}[/]" if aviso
                              else "[green]Formulario enviado (revisa la pantalla).[/]")
        else:
            console.print("[cyan]Revisa el formulario en el navegador y pulsa 'Registrar Formulario' tú mismo.[/]")
        input("ENTER para cerrar el navegador... ")
        ctx.close()


# ------------------------------------------------------------------ #
# 4) INSPECCIONAR: descubrir selectores reales de la página
# ------------------------------------------------------------------ #
JS_INSPECCION = """
() => [...document.querySelectorAll('input, select, textarea, button')].map(e => {
  const lab = e.labels && e.labels[0] ? e.labels[0].innerText.trim()
            : (e.closest('tr,div')?.innerText || '').trim().slice(0, 40).replace(/\\n/g, ' ');
  return {tag: e.tagName.toLowerCase(), type: e.type || '', name: e.name || '', id: e.id || '', texto: lab};
})
"""


def cmd_inspeccionar():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(BASE / CFG["perfil_navegador"]), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CFG["url_formulario"])
        input("Abre el formulario del equipo y pulsa ENTER aquí... ")
        elementos = page.evaluate(JS_INSPECCION)
        t = Table(title="Campos detectados en la página")
        for col in ("tag", "type", "name", "id", "cerca de"):
            t.add_column(col)
        for e in elementos:
            t.add_row(e["tag"], e["type"], e["name"], e["id"], e["texto"])
        console.print(t)
        out = BASE / "campos_detectados.csv"
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["tag", "type", "name", "id", "texto"])
            w.writeheader(); w.writerows(elementos)
        console.print(f"[green]Guardado en {out}.[/]")
        ctx.close()


# ------------------------------------------------------------------ #
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for nombre in ("extraer", "rellenar"):
        sp = sub.add_parser(nombre)
        sp.add_argument("carpeta", type=Path)
        if nombre == "rellenar":
            sp.add_argument("--enviar", action="store_true", help="pulsar 'Registrar Formulario' (pide confirmación)")
            sp.add_argument("--ticket", help="Nº de ticket a abrir en Rutas, p. ej. OS0134427")
            sp.add_argument("--ruta", choices=["dia", "manana"], default="dia", help="pestaña donde buscar el ticket")
            sp.add_argument("--hora-llegada", help="HH:MM a registrar si el ticket aún no tiene hora de llegada")
            sp.add_argument("--sin-navegar", action="store_true", help="no navegar: tú abres el formulario a mano")
    sub.add_parser("inspeccionar")
    a = ap.parse_args()

    if a.cmd == "extraer":
        cmd_extraer(a.carpeta)
    elif a.cmd == "rellenar":
        cmd_rellenar(a.carpeta, a.enviar, a.ticket, a.ruta, a.hora_llegada, not a.sin_navegar)
    else:
        cmd_inspeccionar()


if __name__ == "__main__":
    main()
